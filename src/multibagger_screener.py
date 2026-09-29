"""
multibagger_screener.py
=======================
Screens small/mid-cap stocks for multi-bagger potential (5–10× over 3–5yr).

How this differs from the LT Q-score screener
----------------------------------------------
  Q-score   → optimised for stable quality businesses (ROE, margins, FCF)
  MB-score  → optimised for growth acceleration in structural demand gaps

CONVICTION tiers
----------------
  STRONG    MB-score ≥ 70  AND  gap match  AND  technical gate passes
  STRONG    MB-score ≥ 70  AND  gap match  (tech gate optional — gaps override)
  MODERATE  MB-score ≥ 55
  WATCH     MB-score ≥ 38
  SKIP      below 38

MB-score components (sum to 1.0)
---------------------------------
  rev_growth_abs    0.28   raw 3yr CAGR magnitude
  rev_acceleration  0.20   is recent year faster than historical?
  operating_margin  0.15   scalability proxy
  cap_position      0.15   smaller cap = more headroom
  eps_growth        0.12   earnings following revenue (quality check)
  debt_equity       0.10   balance-sheet resilience

Hard filters (disqualify before scoring)
-----------------------------------------
  Market cap above ceiling (IN: ₹5,000Cr / US+EU: $10B)
  D/E > 4.0
  Revenue declining > 5% YoY
"""

from __future__ import annotations

import math
from typing import Optional

from fundamental import fetch_all_fundamentals, _safe, _score_steps
from sector_gaps import SectorGap, match_gap

# ── Market-cap ceilings ───────────────────────────────────────────────────────
# Above these levels a stock is unlikely to 10× in 5 years.
_CAP_CEILING: dict[str, float] = {
    "IN": 500_000_000_000,    # ₹5,000 Cr (~$6B) — top of Indian mid-cap range
    "US": 10_000_000_000,     # $10B
    "EU": 10_000_000_000,     # €10B
}

# ── MB-score weights ──────────────────────────────────────────────────────────
_MB_WEIGHTS: dict[str, float] = {
    "rev_growth_abs":   0.28,
    "rev_acceleration": 0.20,
    "operating_margin": 0.15,
    "cap_position":     0.15,
    "eps_growth":       0.12,
    "debt_equity":      0.10,
}
assert abs(sum(_MB_WEIGHTS.values()) - 1.0) < 1e-9, "MB weights must sum to 1.0"


# ── Component scorers ─────────────────────────────────────────────────────────

def _score_rev_growth(cagr_3yr: Optional[float]) -> Optional[int]:
    return _score_steps(cagr_3yr, [
        (0.50, 10), (0.40, 9), (0.30, 8), (0.25, 7),
        (0.20, 6),  (0.15, 5), (0.10, 3), (0.0,  1), (-99, 0),
    ])


def _score_acceleration(
    rev_1yr: Optional[float],
    rev_3yr: Optional[float],
) -> Optional[int]:
    """
    Score how much recent growth exceeds the historical CAGR.
    Positive delta = acceleration (growth picking up speed).
    """
    if rev_1yr is None or rev_3yr is None:
        return None
    delta = rev_1yr - rev_3yr
    if   delta >= 0.15: return 10   # strong acceleration
    elif delta >= 0.10: return 8
    elif delta >= 0.05: return 6
    elif delta >= 0.00: return 5    # on trend — neutral
    elif delta >= -0.05:return 3    # slight decel — watch
    else:               return 1    # meaningful decel — risk flag


def _score_cap_position(market_cap: Optional[float], market: str) -> Optional[int]:
    """Smaller cap relative to ceiling = more headroom = higher score."""
    if market_cap is None or market_cap <= 0:
        return None
    ceiling = _CAP_CEILING.get(market.upper(), 10_000_000_000)
    ratio   = market_cap / ceiling
    if   ratio <= 0.02: return 10   # tiny — <2% of ceiling
    elif ratio <= 0.05: return 9
    elif ratio <= 0.10: return 8
    elif ratio <= 0.20: return 7
    elif ratio <= 0.35: return 6
    elif ratio <= 0.55: return 5
    elif ratio <= 0.80: return 3
    elif ratio <= 1.00: return 1    # near ceiling — borderline
    else:               return 0    # above ceiling — disqualified


def _score_de(de: Optional[float]) -> Optional[int]:
    if de is None:
        return None
    if   de <= 0.20: return 10
    elif de <= 0.50: return 8
    elif de <= 1.00: return 6
    elif de <= 1.50: return 5
    elif de <= 2.00: return 3
    elif de <= 3.00: return 2
    else:            return 0    # >3× — disqualifier territory


def _score_eps_growth(eg: Optional[float]) -> Optional[int]:
    return _score_steps(eg, [
        (0.50, 10), (0.35, 9), (0.25, 8), (0.20, 7),
        (0.15, 6),  (0.10, 5), (0.05, 3), (0.0,  2), (-99, 0),
    ])


# ── MB-score ──────────────────────────────────────────────────────────────────

def mb_score(
    fundamental_data: dict,
    market: str = "IN",
) -> tuple[float, dict[str, Optional[int]]]:
    """
    Compute Multi-Bagger Score (0–100) and per-component breakdown.

    Returns
    -------
    (score: float 0–100, components: dict[name → score 0–10 | None])
    """
    d = fundamental_data

    # Revenue — prefer the computed 3yr CAGR over the YoY from info
    rev_3yr = _safe(d.get("revenue_cagr_3yr")) or _safe(d.get("revenue_growth"))
    rev_1yr = _safe(d.get("revenue_growth"))

    c: dict[str, Optional[int]] = {
        "rev_growth_abs":   _score_rev_growth(rev_3yr),
        "rev_acceleration": _score_acceleration(rev_1yr, rev_3yr),
        "operating_margin": _score_steps(
            _safe(d.get("operating_margin")),
            [(0.30, 10), (0.25, 9), (0.20, 8), (0.15, 7),
             (0.10, 6),  (0.07, 5), (0.05, 4), (0.0,  2), (-99, 0)],
        ),
        "cap_position": _score_cap_position(_safe(d.get("market_cap")), market),
        "debt_equity":  _score_de(_safe(d.get("debt_equity"))),
        "eps_growth":   _score_eps_growth(_safe(d.get("eps_growth"))),
    }

    total_w = sum(_MB_WEIGHTS[m] for m, s in c.items() if s is not None)
    total_s = sum(_MB_WEIGHTS[m] * s for m, s in c.items() if s is not None)

    score = min(100.0, (total_s / total_w * 10)) if total_w > 0 else 0.0
    return score, c


# ── Hard filter ───────────────────────────────────────────────────────────────

def _passes_hard_filter(d: dict, market: str) -> tuple[bool, str]:
    """
    Returns (passes, reason_if_failed).
    Hard filters remove stocks structurally unable to be multi-baggers.
    """
    mc      = _safe(d.get("market_cap"))
    ceiling = _CAP_CEILING.get(market.upper(), 10_000_000_000)
    if mc is not None and mc > ceiling:
        return False, f"Market cap {mc/1e9:.1f}B above {ceiling/1e9:.0f}B ceiling"

    de = _safe(d.get("debt_equity"))
    if de is not None and de > 4.0:
        return False, f"D/E {de:.1f}× — dilution / distress risk"

    rev = _safe(d.get("revenue_cagr_3yr")) or _safe(d.get("revenue_growth"))
    if rev is not None and rev < -0.05:
        return False, f"Revenue declining {rev*100:.1f}% — disqualified"

    return True, ""


# ── Technical gate ────────────────────────────────────────────────────────────

def _check_technical_gate(price_df) -> bool:
    """
    Returns True if SMA_50 > SMA_200, Close > SMA_200, and SMA_50 rising.
    Returns False if price_df is None or required columns are missing.
    """
    if price_df is None or price_df.empty:
        return False
    try:
        last   = price_df.iloc[-1]
        sma50  = last.get("SMA_50")
        sma200 = last.get("SMA_200")
        close  = last.get("Close")
        if not all(
            x is not None and math.isfinite(float(x))
            for x in [sma50, sma200, close]
        ):
            return False
        if float(sma50) <= float(sma200):
            return False
        if float(close) <= float(sma200):
            return False
        # SMA_50 rising: compare to 10 bars ago
        if len(price_df) > 10:
            lookback = float(price_df.iloc[-11].get("SMA_50", float("nan")))
            if math.isfinite(lookback) and float(sma50) <= lookback:
                return False
        return True
    except Exception:
        return False


# ── Conviction tier ───────────────────────────────────────────────────────────

def conviction_tier(
    score: float,
    gap: Optional[SectorGap],
    technical_pass: bool,
) -> str:
    if score >= 70 and gap is not None:
        return "STRONG"
    if score >= 65 and technical_pass:
        return "STRONG"
    if score >= 55:
        return "MODERATE"
    if score >= 38:
        return "WATCH"
    return "SKIP"


# ── Signal flags ──────────────────────────────────────────────────────────────

def mb_flags(d: dict) -> list[str]:
    """Positive signals and risk flags specific to multi-bagger screening."""
    flags = []

    rev_3yr = _safe(d.get("revenue_cagr_3yr")) or _safe(d.get("revenue_growth"))
    rev_1yr = _safe(d.get("revenue_growth"))

    # Positive signals
    if rev_1yr and rev_3yr and rev_1yr > rev_3yr + 0.10:
        flags.append(
            f"Revenue accelerating — 1yr {rev_1yr*100:.0f}% vs 3yr CAGR {rev_3yr*100:.0f}%"
        )

    eg = _safe(d.get("eps_growth"))
    if eg and eg > 0.30:
        flags.append(f"EPS growing {eg*100:.0f}% — earnings following revenue")

    om = _safe(d.get("operating_margin"))
    if om and om > 0.20:
        flags.append(f"Operating margin {om*100:.0f}% — high-quality scalable model")

    # Risk flags
    if rev_1yr and rev_3yr and rev_1yr < rev_3yr - 0.10:
        flags.append(
            f"Revenue decelerating — 1yr {rev_1yr*100:.0f}% below 3yr {rev_3yr*100:.0f}%"
        )

    de = _safe(d.get("debt_equity"))
    if de and de > 2.0:
        flags.append(f"D/E {de:.1f}× — leverage adds risk at high growth multiples")

    fcf = _safe(d.get("fcf_yield"))
    if fcf is not None and fcf < -0.02:
        flags.append("Burning cash — verify funding runway before investing")

    pe = _safe(d.get("pe"))
    if pe and pe > 80:
        flags.append(f"P/E {pe:.0f}× — priced for perfection; execution must be flawless")

    return flags


# ── Main screener ─────────────────────────────────────────────────────────────

def screen_multibaggers(
    tickers: list[str],
    market: str,
    fundamentals: Optional[dict[str, dict]] = None,
    price_data: Optional[dict] = None,
    min_score: float = 35.0,
    gap_only: bool = False,
) -> list[dict]:
    """
    Screen a list of tickers for multi-bagger potential.

    Parameters
    ----------
    tickers       : symbols to screen
    market        : "IN" | "US" | "EU"
    fundamentals  : pre-fetched fundamental dicts; fetched if None
    price_data    : pre-computed indicator DataFrames keyed by ticker;
                    technical gate is skipped if None
    min_score     : minimum MB-score to include (default 35)
    gap_only      : return only tickers matching a structural gap

    Returns
    -------
    list of result dicts sorted by mb_score descending
    """
    if fundamentals is None:
        print(f"  Fetching fundamentals for {len(tickers)} tickers...")
        fundamentals = fetch_all_fundamentals(tickers)

    results: list[dict] = []

    for ticker in tickers:
        f = fundamentals.get(ticker)
        if f is None or f.get("_error"):
            continue

        passes, _ = _passes_hard_filter(f, market)
        if not passes:
            continue

        score, components = mb_score(f, market)
        if score < min_score:
            continue

        gap     = match_gap(ticker, f.get("sector", ""), f.get("industry", ""), market)
        tech_ok = _check_technical_gate(
            price_data.get(ticker) if price_data else None
        )

        if gap_only and gap is None:
            continue

        tier  = conviction_tier(score, gap, tech_ok)
        flags = mb_flags(f)

        results.append({
            "ticker":           ticker,
            "name":             f.get("name", ticker),
            "market":           market,
            "sector":           f.get("sector", "Unknown"),
            "industry":         f.get("industry", "Unknown"),
            "mb_score":         round(score, 1),
            "conviction":       tier,
            "gap":              gap,
            "technical_gate":   tech_ok,
            "components":       components,
            "flags":            flags,
            "rev_cagr_3yr":     _safe(f.get("revenue_cagr_3yr")),
            "rev_growth_1yr":   _safe(f.get("revenue_growth")),
            "operating_margin": _safe(f.get("operating_margin")),
            "market_cap":       _safe(f.get("market_cap")),
            "debt_equity":      _safe(f.get("debt_equity")),
            "eps_growth":       _safe(f.get("eps_growth")),
            "pe":               _safe(f.get("pe")),
        })

    results.sort(key=lambda x: x["mb_score"], reverse=True)
    return results


# ── ASCII report ──────────────────────────────────────────────────────────────

_TIER_COLOR = {
    "STRONG":   "\033[92m",
    "MODERATE": "\033[93m",
    "WATCH":    "\033[96m",
    "SKIP":     "\033[90m",
}
_RESET = "\033[0m"
_BOLD  = "\033[1m"


def multibagger_report(results: list[dict], market: str) -> str:
    if not results:
        return "\n  No multi-bagger candidates found with current filters.\n"

    SEP = "=" * 72

    def _pct(v: Optional[float]) -> str:
        return f"{v*100:.0f}%" if v is not None else "N/A"

    def _row(i: int, r: dict) -> str:
        tier  = r["conviction"]
        col   = _TIER_COLOR.get(tier, "")
        gap   = r["gap"]
        gap_s = f"{gap.name} [{gap.tailwind}]" if gap else "—"
        return (
            f"  {i:<3}  {col}{r['ticker']:<14}{_RESET}  "
            f"{r['mb_score']:>5.1f}  {col}{tier:<10}{_RESET}  "
            f"{_pct(r['rev_cagr_3yr']):>7}  "
            f"{_pct(r['rev_growth_1yr']):>7}  "
            f"{_pct(r['operating_margin']):>7}  "
            f"{gap_s}"
        )

    strong   = [r for r in results if r["conviction"] == "STRONG"]
    moderate = [r for r in results if r["conviction"] == "MODERATE"]
    watch    = [r for r in results if r["conviction"] == "WATCH"]

    lines = [
        "",
        f"{_BOLD}\033[94m{SEP}{_RESET}",
        f"{_BOLD}\033[97m  MULTI-BAGGER SCREENER  --  {market.upper()}{_RESET}",
        f"\033[94m  Strategy: Revenue acceleration × structural gap × technical gate{_RESET}",
        f"{_BOLD}\033[94m{SEP}{_RESET}",
        f"  {'#':<3}  {'Ticker':<14}  {'MB':>5}  {'Conviction':<10}  "
        f"{'Rev3yr':>7}  {'Rev1yr':>7}  {'OpMgn':>7}  Gap",
        f"  {'-'*3}  {'-'*14}  {'-'*5}  {'-'*10}  "
        f"{'-'*7}  {'-'*7}  {'-'*7}  {'-'*22}",
    ]

    if strong:
        lines += [
            "",
            f"{_BOLD}\033[92m  ── STRONG ({len(strong)}) ───────────────────────────────────────────{_RESET}",
        ]
        for i, r in enumerate(strong, 1):
            lines.append(_row(i, r))
            for fl in r["flags"]:
                lines.append(f"       \033[93m↳ {fl}{_RESET}")

    if moderate:
        lines += [
            "",
            f"{_BOLD}\033[93m  ── MODERATE ({len(moderate)}) ────────────────────────────────────────{_RESET}",
        ]
        for i, r in enumerate(moderate, 1):
            lines.append(_row(i, r))
            for fl in r["flags"]:
                lines.append(f"       \033[96m↳ {fl}{_RESET}")

    if watch:
        lines += [
            "",
            f"{_BOLD}\033[96m  ── WATCH ({len(watch)}) ──────────────────────────────────────────────{_RESET}",
        ]
        for i, r in enumerate(watch, 1):
            lines.append(_row(i, r))

    lines += [
        "",
        f"{_BOLD}\033[94m{SEP}{_RESET}",
        f"  Screened: {len(results)}   Strong: {len(strong)}   "
        f"Moderate: {len(moderate)}   Watch: {len(watch)}",
        f"{_BOLD}\033[94m{SEP}{_RESET}",
        "",
        f"\033[93m  NOTE: MB-score measures historical growth velocity. "
        f"Past acceleration ≠ future result.\033[0m",
        f"\033[93m  Verify gap thesis, management quality, and competitive moat "
        f"before any position.\033[0m",
        f"{_BOLD}\033[94m{SEP}{_RESET}",
        "",
    ]
    return "\n".join(lines)
