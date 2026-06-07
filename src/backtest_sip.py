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


BENCHMARK_TICKER = {"US": "^GSPC", "EU": "^STOXX50E", "US+EU": "^GSPC"}
_DEFAULT_PERIODS  = [21, 63, 126, 252]   # 1M / 3M / 6M / 12M


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
    picks = []
    for t, _ in ranked:
        if len(picks) >= max_picks:
            break
        picks.append(t)
    return picks


# ── Core backtest ─────────────────────────────────────────────────────────────

def run_sip_backtest(
    data_map:          dict,
    benchmark_df:      Optional[pd.DataFrame],
    start:             str            = "2016-01-01",
    end:               Optional[str]  = None,
    monthly_budget:    float          = 2000.0,
    max_picks:         int            = 5,
    min_alloc:         float          = 200.0,
    commission:        float          = 0.001,
    slippage:          float          = 0.001,
    sma_breakdown_days: int           = 10,
    momentum_periods:  Optional[list] = None,
    markets:           Optional[list] = None,
) -> dict:
    """
    Simulate the monthly SIP strategy over a historical period.

    Parameters
    ----------
    data_map          : {ticker: DataFrame} with SMA_50, SMA_200, Close computed
    benchmark_df      : price DataFrame for benchmark (e.g. ^GSPC); None skips benchmark
    start / end       : "YYYY-MM-DD" period bounds
    monthly_budget    : EUR deployed each month
    max_picks         : max stocks to buy per month
    min_alloc         : min EUR per position (fewer picks if budget/picks < min_alloc)
    commission/slippage: one-way cost each
    sma_breakdown_days: consecutive days SMA_50 < SMA_200 before forced exit
    momentum_periods  : lookback periods for momentum score
    markets           : filter data_map to these markets; None = all

    Returns dict with: xirr, benchmark_xirr, total_invested, final_nav,
                       total_gain, max_drawdown, nav_history, year_returns,
                       all_trades, report_text
    """
    periods       = momentum_periods or _DEFAULT_PERIODS
    cost          = commission + slippage
    active_mkts   = [m.upper() for m in (markets or ["US", "EU", "IN"])]

    # Regional budget split — default equal weight across active markets
    n_mkts        = len(active_mkts)
    region_budget = {m: monthly_budget / n_mkts for m in active_mkts}

    # Currency display per region
    _CURRENCY = {"US": "USD", "EU": "EUR", "IN": "INR"}
    _SYMBOL   = {"US": "$",   "EU": "€",   "IN": "₹"}

    # ── Date range ────────────────────────────────────────────────────────────
    start_dt = pd.Timestamp(start)
    end_dt   = pd.Timestamp(end) if end else pd.Timestamp.now().normalize()

    all_dates: set = set()
    for df in data_map.values():
        all_dates.update(df.index)
    trading_days = sorted(d for d in all_dates if start_dt <= d <= end_dt)
    if len(trading_days) < 63:
        return {"error": "Insufficient historical data for backtest period"}

    # Tickers grouped by market
    tickers_by_mkt: dict[str, list[str]] = {m: [] for m in active_mkts}
    for t, df in data_map.items():
        m = _get_market(t)
        if m in tickers_by_mkt:
            tickers_by_mkt[m].append(t)

    monthly_dates = trading_days[::21]

    # ── Per-region state ──────────────────────────────────────────────────────
    region_portfolio: dict[str, dict] = {m: {} for m in active_mkts}
    region_cash:      dict[str, float] = {m: 0.0 for m in active_mkts}
    region_invested:  dict[str, float] = {m: 0.0 for m in active_mkts}
    region_cfs:       dict[str, list]  = {m: [] for m in active_mkts}
    region_nav_hist:  dict[str, list]  = {m: [] for m in active_mkts}

    total_invested  = 0.0
    cash_flows:    list[tuple[float, date]] = []
    nav_history:   list[dict]  = []
    all_trades:    list[dict]  = []

    bench_shares = 0.0
    bench_cfs:   list[tuple[float, date]] = []

    for cycle_date in monthly_dates:
        py_date = cycle_date.date()
        cycle_nav = 0.0

        for mkt in active_mkts:
            bgt = region_budget[mkt]
            region_cash[mkt]    += bgt
            region_invested[mkt]+= bgt
            total_invested       += bgt
            region_cfs[mkt].append((-bgt, py_date))
            cash_flows.append((-bgt, py_date))

        # Benchmark: buy S&P 500 with full monthly_budget
        if benchmark_df is not None:
            bp = _price_at(benchmark_df, cycle_date, offset=cost)
            if bp and bp > 0:
                bench_shares += monthly_budget / bp
                bench_cfs.append((-monthly_budget, py_date))

        for mkt in active_mkts:
            port   = region_portfolio[mkt]
            tickers = tickers_by_mkt[mkt]
            bgt    = region_budget[mkt]

            # ── Exits
            for ticker in list(port.keys()):
                df  = data_map.get(ticker)
                if df is None:
                    continue
                sub = df.loc[:cycle_date]
                if _breakdown_days(sub) >= sma_breakdown_days:
                    sell_px = _price_at(sub, cycle_date, offset=-cost)
                    if sell_px and sell_px > 0:
                        proceeds = port[ticker]["shares"] * sell_px
                        region_cash[mkt] += proceeds
                        all_trades.append({
                            "date": str(py_date), "action": "SELL",
                            "ticker": ticker, "market": mkt,
                            "shares": round(port[ticker]["shares"], 4),
                            "price": round(sell_px, 4),
                            "local_currency": _CURRENCY[mkt],
                        })
                    del port[ticker]

            # ── Select picks for this region
            picks = _select_picks(data_map, tickers, cycle_date, max_picks,
                                  sma_breakdown_days, port, periods)

            # ── Allocate: deploy regional_budget (not all accumulated cash)
            deploy = min(bgt, region_cash[mkt])
            if picks and deploy >= min_alloc:
                n = min(len(picks), max(1, int(deploy / min_alloc)))
                picks = picks[:n]
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
                        "price": round(buy_px, 4),
                        "local_currency": _CURRENCY[mkt],
                        "amount_eur": round(alloc_per, 2),
                    })

            # ── Regional NAV (in local currency terms)
            region_nav = region_cash[mkt]
            for ticker, pos in port.items():
                df  = data_map.get(ticker)
                sub = df.loc[:cycle_date] if df is not None else None
                if sub is not None and not sub.empty:
                    region_nav += pos["shares"] * float(sub.iloc[-1]["Close"])

            region_nav_hist[mkt].append({
                "date":      str(py_date),
                "nav":       round(region_nav, 2),
                "cash":      round(region_cash[mkt], 2),
                "positions": len(port),
                "invested":  round(region_invested[mkt], 2),
                "currency":  _CURRENCY[mkt],
                "symbol":    _SYMBOL[mkt],
            })
            cycle_nav += region_nav

        nav_history.append({
            "date":      str(py_date),
            "nav":       round(cycle_nav, 2),
            "invested":  round(total_invested, 2),
            "positions": sum(len(region_portfolio[m]) for m in active_mkts),
        })

    # ── Final liquidation ─────────────────────────────────────────────────────
    final_date = end_dt.date()
    final_nav  = 0.0
    region_final: dict[str, float] = {}
    region_gain:  dict[str, float] = {}

    for mkt in active_mkts:
        port = region_portfolio[mkt]
        r_nav = region_cash[mkt]
        for ticker, pos in port.items():
            df  = data_map.get(ticker)
            sub = df.loc[:end_dt] if df is not None else None
            if sub is not None and not sub.empty:
                r_nav += pos["shares"] * float(sub.iloc[-1]["Close"])
        region_final[mkt] = round(r_nav, 2)
        region_gain[mkt]  = round(r_nav - region_invested[mkt], 2)
        final_nav         += r_nav
        region_cfs[mkt].append((r_nav, final_date))
        cash_flows.append((r_nav, final_date))

    # Benchmark
    bench_final = 0.0
    if benchmark_df is not None and bench_cfs:
        bsub = benchmark_df.loc[:end_dt]
        if not bsub.empty:
            bench_final = bench_shares * float(bsub.iloc[-1]["Close"])
            bench_cfs.append((bench_final, final_date))

    # ── Metrics ───────────────────────────────────────────────────────────────
    xirr_val    = _xirr(cash_flows)
    bench_xirr  = _xirr(bench_cfs) if bench_cfs else float("nan")
    region_xirr = {m: _xirr(region_cfs[m]) for m in active_mkts}
    nav_vals    = [h["nav"] for h in nav_history]
    max_dd      = _max_drawdown(nav_vals)
    total_gain  = final_nav - total_invested
    avg_pos     = float(np.mean([h["positions"] for h in nav_history])) if nav_history else 0.0
    year_returns = _year_returns(nav_history, monthly_budget)

    report = _format_report(
        start=start, end=str(end_dt.date()),
        markets=active_mkts,
        monthly_budget=monthly_budget,
        total_invested=total_invested,
        final_nav=final_nav,
        total_gain=total_gain,
        xirr=xirr_val,
        benchmark_xirr=bench_xirr,
        max_dd=max_dd,
        n_cycles=len(monthly_dates),
        avg_positions=avg_pos,
        year_returns=year_returns,
        n_tickers=sum(len(v) for v in tickers_by_mkt.values()),
        n_trades=len(all_trades),
        region_invested=region_invested,
        region_final=region_final,
        region_gain=region_gain,
        region_xirr=region_xirr,
        region_symbol=_SYMBOL,
        region_currency=_CURRENCY,
    )

    return {
        "xirr":            xirr_val,
        "benchmark_xirr":  bench_xirr,
        "region_xirr":     region_xirr,
        "region_invested": region_invested,
        "region_final":    region_final,
        "region_gain":     region_gain,
        "total_invested":  round(total_invested, 2),
        "final_nav":       round(final_nav, 2),
        "total_gain":      round(total_gain, 2),
        "max_drawdown":    round(max_dd * 100, 2),
        "n_cycles":        len(monthly_dates),
        "avg_positions":   round(avg_pos, 1),
        "nav_history":     nav_history,
        "region_nav_hist": region_nav_hist,
        "year_returns":    year_returns,
        "all_trades":      all_trades,
        "report_text":     report,
        "markets":         active_mkts,
        "region_currency": _CURRENCY,
        "region_symbol":   _SYMBOL,
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
    monthly_budget: float, total_invested: float,
    final_nav: float, total_gain: float,
    xirr: float, benchmark_xirr: float,
    max_dd: float, n_cycles: int, avg_positions: float,
    year_returns: list, n_tickers: int, n_trades: int,
    region_invested: dict | None = None,
    region_final: dict | None = None,
    region_gain: dict | None = None,
    region_xirr: dict | None = None,
    region_symbol: dict | None = None,
    region_currency: dict | None = None,
) -> str:
    sep  = "=" * 68
    dash = "-" * 44
    pct  = lambda v: f"{v*100:+.2f}%" if not math.isnan(v) else "N/A"

    _sym = region_symbol or {}
    _cur = region_currency or {}

    lines = [
        "",
        sep,
        "  MASTERMIND PRO — SIP BACKTEST",
        f"  Markets  : {', '.join(markets)}  |  Universe: {n_tickers} tickers",
        f"  Period   : {start}  to  {end}",
        f"  Budget   : €{monthly_budget:,.0f}/month  |  Cycles: {n_cycles}",
        sep,
        "",
        "  PERFORMANCE SUMMARY (Combined)",
        "  " + dash,
        f"  Total invested         €{total_invested:>12,.0f}",
        f"  Final portfolio value  €{final_nav:>12,.0f}",
        f"  Total gain             €{total_gain:>12,.0f}  ({total_gain/total_invested*100:+.1f}%)",
        f"  XIRR (annualised IRR)  {pct(xirr):>13}",
        f"  Benchmark XIRR (index) {pct(benchmark_xirr):>13}",
        f"  Alpha vs benchmark     {pct(xirr - benchmark_xirr) if not math.isnan(benchmark_xirr) else 'N/A':>13}",
        f"  Max drawdown (NAV)     {max_dd*100:>+12.2f}%",
        f"  Avg open positions     {avg_positions:>12.1f}",
        f"  Total trades           {n_trades:>12}",
    ]

    # ── Regional breakdown
    if region_xirr and region_invested and region_final and region_gain:
        lines += [
            "",
            "  REGIONAL BREAKDOWN",
            "  " + dash,
            f"  {'Market':<6} {'Currency':<8} {'Invested':>14} {'Final Value':>14} {'Gain':>12} {'XIRR':>9}",
        ]
        for m in markets:
            sym = _sym.get(m, "€")
            cur = _cur.get(m, "EUR")
            inv = region_invested.get(m, 0.0)
            fin = region_final.get(m, 0.0)
            gn  = region_gain.get(m, 0.0)
            xi  = region_xirr.get(m, float("nan"))
            lines.append(
                f"  {m:<6} {cur:<8} "
                f"{sym}{inv:>12,.0f} "
                f"{sym}{fin:>12,.0f} "
                f"{sym}{gn:>10,.0f} "
                f"{pct(xi):>9}"
            )

    lines += [
        "",
        "  YEAR-BY-YEAR RETURNS  (Modified Dietz — money-weighted)",
        "  " + dash,
        f"  {'Year':<6} {'NAV Start':>12} {'Contributed':>12} {'NAV End':>12} {'Return':>8}",
    ]

    for r in year_returns:
        lines.append(
            f"  {r['year']:<6} "
            f"€{r['nav_start']:>10,.0f} "
            f"€{r['contributions']:>10,.0f} "
            f"€{r['nav_end']:>10,.0f} "
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
        "",
        sep,
        "  DISCLAIMER: Research and paper trading only. Not financial advice.",
        sep,
        "",
    ]
    return "\n".join(lines)
