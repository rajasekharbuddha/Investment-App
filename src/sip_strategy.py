"""
sip_strategy.py
===============
Monthly SIP (Systematic Investment Plan) strategy for US + EU equity accumulation.

Deploy a fixed budget each month into quality stocks that pass:
  1. Technical gate: SMA_50 > SMA_200 (uptrend only — no catching falling knives)
  2. Fundamental gate: Q-score >= 55 (from fundamental.py scoring)
  3. Ranked by composite: 40% Q-score + 60% momentum (1M/3M/6M/12M)

Exit rules (checked before each monthly deployment):
  - SMA_50 < SMA_200 for >= 10 consecutive trading days  -> structural breakdown exit
  - Q-score drops below 35                               -> quality deterioration exit
  - Position > 15% of portfolio value                    -> trim to 10%

Allocation:
  - Equal weight across top N picks (default 5)
  - Minimum €200 per position
  - Sector cap: no sector may exceed 25% of total portfolio
  - If fewer candidates pass all gates: reduce picks; hold remainder as cash

Portfolio state persisted in portfolio/sip_holdings.json.
"""

from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np

ROOT = Path(__file__).parent.parent
SIP_HOLDINGS_FILE = ROOT / "portfolio" / "sip_holdings.json"

SIP_CONFIG: dict = {
    "markets":            ["US", "EU", "IN"],
    # Monthly budget per region in LOCAL CURRENCY (USD / EUR / INR)
    "region_budget":      {"US": 2000.0, "EU": 2000.0, "IN": 20000.0},
    # Max picks per region per month
    "picks_per_region":   {"US": 2, "EU": 2, "IN": 2},
    # Local currencies for display
    "region_currency":    {"US": "USD", "EU": "EUR", "IN": "INR"},
    "region_symbol":      {"US": "$",   "EU": "€",   "IN": "₹"},
    # Min position size per region in local currency
    "region_min_alloc":   {"US": 200.0, "EU": 200.0, "IN": 2000.0},
    # Regime reserve (C2): hold back 10% each month; deploy when regional index < SMA_200
    "regime_reserve_pct": 0.10,
    "regime_bench":       {"US": "^GSPC", "EU": "^STOXX50E", "IN": "^NSEI"},
    "min_q_entry":        55.0,
    "min_q_exit":         35.0,
    "max_picks":          5,         # fallback
    "min_alloc":          200.0,     # fallback minimum per position
    "sector_cap":         0.25,
    "max_position_pct":   0.15,
    "trim_to_pct":        0.10,
    "sma_breakdown_days": 10,
    "q_weight":           0.40,
    "mom_weight":         0.60,
    "momentum_periods":   [21, 63, 126, 252],  # 1M / 3M / 6M / 12M
}


# ── Portfolio state ──────────────────────────────────────────────────────────

def load_sip_holdings() -> dict:
    if SIP_HOLDINGS_FILE.exists():
        try:
            state = json.loads(SIP_HOLDINGS_FILE.read_text(encoding="utf-8"))
            # Migrate: add dip_reserve field if missing from older saves
            if "dip_reserve" not in state:
                state["dip_reserve"] = {m: 0.0 for m in SIP_CONFIG["markets"]}
            return state
        except Exception:
            pass
    return {
        "holdings":       {},
        "region_budget":  SIP_CONFIG["region_budget"],
        "dip_reserve":    {m: 0.0 for m in SIP_CONFIG["markets"]},
        "total_deployed": 0.0,
        "start_date":     datetime.now().strftime("%Y-%m-%d"),
        "cycles":         [],
    }


def save_sip_holdings(state: dict) -> None:
    SIP_HOLDINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SIP_HOLDINGS_FILE.write_text(
        json.dumps(state, indent=2, default=str), encoding="utf-8"
    )


# ── Internal helpers ─────────────────────────────────────────────────────────

def _momentum_score(df, periods: list[int] | None = None) -> float:
    """Unweighted average return across momentum lookback periods."""
    periods = periods or SIP_CONFIG["momentum_periods"]
    close = df["Close"].dropna()
    if len(close) < max(periods):
        return float("nan")
    scores = []
    for p in periods:
        if len(close) > p:
            scores.append(float(close.iloc[-1] / close.iloc[-p] - 1))
    return float(np.mean(scores)) if scores else float("nan")


def _sma_breakdown_days(df) -> int:
    """Consecutive trailing days where SMA_50 < SMA_200."""
    if df is None or "SMA_50" not in df.columns or "SMA_200" not in df.columns:
        return 0
    diff = (df["SMA_50"] - df["SMA_200"]).dropna()
    count = 0
    for v in reversed(diff.values):
        if v < 0:
            count += 1
        else:
            break
    return count


def _norm(v: float, lo: float, hi: float) -> float:
    return (v - lo) / (hi - lo) if hi > lo else 0.5


# ── Core functions ────────────────────────────────────────────────────────────

def screen_candidates(
    data_map: dict,
    q_scores: dict,
    markets: list[str] | None = None,
    min_q: float | None = None,
) -> list[dict]:
    """
    Filter and rank tickers for SIP deployment.

    Returns list sorted by composite score (descending):
      {ticker, market, sector, q_score, momentum, composite, price}
    """
    from config import get_market, get_sector

    markets = [m.upper() for m in (markets or SIP_CONFIG["markets"])]
    min_q = min_q if min_q is not None else SIP_CONFIG["min_q_entry"]
    rows = []

    for ticker, df in data_map.items():
        mkt = get_market(ticker)
        if mkt not in markets:
            continue
        if df is None or len(df) < 200:
            continue

        if "SMA_50" not in df.columns or "SMA_200" not in df.columns:
            continue

        last = df.iloc[-1]
        sma50  = float(last.get("SMA_50",  float("nan")))
        sma200 = float(last.get("SMA_200", float("nan")))
        price  = float(last.get("Close",   float("nan")))

        if any(math.isnan(v) for v in [sma50, sma200, price]) or price <= 0:
            continue
        if sma50 <= sma200:   # must be in uptrend
            continue

        q = q_scores.get(ticker, 0.0)
        if q < min_q:
            continue

        mom = _momentum_score(df)
        if math.isnan(mom):
            continue

        rows.append({
            "ticker":   ticker,
            "market":   mkt,
            "sector":   get_sector(ticker),
            "q_score":  q,
            "momentum": mom,
            "price":    price,
        })

    if not rows:
        return rows

    q_vals = [r["q_score"] for r in rows]
    m_vals = [r["momentum"] for r in rows]
    q_lo, q_hi = min(q_vals), max(q_vals)
    m_lo, m_hi = min(m_vals), max(m_vals)

    for r in rows:
        r["composite"] = (
            SIP_CONFIG["q_weight"]   * _norm(r["q_score"],  q_lo, q_hi)
            + SIP_CONFIG["mom_weight"] * _norm(r["momentum"], m_lo, m_hi)
        )

    rows.sort(key=lambda x: x["composite"], reverse=True)
    return rows


def allocate_budget(
    candidates: list[dict],
    monthly_budget: float,
    held_portfolio: dict,
    total_portfolio_value: float,
    max_picks: int | None = None,
    min_alloc: float | None = None,
    sector_cap: float | None = None,
) -> dict:
    """
    Allocate monthly_budget across top candidates with sector cap.
    Returns {ticker: euro_amount}.
    """
    max_picks  = max_picks  or SIP_CONFIG["max_picks"]
    min_alloc  = min_alloc  or SIP_CONFIG["min_alloc"]
    sector_cap = sector_cap or SIP_CONFIG["sector_cap"]

    sector_totals: dict[str, float] = {}
    for t, h in held_portfolio.items():
        sec = h.get("sector", "Unknown")
        val = h.get("shares", 0) * h.get("avg_cost", 0)
        sector_totals[sec] = sector_totals.get(sec, 0.0) + val

    selected: list[dict] = []
    for c in candidates:
        if len(selected) >= max_picks:
            break
        sec = c["sector"]
        sec_pct = sector_totals.get(sec, 0.0) / total_portfolio_value if total_portfolio_value > 0 else 0.0
        if sec_pct >= sector_cap:
            continue
        selected.append(c)

    if not selected:
        return {}

    alloc_per = monthly_budget / len(selected)
    if alloc_per < min_alloc:
        count = max(1, int(monthly_budget / min_alloc))
        selected = selected[:count]
        alloc_per = monthly_budget / len(selected)

    return {c["ticker"]: round(alloc_per, 2) for c in selected}


def check_exits(
    held_portfolio: dict,
    data_map: dict,
    q_scores: dict,
    current_prices: dict | None = None,
    total_portfolio_value: float = 0.0,
) -> list[dict]:
    """
    Return exit/trim recommendations for held positions.
    Each entry: {ticker, action, reason, shares, current_price, gain_pct}
    """
    cfg   = SIP_CONFIG
    exits = []

    for ticker, h in held_portfolio.items():
        df    = data_map.get(ticker)
        price = (current_prices or {}).get(ticker) or h.get("avg_cost", 0.0)
        pos_val = h.get("shares", 0) * price
        pos_pct = pos_val / total_portfolio_value if total_portfolio_value > 0 else 0.0
        gain_pct = round((price / h.get("avg_cost", price) - 1) * 100, 1) if h.get("avg_cost") else 0.0

        breakdown = _sma_breakdown_days(df)
        if breakdown >= cfg["sma_breakdown_days"]:
            exits.append({
                "ticker":        ticker,
                "action":        "EXIT",
                "reason":        f"SMA_50 < SMA_200 for {breakdown} consecutive days — structural breakdown",
                "shares":        h.get("shares", 0),
                "current_price": price,
                "gain_pct":      gain_pct,
            })
            continue

        q = q_scores.get(ticker, 100.0)
        if q < cfg["min_q_exit"]:
            exits.append({
                "ticker":        ticker,
                "action":        "EXIT",
                "reason":        f"Q-score {q:.0f} below exit threshold {cfg['min_q_exit']:.0f} — quality deterioration",
                "shares":        h.get("shares", 0),
                "current_price": price,
                "gain_pct":      gain_pct,
            })
            continue

        if pos_pct > cfg["max_position_pct"]:
            target_val  = total_portfolio_value * cfg["trim_to_pct"]
            trim_val    = pos_val - target_val
            trim_shares = round(trim_val / price, 2) if price > 0 else 0
            exits.append({
                "ticker":        ticker,
                "action":        "TRIM",
                "reason":        f"Position at {pos_pct*100:.1f}% of portfolio — trim to {cfg['trim_to_pct']*100:.0f}%",
                "shares":        trim_shares,
                "current_price": price,
                "gain_pct":      gain_pct,
            })

    return exits


def format_report(
    cycle_date: str,
    candidates: list[dict],
    allocation: dict,
    exits: list[dict],
    held_portfolio: dict,
    monthly_budget: float,
    total_deployed: float,
) -> str:
    sep  = "=" * 68
    dash = "-" * 44

    lines = [
        "",
        sep,
        "  MASTERMIND PRO — MONTHLY SIP REPORT",
        f"  Date   : {cycle_date}",
        f"  Budget : €{monthly_budget:,.0f}/month  |  Total deployed: €{total_deployed:,.0f}",
        sep,
    ]

    # ── Exits
    lines += ["", f"  EXIT / TRIM SIGNALS ({len(exits)} actions)", "  " + dash]
    if exits:
        for e in exits:
            sign = "+" if e.get("gain_pct", 0) >= 0 else ""
            lines.append(
                f"  [{e['action']:<4}] {e['ticker']:<12} "
                f"{e['shares']:.2f} sh @ €{e['current_price']:.2f}  "
                f"({sign}{e['gain_pct']:.1f}%)  {e['reason']}"
            )
    else:
        lines.append("  None — all holdings healthy.")

    # ── This month's buys
    lines += ["", f"  THIS MONTH'S BUYS  (€{sum(allocation.values()):,.0f} deployed)", "  " + dash]
    if allocation:
        for ticker, eur in allocation.items():
            c = next((x for x in candidates if x["ticker"] == ticker), {})
            approx_shares = eur / c.get("price", 1.0) if c.get("price") else 0
            lines.append(
                f"  BUY  {ticker:<12}  €{eur:,.0f}  "
                f"(~{approx_shares:.1f} sh @ €{c.get('price', 0):.2f})  "
                f"Q={c.get('q_score', 0):.0f}  "
                f"Mom={c.get('momentum', 0)*100:+.1f}%  "
                f"[{c.get('market', '')} · {c.get('sector', '')}]"
            )
    else:
        lines.append("  No qualifying candidates — hold cash this month.")

    # ── Candidates screened
    show_n = min(15, len(candidates))
    lines += ["", f"  CANDIDATES SCREENED — top {show_n} of {len(candidates)} passing gates", "  " + dash]
    lines.append(f"  {'Ticker':<12} {'Mkt':<4} {'Sector':<18} {'Q':>5} {'Mom%':>7} {'Score':>7}  Sel")
    for c in candidates[:show_n]:
        sel = " ✓" if c["ticker"] in allocation else "  "
        lines.append(
            f"  {c['ticker']:<12} {c['market']:<4} {c['sector']:<18} "
            f"{c['q_score']:>5.0f} {c['momentum']*100:>7.1f}% {c['composite']:>7.3f} {sel}"
        )

    # ── Current holdings
    lines += ["", f"  CURRENT HOLDINGS ({len(held_portfolio)} positions)", "  " + dash]
    if held_portfolio:
        for t, h in held_portfolio.items():
            lines.append(
                f"  {t:<12}  {h.get('shares', 0):.2f} sh  "
                f"avg €{h.get('avg_cost', 0):.2f}  "
                f"cost €{h.get('total_cost_eur', 0):,.0f}  "
                f"[{h.get('market', '')} · {h.get('sector', '')}]  "
                f"since {h.get('first_bought', '?')}"
            )
    else:
        lines.append("  Portfolio is empty — first deployment.")

    lines += [
        "",
        sep,
        "  DISCLAIMER: Research and paper trading only. Not financial advice.",
        sep,
        "",
    ]
    return "\n".join(lines)


def _sma200_of_index(bench_df, as_of=None) -> tuple[float, float]:
    """Return (close, sma200) for the index. Both NaN if < 200 rows."""
    sub = bench_df if as_of is None else bench_df.loc[:as_of]
    if len(sub) < 200:
        return float("nan"), float("nan")
    close  = float(sub["Close"].iloc[-1])
    sma200 = float(sub["Close"].iloc[-200:].mean())
    return close, sma200


def run_sip_cycle(
    data_map: dict,
    q_scores: dict,
    current_prices: dict | None = None,
    override_budget: float | None = None,
    override_min_q: float | None = None,
    benchmark_dfs: dict | None = None,   # {mkt: DataFrame} for regime check
    dry_run: bool = False,
) -> dict:
    """
    Full monthly SIP cycle: check exits → screen candidates → allocate → report.

    Implements C2 regime-reserve strategy: 10% of each region's budget is held
    back each month and deployed in bulk when the regional index drops below its
    SMA_200 (market downtrend signal).

    Returns {candidates, allocation, exits, report_text, state, cycle_date,
             regime_status, dip_reserve}.
    If dry_run=True, portfolio state is not written to disk.
    """
    cfg    = SIP_CONFIG
    state  = load_sip_holdings()
    held   = state.get("holdings", {})
    markets = cfg["markets"]

    rb          = cfg["region_budget"]
    reserve_pct = cfg["regime_reserve_pct"]
    rma         = cfg["region_min_alloc"]

    # Per-region cash buckets
    dip_reserve: dict[str, float] = state.get("dip_reserve", {m: 0.0 for m in markets})

    # Compute current portfolio value for exit checks
    total_value = sum(
        h.get("shares", 0) * ((current_prices or {}).get(t) or h.get("avg_cost", 0))
        for t, h in held.items()
    )

    # ── Check exits (across all regions)
    exits = check_exits(held, data_map, q_scores, current_prices, total_value)

    # ── Regime check per region: release reserve if index < SMA_200
    regime_status: dict[str, str] = {}
    regime_released: dict[str, float] = {}
    for mkt in markets:
        bdf = (benchmark_dfs or {}).get(mkt)
        if bdf is not None and dip_reserve.get(mkt, 0.0) > 0:
            idx_close, idx_sma200 = _sma200_of_index(bdf)
            if not math.isnan(idx_close) and idx_close < idx_sma200:
                regime_released[mkt] = dip_reserve[mkt]
                dip_reserve[mkt]     = 0.0
                regime_status[mkt]   = f"REGIME DOWN — released {cfg['region_symbol'].get(mkt,'')}{regime_released[mkt]:,.0f} reserve"
            else:
                regime_status[mkt] = "uptrend — reserve held"
        elif bdf is None:
            regime_status[mkt] = "no benchmark — reserve held"
        else:
            regime_status[mkt] = "reserve empty"

    # ── Screen and allocate per region
    allocation: dict[str, float] = {}
    region_deploy: dict[str, float] = {}

    for mkt in markets:
        bgt       = override_budget or rb.get(mkt, 2000.0)
        to_invest = bgt * (1.0 - reserve_pct)
        released  = regime_released.get(mkt, 0.0)
        deploy    = to_invest + released   # released reserve adds to this month's deployment

        dip_reserve[mkt] = dip_reserve.get(mkt, 0.0) + bgt * reserve_pct  # accumulate new reserve
        region_deploy[mkt] = deploy

        mkt_candidates = screen_candidates(data_map, q_scores, markets=[mkt], min_q=override_min_q)
        mkt_held       = {t: h for t, h in held.items() if h.get("market") == mkt}
        mkt_val        = sum(
            h.get("shares", 0) * ((current_prices or {}).get(t) or h.get("avg_cost", 0))
            for t, h in mkt_held.items()
        )
        mkt_alloc = allocate_budget(
            mkt_candidates, deploy, mkt_held, mkt_val,
            min_alloc=rma.get(mkt, 200.0),
        )
        allocation.update(mkt_alloc)

    candidates = screen_candidates(data_map, q_scores, min_q=override_min_q)

    cycle_date     = datetime.now().strftime("%Y-%m-%d")
    total_deployed = state.get("total_deployed", 0.0) + sum(allocation.values())

    report = format_report(
        cycle_date, candidates, allocation, exits,
        held, sum(rb.values()), total_deployed,
    )

    if not dry_run:
        state["total_deployed"] = total_deployed
        state["dip_reserve"]    = dip_reserve
        state["cycles"].append({
            "date":            cycle_date,
            "buys":            allocation,
            "exits":           [e["ticker"] for e in exits if e["action"] == "EXIT"],
            "region_deploy":   region_deploy,
            "regime_released": regime_released,
        })
        save_sip_holdings(state)

    return {
        "candidates":      candidates,
        "allocation":      allocation,
        "exits":           exits,
        "report_text":     report,
        "state":           state,
        "cycle_date":      cycle_date,
        "regime_status":   regime_status,
        "dip_reserve":     dip_reserve,
        "region_deploy":   region_deploy,
    }
