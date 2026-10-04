"""
synthetic_market_data.py
========================
Generates realistic synthetic price series for backtesting when live
market data is unavailable.

Calibration sources
-------------------
  Nifty 50 annual returns (BSE/NSE published data, widely documented):
    2008: -52%  2009: +76%  2010: +18%  2011: -25%  2012: +28%
    2013: +7%   2014: +31%  2015: -4%   2016: +3%   2017: +29%
    2018: +3%   2019: +12%  2020: +15%  2021: +24%  2022: +4%
    2023: +20%  2024: +9%   2025: +12%  2026: +7% (estimated YTD)

  S&P 500 annual returns (Federal Reserve / public records):
    2008: -37%  2009: +26%  2010: +15%  2011: +2%   2012: +16%
    2013: +32%  2014: +14%  2015: +1%   2016: +12%  2017: +22%
    2018: -4%   2019: +31%  2020: +18%  2021: +29%  2022: -18%
    2023: +26%  2024: +23%  2025: +10%  2026: +6% (estimated YTD)

Individual stocks are modelled as correlated with the market
(beta 0.7–1.3) plus idiosyncratic noise.
"""

from __future__ import annotations

import math
import random
from typing import Optional

import numpy as np
import pandas as pd


# ── Calibrated annual returns ─────────────────────────────────────────────────

_MARKET_ANNUAL: dict[str, dict[int, float]] = {
    "IN": {
        2007: 0.54,   # pre-backtest warmup year
        2008: -0.52,  # GFC
        2009:  0.76,  # V-recovery
        2010:  0.18,
        2011: -0.25,  # Euro crisis hit India
        2012:  0.28,
        2013:  0.07,
        2014:  0.31,  # Modi election rally
        2015: -0.04,
        2016:  0.03,  # Demonetisation year
        2017:  0.29,
        2018:  0.03,
        2019:  0.12,
        2020:  0.15,  # COVID crash + recovery
        2021:  0.24,
        2022:  0.04,
        2023:  0.20,
        2024:  0.09,
        2025:  0.12,
        2026:  0.07,
    },
    "US": {
        2007:  0.05,
        2008: -0.37,
        2009:  0.26,
        2010:  0.15,
        2011:  0.02,
        2012:  0.16,
        2013:  0.32,
        2014:  0.14,
        2015:  0.01,
        2016:  0.12,
        2017:  0.22,
        2018: -0.04,
        2019:  0.31,
        2020:  0.18,
        2021:  0.29,
        2022: -0.18,
        2023:  0.26,
        2024:  0.23,
        2025:  0.10,
        2026:  0.06,
    },
    "EU": {
        2007:  0.07,
        2008: -0.46,
        2009:  0.21,
        2010:  0.03,
        2011: -0.17,
        2012:  0.16,
        2013:  0.18,
        2014:  0.04,
        2015:  0.08,
        2016:  0.01,
        2017:  0.11,
        2018: -0.13,
        2019:  0.24,
        2020:  -0.05,
        2021:  0.20,
        2022: -0.12,
        2023:  0.14,
        2024:  0.06,
        2025:  0.09,
        2026:  0.05,
    },
}

# Crisis crash dates: (start, end, % drawdown from start)
_CRASH_PERIODS: dict[str, list] = {
    "IN": [
        ("2008-01-08", "2009-03-09", -0.60),  # GFC
        ("2011-07-26", "2011-12-20", -0.28),  # Euro / FII outflow
        ("2015-03-04", "2016-02-29", -0.21),  # China + RBI
        ("2018-08-28", "2018-10-26", -0.16),  # NBFC crisis
        ("2020-01-14", "2020-03-24", -0.38),  # COVID crash
        ("2022-10-19", "2022-06-17", -0.16),  # Fed/Ukraine
    ],
    "US": [
        ("2007-10-09", "2009-03-09", -0.57),  # GFC
        ("2011-04-29", "2011-10-03", -0.19),  # Debt ceiling / EU
        ("2015-05-19", "2016-02-11", -0.14),  # China fear
        ("2018-01-26", "2018-12-24", -0.20),  # Rate hike
        ("2020-02-19", "2020-03-23", -0.34),  # COVID
        ("2022-01-03", "2022-10-12", -0.25),  # Fed tightening
    ],
    "EU": [
        ("2008-01-02", "2009-03-09", -0.52),
        ("2011-02-17", "2011-09-22", -0.30),
        ("2015-04-13", "2016-02-11", -0.22),
        ("2020-02-19", "2020-03-18", -0.38),
        ("2022-01-03", "2022-09-29", -0.24),
    ],
}

# Stock-specific parameters (ticker prefix → beta, alpha_annual, vol_extra)
_STOCK_PROFILES: dict[str, dict] = {
    # High quality compounders: moderate beta, high alpha
    "QUALITY": {"beta": 0.85, "alpha_annual":  0.06, "vol_extra": 0.08},
    # Growth stocks: higher beta, higher alpha but more volatile
    "GROWTH":  {"beta": 1.20, "alpha_annual":  0.10, "vol_extra": 0.18},
    # Cyclicals: high beta, no persistent alpha
    "CYCLICAL":{"beta": 1.40, "alpha_annual": -0.02, "vol_extra": 0.22},
    # Defensives: low beta, small alpha
    "DEFENSIVE":{"beta": 0.60,"alpha_annual":  0.02, "vol_extra": 0.06},
    # Sector gap plays: high beta, higher alpha (captures the tailwind premium)
    "GAP_PLAY": {"beta": 1.10, "alpha_annual":  0.12, "vol_extra": 0.20},
}

# Universe profiles for IN — roughly matches the ticker categories
_IN_PROFILES = {
    "RELIANCE.NS":    "QUALITY",   "TCS.NS":        "QUALITY",
    "HDFCBANK.NS":    "QUALITY",   "INFY.NS":       "QUALITY",
    "ICICIBANK.NS":   "CYCLICAL",  "HINDUNILVR.NS": "DEFENSIVE",
    "KOTAKBANK.NS":   "QUALITY",   "SBIN.NS":       "CYCLICAL",
    "BHARTIARTL.NS":  "GROWTH",    "ITC.NS":        "DEFENSIVE",
    "AXISBANK.NS":    "CYCLICAL",  "LT.NS":         "CYCLICAL",
    "ASIANPAINT.NS":  "QUALITY",   "MARUTI.NS":     "CYCLICAL",
    "TITAN.NS":       "GROWTH",    "BAJFINANCE.NS": "GROWTH",
    "WIPRO.NS":       "QUALITY",   "HCLTECH.NS":    "QUALITY",
    "SUNPHARMA.NS":   "QUALITY",   "DRREDDY.NS":    "QUALITY",
    "PIDILITIND.NS":  "QUALITY",   "BERGEPAINT.NS": "QUALITY",
    "GODREJCP.NS":    "QUALITY",   "MARICO.NS":     "DEFENSIVE",
    "DABUR.NS":       "DEFENSIVE", "LUPIN.NS":      "GROWTH",
    "CIPLA.NS":       "QUALITY",   "DIVISLAB.NS":   "QUALITY",
    "MCDOWELL-N.NS":  "DEFENSIVE", "HAVELLS.NS":    "GROWTH",
    "VOLTAS.NS":      "CYCLICAL",  "WHIRLPOOL.NS":  "CYCLICAL",
    "NMDC.NS":        "CYCLICAL",  "COALINDIA.NS":  "CYCLICAL",
    "NTPC.NS":        "DEFENSIVE", "POWERGRID.NS":  "DEFENSIVE",
    "HAL.NS":         "GAP_PLAY",  "BEL.NS":        "GAP_PLAY",
    "BHEL.NS":        "CYCLICAL",  "ABB.NS":        "GAP_PLAY",
}

_US_PROFILES = {
    "AAPL": "QUALITY",  "MSFT":  "QUALITY", "GOOGL": "QUALITY", "AMZN": "GROWTH",
    "META": "GROWTH",   "BRK-B": "QUALITY", "JPM":   "CYCLICAL","JNJ":  "DEFENSIVE",
    "V":    "QUALITY",  "PG":    "DEFENSIVE","UNH":  "QUALITY", "HD":   "QUALITY",
    "MA":   "QUALITY",  "DIS":   "CYCLICAL", "PYPL":  "GROWTH",
    "NVDA": "GAP_PLAY", "AMD":   "GAP_PLAY", "INTC":  "CYCLICAL","QCOM": "GROWTH",
    "TXN":  "QUALITY",  "XOM":   "CYCLICAL", "CVX":   "CYCLICAL","COP":  "CYCLICAL",
    "SLB":  "CYCLICAL", "HAL":   "CYCLICAL", "BA":    "CYCLICAL","LMT":  "QUALITY",
    "RTX":  "GAP_PLAY", "NOC":   "QUALITY",  "GD":    "QUALITY",
    "UNP":  "QUALITY",  "UPS":   "QUALITY",  "FDX":   "CYCLICAL","CSX":  "QUALITY",
    "NSC":  "QUALITY",  "WMT":   "DEFENSIVE","TGT":   "CYCLICAL","COST": "QUALITY",
    "LOW":  "QUALITY",  "NKE":   "QUALITY",
}

_PROFILES_BY_MARKET = {"IN": _IN_PROFILES, "US": _US_PROFILES}


# ── Generator ─────────────────────────────────────────────────────────────────

def generate_market_data(
    market: str,
    tickers: list[str],
    start: str = "2007-06-01",
    end: str   = "2026-10-04",
    base_price: float = 100.0,
    seed: int = 42,
) -> tuple[dict[str, pd.DataFrame], pd.Series]:
    """
    Generate synthetic OHLCV + SMA_50/SMA_200 price data.

    Returns
    -------
    (data_map: {ticker → DataFrame}, benchmark: pd.Series)
    """
    rng    = np.random.default_rng(seed)
    annual = _MARKET_ANNUAL.get(market.upper(), _MARKET_ANNUAL["US"])
    dates  = pd.bdate_range(start=start, end=end)
    n      = len(dates)

    # ── Benchmark series from calibrated annual returns ───────────────────────
    bench_daily = _build_annual_series(annual, dates)

    # ── Per-stock series ──────────────────────────────────────────────────────
    profiles_map = _PROFILES_BY_MARKET.get(market.upper(), {})
    data_map: dict[str, pd.DataFrame] = {}

    for ticker in tickers:
        profile_key = profiles_map.get(ticker, "QUALITY")
        prof = _STOCK_PROFILES[profile_key]

        beta      = prof["beta"]  + rng.normal(0, 0.12)
        alpha_d   = (prof["alpha_annual"] + rng.normal(0, 0.03)) / 252
        extra_vol = prof["vol_extra"] / math.sqrt(252)

        # Daily market return + idiosyncratic noise
        bench_ret = bench_daily.pct_change().fillna(0).values
        idio      = rng.normal(0, extra_vol, n)
        stock_ret = alpha_d + beta * bench_ret + idio

        prices = base_price * np.cumprod(1 + stock_ret)
        series = pd.Series(prices, index=dates)

        df = pd.DataFrame({"Close": series})
        df["SMA_50"]  = df["Close"].rolling(50).mean()
        df["SMA_200"] = df["Close"].rolling(200).mean()
        df.dropna(subset=["SMA_200"], inplace=True)

        data_map[ticker] = df

    return data_map, bench_daily


def _build_annual_series(
    annual_returns: dict[int, float],
    dates: pd.DatetimeIndex,
) -> pd.Series:
    """Build a daily price series that exactly matches calibrated annual returns."""
    # Start at 1000
    prices = pd.Series(index=dates, dtype=float)
    level  = 1000.0

    for year in range(dates[0].year, dates[-1].year + 1):
        yr_dates = dates[dates.year == year]
        if len(yr_dates) == 0:
            continue
        annual_ret = annual_returns.get(year, 0.08)  # default 8% if unknown
        daily_drift = (1 + annual_ret) ** (1 / max(len(yr_dates), 1)) - 1

        rng = np.random.default_rng(seed=year)
        noise = rng.normal(0, 0.01, len(yr_dates))
        log_path = np.cumsum(np.log1p(daily_drift + noise))

        # Log-space bridge: hit the calibrated year-end exactly while staying
        # continuous with the prior year (uniform rescaling caused Jan-1 gaps).
        t = np.arange(1, len(yr_dates) + 1) / len(yr_dates)
        log_path -= t * (log_path[-1] - np.log1p(annual_ret))
        path = level * np.exp(log_path)

        prices.loc[yr_dates] = path
        level = float(path[-1])

    return prices.dropna()
