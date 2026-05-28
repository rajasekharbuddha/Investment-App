"""
data.py
=======
Yahoo Finance OHLCV fetcher with parquet cache, retry, and parallel fetch.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
import yfinance as yf

from config import DATA, FALLBACK_SYMBOLS


CACHE_DIR = Path(__file__).parent.parent / DATA["CACHE_DIR"]
CACHE_DIR.mkdir(exist_ok=True)

_PARALLEL_WORKERS = 12   # concurrent Yahoo Finance connections
_BATCH_SIZE       = 100  # tickers per yfinance batch call


def _cache_file(ticker: str) -> Path:
    safe = ticker.replace("/", "_")
    return CACHE_DIR / f"{safe}.parquet"


def _cache_fresh(path: Path, max_age_hours: float = 20.0) -> bool:
    if not path.exists():
        return False
    age = datetime.now() - datetime.fromtimestamp(path.stat().st_mtime)
    return age < timedelta(hours=max_age_hours)


def _cache_covers(cached_df: pd.DataFrame, years: int, cache_path: Path) -> bool:
    """
    True if cached data either:
      (a) starts early enough to cover the requested lookback, or
      (b) was freshly written < 2 hrs ago — meaning we already tried fetching
          the full history and this IS all that's available (recent IPO etc).
    """
    if cached_df.empty:
        return False
    required_start = (pd.Timestamp.now()
                      - pd.DateOffset(years=years)
                      - pd.DateOffset(days=60))
    if cached_df.index[0] <= required_start:
        return True
    # Accept short history if the cache file was written very recently
    cache_age = datetime.now() - datetime.fromtimestamp(cache_path.stat().st_mtime)
    return cache_age < timedelta(hours=2)


def _download_one(symbol: str, years: int) -> Optional[pd.DataFrame]:
    end   = datetime.now()
    start = end - timedelta(days=365 * years + 90)
    try:
        df = yf.download(
            symbol,
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            progress=False,
            auto_adjust=True,
        )
        if df is None or df.empty:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        cols = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in df.columns]
        df = df[cols].dropna()
        return df if len(df) >= 30 else None
    except Exception:
        return None


def _download_batch(symbols: List[str], years: int) -> Dict[str, pd.DataFrame]:
    """Download a list of tickers in one yfinance call."""
    if not symbols:
        return {}
    end   = datetime.now()
    start = end - timedelta(days=365 * years + 90)
    try:
        raw = yf.download(
            symbols,
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            progress=False,
            auto_adjust=True,
            group_by="ticker",
        )
        if raw is None or raw.empty:
            return {}
        out: Dict[str, pd.DataFrame] = {}
        for sym in symbols:
            try:
                # MultiIndex when multiple tickers; flat when single
                if isinstance(raw.columns, pd.MultiIndex):
                    df = raw[sym].copy()
                else:
                    df = raw.copy()
                cols = [c for c in ["Open","High","Low","Close","Volume"] if c in df.columns]
                df = df[cols].dropna()
                if len(df) >= 30:
                    out[sym] = df
            except Exception:
                pass
        return out
    except Exception:
        return {}


def fetch_history(ticker: str, years: int = 3, use_cache: bool = True) -> pd.DataFrame:
    symbols_to_try = [ticker]
    if ticker in FALLBACK_SYMBOLS:
        symbols_to_try.append(FALLBACK_SYMBOLS[ticker])

    cache = _cache_file(ticker)
    if use_cache and _cache_fresh(cache):
        try:
            cached_df = pd.read_parquet(cache)
            if _cache_covers(cached_df, years, cache):
                return cached_df
        except Exception:
            pass

    for sym in symbols_to_try:
        for attempt in range(DATA["RETRY_COUNT"]):
            df = _download_one(sym, years)
            if df is not None:
                df.to_parquet(cache)
                return df
            time.sleep(DATA["RETRY_DELAY"] * (attempt + 1))

    raise ValueError(f"No data for {ticker} after {DATA['RETRY_COUNT']} retries")


def fetch_all(
    watchlist,
    years: int = 3,
    use_cache: bool = True,
) -> Dict[str, pd.DataFrame]:
    if watchlist and isinstance(next(iter(watchlist.values())), list):
        tickers = [t for tlist in watchlist.values() for t in tlist]
    else:
        tickers = list(watchlist.keys())

    total   = len(tickers)
    results: Dict[str, pd.DataFrame] = {}

    # ── Phase 1: serve from cache ─────────────────────────────────────────────
    needs_dl: List[str] = []
    for ticker in tickers:
        cache = _cache_file(ticker)
        if use_cache and _cache_fresh(cache):
            try:
                cached_df = pd.read_parquet(cache)
                if _cache_covers(cached_df, years, cache):
                    results[ticker] = cached_df
                    continue
            except Exception:
                pass
        needs_dl.append(ticker)

    cached_n = total - len(needs_dl)
    if cached_n:
        print(f"  Cache: {cached_n}/{total} served from disk", flush=True)
    if not needs_dl:
        print(f"  Data: {total}/{total} ok, 0 failed []", flush=True)
        return results

    # ── Phase 2: batch download in groups ────────────────────────────────────
    print(f"  Downloading {len(needs_dl)} tickers"
          f" (batches of {_BATCH_SIZE}, {_PARALLEL_WORKERS} workers)...", flush=True)

    batch_failed: List[str] = []
    lock = threading.Lock()

    def _fetch_batch(batch: List[str]) -> Dict[str, pd.DataFrame]:
        batch_res = _download_batch(batch, years)
        for sym in batch:
            if sym in batch_res:
                try:
                    batch_res[sym].to_parquet(_cache_file(sym))
                except Exception:
                    pass
        return batch_res

    batches = [needs_dl[i:i + _BATCH_SIZE]
               for i in range(0, len(needs_dl), _BATCH_SIZE)]

    done_batches = [0]
    with ThreadPoolExecutor(max_workers=max(1, _PARALLEL_WORKERS // _BATCH_SIZE + 1)) as ex:
        futures = {ex.submit(_fetch_batch, b): b for b in batches}
        for future in as_completed(futures):
            batch = futures[future]
            try:
                batch_res = future.result()
            except Exception:
                batch_res = {}
            with lock:
                for sym in batch:
                    if sym in batch_res:
                        results[sym] = batch_res[sym]
                    else:
                        batch_failed.append(sym)
                done_batches[0] += 1
                done = cached_n + sum(len(b) for b in batches[:done_batches[0]])
                print(f"  Progress: {done}/{total}"
                      f"  ({len(batch_failed)} failed so far)", flush=True)

    # ── Phase 3: retry failures individually in parallel ─────────────────────
    if batch_failed:
        print(f"  Retrying {len(batch_failed)} tickers individually...", flush=True)

        def _fetch_one(ticker: str):
            try:
                df = fetch_history(ticker, years=years, use_cache=False)
                return ticker, df
            except Exception:
                return ticker, None

        with ThreadPoolExecutor(max_workers=_PARALLEL_WORKERS) as ex:
            for ticker, df in ex.map(_fetch_one, batch_failed):
                if df is not None:
                    results[ticker] = df

    failed = [t for t in tickers if t not in results]
    print(f"  Data: {len(results)}/{total} ok,"
          f" {len(failed)} failed {failed[:10]}", flush=True)
    return results


def fetch_and_cache(
    tickers: List[str],
    years: int = 3,
    force_refresh: bool = False,
) -> Tuple[Dict[str, pd.DataFrame], Dict]:
    stats: Dict = {
        "attempted": 0, "succeeded": 0, "failed": 0,
        "failed_list": [], "success_rate_pct": 0.0,
    }
    results: Dict[str, pd.DataFrame] = {}

    for ticker in tickers:
        stats["attempted"] += 1
        cache = _cache_file(ticker)
        if force_refresh and cache.exists():
            cache.unlink()
        try:
            df = fetch_history(ticker, years=years, use_cache=not force_refresh)
            results[ticker] = df
            stats["succeeded"] += 1
        except Exception:
            stats["failed"] += 1
            stats["failed_list"].append(ticker)

    if stats["attempted"]:
        stats["success_rate_pct"] = round(stats["succeeded"] / stats["attempted"] * 100, 1)
    return results, stats


def health_check(data_map: Dict[str, pd.DataFrame], min_bars: int = 200) -> Dict:
    issues: List[str] = []
    for ticker, df in data_map.items():
        if len(df) < min_bars:
            issues.append(f"{ticker}: only {len(df)} bars (need {min_bars})")
        nan_count = int(df["Close"].isna().sum())
        if nan_count > 5:
            issues.append(f"{ticker}: {nan_count} NaN closes")
        zero_vol = int(df["Volume"].eq(0).sum())
        if len(df) and zero_vol / len(df) > 0.3:
            issues.append(f"{ticker}: >{zero_vol/len(df)*100:.0f}% zero-volume bars")
    return {
        "total": len(data_map),
        "healthy": len(data_map) - len(issues),
        "issues": issues,
    }
