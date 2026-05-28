"""
replacement_list.py
===================
Builds a bench (replacement candidate) list for a given market.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

from config import WATCHLIST, TUNER_PARAMS, GATE_DEFAULTS, get_market, get_sector
from indicators import calculate_all
from rules import evaluate_gates


def build_replacement_list(
    market: str,
    data_map: Dict[str, pd.DataFrame],
    tuner_mode: str = "BASE",
    top_n: Optional[int] = None,
    quality_scores: Optional[Dict[str, float]] = None,
) -> List[Dict[str, Any]]:
    """
    Evaluate all tickers in `market` and return bench list sorted by priority:
    ENTER first (quality desc, then atr_pct asc), then NEAR, WAIT, SKIP.
    """
    params  = TUNER_PARAMS.get(tuner_mode, TUNER_PARAMS["BASE"])
    tickers = WATCHLIST.get(market, [])
    bench: List[Dict[str, Any]] = []

    for ticker in tickers:
        if ticker not in data_map:
            continue
        df = data_map[ticker]
        if len(df) < 3:
            continue

        df2 = df.copy()
        df2["SMA_50_20AGO"] = df2["SMA_50"].shift(GATE_DEFAULTS["sma_rising_lookback"])

        row   = df2.iloc[-1]
        prev  = df2.iloc[-2] if len(df2) >= 2 else None
        prev2 = df2.iloc[-3] if len(df2) >= 3 else None

        tqs = quality_scores.get(ticker) if quality_scores else None
        idx_close = float(df2["Index_Close"].iloc[-1]) if "Index_Close" in df2.columns else None
        idx_sma   = float(df2["Index_SMA200"].iloc[-1]) if "Index_SMA200" in df2.columns else None
        result = evaluate_gates(
            ticker, row, prev, prev2, params, market,
            trend_quality_score=tqs,
            index_close=idx_close,
            index_sma200=idx_sma,
        )
        if tqs is not None:
            result["quality_score"] = tqs
        bench.append(result)

    # Sort: ENTER by quality desc + atr_pct asc, then NEAR, WAIT, SKIP
    order = {"ENTER": 0, "NEAR": 1, "WAIT": 2, "SKIP": 3}
    bench.sort(key=lambda x: (
        order.get(x["decision"], 9),
        -(x.get("quality_score") or 0),
        x.get("atr_pct") or 999,
    ))

    if top_n is not None:
        bench = bench[:top_n]
    return bench


def format_bench_table(bench: List[Dict]) -> str:
    lines = []
    lines.append(f"{'Ticker':<16} {'Decision':<8} {'Q-Score':>8} {'ATR%':>6} {'Regime':<10} {'Sector':<18} Reason")
    lines.append("-" * 100)
    for r in bench:
        atr_s  = f"{r['atr_pct']:.2f}" if r.get("atr_pct") else "—"
        reg    = r.get("regime", {})
        reg_s  = reg.get("label", "—") if isinstance(reg, dict) else str(reg)
        qs     = f"{r['quality_score']:.0f}" if r.get("quality_score") is not None else "—"
        lines.append(
            f"{r['ticker']:<16} {r['decision']:<8} {qs:>8} {atr_s:>6} "
            f"{reg_s:<10} {r.get('sector','?'):<18} {r.get('reason','')}"
        )
    return "\n".join(lines)
