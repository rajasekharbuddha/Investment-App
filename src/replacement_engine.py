"""
replacement_engine.py
=====================
Localized tiered replacement selection — aggressive regional recycling.

Priority:
  1. same market + same sector ENTER
  2. same market + any sector ENTER  (aggressive regional recycling)
  3. hold cash inside the region     — no cross-border allocation

Hysteresis buffer: a replacement candidate must outperform the exiting
position's quality_score by at least 15% to justify the transaction friction.
This prevents toxic churn from marginal score differences.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from config import RISK, get_market, get_sector


def find_replacement(
    exit_market: str,
    exit_sector: str,
    candidates: Dict[str, Any],
    sizing: Dict[str, Any],
    held: List[Dict],
    virtual_pending: List[str],
    equity: float,
    risk_scale: float = 1.0,
    exit_score: float = 0.0,
) -> Optional[Dict[str, Any]]:
    all_tickers = {p["ticker"] for p in held} | set(virtual_pending)

    # Hysteresis hurdle: candidate must beat the exiting position's score by ≥15%
    # to justify transaction friction. Falls back to a flat 5-point floor when
    # the exiting position had no score (e.g. legacy positions without quality_score).
    _hurdle = abs(exit_score) * 0.15 if exit_score else 5.0

    def _eligible(ticker: str, cand: Dict) -> Tuple[bool, str]:
        if cand.get("decision") != "ENTER":
            return False, "no_ENTER"
        if ticker in all_tickers:
            return False, "already_held"
        if ticker not in sizing or sizing[ticker].get("shares", 0) <= 0:
            return False, "no_sizing"
        # Hysteresis: skip marginal upgrades that don't justify the trade friction
        cand_score = float(cand.get("quality_score") or 0.0)
        if cand_score < (exit_score + _hurdle):
            return False, "hysteresis"
        return _capacity_ok(ticker, cand, held, virtual_pending)

    # Priority 1: Same market + same sector ENTER
    for ticker, cand in candidates.items():
        if cand.get("market") == exit_market and cand.get("sector") == exit_sector:
            ok, _ = _eligible(ticker, cand)
            if ok:
                return _package(ticker, cand, sizing)

    # Priority 2: Same market + ANY alternative sector ENTER (aggressive regional recycling)
    for ticker, cand in candidates.items():
        if cand.get("market") == exit_market:
            ok, _ = _eligible(ticker, cand)
            if ok:
                return _package(ticker, cand, sizing)

    # Priority 3: Hold cash inside the region — no cross-border allocation
    return None


def _capacity_ok(
    ticker: str,
    cand: Dict,
    held: List[Dict],
    virtual_pending: List[str],
) -> Tuple[bool, str]:
    market = cand.get("market", "US")
    sector = cand.get("sector", "Unknown")
    is_hv  = cand.get("regime", {}).get("label", "") == "High Vol"
    vp_set = set(virtual_pending)

    all_pos = held + [{"ticker": t, "market": get_market(t), "sector": get_sector(t),
                       "is_high_vol": False} for t in vp_set]

    max_open = RISK["MAX_OPEN_POSITIONS"]

    if len(all_pos) >= max_open:
        return False, "slots_full"

    def _resolve(limit_dict: dict, key: str, fallback) -> int:
        v = limit_dict.get(key, fallback)
        return int(max_open * v) if isinstance(v, float) and v <= 1.0 else int(v)

    mkt_count = sum(1 for p in all_pos if p.get("market") == market)
    if mkt_count >= _resolve(RISK["MAX_PER_MARKET"], market, 99):
        return False, "market_cap"

    sec_count = sum(1 for p in all_pos
                    if p.get("market") == market and p.get("sector") == sector)
    if sec_count >= _resolve(RISK["MAX_PER_SECTOR"], market, 99):
        return False, "sector_cap"

    if is_hv:
        hv_count = sum(1 for p in all_pos
                       if p.get("market") == market and p.get("is_high_vol", False))
        if hv_count >= RISK["MAX_HIGH_VOL_PER_MARKET"].get(market, 1):
            return False, "high_vol_cap"

    return True, ""


def _package(ticker: str, cand: Dict, sizing: Dict,
             cross_market: bool = False) -> Dict[str, Any]:
    sz = sizing.get(ticker, {})
    reason = f"Replacement for exit: {cand.get('reason', '')}"
    if cross_market:
        reason = f"[Cross-Market] {reason}"
    return {
        "ticker":       ticker,
        "market":       cand.get("market"),
        "sector":       cand.get("sector"),
        "price":        cand.get("price", 0),
        "atr":          cand.get("atr", 0),
        "atr_pct":      cand.get("atr_pct", 0),
        "regime":       cand.get("regime", {}),
        "is_high_vol":  sz.get("is_high_vol", False),
        "stop_price":   sz.get("stop_price", 0),
        "trail_mult":   sz.get("trail_mult", 5.0),
        "shares":       sz.get("shares", 0),
        "cost":         sz.get("cost", 0),
        "risk_amount":  sz.get("risk_amount", 0),
        "decision":     "ENTER",
        "reason":       reason,
        "sizing":       sz,
        "quality_score":  cand.get("quality_score"),
        "cross_market": cross_market,
    }
