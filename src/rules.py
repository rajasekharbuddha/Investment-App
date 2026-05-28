"""
rules.py
========
Five sequential gates + anti-chase timing, with NEAR classification.

Gate 1 — Trend       (structural)
Gate 2 — Momentum    (structural/execution hybrid)
Gate 3 — Volatility  (structural)
Gate 4 — Liquidity   (structural)
Gate 5 — MACD Execution (execution)
Bonus  — Timing Anti-Chase (execution blocker)

Decision outputs:
  ENTER — all gates pass
  WAIT  — structural gates pass; momentum/execution/timing fail
  NEAR  — 4 of 5 gates pass (close miss; monitor)
  SKIP  — a structural gate fails

evaluate_gates() is the single entry point for the engine.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

from config import (
    GATE_DEFAULTS, RISK, get_market, get_sector, get_regime, get_gate_params, ACCOUNT,
)


def _b(x) -> bool:
    return bool(x)


# ===========================================================================
# Individual gate functions
# ===========================================================================

def check_trend(row: pd.Series, sma50_20ago: float, sma_dist_min: float) -> Dict:
    sma50  = float(row["SMA_50"])
    sma200 = float(row["SMA_200"])
    close  = float(row["Close"])
    sma_dist = (sma50 - sma200) / sma200 if sma200 != 0 else 0.0

    checks = {
        "sma50_gt_sma200": _b(sma50 > sma200),
        "close_gt_sma200": _b(close > sma200),
        "close_gt_sma50":  _b(close > sma50),
        "sma50_rising":    _b(sma50 > sma50_20ago) if not np.isnan(sma50_20ago) else False,
        "sma_dist_ok":     _b(sma_dist >= sma_dist_min),
    }
    return {"pass": _b(all(checks.values())), "details": checks, "sma_dist": round(sma_dist * 100, 2)}


def check_momentum(row: pd.Series, rsi_lo: float, rsi_hi: float) -> Dict:
    macd  = float(row["MACD"])
    sig   = float(row["MACD_SIG"])
    rsi   = float(row["RSI"])
    checks = {
        "macd_gt_signal": _b(macd > sig),
        "rsi_in_band":    _b(rsi_lo <= rsi <= rsi_hi),
    }
    return {"pass": _b(all(checks.values())), "details": checks, "rsi": round(rsi, 2)}


def check_volatility(row: pd.Series, market: str = "US") -> Dict:
    atr_pct = float(row["ATR_PCT"])
    regime  = get_regime(atr_pct, market)
    return {
        "pass": _b(regime["can_trade"]),
        "details": {"atr_pct": round(atr_pct, 3), "regime": regime["label"]},
        "regime": regime,
    }


def check_liquidity(row: pd.Series, market: str, volume_mult: float) -> Dict:
    vol    = float(row["Volume"])
    avg20  = float(row["VOL_AVG_20"])
    baseline = (GATE_DEFAULTS["volume_mult_us"]
                if market == "US" else GATE_DEFAULTS["volume_mult_eu_in"])
    required = baseline * volume_mult
    ratio    = vol / avg20 if avg20 > 0 else 0.0
    return {
        "pass": _b(ratio >= required),
        "details": {
            "vol_ratio":  round(ratio, 3),
            "required":   round(required, 3),
            "market_baseline": baseline,
            "volume_mult": volume_mult,
        },
    }


def check_execution(row: pd.Series, prev_row: Optional[pd.Series],
                    macd_hist_eps: float) -> Dict:
    hist_today = float(row["MACD_HIST"])
    hist_prev  = float(prev_row["MACD_HIST"]) if prev_row is not None else float("nan")

    g5a = _b(hist_today >= macd_hist_eps)
    g5b = _b(not np.isnan(hist_prev) and hist_prev >= macd_hist_eps)
    g5c = _b(not np.isnan(hist_prev) and hist_today > hist_prev)

    checks = {"hist_gte_eps": g5a, "hist_consec2": g5b, "hist_rising": g5c}
    return {
        "pass": _b(all(checks.values())),
        "details": checks,
        "hist_today": round(hist_today, 6),
        "hist_prev":  round(hist_prev,  6) if not np.isnan(hist_prev) else None,
    }


def check_timing(row: pd.Series, prev_row: Optional[pd.Series],
                 prev2_row: Optional[pd.Series], atr: float) -> Dict:
    """
    Anti-chase: 3 consecutive large green candles → block entry.
    Returns pass=True when NOT blocking.
    """
    if prev_row is None or prev2_row is None or atr <= 0:
        return {"pass": True, "details": {"anti_chase_triggered": False}}

    ratio = GATE_DEFAULTS["green_body_ratio"]
    rows  = [row, prev_row, prev2_row]

    def is_large_green(r) -> bool:
        body  = float(r["BODY"])
        close = float(r["Close"])
        op    = float(r["Open"])
        return _b(close > op and body >= ratio * atr)

    triggered = all(is_large_green(r) for r in rows)
    return {
        "pass": _b(not triggered),
        "details": {"anti_chase_triggered": triggered,
                    "consecutive_large_green": 3 if triggered else 0},
    }


# ===========================================================================
# Main entry point
# ===========================================================================

def evaluate_gates(
    ticker: str,
    row: pd.Series,
    prev_row: Optional[pd.Series],
    prev2_row: Optional[pd.Series],
    tuner_params: Dict[str, float],
    market: Optional[str] = None,
    trend_quality_score: Optional[float] = None,
    index_close: Optional[float] = None,
    index_sma200: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Evaluate all five gates for a single ticker on a single day.

    trend_quality_score: composite score from stock_selector (0-100).
        >= 60 ("Excellent") + bullish index regime → elastic boundary widening.

    index_close / index_sma200: macro regime signal (e.g. % tickers > SMA200
        vs threshold 0.50). Bearish regime tightens gates defensively.

    Returns: {decision, reason, gates, price, atr, atr_pct, regime, sector,
              market, gates_passed, elastic_applied, index_is_bullish}
    """
    if market is None:
        market = get_market(ticker)
    sector = get_sector(ticker)

    # Isolate caller's dict — never mutate shared tuner_params reference
    local_params = dict(tuner_params)

    elastic_applied = False

    # Macro regime: bullish when index_close > index_sma200 (or unknown)
    index_is_bullish = True
    if index_close is not None and index_sma200 is not None:
        index_is_bullish = bool(index_close > index_sma200)

    def _nan(col) -> bool:
        v = row.get(col)
        return v is None or (isinstance(v, float) and np.isnan(v))

    required = ["Close", "SMA_50", "SMA_200", "ATR", "ATR_PCT",
                "RSI", "MACD", "MACD_SIG", "MACD_HIST", "Volume", "VOL_AVG_20"]
    if any(_nan(c) for c in required):
        res = _skip(ticker, market, sector, row,
                    "Insufficient indicator data", {})
        res["elastic_applied"] = elastic_applied
        res["index_is_bullish"] = index_is_bullish
        return res

    price   = float(row["Close"])
    atr     = float(row["ATR"])
    atr_pct = float(row["ATR_PCT"])
    gates: Dict[str, Any] = {}

    # Per-region gate defaults (macd_hist_eps only — velocity overrides below)
    gp       = get_gate_params(market)
    hist_eps = local_params.get("macd_hist_eps", gp["macd_hist_eps"])

    # Gate parameters: tuner overrides take precedence; MARKET_PARAMS (via gp) are the
    # authoritative baseline — changing config.py propagates here without code edits.
    rsi_lo       = gp["rsi_lo"]
    rsi_hi       = local_params.get("rsi_hi",       gp["rsi_hi"])
    volume_mult  = local_params.get("volume_mult",   gp["volume_mult"])
    sma_dist_min = local_params.get("sma_dist_min",  gp["sma_dist_min"])

    # ELASTIC ADAPTATION: bullish macro + high-conviction trend → widen further.
    # Bearish macro → tighten from the high-velocity base.
    if index_is_bullish and trend_quality_score is not None and trend_quality_score >= 60.0:
        rsi_hi      = rsi_hi      * 1.15   # stretch ceiling further (85 → ~98)
        volume_mult = volume_mult * 0.80   # lower friction further
        elastic_applied = True
    elif not index_is_bullish:
        rsi_hi       = rsi_hi       * 0.90   # tighten ceiling (85 → ~76)
        sma_dist_min = max(sma_dist_min * 1.50, 0.005)  # require positive SMA gap in bear regime
        volume_mult  = volume_mult  * 1.25   # require stronger volume confirmation

    sma50_20ago = float(row.get("SMA_50_20AGO", float("nan")))

    # Gate 1 — Trend
    g1 = check_trend(row, sma50_20ago, sma_dist_min)
    gates["gate1_trend"] = g1

    # Gate 2 — Momentum (uses elastically-adjusted rsi_hi when applicable)
    g2 = check_momentum(row, rsi_lo, rsi_hi)
    gates["gate2_momentum"] = g2

    # Gate 3 — Volatility (market-aware regime)
    g3 = check_volatility(row, market)
    gates["gate3_volatility"] = g3
    regime = g3["regime"]

    # Gate 4 — Liquidity (uses elastically-adjusted volume_mult when applicable)
    g4 = check_liquidity(row, market, volume_mult)
    gates["gate4_liquidity"] = g4

    # Timing anti-chase
    timing = check_timing(row, prev_row, prev2_row, atr)
    gates["timing_anti_chase"] = timing

    # Gate 5 — Execution
    g5 = check_execution(row, prev_row, hist_eps)
    gates["gate5_execution"] = g5

    gates_passed = sum(1 for g in [g1, g2, g3, g4, g5] if g["pass"])

    # ── Sequential gate evaluation ───────────────────────────────────────────
    # Structural gates: G1, G3, G4 must all pass to avoid SKIP
    structural_ok = g1["pass"] and g3["pass"] and g4["pass"]

    if not structural_ok:
        # NEAR: 4+ gates pass even though structural failed
        if gates_passed >= 4:
            res = _near(ticker, market, sector, row,
                        "Near-miss: 4+ gates pass but structural not complete",
                        gates, atr_pct=atr_pct, regime=regime,
                        gates_passed=gates_passed)
            res["elastic_applied"] = elastic_applied
            res["index_is_bullish"] = index_is_bullish
            return res
        failed = []
        if not g1["pass"]:
            failed += [k for k, v in g1["details"].items() if not v]
        if not g3["pass"]:
            failed.append(f"extreme_vol_{atr_pct:.2f}%")
        if not g4["pass"]:
            failed.append(f"liquidity_ratio_{g4['details']['vol_ratio']:.2f}")
        res = _skip(ticker, market, sector, row,
                    f"Structural fail: {', '.join(failed)}", gates,
                    atr_pct=atr_pct, regime=regime, gates_passed=gates_passed)
        res["elastic_applied"] = elastic_applied
        res["index_is_bullish"] = index_is_bullish
        return res

    # Structural ok — check momentum (G2)
    if not g2["pass"]:
        failed = [k for k, v in g2["details"].items() if not v]
        if gates_passed >= 4:
            res = _near(ticker, market, sector, row,
                        f"NEAR — G2 Momentum: {', '.join(failed)}",
                        gates, atr_pct=atr_pct, regime=regime,
                        gates_passed=gates_passed)
            res["elastic_applied"] = elastic_applied
            res["index_is_bullish"] = index_is_bullish
            return res
        res = _wait(ticker, market, sector, row,
                    f"G2 Momentum fail: {', '.join(failed)}", gates,
                    atr_pct=atr_pct, regime=regime, gates_passed=gates_passed)
        res["elastic_applied"] = elastic_applied
        res["index_is_bullish"] = index_is_bullish
        return res

    # Anti-chase blocker
    if not timing["pass"]:
        res = _wait(ticker, market, sector, row,
                    "Anti-chase: 3 consecutive large green candles", gates,
                    atr_pct=atr_pct, regime=regime, gates_passed=gates_passed)
        res["elastic_applied"] = elastic_applied
        res["index_is_bullish"] = index_is_bullish
        return res

    # Gate 5 — Execution
    if not g5["pass"]:
        failed = [k for k, v in g5["details"].items() if not v]
        res = _wait(ticker, market, sector, row,
                    f"G5 Execution fail: {', '.join(failed)}", gates,
                    atr_pct=atr_pct, regime=regime, gates_passed=gates_passed)
        res["elastic_applied"] = elastic_applied
        res["index_is_bullish"] = index_is_bullish
        return res

    # All gates pass
    return {
        "ticker": ticker, "decision": "ENTER", "reason": "All gates pass",
        "gates": gates, "price": price, "atr": atr, "atr_pct": atr_pct,
        "regime": regime, "sector": sector, "market": market,
        "gates_passed": gates_passed,
        "elastic_applied": elastic_applied,
        "index_is_bullish": index_is_bullish,
    }


# ===========================================================================
# Backward-compat helpers
# ===========================================================================

def evaluate_entry(df: pd.DataFrame, ticker: str) -> Dict[str, Any]:
    from config import TUNER_PARAMS
    if len(df) < 200:
        market = get_market(ticker)
        sector = get_sector(ticker)
        return {
            "ticker": ticker, "decision": "SKIP", "market": market, "sector": sector,
            "reason": f"Insufficient history ({len(df)} bars, need 200)",
            "gates": {}, "price": float(df["Close"].iloc[-1]) if len(df) else 0,
            "atr": 0.0, "atr_pct": 0.0, "regime": None, "gates_passed": 0,
        }

    df2 = df.copy()
    df2["SMA_50_20AGO"] = df2["SMA_50"].shift(GATE_DEFAULTS["sma_rising_lookback"])

    row   = df2.iloc[-1]
    prev  = df2.iloc[-2] if len(df2) >= 2 else None
    prev2 = df2.iloc[-3] if len(df2) >= 3 else None

    params = dict(TUNER_PARAMS["BASE"])
    return evaluate_gates(ticker, row, prev, prev2, params)


def calculate_position(account_eur: float, entry: float,
                       atr: float, regime: dict) -> dict:
    if not regime.get("can_trade", False):
        return {"can_trade": False, "reason": f"Regime '{regime.get('regime')}' blocks trading"}

    stop_mult   = regime["stop_mult"]
    trail_mult  = regime["trail_mult"]
    risk_pct_pct = regime["risk_pct"]

    if isinstance(risk_pct_pct, float) and risk_pct_pct < 1:
        risk_pct_pct = risk_pct_pct * 100

    stop_price   = entry - (atr * stop_mult)
    risk_amount  = account_eur * (risk_pct_pct / 100)
    risk_per_sh  = entry - stop_price
    if risk_per_sh <= 0:
        return {"can_trade": False, "reason": "Stop at or above entry"}

    shares       = risk_amount / risk_per_sh
    position_val = shares * entry
    breakeven    = entry + risk_per_sh
    target_2r    = entry + 2 * risk_per_sh

    return {
        "can_trade":    True,
        "regime":       regime.get("regime", regime.get("label", "?")),
        "risk_pct":     risk_pct_pct,
        "risk_amount":  round(risk_amount, 2),
        "stop_mult":    stop_mult,
        "trail_mult":   trail_mult,
        "entry":        round(entry, 2),
        "stop":         round(stop_price, 2),
        "shares":       round(shares, 1),
        "position_val": round(position_val, 2),
        "breakeven":    round(breakeven, 2),
        "target_2r":    round(target_2r, 2),
    }


# ===========================================================================
# Internal helpers
# ===========================================================================

def _skip(ticker, market, sector, row, reason, gates, atr_pct=None, regime=None, gates_passed=0):
    return _result("SKIP", ticker, market, sector, row, reason, gates, atr_pct, regime, gates_passed)

def _wait(ticker, market, sector, row, reason, gates, atr_pct=None, regime=None, gates_passed=0):
    return _result("WAIT", ticker, market, sector, row, reason, gates, atr_pct, regime, gates_passed)

def _near(ticker, market, sector, row, reason, gates, atr_pct=None, regime=None, gates_passed=0):
    return _result("NEAR", ticker, market, sector, row, reason, gates, atr_pct, regime, gates_passed)

def _result(decision, ticker, market, sector, row, reason, gates, atr_pct, regime, gates_passed):
    price   = float(row["Close"]) if "Close" in row.index else 0.0
    atr_val = float(row["ATR"])   if "ATR" in row.index and not _isnan(row["ATR"]) else None
    if atr_pct is None and "ATR_PCT" in row.index:
        try:
            atr_pct = float(row["ATR_PCT"])
        except Exception:
            pass
    return {
        "ticker": ticker, "decision": decision, "reason": reason,
        "gates": gates, "price": price, "atr": atr_val, "atr_pct": atr_pct,
        "regime": regime, "sector": sector, "market": market,
        "gates_passed": gates_passed,
    }

def _isnan(v) -> bool:
    try:
        return bool(v != v)
    except Exception:
        return False
