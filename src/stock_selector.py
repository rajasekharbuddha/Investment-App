"""
stock_selector.py
=================
Velocity-first stock scoring — 2 dimensions:

  1. momentum  — pure unweighted average return across [1M, 3M, 6M, 12M]
  2. trend     — % distance above SMA50 (active acceleration confirmation)

Composite = 0.70 × momentum_score + 0.30 × trend_factor (mapped 0–100).
Grades:
  >= 60  Excellent    >= 45  Good
  >= 35  Borderline   <  35  Drag (filter out)
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def score_stock(df: pd.DataFrame, ticker: str = "") -> dict:
    """
    Score a single stock on pure velocity + SMA50 acceleration.
    Minimum 63 bars required; SMA_50 column used if present.
    """
    from ranking import momentum_score

    result: dict = {"ticker": ticker, "bars": len(df)}

    if len(df) < 63:
        return {**result, "composite": 0.0, "error": f"only {len(df)} bars (need 63+)"}

    close = df["Close"].dropna()

    # Dimension 1: pure unweighted momentum velocity
    raw_mom = momentum_score(df, vol_penalty=False)
    mom_val = float(raw_mom) if (raw_mom is not None and not np.isnan(raw_mom)) else 0.0
    # Map to 0-100: cap at ±100% annual return range
    mom_score = float(np.clip((mom_val + 0.50) / 1.00 * 100, 0, 100))

    # Dimension 2: distance above SMA50 — confirms active breakout, not lagging trend
    trend_factor = 0.0
    if "SMA_50" in df.columns:
        sma50 = df["SMA_50"].iloc[-1]
        if sma50 and sma50 > 0:
            trend_factor = float((close.iloc[-1] - sma50) / sma50)
    # Map to 0-100: cap at ±30% distance from SMA50
    trend_score = float(np.clip((trend_factor + 0.30) / 0.60 * 100, 0, 100))

    composite = (mom_val * 0.70) + (trend_factor * 0.30)
    # Rescale composite to 0-100 grade range
    composite_100 = float(np.clip((composite + 0.50) / 1.00 * 100, 0, 100))

    result.update({
        "momentum_raw":   round(mom_val,      4),
        "momentum_score": round(mom_score,    1),
        "trend_factor":   round(trend_factor, 4),
        "trend_score":    round(trend_score,  1),
        "composite":      round(composite_100, 1),
        "grade":          grade(composite_100),
    })
    return result


def score_all(data_dict: dict) -> pd.DataFrame:
    """Score every ticker in {ticker: DataFrame}. Returns DataFrame sorted by composite."""
    rows = [score_stock(df, ticker) for ticker, df in data_dict.items()]
    out  = pd.DataFrame(rows)
    if "composite" in out.columns:
        out = out.sort_values("composite", ascending=False).reset_index(drop=True)
    return out


def grade(score: float) -> str:
    if score >= 60:
        return "Excellent"
    if score >= 45:
        return "Good"
    if score >= 35:
        return "Borderline"
    return "Drag"
