"""
backtest_multibagger.py
=======================
Simulates the multi-bagger strategy from 2008 using price data only.

IMPORTANT — WHAT IS PROXIED
-----------------------------
Historical fundamental data (ROE, revenue CAGR, D/E) is unavailable via
yfinance for pre-2015 periods.  This backtest uses price-derived proxies:

  MB proxy                     Historical price proxy
  ──────────────────────       ───────────────────────────────────────
  Revenue acceleration         Momentum score delta [14 vs 63 periods]
  Cap position                 Market cap tier from universe definition
  Operating margin             Not proxied — structural gate acts as moat filter
  Technical gate               SMA_50 > SMA_200, Close > SMA_200, rising
  Structural gap match         Sector keyword filter (approximate)

HOW THIS DIFFERS FROM THE LT MOMENTUM BACKTEST
-----------------------------------------------
  LT backtest : rotation model — exit any position NOT in top-N at rebalance.
  MB backtest : conviction-hold model — hold as long as the structural gate
                (SMA_50/200) holds, regardless of relative ranking.
                Only enter when gate passes AND momentum is in the top half.
                Exit ONLY on structural gate failure (SMA_50 < SMA_200).
                This is how multi-baggers are actually captured: buy good
                companies in structural tailwinds and hold them.

V2 ENHANCEMENTS (return-improving changes)
------------------------------------------
  1. Conviction sizing      — STRONG ideas (top 20% mom + high accel) get 1.5×
                              the capital allocation of MODERATE ideas.
  2. Faster crash recovery  — review interval drops from 63d → 21d when the
                              market proxy recovers from a >20% drawdown.
  3. Partial profit-taking  — trim 33% of a position when it is up 40%.
                              Locks in gains; core position keeps running.
  4. Trailing stop          — exit if price falls >20% from its post-entry peak.
                              Faster than waiting for SMA breakdown.
  5. Regime exposure cap    — no new entries when proxy index is below SMA200.
                              Capital stays in cash until the market confirms
                              a bull regime.  Existing positions are NOT forced
                              out — the SMA breakdown handles that.

CRISIS PERIODS COVERED
----------------------
  2008–2009  Global Financial Crisis          (worst drawdown period)
  2011       Euro debt crisis / India rate hike
  2015–2016  China hard-landing fear / US rate liftoff
  2018       US–China trade war
  2020       COVID crash + recovery
  2022       Fed tightening / Ukraine war
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Optional

import numpy as np
import pandas as pd

BENCHMARK_TICKER = {"IN": "^NSEI", "US": "^GSPC", "EU": "^STOXX50E"}

# Crisis period labels (for annotation in report)
CRISIS_PERIODS = [
    ("2008-09-15", "2009-03-09",  "GFC"),
    ("2011-07-22", "2011-11-25",  "Euro crisis"),
    ("2015-06-12", "2016-02-11",  "China fear"),
    ("2018-01-26", "2018-12-24",  "Trade war"),
    ("2020-02-19", "2020-03-23",  "COVID crash"),
    ("2022-01-03", "2022-10-12",  "Rate hike"),
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ok(v) -> bool:
    if v is None:
        return False
    try:
        return math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


def _sharpe(daily_ret: pd.Series, rf_annual: float = 0.05) -> float:
    if len(daily_ret) < 20:
        return 0.0
    daily_rf = (1 + rf_annual) ** (1 / 252) - 1
    excess   = daily_ret - daily_rf
    std      = float(excess.std())
    return float((excess.mean() / std) * math.sqrt(252)) if std > 0 else 0.0


def _sortino(daily_ret: pd.Series, rf_annual: float = 0.05) -> float:
    if len(daily_ret) < 20:
        return 0.0
    ann_ret = float(daily_ret.mean() * 252)
    neg     = daily_ret[daily_ret < 0]
    denom   = float(neg.std() * math.sqrt(252)) if len(neg) > 1 else 1.0
    return (ann_ret - rf_annual) / denom if denom > 0 else 0.0


def _max_dd(curve: pd.Series) -> float:
    peak = curve.cummax()
    return float(((curve - peak) / peak).min())


def _calmar(cagr: float, max_dd: float) -> float:
    return cagr / abs(max_dd) if max_dd != 0 else 0.0


# ── Momentum proxy ────────────────────────────────────────────────────────────

def _compute_momentum_score(close_m: pd.DataFrame) -> pd.DataFrame:
    """
    Mean return across [14, 30, 63] lookback periods.
    Proxies revenue acceleration: short-term momentum stronger than long-term.
    """
    scores = pd.DataFrame(0.0, index=close_m.index, columns=close_m.columns)
    counts = pd.DataFrame(0,   index=close_m.index, columns=close_m.columns)
    for p in [14, 30, 63]:
        past  = close_m.shift(p)
        valid = (past > 0) & past.notna()
        ret   = (close_m - past) / past.where(valid, np.nan)
        scores = scores + ret.where(valid, 0.0)
        counts = counts + valid.astype(int)
    return scores / counts.where(counts > 0, np.nan)


def _compute_acceleration_score(close_m: pd.DataFrame) -> pd.DataFrame:
    """
    Short-term momentum (14d) minus long-term momentum (63d).
    Positive = recent price velocity exceeds historical = acceleration proxy.
    """
    past14 = close_m.shift(14)
    past63 = close_m.shift(63)
    v14 = (close_m - past14) / past14.where(past14 > 0, np.nan)
    v63 = (close_m - past63) / past63.where(past63 > 0, np.nan)
    return v14 - v63


# ── Technical structural gate ─────────────────────────────────────────────────

def _compute_structural_gate(
    close_m: pd.DataFrame,
    sma50_m: pd.DataFrame,
    sma200_m: pd.DataFrame,
) -> pd.DataFrame:
    """Returns bool DataFrame: True where SMA_50>SMA_200, Close>SMA_200, SMA_50 rising 5d."""
    sma50_5d = sma50_m.shift(5)
    return (
        (sma50_m  > sma200_m)  &
        (close_m  > sma200_m)  &
        (sma50_m  > sma50_5d)
    )


# ── Core backtest ─────────────────────────────────────────────────────────────

def run_mb_backtest(
    market: str,
    data_map: dict,
    start: str,
    end: str,
    equity: float              = 100_000,
    max_positions: int         = 8,
    review_days: int           = 63,
    commission: float          = 0.001,
    slippage: float            = 0.001,
    min_entry_rank: float      = 0.40,
    require_acceleration: bool = True,
    # ── V2 enhancement flags ───────────────────────────────────────────────
    use_conviction_sizing: bool = True,   # STRONG ideas get 1.5× allocation
    partial_profit_at: float    = 1.00,   # trim at +100% (doubled) unrealised (0 = off)
    partial_profit_trim: float  = 0.50,   # sell half at partial event; rest compounds
    trailing_stop: float        = 0.20,   # exit if 20% below peak (0 = off)
    stop_loss: float            = 0.10,   # hard stop: exit full pos if 10% below entry
    regime_scaling: bool        = True,   # no new entries when market < SMA200
    recovery_fast_review: int   = 21,     # review interval during crash recovery
) -> dict:
    """
    Conviction-hold multi-bagger backtest (V3 — refined exits).

    V3 changes vs V2
    ----------------
    1. Hard stop loss (10%)    Exit full position if price drops 10% below entry cost.
    2. Partial at doubling     Sell 50% when position is up 100% (stock doubled).
                               Freed cash is redeployed in next review into best candidates.
                               (was: trim 33% at +40%)

    V2 changes vs V1
    ----------------
    3. Conviction sizing       STRONG (top 20% mom + accel > 0.05) → 1.5× alloc.
    4. Crash recovery speed    Review interval → 21d when market recovers from >20% DD.
    5. Trailing stop (20%)     Exit if price falls >20% from post-entry peak (after +15% gain).
    6. Regime cap              No new entries while proxy index < SMA200.
    """
    start_ts = pd.Timestamp(start)
    end_ts   = pd.Timestamp(end)

    all_tickers = [t for t, df in data_map.items() if len(df) > 0]
    raw_close  = {t: data_map[t]["Close"]   for t in all_tickers if "Close"   in data_map[t].columns}
    raw_sma50  = {t: data_map[t]["SMA_50"]  for t in all_tickers if "SMA_50"  in data_map[t].columns}
    raw_sma200 = {t: data_map[t]["SMA_200"] for t in all_tickers if "SMA_200" in data_map[t].columns}

    common = list(set(raw_close) & set(raw_sma50) & set(raw_sma200))
    if not common:
        return {"error": "No tickers with SMA_50, SMA_200, Close columns"}

    all_dates = sorted({
        d for t in common for d in data_map[t].index
        if start_ts <= d <= end_ts
    })
    if not all_dates:
        return {"error": "No data in specified date range"}

    idx = pd.DatetimeIndex(all_dates)

    close_m  = pd.DataFrame({t: raw_close[t]  for t in common}).reindex(idx).ffill()
    sma50_m  = pd.DataFrame({t: raw_sma50[t]  for t in common}).reindex(idx).ffill()
    sma200_m = pd.DataFrame({t: raw_sma200[t] for t in common}).reindex(idx).ffill()

    # Pre-compute matrices
    structural_m = _compute_structural_gate(close_m, sma50_m, sma200_m)
    mom_m        = _compute_momentum_score(close_m)
    accel_m      = _compute_acceleration_score(close_m)
    breakdown_m  = sma50_m < sma200_m

    # ── V2: Market proxy for regime and crash-recovery signals ────────────────
    proxy_index  = close_m.mean(axis=1)
    proxy_sma200 = proxy_index.rolling(200, min_periods=50).mean()
    # Rolling 6-month minimum drawdown from 1-year peak
    proxy_1yr_max = proxy_index.rolling(252, min_periods=1).max()
    proxy_dd      = (proxy_index - proxy_1yr_max) / proxy_1yr_max.where(proxy_1yr_max > 0, np.nan)
    proxy_min_6m  = proxy_dd.rolling(126, min_periods=1).min()
    # "in crash recovery" = was down >20% within 6 months AND now above SMA200
    in_bull_m      = proxy_index > proxy_sma200.fillna(proxy_index)
    in_recovery_m  = (proxy_min_6m < -0.20) & in_bull_m

    # ── Simulation ────────────────────────────────────────────────────────────
    cash           = equity
    portfolio      = {}    # {ticker: shares}
    entry_price    = {}    # {ticker: avg fill price}
    entry_date     = {}    # {ticker: entry date}
    peak_price     = {}    # {ticker: highest close since entry}  — for trailing stop
    partial_taken  = {}    # {ticker: True}  — one partial event per position
    entry_conv     = {}    # {ticker: 1.0 or 1.5}  — conviction weight at entry
    eq_curve       = []
    trades         = []
    next_review    = all_dates[0]

    for date in all_dates:
        prices = close_m.loc[date]

        # ── Update peak prices ────────────────────────────────────────────────
        for t in list(portfolio.keys()):
            px = float(prices.get(t, 0))
            if px > 0:
                peak_price[t] = max(peak_price.get(t, px), px)

        # ── Exit 1: trailing stop ─────────────────────────────────────────────
        # Only activate after position has gained >=15% (avoids premature exits
        # on normal early-hold volatility before the thesis is confirmed).
        if trailing_stop > 0:
            for t in list(portfolio.keys()):
                pk = peak_price.get(t, 0)
                px = float(prices.get(t, 0))
                ep = entry_price.get(t, 0)
                gain = (px / ep - 1) if ep > 0 else 0
                if pk > 0 and px > 0 and gain >= 0.15 and px < pk * (1 - trailing_stop):
                    shares = portfolio.pop(t)
                    ep     = entry_price.pop(t, 0)
                    ed     = entry_date.pop(t, date)
                    peak_price.pop(t, None)
                    partial_taken.pop(t, None)
                    entry_conv.pop(t, None)
                    fill   = px * (1 - slippage)
                    cash  += shares * fill * (1 - commission)
                    pnl    = (fill * (1 - commission) - ep) * shares
                    trades.append({
                        "date": date, "action": "SELL", "ticker": t,
                        "shares": shares, "price": px, "entry_price": ep,
                        "pnl": pnl, "hold_days": (date - ed).days,
                        "reason": "trailing_stop",
                    })

        # ── Exit 2: hard stop loss (10% below entry) ─────────────────────────
        if stop_loss > 0:
            for t in list(portfolio.keys()):
                ep = entry_price.get(t, 0)
                px = float(prices.get(t, 0))
                if ep > 0 and px > 0 and px < ep * (1 - stop_loss):
                    shares = portfolio.pop(t)
                    ed     = entry_date.pop(t, date)
                    entry_price.pop(t, None)
                    peak_price.pop(t, None)
                    partial_taken.pop(t, None)
                    entry_conv.pop(t, None)
                    fill   = px * (1 - slippage)
                    cash  += shares * fill * (1 - commission)
                    pnl    = (fill * (1 - commission) - ep) * shares
                    trades.append({
                        "date": date, "action": "SELL", "ticker": t,
                        "shares": shares, "price": px, "entry_price": ep,
                        "pnl": pnl, "hold_days": (date - ed).days,
                        "reason": "stop_loss",
                    })

        # ── Exit 3: structural breakdown ──────────────────────────────────────
        bd_row = breakdown_m.loc[date]
        for t in list(portfolio.keys()):
            if bd_row.get(t, False):
                shares = portfolio.pop(t)
                ep     = entry_price.pop(t, 0)
                ed     = entry_date.pop(t, date)
                peak_price.pop(t, None)
                partial_taken.pop(t, None)
                entry_conv.pop(t, None)
                px     = float(prices.get(t, 0))
                fill   = px * (1 - slippage)
                cash  += shares * fill * (1 - commission)
                pnl    = (fill * (1 - commission) - ep) * shares
                trades.append({
                    "date": date, "action": "SELL", "ticker": t,
                    "shares": shares, "price": px, "entry_price": ep,
                    "pnl": pnl, "hold_days": (date - ed).days,
                    "reason": "breakdown",
                })

        # ── Partial profit-taking ─────────────────────────────────────────────
        if partial_profit_at > 0:
            for t in list(portfolio.keys()):
                if partial_taken.get(t, False):
                    continue
                ep = entry_price.get(t, 0)
                px = float(prices.get(t, 0))
                if ep > 0 and px > 0 and (px / ep - 1) >= partial_profit_at:
                    shares_held  = portfolio[t]
                    shares_trim  = math.floor(shares_held * partial_profit_trim)
                    if shares_trim >= 1:
                        fill     = px * (1 - slippage)
                        proceeds = shares_trim * fill * (1 - commission)
                        pnl      = (fill * (1 - commission) - ep) * shares_trim
                        cash    += proceeds
                        portfolio[t] -= shares_trim
                        partial_taken[t] = True
                        trades.append({
                            "date": date, "action": "SELL_PARTIAL", "ticker": t,
                            "shares": shares_trim, "price": px, "entry_price": ep,
                            "pnl": pnl, "hold_days": (date - entry_date.get(t, date)).days,
                            "reason": "partial_profit",
                        })

        # ── Periodic review: look for new entries ─────────────────────────────
        date_in_bull     = bool(in_bull_m.get(date, True))
        date_in_recovery = bool(in_recovery_m.get(date, False))
        # Faster review if crash recovery; skip new entries in bear regime
        effective_review = recovery_fast_review if date_in_recovery else review_days

        if date >= next_review and len(portfolio) < max_positions:
            # Regime cap: don't add new positions in bear regime
            if not regime_scaling or date_in_bull or date_in_recovery:
                mom_today   = mom_m.loc[date].dropna()
                accel_today = accel_m.loc[date].dropna()
                n_slots = max_positions - len(portfolio)

                if n_slots > 0 and len(mom_today) > 0:
                    rank_cut      = mom_today.quantile(min_entry_rank)
                    strong_cut    = mom_today.quantile(0.80)  # top 20% = STRONG

                    candidates = [
                        t for t in mom_today.index
                        if t not in portfolio
                        and structural_m.loc[date].get(t, False)
                        and mom_today[t] >= rank_cut
                        and (
                            not require_acceleration
                            or (t in accel_today and _ok(accel_today[t]) and accel_today[t] > 0)
                        )
                    ]
                    candidates.sort(key=lambda t: mom_today.get(t, 0), reverse=True)

                    held_val  = sum(portfolio[t] * float(prices.get(t, 0)) for t in portfolio)
                    curr_eq   = cash + held_val
                    base_alloc = curr_eq / max_positions   # one equal slot

                    for t in candidates[:n_slots]:
                        px = float(prices.get(t, 0))
                        if px <= 0:
                            continue

                        # Conviction weight: STRONG (1.5×) or MODERATE (1.0×)
                        if use_conviction_sizing:
                            is_strong = (
                                mom_today[t] >= strong_cut
                                and t in accel_today
                                and _ok(accel_today[t])
                                and accel_today[t] >= 0.05
                            )
                            cw = 1.5 if is_strong else 1.0
                        else:
                            cw = 1.0

                        alloc  = base_alloc * cw
                        fill   = px * (1 + slippage)
                        shares = math.floor(alloc / (fill * (1 + commission)))
                        cost   = shares * fill * (1 + commission)
                        if shares > 0 and cost <= cash:
                            cash -= cost
                            portfolio[t]   = portfolio.get(t, 0) + shares
                            entry_price[t] = fill * (1 + commission)   # per-share cost
                            entry_date[t]  = date
                            peak_price[t]  = px
                            entry_conv[t]  = cw
                            trades.append({
                                "date": date, "action": "BUY", "ticker": t,
                                "shares": shares, "price": px,
                                "conviction": "STRONG" if cw > 1.0 else "MODERATE",
                                "reason": "entry",
                            })

            next_review = date + pd.Timedelta(days=effective_review)

        # Mark to market
        port_val = sum(portfolio[t] * float(prices.get(t, 0)) for t in portfolio)
        eq_curve.append({"date": date, "equity": cash + port_val})

    # ── Metrics ───────────────────────────────────────────────────────────────
    eq_s = pd.DataFrame(eq_curve).set_index("date")["equity"]
    if len(eq_s) < 2:
        return {"error": "Insufficient equity curve data"}

    n_yrs     = max((eq_s.index[-1] - eq_s.index[0]).days / 365.25, 0.01)
    total_ret = float(eq_s.iloc[-1] / eq_s.iloc[0]) - 1
    cagr      = float((eq_s.iloc[-1] / eq_s.iloc[0]) ** (1 / n_yrs)) - 1
    max_dd    = _max_dd(eq_s)
    daily_ret = eq_s.pct_change().dropna()
    sharpe    = _sharpe(daily_ret)
    sortino   = _sortino(daily_ret)
    calmar    = _calmar(cagr, max_dd)

    sell_trades = [t for t in trades if t["action"] in ("SELL", "SELL_PARTIAL")]
    buy_trades  = [t for t in trades if t["action"] == "BUY"]

    hold_days_list = [t["hold_days"] for t in sell_trades if "hold_days" in t]
    avg_hold   = sum(hold_days_list) / len(hold_days_list) if hold_days_list else None
    median_hold = float(np.median(hold_days_list)) if hold_days_list else None

    # Win rate from closed trades
    pnl_list = [t["pnl"] for t in sell_trades if "pnl" in t]
    win_rate = sum(1 for p in pnl_list if p > 0) / len(pnl_list) if pnl_list else None

    # Avg win vs avg loss
    wins  = [p for p in pnl_list if p > 0]
    losses= [p for p in pnl_list if p <= 0]
    avg_win  = sum(wins)  / len(wins)   if wins   else None
    avg_loss = sum(losses)/ len(losses) if losses else None

    # Year-by-year
    annual_returns: dict[int, float] = {}
    for yr, grp in eq_s.groupby(eq_s.index.year):
        annual_returns[int(yr)] = float(grp.iloc[-1] / grp.iloc[0] - 1)

    # Crisis period drawdowns from strategy equity curve
    crisis_dd: dict[str, float] = {}
    for cs, ce, label in CRISIS_PERIODS:
        try:
            cs_ts, ce_ts = pd.Timestamp(cs), pd.Timestamp(ce)
            seg = eq_s[(eq_s.index >= cs_ts) & (eq_s.index <= ce_ts)]
            if len(seg) > 1:
                crisis_dd[label] = _max_dd(seg)
        except Exception:
            pass

    # Benchmark
    bench_info: dict = {}
    bench_ticker = BENCHMARK_TICKER.get(market, "^NSEI")
    try:
        import yfinance as yf
        bdf = yf.download(bench_ticker, start=start, end=end,
                          progress=False, auto_adjust=True)
        if not bdf.empty:
            bc     = bdf["Close"].squeeze().dropna()
            b0, b1 = float(bc.iloc[0]), float(bc.iloc[-1])
            b_tot  = (b1 / b0) - 1
            b_cagr = (b1 / b0) ** (1 / n_yrs) - 1
            b_dd   = _max_dd(bc)
            b_sh   = _sharpe(bc.pct_change().dropna())
            b_ann: dict[int, float] = {}
            for yr, grp in bc.groupby(bc.index.year):
                b_ann[int(yr)] = float(grp.iloc[-1] / grp.iloc[0] - 1)
            bench_info = {
                "ticker":         bench_ticker,
                "cagr":           b_cagr,
                "total_return":   b_tot,
                "max_dd":         b_dd,
                "sharpe":         b_sh,
                "annual_returns": b_ann,
            }
    except Exception as exc:
        bench_info = {"error": str(exc)}

    # Top tickers by hold duration (conviction held the longest)
    hold_by_ticker: dict[str, list] = {}
    for t in sell_trades:
        hold_by_ticker.setdefault(t["ticker"], []).append(t.get("hold_days", 0))
    avg_hold_by_ticker = {
        tk: sum(v) / len(v)
        for tk, v in hold_by_ticker.items()
    }
    top_by_hold = sorted(avg_hold_by_ticker.items(), key=lambda x: x[1], reverse=True)[:15]

    # Final holdings
    final_holdings = {
        t: {
            "shares": s,
            "price":  float(close_m[t].iloc[-1]) if t in close_m.columns else 0.0,
            "entry":  float(entry_price.get(t, 0)),
        }
        for t, s in portfolio.items()
    }

    return {
        "market":               market,
        "start":                str(eq_s.index[0].date()),
        "end":                  str(eq_s.index[-1].date()),
        "initial_equity":       equity,
        "final_equity":         float(eq_s.iloc[-1]),
        "total_return":         total_ret,
        "cagr":                 cagr,
        "max_dd":               max_dd,
        "sharpe":               sharpe,
        "sortino":              sortino,
        "calmar":               calmar,
        "n_buys":               len(buy_trades),
        "n_sells":              len(sell_trades),
        "n_breakdown":          sum(1 for t in sell_trades if t.get("reason") == "breakdown"),
        "n_trailing_stop":      sum(1 for t in sell_trades if t.get("reason") == "trailing_stop"),
        "n_stop_loss":          sum(1 for t in sell_trades if t.get("reason") == "stop_loss"),
        "n_partial_profit":     sum(1 for t in sell_trades if t.get("reason") == "partial_profit"),
        "n_strong_entries":     sum(1 for t in buy_trades if t.get("conviction") == "STRONG"),
        "n_moderate_entries":   sum(1 for t in buy_trades if t.get("conviction") == "MODERATE"),
        "avg_hold_days":        avg_hold,
        "median_hold_days":     median_hold,
        "win_rate":             win_rate,
        "avg_win":              avg_win,
        "avg_loss":             avg_loss,
        "annual_returns":       annual_returns,
        "crisis_drawdowns":     crisis_dd,
        "top_by_hold":          top_by_hold,
        "equity_curve":         eq_s,
        "trades":               trades,
        "benchmark":            bench_info,
        "max_positions":        max_positions,
        "review_days":          review_days,
        "min_entry_rank":       min_entry_rank,
        "require_acceleration": require_acceleration,
        "final_holdings":       final_holdings,
    }


# ── ASCII equity chart ────────────────────────────────────────────────────────

def _equity_chart(eq_s: pd.Series, width: int = 60, height: int = 12) -> list[str]:
    if eq_s is None or len(eq_s) < 3:
        return []
    monthly = eq_s.resample("ME").last().dropna()
    if len(monthly) < 3:
        return []
    vals   = list(monthly.values)
    lo, hi = min(vals), max(vals)
    rng    = hi - lo or 1

    def _norm(v):
        return int((v - lo) / rng * (height - 1))

    grid = [[" "] * width for _ in range(height)]
    step = max(1, len(vals) // width)
    sampled = vals[::step][:width]
    for x, v in enumerate(sampled):
        y = _norm(v)
        grid[height - 1 - y][x] = "▪"

    def _fmt(v):
        if v >= 1_000_000: return f"{v/1_000_000:.1f}M"
        if v >= 100_000:   return f"{v/100_000:.1f}L"
        return f"{v:,.0f}"

    lines = []
    for row_i, row in enumerate(grid):
        level = hi - (hi - lo) * row_i / (height - 1)
        lbl   = f"{_fmt(level):>9} |"
        lines.append(lbl + "".join(row))
    lines.append("          +" + "─" * width)
    years = [str(d.year) for d in monthly.index[::max(1, len(monthly) // 10)]]
    lines.append("           " + "  ".join(f"{y:<5}" for y in years[:12]))
    return lines


# ── Report ────────────────────────────────────────────────────────────────────

def mb_backtest_report(r: dict) -> str:
    if "error" in r:
        return f"\033[91mBacktest error: {r['error']}\033[0m\n"

    bench      = r.get("benchmark", {})
    b_name     = {"IN": "Nifty 50", "US": "S&P 500", "EU": "STOXX 50"}.get(r["market"], "Benchmark")
    alpha      = r["cagr"] - bench["cagr"] if "cagr" in bench else None
    eq_start   = r["initial_equity"]
    eq_end     = r["final_equity"]

    def _pct(v):   return f"{v*100:+.1f}%" if v is not None else "  N/A"
    def _p2(v):    return f"{v*100:.2f}%"  if v is not None else "N/A"
    def _bar(v, max_v=1.5, w=22):
        filled = min(w, max(0, round(abs(v) / max_v * w)))
        return "█" * filled + "░" * (w - filled)

    SEP  = "═" * 70
    sep  = "─" * 70

    lines = [
        "",
        f"\033[1m\033[94m{SEP}\033[0m",
        f"\033[1m\033[97m  MULTI-BAGGER BACKTEST  ──  {r['market']}   "
        f"({r['start']} → {r['end']})\033[0m",
        f"\033[94m  Strategy: Conviction-hold V2 · trailing stop + partial profits + regime gate\033[0m",
        f"\033[94m  Entry: structural gate + top {round((1-r['min_entry_rank'])*100):.0f}% momentum"
        f" + acceleration filter\033[0m",
        f"\033[1m\033[94m{SEP}\033[0m",
        f"  Starting capital  : {eq_start:>14,.0f}",
        f"  Slots             : {r['max_positions']:>14}   "
        f"(entry review every {r['review_days']}d)",
        f"  Commission/slip   : {'0.10% / 0.10%':>14}",
        "",
        f"  {'Final equity':<26}  \033[1m\033[97m{eq_end:>14,.0f}\033[0m",
        f"  {'Total return':<26}  \033[92m{_pct(r['total_return']):>14}\033[0m",
        f"  {'CAGR':<26}  \033[92m{_p2(r['cagr']):>14}\033[0m",
        f"  {'Max drawdown':<26}  \033[91m{_pct(r['max_dd']):>14}\033[0m",
        f"  {'Sharpe ratio':<26}  \033[97m{r['sharpe']:>14.3f}\033[0m",
        f"  {'Sortino ratio':<26}  \033[97m{r['sortino']:>14.3f}\033[0m",
        f"  {'Calmar ratio':<26}  \033[97m{r['calmar']:>14.3f}\033[0m",
        "",
        f"  {'Trades (buy / sell)':<26}  \033[97m{r['n_buys']:>6} / {r['n_sells']:<6}\033[0m",
        f"  {'  ↳ SMA breakdown exits':<26}  \033[91m{r['n_breakdown']:>14}\033[0m",
        f"  {'  ↳ Hard stop-loss exits':<26}  \033[91m{r.get('n_stop_loss',0):>14}\033[0m",
        f"  {'  ↳ Trailing stop exits':<26}  \033[93m{r.get('n_trailing_stop',0):>14}\033[0m",
        f"  {'  ↳ Partial profit trims':<26}  \033[92m{r.get('n_partial_profit',0):>14}\033[0m",
        f"  {'  ↳ STRONG / MODERATE buys':<26}  \033[97m{r.get('n_strong_entries',0):>5} / {r.get('n_moderate_entries',0):<8}\033[0m",
        f"  {'Avg hold duration':<26}  "
        + (f"\033[97m{r['avg_hold_days']:.0f} days\033[0m" if r.get('avg_hold_days') else "  N/A"),
        f"  {'Median hold duration':<26}  "
        + (f"\033[97m{r['median_hold_days']:.0f} days\033[0m" if r.get('median_hold_days') else "  N/A"),
        f"  {'Win rate':<26}  "
        + (f"\033[92m{r['win_rate']*100:.1f}%\033[0m" if r.get('win_rate') is not None else "  N/A"),
    ]

    # ── Equity chart ──────────────────────────────────────────────────────────
    chart_lines = _equity_chart(r["equity_curve"])
    if chart_lines:
        lines += [
            "",
            f"\033[1m\033[94m{SEP}\033[0m",
            f"\033[1m\033[97m  EQUITY CURVE  (monthly sampled)\033[0m",
            f"\033[1m\033[94m{SEP}\033[0m",
        ]
        lines += [f"  \033[92m{cl}\033[0m" for cl in chart_lines]

    # ── Year-by-year returns ──────────────────────────────────────────────────
    ann   = r.get("annual_returns", {})
    b_ann = bench.get("annual_returns", {})
    if ann:
        lines += [
            "",
            f"\033[1m\033[94m{SEP}\033[0m",
            f"\033[1m\033[97m  YEAR-BY-YEAR  (bar scale 22 chars = 150%)\033[0m",
            f"\033[1m\033[94m{SEP}\033[0m",
            f"  {'Year':<6}  {'Strategy':>9}  {b_name:>9}  {'Alpha':>9}  Bar",
            f"  {'-'*6}  {'-'*9}  {'-'*9}  {'-'*9}  {'-'*22}",
        ]
        for yr in sorted(ann):
            sv    = ann[yr]
            bv    = b_ann.get(yr)
            alp   = sv - bv if bv is not None else None
            bv_s  = _pct(bv)  if bv is not None else "    N/A"
            alp_s = _pct(alp) if alp is not None else "    N/A"
            col   = "\033[92m" if sv >= 0 else "\033[91m"
            lines.append(
                f"  {yr:<6}  {col}{_pct(sv):>9}\033[0m"
                f"  {bv_s:>9}  {alp_s:>9}  {col}{_bar(sv)}\033[0m"
            )

    # ── Crisis drawdowns ──────────────────────────────────────────────────────
    crisis = r.get("crisis_drawdowns", {})
    b_crisis = {}
    if bench and "annual_returns" not in bench:
        pass
    if crisis:
        lines += [
            "",
            f"\033[1m\033[94m{SEP}\033[0m",
            f"\033[1m\033[97m  CRISIS DRAWDOWNS  (max intra-period drawdown)\033[0m",
            f"\033[1m\033[94m{SEP}\033[0m",
            f"  {'Crisis':<20}  {'Strategy':>12}",
            f"  {'-'*20}  {'-'*12}",
        ]
        for label, dd in sorted(crisis.items(), key=lambda x: x[1]):
            col = "\033[91m" if dd < -0.30 else "\033[93m" if dd < -0.15 else "\033[97m"
            lines.append(f"  {label:<20}  {col}{_pct(dd):>12}\033[0m")

    # ── Benchmark comparison ──────────────────────────────────────────────────
    if bench and "cagr" in bench:
        lines += [
            "",
            f"\033[1m\033[94m{SEP}\033[0m",
            f"\033[1m\033[97m  BENCHMARK COMPARISON  ──  {b_name} ({bench['ticker']})\033[0m",
            f"\033[1m\033[94m{SEP}\033[0m",
            f"  {'Metric':<26}  {'Strategy':>12}  {'Benchmark':>12}  {'Alpha':>9}",
            f"  {'-'*26}  {'-'*12}  {'-'*12}  {'-'*9}",
            f"  {'CAGR':<26}  \033[92m{_p2(r['cagr']):>12}\033[0m"
            f"  \033[96m{_p2(bench['cagr']):>12}\033[0m"
            f"  \033[92m{_pct(alpha):>9}\033[0m",
            f"  {'Total Return':<26}  \033[92m{_pct(r['total_return']):>12}\033[0m"
            f"  \033[96m{_pct(bench.get('total_return')):>12}\033[0m",
            f"  {'Max Drawdown':<26}  \033[91m{_pct(r['max_dd']):>12}\033[0m"
            f"  \033[91m{_pct(bench.get('max_dd')):>12}\033[0m",
            f"  {'Sharpe':<26}  \033[97m{r['sharpe']:>12.3f}\033[0m"
            f"  \033[97m{bench.get('sharpe', 0):>12.3f}\033[0m",
        ]

    # ── Longest-held tickers ──────────────────────────────────────────────────
    top_hold = r.get("top_by_hold", [])
    if top_hold:
        max_h = top_hold[0][1] if top_hold else 1
        lines += [
            "",
            f"\033[1m\033[94m{SEP}\033[0m",
            f"\033[1m\033[97m  CONVICTION HOLDS  (avg hold days — multi-bagger candidates)\033[0m",
            f"\033[1m\033[94m{SEP}\033[0m",
            f"  {'Ticker':<20}  {'Avg hold':>8}  Bar",
            f"  {'-'*20}  {'-'*8}  {'-'*22}",
        ]
        for tk, h in top_hold[:12]:
            bar_w = max(1, round(h / max_h * 22))
            lines.append(
                f"  \033[92m{tk:<20}\033[0m  {h:>7.0f}d  "
                f"\033[96m{'█' * bar_w}\033[0m"
            )

    # ── Final holdings ────────────────────────────────────────────────────────
    if r.get("final_holdings"):
        lines += [
            "",
            f"\033[1m\033[94m{SEP}\033[0m",
            f"\033[1m\033[97m  OPEN POSITIONS  (held at end date)\033[0m",
            f"\033[1m\033[94m{SEP}\033[0m",
            f"  {'Ticker':<20}  {'Shares':>7}  {'Entry':>10}  {'Price':>10}  {'Return':>9}",
            f"  {'-'*62}",
        ]
        for t, h in r["final_holdings"].items():
            entry  = h.get("entry", 0)
            price  = h["price"]
            ret    = (price / entry - 1) if entry > 0 else 0
            col    = "\033[92m" if ret >= 0 else "\033[91m"
            lines.append(
                f"  \033[92m{t:<20}\033[0m  {h['shares']:>7}  "
                f"{entry:>10,.2f}  {price:>10,.2f}  {col}{_pct(ret):>9}\033[0m"
            )

    lines += [
        "",
        f"\033[1m\033[94m{SEP}\033[0m",
        f"\033[93m  ⚠  Survivorship bias: uses tickers alive today — overstates returns.\033[0m",
        f"\033[93m     Fundamentals not replayed; technical proxies only for pre-2015.\033[0m",
        f"\033[93m     Results are directional, not precise. Validate with live data.\033[0m",
        f"\033[1m\033[94m{SEP}\033[0m",
        "",
    ]
    return "\n".join(lines)
