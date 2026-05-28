"""
universe.py
===========
Dynamic universe builder with caching.
- US: S&P 500 constituents (GitHub datasets)           ~503 tickers
- EU: DAX40 + FTSE100 + FTSEMIB40 (yfiua) +           ~257 tickers
      CAC40 / AEX / SMI / IBEX35 (static supplement)
- IN: Nifty Large+MidCap 250 (niftyindices.com)        ~254 tickers

Cached under ../universes/. Uses cached if fresh; downloads if stale;
falls back to stale if download fails.
"""

from __future__ import annotations

import io
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd


UNIVERSE_DIR = Path(__file__).parent.parent / "universes"
UNIVERSE_DIR.mkdir(exist_ok=True)

SP500_URL       = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"
NIFTY250_URL    = "https://www.niftyindices.com/IndexConstituent/ind_niftylargemidcap250list.csv"
NIFTY100_URL    = "https://www.niftyindices.com/IndexConstituent/ind_nifty100list.csv"  # fallback
YFIUA_URL       = "https://yfiua.github.io/index-constituents/constituents-{code}.csv"

# EU index codes on yfiua (cac40/ibex35/aex/smi return 0 bytes — covered by EU_SUPPLEMENTAL)
EU_INDEX_CODES = ["dax", "ftse100", "ftsemib"]

# Static supplement for indices yfiua cannot serve
EU_SUPPLEMENTAL: dict[str, dict] = {
    # CAC 40
    "AI.PA":    {"sector": "Materials",    "market": "EU"},
    "SAN.PA":   {"sector": "Healthcare",   "market": "EU"},
    "CS.PA":    {"sector": "Financials",   "market": "EU"},
    "SU.PA":    {"sector": "Industrials",  "market": "EU"},
    "SAF.PA":   {"sector": "Industrials",  "market": "EU"},
    "EL.PA":    {"sector": "Healthcare",   "market": "EU"},
    "BN.PA":    {"sector": "Consumer",     "market": "EU"},
    "KER.PA":   {"sector": "Consumer",     "market": "EU"},
    "RMS.PA":   {"sector": "Consumer",     "market": "EU"},
    "RI.PA":    {"sector": "Consumer",     "market": "EU"},
    "SGO.PA":   {"sector": "Industrials",  "market": "EU"},
    "LR.PA":    {"sector": "Industrials",  "market": "EU"},
    "ML.PA":    {"sector": "Consumer",     "market": "EU"},
    "GLE.PA":   {"sector": "Financials",   "market": "EU"},
    "ACA.PA":   {"sector": "Financials",   "market": "EU"},
    "CAP.PA":   {"sector": "Technology",   "market": "EU"},
    "DSY.PA":   {"sector": "Technology",   "market": "EU"},
    "VIE.PA":   {"sector": "Utilities",    "market": "EU"},
    "ORA.PA":   {"sector": "Telecom",      "market": "EU"},
    "PUB.PA":   {"sector": "Consumer",     "market": "EU"},
    "HO.PA":    {"sector": "Industrials",  "market": "EU"},
    "DG.PA":    {"sector": "Industrials",  "market": "EU"},
    "RNO.PA":   {"sector": "Consumer",     "market": "EU"},
    "STM.PA":   {"sector": "Technology",   "market": "EU"},
    "ALO.PA":   {"sector": "Industrials",  "market": "EU"},
    # AEX (Amsterdam)
    "INGA.AS":  {"sector": "Financials",   "market": "EU"},
    "WKL.AS":   {"sector": "Technology",   "market": "EU"},
    "ABN.AS":   {"sector": "Financials",   "market": "EU"},
    "HEIA.AS":  {"sector": "Consumer",     "market": "EU"},
    "PHIA.AS":  {"sector": "Technology",   "market": "EU"},
    "NN.AS":    {"sector": "Financials",   "market": "EU"},
    "ASM.AS":   {"sector": "Technology",   "market": "EU"},
    "RAND.AS":  {"sector": "Industrials",  "market": "EU"},
    "AGN.AS":   {"sector": "Financials",   "market": "EU"},
    "MT.AS":    {"sector": "Materials",    "market": "EU"},
    "LIGHT.AS": {"sector": "Industrials",  "market": "EU"},
    "ADYEN.AS": {"sector": "Financials",   "market": "EU"},
    "PRX.AS":   {"sector": "Technology",   "market": "EU"},
    "AKZA.AS":  {"sector": "Materials",    "market": "EU"},
    "STLAM.MI": {"sector": "Consumer",     "market": "EU"},
    # SMI (Switzerland)
    "NOVN.SW":  {"sector": "Healthcare",   "market": "EU"},
    "UBSG.SW":  {"sector": "Financials",   "market": "EU"},
    "ABBN.SW":  {"sector": "Industrials",  "market": "EU"},
    "ZURN.SW":  {"sector": "Financials",   "market": "EU"},
    "CFR.SW":   {"sector": "Consumer",     "market": "EU"},
    "LONN.SW":  {"sector": "Healthcare",   "market": "EU"},
    "GIVN.SW":  {"sector": "Materials",    "market": "EU"},
    "HOLN.SW":  {"sector": "Materials",    "market": "EU"},
    "SLHN.SW":  {"sector": "Financials",   "market": "EU"},
    "SREN.SW":  {"sector": "Financials",   "market": "EU"},
    "PGHN.SW":  {"sector": "Financials",   "market": "EU"},
    "SIKA.SW":  {"sector": "Materials",    "market": "EU"},
    "GEBN.SW":  {"sector": "Industrials",  "market": "EU"},
    "SGSN.SW":  {"sector": "Industrials",  "market": "EU"},
    "SCMN.SW":  {"sector": "Telecom",      "market": "EU"},
    "LISN.SW":  {"sector": "Consumer",     "market": "EU"},
    # IBEX 35 (Spain)
    "ITX.MC":   {"sector": "Consumer",     "market": "EU"},
    "SAN.MC":   {"sector": "Financials",   "market": "EU"},
    "BBVA.MC":  {"sector": "Financials",   "market": "EU"},
    "CABK.MC":  {"sector": "Financials",   "market": "EU"},
    "IBE.MC":   {"sector": "Utilities",    "market": "EU"},
    "REP.MC":   {"sector": "Energy",       "market": "EU"},
    "AMS.MC":   {"sector": "Technology",   "market": "EU"},
    "TEF.MC":   {"sector": "Telecom",      "market": "EU"},
    "FER.MC":   {"sector": "Industrials",  "market": "EU"},
    "CLNX.MC":  {"sector": "Telecom",      "market": "EU"},
    "ACS.MC":   {"sector": "Industrials",  "market": "EU"},
    "ELE.MC":   {"sector": "Utilities",    "market": "EU"},
    "NTGY.MC":  {"sector": "Utilities",    "market": "EU"},
    "MAP.MC":   {"sector": "Financials",   "market": "EU"},
    "GRF.MC":   {"sector": "Healthcare",   "market": "EU"},
    # Nordic
    "ERICB.ST": {"sector": "Technology",   "market": "EU"},
    "VOLV-B.ST":{"sector": "Industrials",  "market": "EU"},
    "ATCO-A.ST":{"sector": "Industrials",  "market": "EU"},
    "SAND.ST":  {"sector": "Industrials",  "market": "EU"},
    "NOKIA.HE": {"sector": "Technology",   "market": "EU"},
    "NESTE.HE": {"sector": "Energy",       "market": "EU"},
    "DSV.CO":   {"sector": "Industrials",  "market": "EU"},
}


def _is_fresh(path: Path, max_age_days: int) -> bool:
    if not path.exists():
        return False
    age = datetime.now() - datetime.fromtimestamp(path.stat().st_mtime)
    return age < timedelta(days=max_age_days)


def _download_csv_bytes(url: str, timeout: int = 30) -> Optional[bytes]:
    try:
        from urllib.request import Request, urlopen
        req = Request(url, headers={"User-Agent": "mastermind-pro/2.0"})
        with urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except Exception:
        return None


def _load_or_refresh_csv(cache_path: Path, url: str, max_age_days: int, encoding: str) -> Optional[pd.DataFrame]:
    if _is_fresh(cache_path, max_age_days):
        try:
            return pd.read_csv(cache_path, encoding=encoding)
        except Exception:
            pass

    raw = _download_csv_bytes(url)
    if raw:
        try:
            df = pd.read_csv(io.BytesIO(raw), encoding=encoding)
            df.to_csv(cache_path, index=False, encoding=encoding)
            return df
        except Exception:
            pass

    if cache_path.exists():
        try:
            return pd.read_csv(cache_path, encoding=encoding)
        except Exception:
            return None

    return None


def _find_col(df: pd.DataFrame, candidates: List[str]) -> Optional[str]:
    cols = {c.lower(): c for c in df.columns}
    for name in candidates:
        if name.lower() in cols:
            return cols[name.lower()]
    return None


def _normalise_us_symbol(sym: str) -> str:
    s = str(sym).strip()
    if "." in s and len(s.split(".")[-1]) <= 2:
        return s.replace(".", "-")
    return s


def _normalise_eu_symbol(sym: str) -> str:
    s = str(sym).strip()
    if not s:
        return s
    if "/" in s:
        parts = s.split("/")
        if len(parts) == 2 and parts[1] and parts[1][0].isalpha():
            s = parts[0] + "-" + parts[1]
        else:
            s = s.replace("/", "")
    while ".." in s:
        s = s.replace("..", ".")
    return s


def build_us_universe(max_age_days: int = 7) -> Dict[str, dict]:
    cache = UNIVERSE_DIR / "US_sp500.csv"
    df = _load_or_refresh_csv(cache, SP500_URL, max_age_days=max_age_days, encoding="utf-8")
    if df is None or df.empty:
        return {}

    sym_col = _find_col(df, ["Symbol"])
    sec_col = _find_col(df, ["GICS Sector", "Sector"])
    if not sym_col:
        return {}

    out: Dict[str, dict] = {}
    for _, row in df.iterrows():
        sym = _normalise_us_symbol(row[sym_col])
        if not sym:
            continue
        sector = str(row[sec_col]).strip() if sec_col else "Unknown"
        out[sym] = {"sector": sector or "Unknown", "market": "US"}
    return out


def build_in_universe(max_age_days: int = 7) -> Dict[str, dict]:
    """Download Nifty 250; fall back to Nifty 100 if unavailable."""
    for url, cache_name in [
        (NIFTY250_URL, "IN_nifty250.csv"),
        (NIFTY100_URL, "IN_nifty100.csv"),
    ]:
        cache = UNIVERSE_DIR / cache_name
        df = _load_or_refresh_csv(cache, url, max_age_days=max_age_days, encoding="latin-1")
        if df is not None and not df.empty:
            break
    else:
        return {}

    sym_col = _find_col(df, ["Symbol"])
    ind_col = _find_col(df, ["Industry"])
    if not sym_col:
        return {}

    out: Dict[str, dict] = {}
    for _, row in df.iterrows():
        base = str(row[sym_col]).strip()
        if not base or base.upper().startswith("DUMMY"):
            continue
        ticker = base + ".NS"
        sector = str(row[ind_col]).strip() if ind_col else "Unknown"
        out[ticker] = {"sector": sector or "Unknown", "market": "IN"}
    return out


def build_eu_universe(max_age_days: int = 7) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    for code in EU_INDEX_CODES:
        cache = UNIVERSE_DIR / f"EU_{code}.csv"
        url = YFIUA_URL.format(code=code)
        df = _load_or_refresh_csv(cache, url, max_age_days=max_age_days, encoding="utf-8")
        if df is None or df.empty:
            continue
        sym_col = _find_col(df, ["Symbol"])
        if not sym_col:
            continue
        for _, row in df.iterrows():
            sym = _normalise_eu_symbol(row[sym_col])
            if sym:
                out[sym] = {"sector": "Unknown", "market": "EU"}

    # Merge static supplement (CAC40, AEX, SMI, IBEX35 — yfiua can't serve these)
    for sym, info in EU_SUPPLEMENTAL.items():
        if sym not in out:
            out[sym] = info

    return out


def build_watchlist(markets: Optional[List[str]] = None, max_age_days: int = 7) -> Dict[str, dict]:
    """
    Returns flat watchlist {ticker: {"sector": str, "market": str}}.
    """
    mkts = [m.upper() for m in (markets or ["US", "EU", "IN"])]
    watchlist: Dict[str, dict] = {}
    if "US" in mkts:
        watchlist.update(build_us_universe(max_age_days=max_age_days))
    if "EU" in mkts:
        watchlist.update(build_eu_universe(max_age_days=max_age_days))
    if "IN" in mkts:
        watchlist.update(build_in_universe(max_age_days=max_age_days))
    return watchlist


def flat_to_nested(flat: Dict[str, dict]) -> Dict[str, list]:
    """Convert {ticker: {market, sector}} → {market: [tickers]}."""
    out: Dict[str, list] = {}
    for ticker, info in flat.items():
        mkt = info.get("market", "US")
        out.setdefault(mkt, []).append(ticker)
    return out


def get_dynamic_watchlist(
    markets: Optional[List[str]] = None,
    top_n_map: Optional[Dict[str, int]] = None,
    max_age_days: int = 7,
) -> Dict[str, List[str]]:
    """
    Build {market: [tickers]} from live index-constituent downloads.

    top_n_map caps each market before data fetching (e.g. {"US": 200, "EU": 200, "IN": 250}).
    When a download fails the local hard-coded WATCHLIST is used as fallback.
    """
    from config import WATCHLIST as FALLBACK_WL

    flat = build_watchlist(markets=markets, max_age_days=max_age_days)
    nested = flat_to_nested(flat)

    # Apply per-market cap (rough pre-filter; quality scoring refines further)
    result: Dict[str, List[str]] = {}
    active = [m.upper() for m in (markets or list(nested.keys()))]
    for mkt in active:
        tickers = nested.get(mkt, [])
        if not tickers:
            tickers = FALLBACK_WL.get(mkt, [])
            print(f"  [universe] WARNING: no data for {mkt} -- using fallback ({len(tickers)} tickers)")
        cap = (top_n_map or {}).get(mkt)
        result[mkt] = tickers[:cap] if cap else tickers

    total = sum(len(v) for v in result.values())
    print(f"  [universe] Loaded {total} tickers across {list(result.keys())}")
    return result
