"""
ranking.py
==========
Cross-sectional momentum ranking for dynamic universe mode.
Score = pure unweighted average return across [1M, 3M, 6M, 12M].
No volatility divisor — explosive breakout names rank on raw velocity.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from config import RANKING, WATCHLIST, get_sector


def momentum_score(
    df: pd.DataFrame,
    periods: Optional[List[int]] = None,
    vol_penalty: bool = False,
) -> float:
    """
    Pure unweighted relative velocity across momentum horizons.
    vol_penalty is accepted for API compatibility but ignored — volatility
    division suppresses explosive breakout names and is disabled by design.
    """
    periods = periods or RANKING.get("MOMENTUM_PERIODS", [21, 63, 126, 252])
    close   = df["Close"].dropna()
    if len(close) < max(periods):
        return float("nan")

    scores = []
    for p in periods:
        if len(close) <= p:
            continue
        ret = float(close.iloc[-1] / close.iloc[-p] - 1)
        scores.append(ret)

    if not scores:
        return float("nan")

    return float(np.mean(scores))


def rank_universe(
    data_map: Dict[str, pd.DataFrame],
    market: str,
    top_n: Optional[int] = None,
    periods: Optional[List[int]] = None,
    vol_penalty: bool = False,
) -> List[Dict[str, Any]]:
    tickers = WATCHLIST.get(market, [])
    rows = []
    for ticker in tickers:
        if ticker not in data_map:
            continue
        score = momentum_score(data_map[ticker], periods, vol_penalty)
        rows.append({"ticker": ticker, "score": score, "sector": get_sector(ticker)})

    rows.sort(key=lambda x: (x["score"] is None or np.isnan(x["score"]), -(x["score"] or 0)))
    top_n = top_n or RANKING["DEFAULT_TOP_N"].get(market, 999)
    return rows[:top_n]


def sector_momentum(
    data_map: Dict[str, pd.DataFrame],
    market: str,
    period: int = 63,
) -> Dict[str, float]:
    from collections import defaultdict
    sector_scores: Dict[str, list] = defaultdict(list)

    for ticker in WATCHLIST.get(market, []):
        if ticker not in data_map:
            continue
        df    = data_map[ticker]
        close = df["Close"].dropna()
        if len(close) <= period:
            continue
        ret = float(close.iloc[-1] / close.iloc[-period] - 1)
        sector_scores[get_sector(ticker)].append(ret)

    return {
        sec: float(np.mean(rets))
        for sec, rets in sector_scores.items()
        if rets
    }


def top_sectors(
    data_map: Dict[str, pd.DataFrame],
    market: str,
    k: int = 3,
    period: int = 63,
) -> List[str]:
    scores = sector_momentum(data_map, market, period)
    ranked = sorted(scores, key=scores.get, reverse=True)
    return ranked[:k]
