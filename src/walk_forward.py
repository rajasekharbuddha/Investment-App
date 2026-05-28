"""
walk_forward.py
===============
Walk-forward optimisation (WFO).
Splits history into (train, test) pairs, optimises a parameter grid on train,
evaluates best params on out-of-sample test window.
"""

from __future__ import annotations

import itertools
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from config import GATE_DEFAULTS, TUNER_PARAMS
from backtest import run_backtest, compute_metrics


DEFAULT_GRID: Dict[str, List] = {
    "sma_dist_min":  [0.01, 0.02, 0.03],
    "volume_mult":   [0.85, 1.00, 1.10],
    "macd_hist_eps": [-0.001, 0.0],
    "rsi_lo":        [50, 53],
    "rsi_hi":        [65, 68],
}


def _grid_combinations(grid: Dict[str, List]) -> List[Dict]:
    keys   = list(grid.keys())
    values = list(grid.values())
    return [dict(zip(keys, combo)) for combo in itertools.product(*values)]


def evaluate_fold(
    data_map: Dict[str, pd.DataFrame],
    watchlist: Dict[str, List[str]],
    train_start: str,
    train_end: str,
    test_start: str,
    test_end: str,
    params: Dict[str, Any],
    initial_equity: float = 100_000.0,
) -> Dict[str, Any]:
    import config

    gd_backup = dict(config.GATE_DEFAULTS)
    for k, v in params.items():
        if k in config.GATE_DEFAULTS:
            config.GATE_DEFAULTS[k] = v

    try:
        is_res = run_backtest(
            market="ALL", start=train_start, end=train_end,
            initial_equity=initial_equity,
            watchlist_override=watchlist,
            data_map_override=data_map,
        )
        oos_res = run_backtest(
            market="ALL", start=test_start, end=test_end,
            initial_equity=initial_equity,
            watchlist_override=watchlist,
            data_map_override=data_map,
        )
    finally:
        config.GATE_DEFAULTS.update(gd_backup)

    return {
        "params":  params,
        "is":      is_res.get("metrics", {}),
        "oos":     oos_res.get("metrics", {}),
        "is_eq":   is_res.get("equity_curve", []),
        "oos_eq":  oos_res.get("equity_curve", []),
    }


def walk_forward(
    data_map: Dict[str, pd.DataFrame],
    watchlist: Dict[str, List[str]],
    all_dates: List[pd.Timestamp],
    train_size: int = 504,
    test_size:  int = 126,
    anchored:   bool = False,
    grid: Optional[Dict[str, List]] = None,
    initial_equity: float = 100_000.0,
    verbose: bool = True,
) -> Dict[str, Any]:
    grid   = grid or DEFAULT_GRID
    combos = _grid_combinations(grid)

    folds: List[Dict] = []
    concat_oos_equity: List[Dict] = []

    start  = 0
    fold_n = 0
    while True:
        train_end_loc  = (0 if anchored else start) + train_size - 1
        test_start_loc = train_end_loc + 1
        test_end_loc   = test_start_loc + test_size - 1

        if test_end_loc >= len(all_dates):
            break

        train_start_ts = all_dates[0 if anchored else start]
        train_end_ts   = all_dates[train_end_loc]
        test_start_ts  = all_dates[test_start_loc]
        test_end_ts    = all_dates[test_end_loc]

        fold_n += 1
        if verbose:
            print(f"\nFold {fold_n}: train [{train_start_ts.date()} to {train_end_ts.date()}]  "
                  f"test [{test_start_ts.date()} to {test_end_ts.date()}]")

        best_sharpe = -999.0
        best_params = combos[0]
        best_is_res = {}

        for params in combos:
            try:
                fold_res = evaluate_fold(
                    data_map=data_map,
                    watchlist=watchlist,
                    train_start=train_start_ts.strftime("%Y-%m-%d"),
                    train_end=train_end_ts.strftime("%Y-%m-%d"),
                    test_start=test_start_ts.strftime("%Y-%m-%d"),
                    test_end=test_end_ts.strftime("%Y-%m-%d"),
                    params=params,
                    initial_equity=initial_equity,
                )
                sh = fold_res["is"].get("sharpe_ratio", -999)
                if sh > best_sharpe:
                    best_sharpe = sh
                    best_params = params
                    best_is_res = fold_res["is"]
            except Exception as e:
                if verbose:
                    print(f"  Skip params {params}: {e}")

        try:
            oos_eval = evaluate_fold(
                data_map=data_map,
                watchlist=watchlist,
                train_start=train_start_ts.strftime("%Y-%m-%d"),
                train_end=train_end_ts.strftime("%Y-%m-%d"),
                test_start=test_start_ts.strftime("%Y-%m-%d"),
                test_end=test_end_ts.strftime("%Y-%m-%d"),
                params=best_params,
                initial_equity=initial_equity,
            )
        except Exception:
            oos_eval = {"oos": {}, "oos_eq": []}

        oos_metrics = oos_eval.get("oos", {})
        concat_oos_equity.extend(oos_eval.get("oos_eq", []))

        is_sharpe   = best_is_res.get("sharpe_ratio", 0)
        oos_sharpe  = oos_metrics.get("sharpe_ratio", 0)
        degradation = (is_sharpe - oos_sharpe) / is_sharpe if is_sharpe != 0 else 0

        folds.append({
            "fold":        fold_n,
            "train_start": train_start_ts.strftime("%Y-%m-%d"),
            "train_end":   train_end_ts.strftime("%Y-%m-%d"),
            "test_start":  test_start_ts.strftime("%Y-%m-%d"),
            "test_end":    test_end_ts.strftime("%Y-%m-%d"),
            "best_params": best_params,
            "is_metrics":  best_is_res,
            "oos_metrics": oos_metrics,
            "degradation": round(degradation, 4),
        })

        if verbose:
            print(f"  Best IS Sharpe={is_sharpe:.3f}  OOS Sharpe={oos_sharpe:.3f}  "
                  f"Degrad={degradation:.1%}")

        start += test_size

    if concat_oos_equity:
        eq = pd.Series(
            [e["equity"] for e in concat_oos_equity],
            index=pd.to_datetime([e["date"] for e in concat_oos_equity]),
        )
        oos_metrics_overall = compute_metrics(eq, [], eq.iloc[0])
    else:
        oos_metrics_overall = {}

    avg_degrad = float(np.mean([f["degradation"] for f in folds])) if folds else 0.0

    return {
        "folds":               folds,
        "oos_equity_curve":    concat_oos_equity,
        "oos_metrics_overall": oos_metrics_overall,
        "avg_degradation":     round(avg_degrad, 4),
        "grid":                grid,
        "n_combos":            len(combos),
    }


def format_wfo_summary(result: Dict) -> str:
    lines = ["\n=== WALK-FORWARD OPTIMISATION SUMMARY ==="]
    lines.append(f"Folds: {len(result['folds'])}  Grid combos: {result['n_combos']}")
    lines.append(f"Avg IS→OOS degradation: {result['avg_degradation']:.1%}")
    oos = result.get("oos_metrics_overall", {})
    if oos:
        lines.append(f"\nConcatenated OOS: CAGR={oos.get('cagr_pct',0):.2f}%  "
                     f"Sharpe={oos.get('sharpe_ratio',0):.3f}  "
                     f"MaxDD={oos.get('max_drawdown_pct',0):.2f}%")
    lines.append("\nPer-fold best params:")
    for f in result["folds"]:
        lines.append(f"  Fold {f['fold']} [{f['test_start']} to {f['test_end']}]  "
                     f"IS Sharpe={f['is_metrics'].get('sharpe_ratio',0):.3f}  "
                     f"OOS Sharpe={f['oos_metrics'].get('sharpe_ratio',0):.3f}  "
                     f"degrad={f['degradation']:.1%}")
        lines.append(f"    params={f['best_params']}")
    return "\n".join(lines)
