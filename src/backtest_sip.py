"""
backtest_sip.py
===============
Historical backtest for the monthly SIP strategy.

Simulation
----------
  Monthly (every ~21 trading days), starting from `start`:
    1. Inject monthly_budget into cash
    2. Check held positions for SMA_50 < SMA_200 breakdown -> sell, add to cash
    3. Rank surviving universe by momentum (1M/3M/6M/12M); filter SMA_50 > SMA_200
    4. Buy top N picks with equal allocation from available cash

Performance metric
------------------
  XIRR (Extended IRR) properly annualizes returns when capital is deployed
  gradually. Standard CAGR overstates performance for periodic investment plans.
  Benchmark: same monthly SIP schedule deployed into S&P 500 (buy-and-hold index).

Limitations (stated clearly in the report)
------------------------------------------
  - Survivorship bias: uses current universe; delisted / bankrupt stocks absent
  - No historical Q-score: Q-score gate is omitted from backtest; live trading
    adds Q >= 55 which may further improve selection
  - Current index constituents: composition drifts over the period
"""

from __future__ import annotations

import math
from datetime import datetime, date, timedelta
from typing import Optional

import numpy as np
import pandas as pd


BENCHMARK_TICKER = {"US": "^GSPC", "EU": "^STOXX50E", "IN": "^NSEI"}
_DEFAULT_PERIODS  = [21, 63, 126, 252]   # 1M / 3M / 6M / 12M
_DEFAULT_REGIME_RESERVE = 0.10           # C2 variant: 10% held back, deployed on index < SMA_200


# ── XIRR ─────────────────────────────────────────────────────────────────────

def _xirr(cash_flows: list[tuple[float, date]], guess: float = 0.10) -> float:
    """
    Extended IRR for irregular cash flows.
    cash_flows: [(amount, date), ...] — negative = outflow, positive = inflow
    Returns annualized rate (float) or NaN on failure.
    """
    if not cash_flows:
        return float("nan")

    amounts = [cf[0] for cf in cash_flows]
    t0      = cash_flows[0][1]
    days    = [(cf[1] - t0).days for cf in cash_flows]

    def npv(r: float) -> float:
        return sum(a / (1 + r) ** (d / 365) for a, d in zip(amounts, days))

    # Bisect in [-0.999, 10.0]
    lo, hi = -0.999, 10.0
    for _ in range(200):
        mid = (lo + hi) / 2
        v   = npv(mid)
        if abs(v) < 1e-6:
            return mid
        if npv(lo) * v < 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


# ── Helpers ───────────────────────────────────────────────────────────────────

def _momentum(df: pd.DataFrame, periods: list[int] = _DEFAULT_PERIODS) -> float:
    close = df["Close"].dropna()
    if len(close) < max(periods):
        return float("nan")
    scores = [float(close.iloc[-1] / close.iloc[-p] - 1) for p in periods if len(close) > p]
    return float(np.mean(scores)) if scores else float("nan")


def _breakdown_days(df: pd.DataFrame) -> int:
    """Consecutive trailing days where SMA_50 < SMA_200."""
    if "SMA_50" not in df.columns or "SMA_200" not in df.columns:
        return 0
    diff  = (df["SMA_50"] - df["SMA_200"]).dropna()
    count = 0
    for v in reversed(diff.values):
        if v < 0:
            count += 1
        else:
            break
    return count


def _price_at(df: pd.DataFrame, as_of, offset: float = 0.0) -> Optional[float]:
    """Closing price on or before as_of, with optional slippage/commission offset."""
    sub = df.loc[:as_of]
    if sub.empty:
        return None
    p = float(sub.iloc[-1]["Close"])
    return p * (1 + offset)


def _get_market(ticker: str) -> str:
    from config import get_market
    return get_market(ticker)


def _sma200_of_index(bench_df: pd.DataFrame, as_of) -> tuple[float, float]:
    """Return (close, sma200) for the index as of `as_of`. Both NaN if < 200 rows."""
    sub = bench_df.loc[:as_of]
    if len(sub) < 200:
        return float("nan"), float("nan")
    close  = float(sub["Close"].iloc[-1])
    sma200 = float(sub["Close"].iloc[-200:].mean())
    return close, sma200


def _max_drawdown(nav_series: list[float]) -> float:
    if not nav_series:
        return 0.0
    peak = nav_series[0]
    worst = 0.0
    for v in nav_series:
        if v > peak:
            peak = v
        dd = (v - peak) / peak
        if dd < worst:
            worst = dd
    return worst


# ── Monthly selection ─────────────────────────────────────────────────────────

def _select_picks(
    data_map:         dict,
    tickers:          list[str],
    as_of,
    max_picks:        int,
    breakdown_days:   int,
    held:             dict,
    momentum_periods: list[int],
) -> list[str]:
    """Rank eligible tickers and return top N picks for this month."""
    ranked = []
    for ticker in tickers:
        df = data_map.get(ticker)
        if df is None:
            continue
        sub = df.loc[:as_of]
        if len(sub) < 200:
            continue
        last = sub.iloc[-1]
        sma50  = last.get("SMA_50",  float("nan"))
        sma200 = last.get("SMA_200", float("nan"))
        if math.isnan(sma50) or math.isnan(sma200):
            continue
        if sma50 <= sma200:          # must be in uptrend
            continue
        mom = _momentum(sub, momentum_periods)
        if math.isnan(mom):
            continue
        ranked.append((ticker, mom))

    ranked.sort(key=lambda x: x[1], reverse=True)
    picks: list[str] = []
    for t, _ in ranked:
        if len(picks) >= max_picks:
            break
        picks.append(t)
    return picks


# ── Core backtest ─────────────────────────────────────────────────────────────

def run_sip_backtest(
    data_map:             dict,
    benchmark_dfs:        dict | None      = None,   # {mkt: DataFrame} per-region benchmarks
    benchmark_df:         Optional[pd.DataFrame] = None,  # legacy — used as US fallback
    start:                str              = "2016-01-01",
    end:                  Optional[str]    = None,
    monthly_budget:       float            = 2000.0,  # fallback if region_budget not given
    region_budget:        dict | None      = None,    # {"US": 2000, "EU": 2000, "IN": 20000} local currency
    region_min_alloc:     dict | None      = None,    # {"US": 200, "EU": 200, "IN": 2000}
    max_picks:            int              = 5,
    commission:           float            = 0.001,
    slippage:             float            = 0.001,
    sma_breakdown_days:   int              = 10,
    momentum_periods:     Optional[list]   = None,
    markets:              Optional[list]   = None,
    regime_reserve_pct:   float            = _DEFAULT_REGIME_RESERVE,
) -> dict:
    """
    Simulate the monthly SIP strategy over a historical period.

    Each region runs with its own local-currency budget (USD / EUR / INR).
    XIRR, invested, final NAV, and gain are all reported in local currency per region.
    """
    periods     = momentum_periods or _DEFAULT_PERIODS
    cost        = commission + slippage
    active_mkts = [m.upper() for m in (markets or ["US", "EU", "IN"])]

    _CURRENCY = {"US": "USD", "EU": "EUR", "IN": "INR"}
    _SYMBOL   = {"US": "$",   "EU": "€",   "IN": "₹"}

    # Regional budgets in local currency
    _DEFAULT_RB  = {"US": 2000.0, "EU": 2000.0, "IN": 20000.0}
    _DEFAULT_RMA = {"US": 200.0,  "EU": 200.0,  "IN": 2000.0}
    rb  = {m: (region_budget  or _DEFAULT_RB ).get(m, monthly_budget) for m in active_mkts}
    rma = {m: (region_min_alloc or _DEFAULT_RMA).get(m, 200.0)        for m in active_mkts}

    # Build per-region benchmark map
    _bench_map: dict[str, pd.DataFrame] = dict(benchmark_dfs or {})
    if benchmark_df is not None and "US" not in _bench_map:
        _bench_map["US"] = benchmark_df

    # ── Date range ────────────────────────────────────────────────────────────
    start_dt = pd.Timestamp(start)
    end_dt   = pd.Timestamp(end) if end else pd.Timestamp.now().normalize()

    all_dates: set = set()
    for df in data_map.values():
        all_dates.update(df.index)
    trading_days = sorted(d for d in all_dates if start_dt <= d <= end_dt)
    if len(trading_days) < 63:
        return {"error": "Insufficient historical data for backtest period"}

    tickers_by_mkt: dict[str, list[str]] = {m: [] for m in active_mkts}
    for t, df in data_map.items():
        m = _get_market(t)
        if m in tickers_by_mkt:
            tickers_by_mkt[m].append(t)

    monthly_dates = trading_days[::21]

    # ── Per-region state (all values in local currency) ───────────────────────
    region_portfolio: dict[str, dict]  = {m: {} for m in active_mkts}
    region_cash:      dict[str, float] = {m: 0.0 for m in active_mkts}
    dip_reserve:      dict[str, float] = {m: 0.0 for m in active_mkts}
    region_invested:  dict[str, float] = {m: 0.0 for m in active_mkts}
    region_cfs:       dict[str, list]  = {m: [] for m in active_mkts}
    region_nav_hist:  dict[str, list]  = {m: [] for m in active_mkts}

    # Per-region benchmark tracking
    bench_shares: dict[str, float] = {m: 0.0 for m in active_mkts}
    bench_cfs:    dict[str, list]  = {m: [] for m in active_mkts}

    all_trades:  list[dict] = []
    nav_history: list[dict] = []   # combined — nav is sum of local-currency navs (mixed, indicative only)
    regime_deploy_events = 0

    for cycle_date in monthly_dates:
        py_date   = cycle_date.date()
        cycle_nav = 0.0

        for mkt in active_mkts:
            bgt        = rb[mkt]
            to_invest  = bgt * (1.0 - regime_reserve_pct)
            to_reserve = bgt * regime_reserve_pct
            region_cash[mkt]     += to_invest
            dip_reserve[mkt]     += to_reserve
            region_invested[mkt] += bgt
            region_cfs[mkt].append((-bgt, py_date))

            # Per-region benchmark: deploy same local-currency budget (no reserve held)
            bdf = _bench_map.get(mkt)
            if bdf is not None:
                bp = _price_at(bdf, cycle_date, offset=cost)
                if bp and bp > 0:
                    bench_shares[mkt] += bgt / bp
                    bench_cfs[mkt].append((-bgt, py_date))

        for mkt in active_mkts:
            port    = region_portfolio[mkt]
            tickers = tickers_by_mkt[mkt]
            bgt     = rb[mkt]
            _rma    = rma[mkt]

            # ── Exits
            for ticker in list(port.keys()):
                df  = data_map.get(ticker)
                if df is None:
                    continue
                sub = df.loc[:cycle_date]
                if _breakdown_days(sub) >= sma_breakdown_days:
                    sell_px = _price_at(sub, cycle_date, offset=-cost)
                    if sell_px and sell_px > 0:
                        region_cash[mkt] += port[ticker]["shares"] * sell_px
                        all_trades.append({
                            "date": str(py_date), "action": "SELL",
                            "ticker": ticker, "market": mkt,
                            "shares": round(port[ticker]["shares"], 4),
                            "price":  round(sell_px, 4),
                            "currency": _CURRENCY[mkt],
                        })
                    del port[ticker]

            # ── Regime check: release reserve when index < SMA_200
            regime_triggered = False
            if regime_reserve_pct > 0 and dip_reserve[mkt] > 0:
                bdf = _bench_map.get(mkt)
                if bdf is not None:
                    idx_close, idx_sma200 = _sma200_of_index(bdf, cycle_date)
                    if not math.isnan(idx_close) and idx_close < idx_sma200:
                        region_cash[mkt] += dip_reserve[mkt]
                        dip_reserve[mkt]  = 0.0
                        regime_triggered   = True
                        regime_deploy_events += 1

            # ── Select picks
            picks = _select_picks(data_map, tickers, cycle_date, max_picks,
                                  sma_breakdown_days, port, periods)

            # ── Deploy: all cash on regime trigger; normal 90% slice otherwise
            if regime_triggered:
                deploy = region_cash[mkt]
            else:
                deploy = min(rb[mkt] * (1.0 - regime_reserve_pct), region_cash[mkt])
            if picks and deploy >= _rma:
                n = min(len(picks), max(1, int(deploy / _rma)))
                picks     = picks[:n]
                alloc_per = deploy / len(picks)

                for ticker in picks:
                    df     = data_map[ticker]
                    sub    = df.loc[:cycle_date]
                    buy_px = _price_at(sub, cycle_date, offset=cost)
                    if not buy_px or buy_px <= 0:
                        continue
                    shares = alloc_per / buy_px
                    region_cash[mkt] -= alloc_per
                    if ticker in port:
                        prev     = port[ticker]
                        total_sh = prev["shares"] + shares
                        total_ct = prev["total_cost"] + alloc_per
                        port[ticker] = {"shares": total_sh,
                                        "avg_cost": total_ct / total_sh,
                                        "total_cost": total_ct}
                    else:
                        port[ticker] = {"shares": shares, "avg_cost": buy_px,
                                        "total_cost": alloc_per}
                    all_trades.append({
                        "date": str(py_date), "action": "BUY",
                        "ticker": ticker, "market": mkt,
                        "shares": round(shares, 4),
                        "price":  round(buy_px, 4),
                        "currency": _CURRENCY[mkt],
                        "amount": round(alloc_per, 2),
                    })

            # ── Regional NAV in local currency (includes dip reserve as cash-equivalent)
            region_nav = region_cash[mkt] + dip_reserve[mkt]
            for ticker, pos in port.items():
                df  = data_map.get(ticker)
                sub = df.loc[:cycle_date] if df is not None else None
                if sub is not None and not sub.empty:
                    region_nav += pos["shares"] * float(sub.iloc[-1]["Close"])

            region_nav_hist[mkt].append({
                "date":      str(py_date),
                "nav":       round(region_nav, 2),
                "cash":      round(region_cash[mkt], 2),
                "reserve":   round(dip_reserve[mkt], 2),
                "positions": len(port),
                "invested":  round(region_invested[mkt], 2),
                "currency":  _CURRENCY[mkt],
                "symbol":    _SYMBOL[mkt],
            })
            cycle_nav += region_nav

        nav_history.append({
            "date":      str(py_date),
            "nav":       round(cycle_nav, 2),
            "invested":  round(sum(region_invested.values()), 2),
            "positions": sum(len(region_portfolio[m]) for m in active_mkts),
        })

    # ── Final liquidation ─────────────────────────────────────────────────────
    final_date = end_dt.date()
    region_final: dict[str, float] = {}
    region_gain:  dict[str, float] = {}

    for mkt in active_mkts:
        port  = region_portfolio[mkt]
        r_nav = region_cash[mkt] + dip_reserve[mkt]
        for ticker, pos in port.items():
            df  = data_map.get(ticker)
            sub = df.loc[:end_dt] if df is not None else None
            if sub is not None and not sub.empty:
                r_nav += pos["shares"] * float(sub.iloc[-1]["Close"])
        region_final[mkt] = round(r_nav, 2)
        region_gain[mkt]  = round(r_nav - region_invested[mkt], 2)
        region_cfs[mkt].append((r_nav, final_date))

    # Per-region benchmark final NAV
    region_benchmark_xirr: dict[str, float] = {}
    for mkt in active_mkts:
        bdf = _bench_map.get(mkt)
        if bdf is not None and bench_cfs[mkt]:
            bsub = bdf.loc[:end_dt]
            if not bsub.empty:
                bench_fin = bench_shares[mkt] * float(bsub.iloc[-1]["Close"])
                bench_cfs[mkt].append((bench_fin, final_date))
                region_benchmark_xirr[mkt] = _xirr(bench_cfs[mkt])
            else:
                region_benchmark_xirr[mkt] = float("nan")
        else:
            region_benchmark_xirr[mkt] = float("nan")

    # ── Metrics ───────────────────────────────────────────────────────────────
    region_xirr = {m: _xirr(region_cfs[m]) for m in active_mkts}
    nav_vals    = [h["nav"] for h in nav_history]
    max_dd      = _max_drawdown(nav_vals)
    avg_pos     = float(np.mean([h["positions"] for h in nav_history])) if nav_history else 0.0

    # Per-region year-by-year returns
    region_year_returns = {
        m: _year_returns(region_nav_hist[m], rb[m]) for m in active_mkts
    }

    idle_reserve_at_end = {m: round(dip_reserve[m], 2) for m in active_mkts}

    report = _format_report(
        start=start, end=str(end_dt.date()),
        markets=active_mkts,
        region_budget=rb,
        max_dd=max_dd,
        n_cycles=len(monthly_dates),
        avg_positions=avg_pos,
        n_tickers=sum(len(v) for v in tickers_by_mkt.values()),
        n_trades=len(all_trades),
        region_invested=region_invested,
        region_final=region_final,
        region_gain=region_gain,
        region_xirr=region_xirr,
        region_benchmark_xirr=region_benchmark_xirr,
        region_symbol=_SYMBOL,
        region_currency=_CURRENCY,
        region_year_returns=region_year_returns,
        regime_reserve_pct=regime_reserve_pct,
        regime_deploy_events=regime_deploy_events,
        idle_reserve_at_end=idle_reserve_at_end,
    )

    return {
        "region_xirr":           region_xirr,
        "region_benchmark_xirr": region_benchmark_xirr,
        "region_invested":       region_invested,
        "region_final":          region_final,
        "region_gain":           region_gain,
        "region_budget":         rb,
        "max_drawdown":          round(max_dd * 100, 2),
        "n_cycles":              len(monthly_dates),
        "avg_positions":         round(avg_pos, 1),
        "nav_history":           nav_history,
        "region_nav_hist":       region_nav_hist,
        "region_year_returns":   region_year_returns,
        "all_trades":            all_trades,
        "report_text":           report,
        "markets":               active_mkts,
        "region_currency":       _CURRENCY,
        "region_symbol":         _SYMBOL,
        "regime_reserve_pct":    regime_reserve_pct,
        "regime_deploy_events":  regime_deploy_events,
        "idle_reserve_at_end":   idle_reserve_at_end,
    }


# ── Year-by-year returns ──────────────────────────────────────────────────────

def _year_returns(nav_history: list[dict], monthly_budget: float) -> list[dict]:
    """Modified Dietz annual returns for the SIP portfolio."""
    if not nav_history:
        return []

    # Group NAV snapshots by calendar year
    by_year: dict[int, list] = {}
    for h in nav_history:
        yr = int(h["date"][:4])
        by_year.setdefault(yr, []).append(h)

    rows = []
    prev_nav = 0.0
    for yr in sorted(by_year):
        snapshots = by_year[yr]
        yr_start  = prev_nav
        yr_end    = snapshots[-1]["nav"]

        # Approximate monthly contributions deposited this year
        contributions = len(snapshots) * monthly_budget

        # Modified Dietz: return = (End - Start - Contrib) / (Start + Contrib/2)
        denom = yr_start + contributions / 2
        dietz = (yr_end - yr_start - contributions) / denom if denom > 0 else 0.0

        rows.append({
            "year":          yr,
            "nav_start":     round(yr_start, 2),
            "nav_end":       round(yr_end, 2),
            "contributions": round(contributions, 2),
            "return_pct":    round(dietz * 100, 2),
        })
        prev_nav = yr_end

    return rows


# ── Report formatter ──────────────────────────────────────────────────────────

def _format_report(
    start: str, end: str, markets: list,
    region_budget: dict,
    max_dd: float, n_cycles: int, avg_positions: float,
    n_tickers: int, n_trades: int,
    region_invested: dict,
    region_final: dict,
    region_gain: dict,
    region_xirr: dict,
    region_benchmark_xirr: dict,
    region_symbol: dict,
    region_currency: dict,
    region_year_returns: dict,
    regime_reserve_pct: float = 0.0,
    regime_deploy_events: int = 0,
    idle_reserve_at_end: dict | None = None,
) -> str:
    sep  = "=" * 68
    dash = "-" * 44
    pct  = lambda v: f"{v*100:+.2f}%" if not math.isnan(v) else "N/A"

    budget_str = "  |  ".join(
        f"{region_symbol.get(m,'')}{region_budget.get(m,0):,.0f}/mo {region_currency.get(m,'')}"
        for m in markets
    )
    regime_str = (
        f"  Regime reserve: {regime_reserve_pct*100:.0f}% held back/mo, "
        f"deployed {regime_deploy_events}× on index < SMA_200"
        if regime_reserve_pct > 0 else "  Regime reserve: disabled (100% deployed each month)"
    )
    lines = [
        "",
        sep,
        "  MASTERMIND PRO — SIP BACKTEST (Regime-Reserve C2 Strategy)",
        f"  Markets  : {', '.join(markets)}  |  Universe: {n_tickers} tickers",
        f"  Period   : {start}  to  {end}",
        f"  Budget   : {budget_str}",
        regime_str,
        f"  Cycles   : {n_cycles}  |  Max DD: {max_dd*100:+.2f}%  |  Avg positions: {avg_positions:.1f}",
        sep,
        "",
        "  REGIONAL PERFORMANCE SUMMARY",
        "  " + dash,
        f"  {'Mkt':<4} {'Currency':<6} {'Budget/mo':>10} {'Invested':>12} "
        f"{'Final NAV':>12} {'Gain':>10} {'XIRR':>8} {'Bench XIRR':>11} {'Alpha':>8}",
    ]

    for m in markets:
        sym  = region_symbol.get(m, "")
        cur  = region_currency.get(m, "")
        bgt  = region_budget.get(m, 0.0)
        inv  = region_invested.get(m, 0.0)
        fin  = region_final.get(m, 0.0)
        gn   = region_gain.get(m, 0.0)
        xi   = region_xirr.get(m, float("nan"))
        bxi  = region_benchmark_xirr.get(m, float("nan"))
        alpha = xi - bxi if not math.isnan(xi) and not math.isnan(bxi) else float("nan")
        lines.append(
            f"  {m:<4} {cur:<6} "
            f"{sym}{bgt:>8,.0f}  "
            f"{sym}{inv:>10,.0f}  "
            f"{sym}{fin:>10,.0f}  "
            f"{sym}{gn:>8,.0f}  "
            f"{pct(xi):>8}  "
            f"{pct(bxi):>11}  "
            f"{pct(alpha):>8}"
        )

    # ── Per-region year-by-year
    for m in markets:
        sym  = region_symbol.get(m, "")
        cur  = region_currency.get(m, "")
        yr_rows = region_year_returns.get(m, [])
        if not yr_rows:
            continue
        lines += [
            "",
            f"  YEAR-BY-YEAR RETURNS — {m} ({cur}, Modified Dietz)",
            "  " + dash,
            f"  {'Year':<6} {'NAV Start':>14} {'Contrib':>12} {'NAV End':>14} {'Return':>8}",
        ]
        for r in yr_rows:
            lines.append(
                f"  {r['year']:<6} "
                f"{sym}{r['nav_start']:>12,.0f}  "
                f"{sym}{r['contributions']:>10,.0f}  "
                f"{sym}{r['nav_end']:>12,.0f}  "
                f"{r['return_pct']:>+7.1f}%"
            )

    lines += [
        "",
        "  IMPORTANT LIMITATIONS",
        "  " + dash,
        "  ! Survivorship bias: current universe excludes delisted / bankrupt stocks.",
        "  ! No Q-score in backtest: fundamental gate (Q>=55) is a live-only filter.",
        "  ! Index-drift: constituent changes not tracked -- universe fixed at today.",
        "  ! XIRR measures return on capital deployed, not absolute portfolio CAGR.",
        "  ! Currency: each region tracked in local currency -- no FX conversion.",
        "",
        sep,
        "  DISCLAIMER: Research and paper trading only. Not financial advice.",
        sep,
        "",
    ]
    return "\n".join(lines)
