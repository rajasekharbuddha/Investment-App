"""
indicators.py
=============
ATR(14) Wilder, SMA50/200, RSI(14), MACD(12,26,9), VOL_AVG_20, BODY.
All added to df copy by calculate_all().
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def wilder_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high  = df["High"]
    low   = df["Low"]
    close = df["Close"]
    prev  = close.shift(1)

    tr = pd.concat([
        high - low,
        (high - prev).abs(),
        (low  - prev).abs(),
    ], axis=1).max(axis=1)

    atr = pd.Series(np.nan, index=tr.index, dtype=float)
    valid = tr.dropna()
    if len(valid) < period:
        return atr

    start_loc = tr.index.get_loc(valid.index[0])
    seed_end   = start_loc + period
    if seed_end > len(tr):
        return atr

    atr.iloc[seed_end - 1] = float(tr.iloc[start_loc:seed_end].mean())
    for i in range(seed_end, len(tr)):
        atr.iloc[i] = (atr.iloc[i - 1] * (period - 1) + tr.iloc[i]) / period

    return atr


def calculate_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain  = delta.clip(lower=0)
    loss  = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs  = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi


def calculate_macd(
    close: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    ema_fast    = close.ewm(span=fast,   adjust=False).mean()
    ema_slow    = close.ewm(span=slow,   adjust=False).mean()
    macd_line   = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram   = macd_line - signal_line
    return macd_line, signal_line, histogram


def calculate_all(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()

    out["ATR"]       = wilder_atr(out)
    out["ATR_PCT"]   = (out["ATR"] / out["Close"] * 100).round(4)

    out["SMA_50"]    = out["Close"].rolling(50).mean()
    out["SMA_200"]   = out["Close"].rolling(200).mean()
    out["EMA_20"]    = out["Close"].ewm(span=20, adjust=False).mean()

    out["RSI"]       = calculate_rsi(out["Close"])

    macd, sig, hist  = calculate_macd(out["Close"])
    out["MACD"]      = macd
    out["MACD_SIG"]  = sig
    out["MACD_HIST"] = hist

    out["VOL_AVG_20"] = out["Volume"].rolling(20).mean()

    out["BODY"]      = (out["Close"] - out["Open"]).abs()

    return out


def get_regime(atr_pct: float) -> dict:
    """Legacy helper — new code uses config.get_regime."""
    if atr_pct is None or (isinstance(atr_pct, float) and np.isnan(atr_pct)):
        return {"regime": "Unknown", "stop_mult": None, "trail_mult": None,
                "risk_pct": 0, "can_trade": False}
    v = float(atr_pct)
    if v < 1.0:
        return {"regime": "Low Vol",  "stop_mult": 2.5, "trail_mult": 4.0,
                "risk_pct": 6, "can_trade": True}
    if v < 2.0:
        return {"regime": "Normal",   "stop_mult": 2.0, "trail_mult": 5.0,
                "risk_pct": 5, "can_trade": True}
    if v < 4.0:
        return {"regime": "High Vol", "stop_mult": 3.0, "trail_mult": 4.5,
                "risk_pct": 3.5, "can_trade": True}
    return {"regime": "Extreme",  "stop_mult": None, "trail_mult": None,
            "risk_pct": 0, "can_trade": False}
