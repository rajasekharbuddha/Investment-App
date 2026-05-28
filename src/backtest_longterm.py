"""
backtest_longterm.py
====================
Long-term portfolio backtest with periodic momentum rebalancing.

Strategy
--------
  At each rebalance date:
    1. Sell positions that have EITHER broken down (SMA_50 < SMA_200) OR
       dropped out of the top-N scored list (relative strength exited).
    2. Fill freed slots with the highest-scoring new entrants.
  Between rebalances:
    - Daily breakdown check: exit immediately if SMA_50 < SMA_200.

This mirrors how a long-term momentum investor behaves: hold quality
uptrends, rotate out when something clearly better exists, exit fast on
structural failure.

Columns required from calculate_all()
---------------------------------------
  SMA_50, SMA_200, Close
"""

from __future__ import annotations

import math
import numpy as np
import pandas as pd

BENCHMARK_TICKER = {"IN": "^NSEI", "US": "^GSPC", "EU": "^STOXX50E"}

REBALANCE_LABEL = {
    21:  "Monthly",
    63:  "Quarterly",
    126: "Semi-Annual",
    252: "Annual",
}


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


# ── Core backtest ─────────────────────────────────────────────────────────────

def run_longterm_backtest(
    market: str,
    data_map: dict,
    start: str,
    end: str,
    equity: float           = 100_000,
    max_positions: int      = 10,
    rebalance_days: int     = 63,      # 21=monthly 63=quarterly 126=semi 252=annual
    exit_on_breakdown: bool = True,    # daily exit if SMA_50 < SMA_200
    momentum_floor: float   = -0.05,   # exit-watch proxy: exit if score < this
    commission: float       = 0.001,
    slippage: float         = 0.001,
) -> dict:
    """
    Momentum-rebalancing long-term backtest with exit-watch signals.

    Parameters
    ----------
    market              : "IN" | "US" | "EU"
    data_map            : {ticker: DataFrame with SMA_50, SMA_200, Close}
    start / end         : "YYYY-MM-DD"
    max_positions       : equal-weight slots
    rebalance_days      : how often to rotate — 63=quarterly (default)
    exit_on_breakdown   : daily exit on SMA_50 < SMA_200 (default True)
    momentum_floor      : exit-watch weakness signal — exit any held position
                          whose momentum score falls below this at rebalance.
                          Proxies fundamental deterioration (ROE declining,
                          revenue slowing) since historical fundamentals are
                          unavailable.  Default -0.05 = exit if avg return
                          across [14,30,63] day periods is below -5%.
                          Set to -9 to disable.

    Sell logic (three independent triggers)
    ----------------------------------------
    1. Daily     : SMA_50 < SMA_200            (structural breakdown)
    2. Rebalance : score < momentum_floor       (exit-watch weakness signal)
    3. Rebalance : dropped out of top-N ranking (rotation to better stock)

    Buy logic
    ---------
    At each rebalance, fill empty slots with the highest-scoring tickers
    not already held (structural gate must pass).

    Returns
    -------
    dict with performance metrics, equity_curve, trades, benchmark
    """
    start_ts = pd.Timestamp(start)
    end_ts   = pd.Timestamp(end)

    # ── Pre-compute price & indicator matrices ────────────────────────────────
    all_tickers = [t for t, df in data_map.items() if len(df) > 0]

    raw_close  = {t: data_map[t]["Close"]   for t in all_tickers
                  if "Close"   in data_map[t].columns}
    raw_sma50  = {t: data_map[t]["SMA_50"]  for t in all_tickers
                  if "SMA_50"  in data_map[t].columns}
    raw_sma200 = {t: data_map[t]["SMA_200"] for t in all_tickers
                  if "SMA_200" in data_map[t].columns}

    common_tickers = list(set(raw_close) & set(raw_sma50) & set(raw_sma200))
    if not common_tickers:
        return {"error": "No tickers with required columns (SMA_50, SMA_200, Close)"}

    all_dates = sorted({
        d for t in common_tickers for d in data_map[t].index
        if start_ts <= d <= end_ts
    })
    if not all_dates:
        return {"error": "No data in specified date range"}

    idx = pd.DatetimeIndex(all_dates)

    close_m  = pd.DataFrame({t: raw_close[t]  for t in common_tickers}).reindex(idx).ffill()
    sma50_m  = pd.DataFrame({t: raw_sma50[t]  for t in common_tickers}).reindex(idx).ffill()
    sma200_m = pd.DataFrame({t: raw_sma200[t] for t in common_tickers}).reindex(idx).ffill()

    # ── Structural gate ───────────────────────────────────────────────────────
    sma50_5d   = sma50_m.shift(5)
    structural = (
        (sma50_m  > sma200_m) &
        (close_m  > sma200_m) &
        (sma50_m  > sma50_5d)
    )

    # ── Momentum score: mean return over [14, 30, 63] periods ────────────────
    mom_scores = pd.DataFrame(0.0, index=idx, columns=common_tickers)
    counts     = pd.DataFrame(0,   index=idx, columns=common_tickers)
    for p in [14, 30, 63]:
        past  = close_m.shift(p)
        valid = past > 0
        ret   = (close_m - past) / past.where(valid, np.nan)
        mom_scores = mom_scores + ret.where(valid, 0.0)
        counts     = counts + valid.astype(int)
    mom_scores = mom_scores / counts.where(counts > 0, np.nan)

    # Combined score: NaN where structural gate fails
    score_m     = mom_scores.where(structural, np.nan)
    breakdown_m = sma50_m < sma200_m

    # ── Simulation loop ───────────────────────────────────────────────────────
    cash        = equity
    portfolio   = {}   # {ticker: shares}
    eq_curve    = []
    trades      = []

    next_reb_ts = all_dates[0]

    for date in all_dates:
        prices = close_m.loc[date]

        # ── 1. Daily breakdown exit ───────────────────────────────────────────
        if exit_on_breakdown:
            bd_row = breakdown_m.loc[date]
            for t in list(portfolio.keys()):
                if bd_row.get(t, False):
                    shares = portfolio.pop(t)
                    px     = float(prices.get(t, 0))
                    fill   = px * (1 - slippage)
                    cash  += shares * fill * (1 - commission)
                    trades.append({"date": date, "action": "SELL", "ticker": t,
                                   "shares": shares, "price": px,
                                   "reason": "breakdown"})

        # ── 2. Periodic rebalance ─────────────────────────────────────────────
        if date >= next_reb_ts:
            scores_today = score_m.loc[date].dropna().sort_values(ascending=False)
            target_set   = set(scores_today.index[:max_positions])

            # Exit-watch weakness signal: exit if raw momentum score below floor
            # (proxies fundamental deterioration — ROE declining, revenue slowing)
            if momentum_floor > -1.0:
                for t in list(portfolio.keys()):
                    if t not in mom_scores.columns:
                        continue
                    try:
                        score_val = float(mom_scores.loc[date, t])
                    except (KeyError, TypeError):
                        continue
                    if math.isfinite(score_val) and score_val < momentum_floor:
                        shares = portfolio.pop(t)
                        px     = float(prices.get(t, 0))
                        fill   = px * (1 - slippage)
                        cash  += shares * fill * (1 - commission)
                        trades.append({"date": date, "action": "SELL", "ticker": t,
                                       "shares": shares, "price": px,
                                       "reason": "weakness"})

            # Sell anything no longer in top-N (or structurally broken)
            for t in list(portfolio.keys()):
                if t not in target_set:
                    shares = portfolio.pop(t)
                    px     = float(prices.get(t, 0))
                    fill   = px * (1 - slippage)
                    cash  += shares * fill * (1 - commission)
                    trades.append({"date": date, "action": "SELL", "ticker": t,
                                   "shares": shares, "price": px,
                                   "reason": "rebalance"})

            # Compute current equity for sizing
            held_val   = sum(portfolio[t] * float(prices.get(t, 0))
                             for t in portfolio)
            curr_eq    = cash + held_val
            alloc_each = curr_eq / max_positions

            # Buy new entrants into empty slots
            new_entries = [t for t in scores_today.index[:max_positions]
                           if t not in portfolio]
            n_empty     = max_positions - len(portfolio)

            for t in new_entries[:n_empty]:
                px = float(prices.get(t, 0))
                if px <= 0:
                    continue
                fill   = px * (1 + slippage)
                shares = math.floor(alloc_each / (fill * (1 + commission)))
                cost   = shares * fill * (1 + commission)
                if shares > 0 and cost <= cash:
                    cash -= cost
                    portfolio[t] = portfolio.get(t, 0) + shares
                    trades.append({"date": date, "action": "BUY", "ticker": t,
                                   "shares": shares, "price": px,
                                   "reason": "rebalance"})

            next_reb_ts = date + pd.Timedelta(days=rebalance_days)

        # Mark to market
        port_val = sum(portfolio[t] * float(prices.get(t, 0)) for t in portfolio)
        eq_curve.append({"date": date, "equity": cash + port_val})

    # ── Metrics ───────────────────────────────────────────────────────────────
    eq_s = pd.DataFrame(eq_curve).set_index("date")["equity"]
    if len(eq_s) < 2:
        return {"error": "Insufficient equity curve data"}

    n_yrs     = max((eq_s.index[-1] - eq_s.index[0]).days / 365.25, 0.01)
    total_ret = (eq_s.iloc[-1] / eq_s.iloc[0]) - 1
    cagr      = (eq_s.iloc[-1] / eq_s.iloc[0]) ** (1 / n_yrs) - 1
    max_dd    = _max_dd(eq_s)
    daily_ret = eq_s.pct_change().dropna()
    sharpe    = _sharpe(daily_ret)
    sortino   = _sortino(daily_ret)

    n_buys      = sum(1 for t in trades if t["action"] == "BUY")
    n_sells     = sum(1 for t in trades if t["action"] == "SELL")
    n_breakdown = sum(1 for t in trades if t.get("reason") == "breakdown")
    n_rebalance = sum(1 for t in trades if t.get("reason") == "rebalance"
                      and t["action"] == "SELL")
    n_weakness  = sum(1 for t in trades if t.get("reason") == "weakness")

    # Average hold duration
    hold_days_list: list[int] = []
    entry_dates: dict[str, pd.Timestamp] = {}
    for tr in trades:
        t, d = tr["ticker"], tr["date"]
        if tr["action"] == "BUY":
            entry_dates[t] = d
        elif tr["action"] == "SELL" and t in entry_dates:
            hold_days_list.append((d - entry_dates.pop(t)).days)
    avg_hold = sum(hold_days_list) / len(hold_days_list) if hold_days_list else None

    # ── Benchmark ─────────────────────────────────────────────────────────────
    bench_info  = {}
    bench_label = BENCHMARK_TICKER.get(market, "^NSEI")
    try:
        import yfinance as yf
        bdf = yf.download(bench_label, start=start, end=end,
                          progress=False, auto_adjust=True)
        if not bdf.empty:
            bc     = bdf["Close"].squeeze().dropna()
            b0, b1 = float(bc.iloc[0]), float(bc.iloc[-1])
            b_tot  = (b1 / b0) - 1
            b_cagr = (b1 / b0) ** (1 / n_yrs) - 1
            b_dd   = _max_dd(bc)
            b_sh   = _sharpe(bc.pct_change().dropna())
            bench_info = {
                "ticker": bench_label, "cagr": b_cagr, "total_return": b_tot,
                "max_dd": b_dd, "sharpe": b_sh,
                "series": bc,   # kept temporarily for year-by-year; popped below
            }
    except Exception as exc:
        bench_info = {"error": str(exc)}

    # Final holdings
    final_holdings = {
        t: {
            "shares": s,
            "price":  float(close_m[t].iloc[-1]) if t in close_m.columns else 0.0,
        }
        for t, s in portfolio.items()
    }

    # Year-by-year returns from equity curve
    annual_returns: dict[int, float] = {}
    for yr, grp in eq_s.groupby(eq_s.index.year):
        annual_returns[int(yr)] = float(grp.iloc[-1] / grp.iloc[0] - 1)

    # Benchmark year-by-year (stored in bench_info if available)
    if bench_info and "series" in bench_info:
        bc_s = bench_info.pop("series")
        bench_annual: dict[int, float] = {}
        for yr, grp in bc_s.groupby(bc_s.index.year):
            bench_annual[int(yr)] = float(grp.iloc[-1] / grp.iloc[0] - 1)
        bench_info["annual_returns"] = bench_annual

    # Top tickers by number of times bought (most consistent performers)
    from collections import Counter
    ticker_freq = Counter(tr["ticker"] for tr in trades if tr["action"] == "BUY")
    top_tickers = ticker_freq.most_common(15)

    return {
        "market":            market,
        "start":             str(eq_s.index[0].date()),
        "end":               str(eq_s.index[-1].date()),
        "initial_equity":    equity,
        "final_equity":      float(eq_s.iloc[-1]),
        "total_return":      total_ret,
        "cagr":              cagr,
        "max_dd":            max_dd,
        "sharpe":            sharpe,
        "sortino":           sortino,
        "n_buys":            n_buys,
        "n_sells":           n_sells,
        "n_breakdown":       n_breakdown,
        "n_rebalance_sells": n_rebalance,
        "n_weakness_exits":  n_weakness,
        "avg_hold_days":     avg_hold,
        "momentum_floor":    momentum_floor,
        "annual_returns":    annual_returns,
        "top_tickers":       top_tickers,
        "equity_curve":      eq_s,
        "trades":            trades,
        "benchmark":         bench_info,
        "max_positions":     max_positions,
        "rebalance_days":    rebalance_days,
        "exit_on_breakdown": exit_on_breakdown,
        "final_holdings":    final_holdings,
    }


# ── ASCII equity chart ────────────────────────────────────────────────────────

def _equity_chart(eq_s, width: int = 60, height: int = 10) -> list[str]:
    """Simple ASCII chart of the equity curve sampled monthly."""
    if eq_s is None or not hasattr(eq_s, "resample"):
        return []
    monthly = eq_s.resample("ME").last().dropna()
    if len(monthly) < 3:
        return []
    vals   = list(monthly.values)
    lo, hi = min(vals), max(vals)
    rng    = hi - lo or 1

    def _norm(v):
        return int((v - lo) / rng * (height - 1))

    # Build grid
    grid = [[" "] * width for _ in range(height)]
    step = max(1, len(vals) // width)
    sampled = vals[::step][:width]
    for x, v in enumerate(sampled):
        y = _norm(v)
        grid[height - 1 - y][x] = "*"

    def _fmt(v):
        if v >= 1e7:   return f"{v/1e7:.1f}Cr"
        if v >= 1e5:   return f"{v/1e5:.1f}L"
        return f"{v:,.0f}"

    lines = []
    for row_i, row in enumerate(grid):
        # y-axis label on left
        level = hi - (hi - lo) * row_i / (height - 1)
        lbl   = f"{_fmt(level):>8} |"
        lines.append(lbl + "".join(row))

    # x-axis
    lines.append("         +" + "-" * width)
    # year labels
    years = [str(d.year) for d in monthly.index[::max(1, len(monthly) // 8)]]
    yr_line = "          " + "  ".join(f"{y:<6}" for y in years[:10])
    lines.append(yr_line)
    return lines


# ── Report ────────────────────────────────────────────────────────────────────

def longterm_backtest_report(r: dict) -> str:
    if "error" in r:
        return f"\033[91mBacktest error: {r['error']}\033[0m\n"

    bench    = r.get("benchmark", {})
    reb_lbl  = REBALANCE_LABEL.get(r["rebalance_days"],
                                    f"every {r['rebalance_days']}d")
    eq_start = r["initial_equity"]
    eq_end   = r["final_equity"]
    alpha    = r["cagr"] - bench["cagr"] if "cagr" in bench else None

    def _pct(v):   return f"{v*100:+.1f}%" if v is not None else "  N/A"
    def _p2(v):    return f"{v*100:.2f}%"  if v is not None else "N/A"
    def _pbar(v, max_v=1.20, w=20):
        # Proportional bar: 0%=empty, max_v*100%=full. Negative shown in red already.
        filled = min(w, max(0, round(abs(v) / max_v * w)))
        return "#" * filled + "." * (w - filled)

    bench_name = {"IN": "Nifty", "US": "S&P 500", "EU": "STOXX"}.get(r["market"], "Benchmark")

    hold_str  = (f"{r['avg_hold_days']:.0f} days"
                 if r.get("avg_hold_days") else "N/A")
    bd_suffix = "  |  Breakdown exit: ON" if r["exit_on_breakdown"] else ""
    mf        = r.get("momentum_floor", -0.05)
    mf_suffix = (f"  |  Mom.floor: {mf*100:.0f}%"
                 if mf > -1.0 else "  |  Mom.floor: OFF")

    SEP = "=" * 68
    sep = "-" * 68

    lines = [
        "",
        f"\033[1m\033[94m{SEP}\033[0m",
        f"\033[1m\033[97m  LONG-TERM BACKTEST  --  {r['market']}\033[0m",
        f"\033[94m  Strategy: Momentum rotation + structural gate (SMA_50/200)\033[0m",
        f"\033[1m\033[94m{SEP}\033[0m",
        f"  Period    : {r['start']}  to  {r['end']}",
        f"  Capital   : {eq_start:,.0f}"
        f"  |  Commission: 0.10%  Slippage: 0.10%",
        f"  Slots     : {r['max_positions']}"
        f"  |  Rebalance: {reb_lbl}{bd_suffix}{mf_suffix}",
        "",
        f"  {'Final equity':<22}  \033[97m{eq_end:>14,.0f}\033[0m",
        f"  {'Total return':<22}  \033[92m{_pct(r['total_return']):>14}\033[0m",
        f"  {'CAGR':<22}  \033[92m{_p2(r['cagr']):>14}\033[0m",
        f"  {'Max drawdown':<22}  \033[91m{_pct(r['max_dd']):>14}\033[0m",
        f"  {'Sharpe ratio':<22}  \033[97m{r['sharpe']:>14.3f}\033[0m",
        f"  {'Sortino ratio':<22}  \033[97m{r['sortino']:>14.3f}\033[0m",
        f"  {'Avg hold duration':<22}  \033[97m{hold_str:>14}\033[0m",
        "",
        f"  {'Buy trades':<22}  \033[97m{r['n_buys']:>14}\033[0m",
        f"  {'Rebalance sells':<22}  \033[97m{r['n_rebalance_sells']:>14}\033[0m",
        f"  {'Breakdown exits':<22}  \033[91m{r['n_breakdown']:>14}\033[0m",
        f"  {'Weakness exits':<22}  \033[93m{r.get('n_weakness_exits', 0):>14}\033[0m",
    ]

    # ── Equity curve chart ────────────────────────────────────────────────────
    chart_lines = _equity_chart(r["equity_curve"])
    if chart_lines:
        lines += [
            "",
            f"\033[1m\033[94m{SEP}\033[0m",
            f"\033[1m\033[97m  EQUITY CURVE\033[0m",
            f"\033[1m\033[94m{SEP}\033[0m",
        ]
        lines += [f"  \033[92m{cl}\033[0m" for cl in chart_lines]

    # ── Year-by-year returns ──────────────────────────────────────────────────
    ann = r.get("annual_returns", {})
    b_ann = bench.get("annual_returns", {})
    if ann:
        lines += [
            "",
            f"\033[1m\033[94m{SEP}\033[0m",
            f"\033[1m\033[97m  YEAR-BY-YEAR RETURNS  (bar scale: 20 chars = 120%)\033[0m",
            f"\033[1m\033[94m{SEP}\033[0m",
            f"  {'Year':<6}  {'Strategy':>9}  {bench_name:>8}  {'Alpha':>9}  Bar (strategy)",
            f"  {'-'*6}  {'-'*9}  {'-'*8}  {'-'*9}  {'-'*20}",
        ]
        for yr in sorted(ann):
            sv    = ann[yr]
            bv    = b_ann.get(yr)
            alp   = sv - bv if bv is not None else None
            bv_s  = _pct(bv)  if bv  is not None else "    N/A"
            alp_s = _pct(alp) if alp is not None else "    N/A"
            bar   = _pbar(sv)
            color = "\033[92m" if sv >= 0 else "\033[91m"
            lines.append(
                f"  {yr:<6}  {color}{_pct(sv):>9}\033[0m"
                f"  {bv_s:>8}  {alp_s:>9}  {color}{bar}\033[0m"
            )

    # ── Benchmark comparison ──────────────────────────────────────────────────
    if bench and "cagr" in bench:
        lines += [
            "",
            f"\033[1m\033[94m{SEP}\033[0m",
            f"\033[1m\033[97m  BENCHMARK COMPARISON  --  {bench_name} ({bench['ticker']})\033[0m",
            f"\033[1m\033[94m{SEP}\033[0m",
            f"  {'Metric':<22}  {'Strategy':>12}  {'Benchmark':>12}  {'Alpha':>9}",
            f"  {'-'*22}  {'-'*12}  {'-'*12}  {'-'*9}",
            f"  {'CAGR':<22}  \033[92m{_p2(r['cagr']):>12}\033[0m"
            f"  \033[96m{_p2(bench['cagr']):>12}\033[0m"
            f"  \033[92m{_pct(alpha):>9}\033[0m",
            f"  {'Total Return':<22}  \033[92m{_pct(r['total_return']):>12}\033[0m"
            f"  \033[96m{_pct(bench.get('total_return')):>12}\033[0m",
            f"  {'Max Drawdown':<22}  \033[91m{_pct(r['max_dd']):>12}\033[0m"
            f"  \033[91m{_pct(bench.get('max_dd')):>12}\033[0m",
            f"  {'Sharpe':<22}  \033[97m{r['sharpe']:>12.3f}\033[0m"
            f"  \033[97m{bench.get('sharpe', 0):>12.3f}\033[0m",
        ]
    elif "error" in bench:
        lines.append(f"  \033[93mBenchmark fetch failed: {bench['error']}\033[0m")

    # ── Top tickers by frequency ──────────────────────────────────────────────
    top = r.get("top_tickers", [])
    if top:
        max_cnt = top[0][1] if top else 1
        lines += [
            "",
            f"\033[1m\033[94m{SEP}\033[0m",
            f"\033[1m\033[97m  TOP TICKERS  (most frequently held)\033[0m",
            f"\033[1m\033[94m{SEP}\033[0m",
            f"  {'Ticker':<20}  {'Times held':>10}  Bar",
            f"  {'-'*20}  {'-'*10}  {'-'*20}",
        ]
        for tk, cnt in top:
            bar_w = max(1, round(cnt / max_cnt * 20))
            lines.append(
                f"  \033[92m{tk:<20}\033[0m  {cnt:>10}  "
                f"\033[96m{'#' * bar_w}\033[0m"
            )

    # ── Final holdings ────────────────────────────────────────────────────────
    if r.get("final_holdings"):
        lines += [
            "",
            f"\033[1m\033[94m{SEP}\033[0m",
            f"\033[1m\033[97m  FINAL HOLDINGS  (held at end date)\033[0m",
            f"\033[1m\033[94m{SEP}\033[0m",
            f"  {'Ticker':<20}  {'Shares':>7}  {'Price':>12}  {'Value':>14}",
            f"  {'-'*58}",
        ]
        total_held = 0.0
        for t, h in r["final_holdings"].items():
            val = h["shares"] * h["price"]
            total_held += val
            lines.append(
                f"  \033[92m{t:<20}\033[0m  {h['shares']:>7}  "
                f"{h['price']:>12,.2f}  {val:>14,.0f}"
            )
        cash_rem = r["final_equity"] - total_held
        lines += [
            f"  {'-'*58}",
            f"  {'Cash (uninvested)':<20}  {'':>7}  {'':>12}  {cash_rem:>14,.0f}",
            f"  {'TOTAL':<20}  {'':>7}  {'':>12}  {r['final_equity']:>14,.0f}",
        ]

    lines += [
        "",
        f"\033[1m\033[94m{SEP}\033[0m",
        f"\033[93m  NOTE: Survivorship bias present "
        f"-- uses current index constituents.\033[0m",
        f"\033[93m  Fundamental data not replayed; technical proxy only.\033[0m",
        f"\033[1m\033[94m{SEP}\033[0m",
        "",
    ]
    return "\n".join(lines)
