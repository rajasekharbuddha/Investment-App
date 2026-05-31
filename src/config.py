"""
config.py
=========
Unified configuration for Mastermind Pro — merges Dir1 robustness
with Dir2 dynamic-universe settings.

All strategy parameters here; no magic numbers elsewhere.
"""

from __future__ import annotations

import os
from pathlib import Path as _Path

# =============================================================================
# WATCHLIST  —  {market: [tickers]}  (fallback when dynamic universe disabled)
# =============================================================================
WATCHLIST: dict[str, list[str]] = {
    "US": [
        "MSFT", "AAPL", "NVDA", "GOOGL", "META", "AVGO",
        "JPM", "V", "MA", "BRK-B",
        "AMZN", "COST", "HD", "PG",
        "LLY", "UNH", "JNJ", "ABBV",
        "XOM", "CVX",
    ],
    "EU": [
        "SAP.DE", "ASML.AS",
        "SIE.DE", "AIR.PA",
        "MC.PA", "OR.PA", "NESN.SW", "UNA.AS", "BMW.DE",
        "NOVO-B.CO", "AZN.L", "RO.SW", "BAYN.DE",
        "RHM.DE",
        "TTE.PA", "SHEL.L",
        "BNP.PA", "ALV.DE", "HSBA.L",
        "DTE.DE",
    ],
    "IN": [
        "TCS.NS", "INFY.NS", "WIPRO.NS", "HCLTECH.NS",
        "HDFCBANK.NS", "ICICIBANK.NS", "KOTAKBANK.NS", "AXISBANK.NS", "BAJFINANCE.NS",
        "HINDUNILVR.NS", "ITC.NS", "MARUTI.NS", "TITAN.NS",
        "LT.NS",
        "RELIANCE.NS", "NTPC.NS",
        "SUNPHARMA.NS", "DRREDDY.NS",
        "BHARTIARTL.NS",
        "POWERGRID.NS",
    ],
}

# =============================================================================
# SECTOR MAP  —  {ticker: sector}
# =============================================================================
SECTOR_MAP: dict[str, str] = {
    # US
    "MSFT": "Technology",          "AAPL": "Technology",
    "NVDA": "Technology",          "GOOGL": "Technology",
    "META": "Technology",          "AVGO": "Technology",
    "JPM": "Financials",           "V": "Financials",
    "MA": "Financials",            "BRK-B": "Financials",
    "AMZN": "Consumer",            "COST": "Consumer",
    "HD": "Consumer",              "PG": "Consumer",
    "LLY": "Healthcare",           "UNH": "Healthcare",
    "JNJ": "Healthcare",           "ABBV": "Healthcare",
    "XOM": "Energy",               "CVX": "Energy",
    # EU
    "SAP.DE": "Technology",        "ASML.AS": "Technology",
    "SIE.DE": "Industrials",       "AIR.PA": "Industrials",
    "MC.PA": "Consumer",           "OR.PA": "Consumer",
    "NESN.SW": "Consumer",         "UNA.AS": "Consumer",
    "BMW.DE": "Consumer",
    "NOVO-B.CO": "Healthcare",     "AZN.L": "Healthcare",
    "RO.SW": "Healthcare",         "BAYN.DE": "Healthcare",
    "RHM.DE": "Defense",
    "TTE.PA": "Energy",            "SHEL.L": "Energy",
    "BNP.PA": "Financials",        "ALV.DE": "Financials",
    "HSBA.L": "Financials",
    "DTE.DE": "Telecom",
    # IN
    "TCS.NS": "Technology",        "INFY.NS": "Technology",
    "WIPRO.NS": "Technology",      "HCLTECH.NS": "Technology",
    "HDFCBANK.NS": "Financials",   "ICICIBANK.NS": "Financials",
    "KOTAKBANK.NS": "Financials",  "AXISBANK.NS": "Financials",
    "BAJFINANCE.NS": "Financials",
    "HINDUNILVR.NS": "Consumer",   "ITC.NS": "Consumer",
    "MARUTI.NS": "Consumer",       "TITAN.NS": "Consumer",
    "LT.NS": "Industrials",
    "RELIANCE.NS": "Energy",       "NTPC.NS": "Energy",
    "SUNPHARMA.NS": "Healthcare",  "DRREDDY.NS": "Healthcare",
    "BHARTIARTL.NS": "Telecom",
    "POWERGRID.NS": "Utilities",
}

# =============================================================================
# BACKWARD-COMPAT FLAT WATCHLIST  —  {ticker: {sector, market}}
# =============================================================================
def _build_flat() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for market, tickers in WATCHLIST.items():
        for t in tickers:
            out[t] = {"sector": SECTOR_MAP.get(t, "Unknown"), "market": market}
    return out

WATCHLIST_FLAT: dict[str, dict] = _build_flat()

# =============================================================================
# MARKET METADATA
# =============================================================================
MARKETS: dict[str, dict] = {
    "US": {"name": "United States", "label": "[US]", "currency": "USD",
           "symbol": "$",    "tradeable": True,  "broker": "Scalable Capital Prime+"},
    "EU": {"name": "Europe",        "label": "[EU]", "currency": "EUR",
           "symbol": "EUR ", "tradeable": True,  "broker": "Scalable Capital Prime+"},
    "IN": {"name": "India",         "label": "[IN]", "currency": "INR",
           "symbol": "Rs ",  "tradeable": True,  "broker": "HDFC Securities / Zerodha"},
}

_MARKET_SUFFIX: dict[str, str] = {
    ".NS": "IN", ".BO": "IN",
    ".L": "EU", ".DE": "EU", ".PA": "EU", ".SW": "EU",
    ".AS": "EU", ".CO": "EU", ".MI": "EU", ".MC": "EU",
    ".ST": "EU", ".HE": "EU",
}

FALLBACK_SYMBOLS: dict[str, str] = {
    "BRK.B":  "BRK-B",
    "BP/.L":  "BP.L",
    "BT/A.L": "BT-A.L",
}

def get_market(ticker: str) -> str:
    if ticker in WATCHLIST_FLAT:
        return WATCHLIST_FLAT[ticker]["market"]
    for suffix, market in _MARKET_SUFFIX.items():
        if ticker.endswith(suffix):
            return market
    return "US"

def get_sector(ticker: str) -> str:
    return SECTOR_MAP.get(ticker, "Unknown")

def get_currency_symbol(ticker: str) -> str:
    return MARKETS[get_market(ticker)]["symbol"]

def get_currency(ticker: str) -> str:
    return MARKETS[get_market(ticker)]["currency"]

def get_sectors() -> dict[str, str]:
    return {t: info["sector"] for t, info in WATCHLIST_FLAT.items()}

# =============================================================================
# ACCOUNT SETTINGS
# =============================================================================
ACCOUNT: dict = {
    "equity":            100_000.0,
    "commission":        0.001,
    "slippage":          0.001,
    "max_position_size": 0.24,   # 24% baseline; 8 slots, leaders cap at 32%
}

ACCOUNT_SIZE_EUR: float = ACCOUNT["equity"]
MAX_OPEN_POSITIONS: int = 8
MAX_PER_SECTOR:    float = 1.0    # IN has no sector cap (Unknown stocks fill all 8 slots)
MAX_PER_MARKET:    float = 1.0    # fraction of MAX_OPEN_POSITIONS

# =============================================================================
# PORTFOLIO RISK LIMITS
# =============================================================================
RISK: dict = {
    "MAX_OPEN_POSITIONS": 8,
    "MAX_POSITION_SIZE_PCT":      0.24,   # 24% baseline — 8 slots
    "MAX_TOTAL_CONCENTRATION_PCT": 0.32,  # 32% ceiling for velocity-scaled leaders
    # Fractional limits (0.0–1.0) — multiplied by MAX_OPEN_POSITIONS at runtime.
    "MAX_PER_MARKET":     {"US": 1.0, "EU": 1.0, "IN": 1.0},
    "MAX_PER_SECTOR":     {"US": 0.50, "EU": 0.50, "IN": 1.0}, # IN: no cap (Unknown stocks)
    "MAX_HIGH_VOL_PER_MARKET": {"US": 4, "EU": 4, "IN": 4},
    "DRAWDOWN_BANDS": [],  # no circuit breaker — full-size entries throughout recovery
    "CIRCUIT_BREAKER_HYSTERESIS": 0.02,
}

# =============================================================================
# VOLATILITY REGIME
# =============================================================================
REGIME: dict = {
    "LOW_VOL_MAX":    1.0,
    "NORMAL_VOL_MAX": 2.0,
    "HIGH_VOL_MAX":   4.0,
    "LOW":     {"stop_mult": 2.5, "trail_mult": 6.0, "risk_pct": 0.07, "can_trade": True,  "label": "Low Vol"},
    "NORMAL":  {"stop_mult": 2.0, "trail_mult": 7.0, "risk_pct": 0.06, "can_trade": True,  "label": "Normal"},
    "HIGH":    {"stop_mult": 3.0, "trail_mult": 4.5, "risk_pct": 0.035,"can_trade": True,  "label": "High Vol"},
    "EXTREME": {"stop_mult": 0.0, "trail_mult": 0.0, "risk_pct": 0.0,  "can_trade": False, "label": "Extreme"},
}

# =============================================================================
# PER-REGION MARKET PARAMETERS
# Each market has its own risk appetite, trailing-stop aggressiveness,
# entry sensitivity and liquidity requirements tuned to that market's
# historical behaviour and growth potential.
# =============================================================================
MARKET_PARAMS: dict[str, dict] = {
    "US": {
        "risk_pct":      {"LOW": 0.10, "NORMAL": 0.08, "HIGH": 0.04, "EXTREME": 0.0},
        "trail_mult":    {"LOW": 12.0, "NORMAL": 10.0, "HIGH": 6.0,  "EXTREME": 0.0},
        "stop_mult":     {"LOW": 2.5,  "NORMAL": 2.0,  "HIGH": 3.0,  "EXTREME": 0.0},
        "sma_dist_min":   0.008,
        "volume_mult":    0.65,
        "rsi_lo":         47,
        "rsi_hi":         78,
        "macd_hist_eps":  0.0,
    },
    "EU": {
        "risk_pct":      {"LOW": 0.06, "NORMAL": 0.05, "HIGH": 0.03, "EXTREME": 0.0},
        "trail_mult":    {"LOW": 6.0,  "NORMAL": 7.0,  "HIGH": 4.5,  "EXTREME": 0.0},
        "stop_mult":     {"LOW": 2.5,  "NORMAL": 2.0,  "HIGH": 3.0,  "EXTREME": 0.0},
        "sma_dist_min":   0.008,
        "volume_mult":    0.65,
        "rsi_lo":         47,
        "rsi_hi":         78,
        "macd_hist_eps": -0.001,
    },
    "IN": {
        "risk_pct":      {"LOW": 0.09, "NORMAL": 0.07, "HIGH": 0.04, "EXTREME": 0.0},
        "trail_mult":    {"LOW": 7.0,  "NORMAL": 7.0,  "HIGH": 5.0,  "EXTREME": 0.0},
        "stop_mult":     {"LOW": 2.5,  "NORMAL": 2.0,  "HIGH": 3.5,  "EXTREME": 0.0},
        "sma_dist_min":   0.005,
        "volume_mult":    0.55,
        "rsi_lo":         42,
        "rsi_hi":         80,
        "macd_hist_eps":  0.0,
    },
}


def get_regime(atr_pct: float, market: str = "US") -> dict:
    """Return regime dict with market-specific risk_pct, trail_mult, stop_mult."""
    if atr_pct is None or (hasattr(atr_pct, '__float__') and atr_pct != atr_pct):
        return dict(REGIME["EXTREME"])
    v = float(atr_pct)
    if v < REGIME["LOW_VOL_MAX"]:
        key = "LOW"
    elif v < REGIME["NORMAL_VOL_MAX"]:
        key = "NORMAL"
    elif v < REGIME["HIGH_VOL_MAX"]:
        key = "HIGH"
    else:
        return dict(REGIME["EXTREME"])

    base = dict(REGIME[key])
    mp   = MARKET_PARAMS.get(market, MARKET_PARAMS["US"])
    base["risk_pct"]   = mp["risk_pct"][key]
    base["trail_mult"] = mp["trail_mult"][key]
    base["stop_mult"]  = mp["stop_mult"][key]
    return base


def get_gate_params(market: str) -> dict:
    """Return per-region gate defaults (sma_dist_min, volume_mult, rsi, macd)."""
    mp = MARKET_PARAMS.get(market, MARKET_PARAMS["US"])
    return {
        "sma_dist_min":  mp["sma_dist_min"],
        "volume_mult":   mp["volume_mult"],
        "rsi_lo":        mp["rsi_lo"],
        "rsi_hi":        mp["rsi_hi"],
        "macd_hist_eps": mp["macd_hist_eps"],
    }


# =============================================================================
# GATE DEFAULTS
# =============================================================================
GATE_DEFAULTS: dict = {
    "rsi_lo":             42,
    "rsi_hi":             80,
    "sma_dist_min":       0.005,
    "volume_mult":        0.55,
    "volume_mult_us":     0.55,
    "volume_mult_eu_in":  0.55,
    "macd_hist_eps":      0.0,
    "consec_green_block": 3,
    "green_body_ratio":   0.8,
    "sma_rising_lookback": 15,
}

# =============================================================================
# ADAPTIVE TUNER
# =============================================================================
TUNER_MODES:  list[str] = ["STRICT", "BASE", "SOFT", "ULTRA_SOFT"]

TUNER_PARAMS: dict[str, dict] = {
    "STRICT":     {"sma_dist_min": 0.015, "volume_mult": 0.80, "macd_hist_eps":  0.000},
    "BASE":       {"sma_dist_min": 0.010, "volume_mult": 0.60, "macd_hist_eps":  0.000},
    "SOFT":       {"sma_dist_min": 0.007, "volume_mult": 0.50, "macd_hist_eps": -0.001},
    "ULTRA_SOFT": {"sma_dist_min": 0.003, "volume_mult": 0.40, "macd_hist_eps": -0.002},
}

TUNER_EMA_ALPHA:          float = 0.2
TUNER_DENSITY_LOOSEN:     float = 0.02
TUNER_DENSITY_TIGHTEN:    float = 0.06
TUNER_DAYS_TO_TRANSITION: int   = 3

# =============================================================================
# QUALITY FILTER (stock_selector integration)
# =============================================================================
QUALITY_FILTER: dict = {
    "ENABLED":        True,
    "MIN_SCORE":      35,       # Exclude "Drag" stocks (score < 35)
    "PREFER_ABOVE":   45,       # "Good" or better
    "SCORE_CACHE_DAYS": 7,      # Re-score weekly
}

# =============================================================================
# MOMENTUM EXIT TIMER
# Ejects held positions that have lost positive momentum before trailing stop fires.
# score = momentum_score() with downside-vol adjustment (Sortino framework).
# score < 0  → average return across [1M,3M,6M,12M] is negative after vol-adj.
# =============================================================================
MOMENTUM_EXIT: dict = {
    "ENABLED":        True,
    "SCORE_THRESHOLD": 0.0,    # exit when momentum turns negative
    "GRACE_DAYS":     7,       # calendar days after entry before momentum exit can fire
}

# =============================================================================
# DYNAMIC UNIVERSE SETTINGS
# =============================================================================
DYNAMIC_UNIVERSE: dict = {
    "ENABLED":    True,          # build universe from live index constituents
    "MAX_AGE_DAYS": 7,
    "SCORE_TOP_N": {             # quality-score top-N kept per market after scoring
        "US": 200,
        "EU": 200,
        "IN": 250,
    },
}

# =============================================================================
# DATA / CACHE
# =============================================================================
DATA: dict = {
    "CACHE_DIR":    "data",
    "LOOKBACK_DAYS": 300,
    "RETRY_COUNT":   3,
    "RETRY_DELAY":   1.0,
}

# =============================================================================
# REPORT
# =============================================================================
REPORT: dict = {
    "OUTPUT_DIR": "reports",
    "DISCLAIMER": (
        "DISCLAIMER: This system is for RESEARCH and PAPER TRADING only. "
        "Output is NOT financial advice. Do NOT deploy live capital without "
        "independent validation, regulatory compliance review, and full "
        "risk disclosure. Past simulated performance does not guarantee "
        "future results."
    ),
}

# =============================================================================
# JOURNAL
# =============================================================================
def _journal_primary_path() -> str:
    """Resolve the journal path cross-platform.
    Checks JOURNAL_PATH env var first, then common OneDrive locations on
    Windows and macOS, falling back to ~/Documents/."""
    env = os.getenv("JOURNAL_PATH")
    if env:
        return env
    candidates = [
        # Windows OneDrive
        _Path.home() / "OneDrive" / "Raj" / "Investments" / "Mastermind-Trading-Journal.xlsx",
        # macOS OneDrive (consumer)
        _Path.home() / "Library" / "CloudStorage" / "OneDrive-Personal" / "Raj" / "Investments" / "Mastermind-Trading-Journal.xlsx",
        # Generic fallback
        _Path.home() / "Documents" / "Mastermind-Trading-Journal.xlsx",
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    return str(candidates[0])  # return Windows path as default if none found


JOURNAL: dict = {
    "PRIMARY_PATH":   _journal_primary_path(),
    "FALLBACK_NAME":  "Mastermind-Trading-Journal.xlsx",
    "SHEET_SIGNALS":  "4. Trade Log",
    "SHEET_PNL":      "OpenPnL",
    "DATA_START_ROW": 6,
}

# =============================================================================
# RANKING
# =============================================================================
RANKING: dict = {
    "MOMENTUM_PERIODS":  [14, 30, 63],   # 3W / 1.5M / 3M — fast entry before trend exhaustion
    "VOLATILITY_PENALTY": False,
    "DEFAULT_TOP_N":     {"US": 10, "EU": 10, "IN": 10},
}

# =============================================================================
# STRESS TEST WINDOWS
# =============================================================================
STRESS_WINDOWS: dict[str, dict] = {
    "crisis_2008":     {"start": "2008-09-01", "end": "2009-03-31"},
    "covid_2020":      {"start": "2020-02-01", "end": "2020-05-31"},
    "rate_shock_2022": {"start": "2022-01-01", "end": "2022-12-31"},
}
