"""
stress_tests.py
===============
Historical and synthetic stress testing.

Historical windows (from config.STRESS_WINDOWS):
  - 2008 crisis    (2008-09-01 → 2009-03-31)
  - 2020 COVID     (2020-02-01 → 2020-05-31)
  - 2022 rate shock (2022-01-01 → 2022-12-31)

Synthetic shocks:
  - ATR doubled (volatility spike)
  - Volume × 0.30 (liquidity collapse)
  - Gap risk injection (overnight gaps ± 3%)
  - Correlation crisis (sector-cap=1)
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from config import STRESS_WINDOWS, WATCHLIST, ACCOUNT, REPORT
from backtest import run_backtest, compute_metrics


def run_historical_stress(
    data_map: Dict[str, pd.DataFrame],
    watchlist: Optional[Dict[str, List[str]]] = None,
    initial_equity: float = 100_000.0,
    windows: Optional[Dict[str, Dict[str, str]]] = None,
    verbose: bool = True,
) -> Dict[str, Any]:
    windows   = windows or STRESS_WINDOWS
    watchlist = watchlist or WATCHLIST
    results: Dict[str, Any] = {}

    for name, w in windows.items():
        if verbose:
            print(f"  Running historical stress: {name} [{w['start']} to {w['end']}]")
        try:
            res = run_backtest(
                market="ALL",
                start=w["start"],
                end=w["end"],
                initial_equity=initial_equity,
                watchlist_override=watchlist,
                data_map_override=data_map,
            )
            results[name] = {"window": w, "metrics": res.get("metrics", {}),
                             "trades": res.get("closed_trades", [])}
        except Exception as e:
            results[name] = {"window": w, "error": str(e), "metrics": {}}

    return results


def _shock_atr_double(data_map: Dict[str, pd.DataFrame]) -> Dict[str, pd.DataFrame]:
    shocked = {}
    for t, df in data_map.items():
        d = df.copy()
        if "ATR" in d.columns:
            d["ATR"]     = d["ATR"] * 2.0
            d["ATR_PCT"] = d["ATR_PCT"] * 2.0
        midpoint = (d["High"] + d["Low"]) / 2
        half_hl  = (d["High"] - d["Low"]) / 2
        d["High"] = midpoint + half_hl * 2
        d["Low"]  = midpoint - half_hl * 2
        shocked[t] = d
    return shocked


def _shock_liquidity_collapse(data_map: Dict[str, pd.DataFrame],
                              volume_mult: float = 0.30) -> Dict[str, pd.DataFrame]:
    shocked = {}
    for t, df in data_map.items():
        d = df.copy()
        d["Volume"] = (d["Volume"] * volume_mult).astype(int)
        if "VOL_AVG_20" in d.columns:
            d["VOL_AVG_20"] = d["VOL_AVG_20"] * volume_mult
        shocked[t] = d
    return shocked


def _shock_gap_risk(data_map: Dict[str, pd.DataFrame],
                    gap_pct: float = 0.03,
                    seed: int = 0) -> Dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    shocked = {}
    for t, df in data_map.items():
        d    = df.copy()
        n    = len(d)
        gaps = rng.choice([-gap_pct, 0.0, gap_pct],
                          size=n, p=[0.05, 0.90, 0.05])
        mults = 1.0 + gaps
        d["Open"]  = (d["Open"]  * mults).clip(lower=0.01)
        d["High"]  = (d["High"]  * mults).clip(lower=0.01)
        d["Low"]   = (d["Low"]   * mults).clip(lower=0.01)
        d["Close"] = (d["Close"] * mults).clip(lower=0.01)
        shocked[t] = d
    return shocked


def run_synthetic_stress(
    data_map: Dict[str, pd.DataFrame],
    watchlist: Optional[Dict[str, List[str]]] = None,
    initial_equity: float = 100_000.0,
    period_years: int = 3,
    verbose: bool = True,
) -> Dict[str, Any]:
    import config
    watchlist = watchlist or WATCHLIST

    all_dates = sorted(set(d for df in data_map.values() for d in df.index))
    if not all_dates:
        return {}
    start_ts = all_dates[max(0, len(all_dates) - period_years * 252)]
    start    = start_ts.strftime("%Y-%m-%d")

    results: Dict[str, Any] = {}

    scenarios = [
        ("volatility_spike",   _shock_atr_double(data_map)),
        ("liquidity_collapse", _shock_liquidity_collapse(data_map)),
        ("gap_risk",           _shock_gap_risk(data_map)),
    ]

    for name, shocked_data in scenarios:
        if verbose:
            print(f"  Running synthetic stress: {name}")
        try:
            res = run_backtest(
                market="ALL", start=start,
                initial_equity=initial_equity,
                watchlist_override=watchlist,
                data_map_override=shocked_data,
            )
            results[name] = {"metrics": res.get("metrics", {}),
                             "trades":  res.get("closed_trades", [])}
        except Exception as e:
            results[name] = {"error": str(e), "metrics": {}}

    if verbose:
        print("  Running synthetic stress: correlation_crisis")
    backup = config.RISK["MAX_PER_SECTOR"].copy()
    for mk in config.RISK["MAX_PER_SECTOR"]:
        config.RISK["MAX_PER_SECTOR"][mk] = 1
    try:
        res = run_backtest(
            market="ALL", start=start,
            initial_equity=initial_equity,
            watchlist_override=watchlist,
            data_map_override=data_map,
        )
        results["correlation_crisis"] = {"metrics": res.get("metrics", {}),
                                         "trades":  res.get("closed_trades", [])}
    except Exception as e:
        results["correlation_crisis"] = {"error": str(e), "metrics": {}}
    finally:
        config.RISK["MAX_PER_SECTOR"] = backup

    return results


def run_all_stress_tests(
    data_map: Dict[str, pd.DataFrame],
    watchlist: Optional[Dict[str, List[str]]] = None,
    initial_equity: float = 100_000.0,
    verbose: bool = True,
) -> Dict[str, Any]:
    if verbose:
        print("\nRunning historical stress tests...")
    historical = run_historical_stress(data_map, watchlist, initial_equity, verbose=verbose)

    if verbose:
        print("\nRunning synthetic stress tests...")
    synthetic  = run_synthetic_stress(data_map, watchlist, initial_equity, verbose=verbose)

    return {"historical": historical, "synthetic": synthetic}


def format_stress_summary(result: Dict) -> str:
    lines = ["\n=== STRESS TEST SUMMARY ==="]
    lines.append(f"\n{'Scenario':<28} {'CAGR%':>8} {'MaxDD%':>8} {'Sharpe':>8} {'Trades':>7}")
    lines.append("-" * 64)

    all_scenarios = {}
    for name, r in result.get("historical", {}).items():
        all_scenarios[f"hist:{name}"] = r
    for name, r in result.get("synthetic", {}).items():
        all_scenarios[f"synth:{name}"] = r

    for name, r in all_scenarios.items():
        m = r.get("metrics", {})
        if "error" in r:
            lines.append(f"  {name:<26}  ERROR: {r['error']}")
            continue
        lines.append(
            f"  {name:<26}  "
            f"{m.get('cagr_pct',0):>+7.2f}  "
            f"{m.get('max_drawdown_pct',0):>7.2f}  "
            f"{m.get('sharpe_ratio',0):>7.3f}  "
            f"{m.get('total_trades',0):>6}"
        )
    return "\n".join(lines)
