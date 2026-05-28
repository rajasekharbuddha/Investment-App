"""
select_stocks.py
================
Unified stock selection combining momentum ranking with trend-quality scoring.

score_ticker()       — fast multi-factor score (momentum + trend + liquidity)
quality_score_all()  — full 6-dimension Hurst/ADX/SMA200 scoring (slower)
select_top_n()       — momentum-based top-N
dynamic_watchlist()  — builds {market: [tickers]} by quality + momentum
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from config import WATCHLIST, RANKING, GATE_DEFAULTS, QUALITY_FILTER, get_sector, get_market
from ranking import momentum_score


def score_ticker(df: pd.DataFrame) -> Dict[str, float]:
    """Fast-velocity selection: pure momentum + SMA50 acceleration confirmation."""
    if len(df) < 63:
        return {"momentum": 0.0, "trend": 0.0, "liquidity": 0.0, "total": 0.0}

    close = df["Close"].dropna()

    # Pure unweighted relative velocity (vol_penalty disabled in momentum_score)
    raw_mom = momentum_score(df, vol_penalty=False)
    mom_val = float(raw_mom) if (raw_mom is not None and not np.isnan(raw_mom)) else 0.0

    # Distance above 50-day SMA — confirms active acceleration, not stale trend
    trend_factor = 0.0
    if "SMA_50" in df.columns:
        sma50 = df["SMA_50"].iloc[-1]
        if sma50 and sma50 > 0:
            trend_factor = float((close.iloc[-1] - sma50) / sma50)

    total_score = (mom_val * 0.70) + (trend_factor * 0.30)
    return {"momentum": round(mom_val, 4), "trend": round(trend_factor, 4),
            "liquidity": 1.0, "total": round(total_score, 4)}


def quality_score_all(
    data_map: Dict[str, pd.DataFrame],
    verbose: bool = False,
) -> Dict[str, float]:
    """
    Run full stock_selector scoring on all tickers in data_map.
    Returns {ticker: composite_score}. Skips tickers with < 252 bars.
    """
    from stock_selector import score_stock
    scores: Dict[str, float] = {}
    total = len(data_map)
    for i, (ticker, df) in enumerate(data_map.items(), 1):
        if verbose:
            print(f"  Scoring [{i}/{total}] {ticker:<20}", end=" ", flush=True)
        try:
            result = score_stock(df, ticker)
            scores[ticker] = result.get("composite", 0.0)
            if verbose:
                print(f"{scores[ticker]:.1f}  {result.get('grade','?')}")
        except Exception as e:
            scores[ticker] = 0.0
            if verbose:
                print(f"ERROR: {e}")
    return scores


def filter_by_quality(
    tickers: List[str],
    quality_scores: Dict[str, float],
    min_score: Optional[float] = None,
) -> Tuple[List[str], List[str]]:
    """
    Split tickers into (pass, filtered_out) by quality score.
    Returns (passing_tickers, drag_tickers).
    """
    min_q = min_score if min_score is not None else QUALITY_FILTER.get("MIN_SCORE", 0)
    passed, filtered = [], []
    for t in tickers:
        score = quality_scores.get(t, 100.0)  # unknown → allow through
        if score >= min_q:
            passed.append(t)
        else:
            filtered.append(t)
    return passed, filtered


def select_top_n(
    data_map: Dict[str, pd.DataFrame],
    market: str,
    n: Optional[int] = None,
    watchlist: Optional[Dict[str, List[str]]] = None,
) -> List[str]:
    """Rank tickers for `market` by composite score; return top-N."""
    n = n or RANKING["DEFAULT_TOP_N"].get(market, 999)
    source = watchlist if watchlist is not None else WATCHLIST
    tickers = source.get(market, [])

    scored: List[Tuple[str, float]] = []
    for ticker in tickers:
        if ticker not in data_map:
            continue
        s = score_ticker(data_map[ticker])
        scored.append((ticker, s["total"]))

    scored.sort(key=lambda x: -x[1])
    return [t for t, _ in scored[:n]]


def dynamic_watchlist(
    data_map: Dict[str, pd.DataFrame],
    top_n_map: Optional[Dict[str, int]] = None,
    quality_scores: Optional[Dict[str, float]] = None,
    watchlist: Optional[Dict[str, List[str]]] = None,
) -> Dict[str, List[str]]:
    """
    Build {market: [top tickers]}, optionally filtered by quality score.

    watchlist: source universe {market: [tickers]}; defaults to config.WATCHLIST.
               Pass a dynamically-built universe here for large-universe mode.
    """
    source = watchlist if watchlist is not None else WATCHLIST
    result: Dict[str, List[str]] = {}
    for market in source:
        n = (top_n_map or {}).get(market) or RANKING["DEFAULT_TOP_N"].get(market)
        top = select_top_n(data_map, market, n, watchlist=source)
        if quality_scores and QUALITY_FILTER.get("ENABLED"):
            top, _ = filter_by_quality(top, quality_scores)
        result[market] = top
    return result
