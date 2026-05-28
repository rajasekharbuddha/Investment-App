"""
monte_carlo.py
==============
Monte Carlo robustness analysis over N simulations.

Each simulation:
  1. Random trade order (bootstrap with replacement)
  2. Random trade skipping (missed-trade shock)
  3. Cost multiplier shock (slippage/commission × factor)
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd


def simulate_equity_curve(
    trades: List[Dict],
    initial_equity: float,
    skip_prob: float = 0.0,
    cost_mult: float = 1.0,
    shuffle: bool = True,
    rng: Optional[np.random.Generator] = None,
) -> List[float]:
    if rng is None:
        rng = np.random.default_rng()

    trades_sim = list(trades)
    if shuffle:
        trades_sim = [trades_sim[i] for i in rng.choice(len(trades_sim), len(trades_sim), replace=True)]

    equity = initial_equity
    path   = [equity]

    for trade in trades_sim:
        if skip_prob > 0 and rng.random() < skip_prob:
            continue
        pnl = float(trade.get("pnl", 0)) * cost_mult
        equity += pnl
        path.append(max(equity, 0.0))
        if equity <= 0:
            break

    return path


def _max_drawdown(path: List[float]) -> float:
    if not path:
        return 0.0
    arr  = np.array(path)
    peak = np.maximum.accumulate(arr)
    dd   = (arr - peak) / np.where(peak > 0, peak, 1.0)
    return float(dd.min())


def _cagr(path: List[float], n_trades: int, avg_days_per_trade: float = 10.0) -> float:
    if len(path) < 2 or path[0] <= 0:
        return -1.0
    years = max(n_trades * avg_days_per_trade / 252, 0.01)
    return float((path[-1] / path[0]) ** (1 / years) - 1)


def run_monte_carlo(
    trades: List[Dict],
    initial_equity: float = 100_000.0,
    n_sims: int = 10_000,
    skip_prob: float = 0.05,
    cost_mult_range: tuple = (0.8, 1.5),
    ruin_threshold: float = 0.5,
    n_path_samples: int = 100,
    seed: int = 42,
    avg_days_per_trade: float = 10.0,
) -> Dict[str, Any]:
    if not trades:
        return {"error": "No trades provided"}

    rng = np.random.default_rng(seed)
    n_trades = len(trades)

    cagrs:   List[float] = []
    max_dds: List[float] = []
    finals:  List[float] = []
    ruined:  int = 0
    sample_paths: List[List[float]] = []

    for i in range(n_sims):
        cost_mult = float(rng.uniform(*cost_mult_range))
        path = simulate_equity_curve(
            trades=trades,
            initial_equity=initial_equity,
            skip_prob=skip_prob,
            cost_mult=cost_mult,
            shuffle=True,
            rng=rng,
        )
        final = path[-1]
        finals.append(final)
        cagrs.append(_cagr(path, n_trades, avg_days_per_trade))
        max_dds.append(_max_drawdown(path))
        if final < ruin_threshold * initial_equity:
            ruined += 1
        if i < n_path_samples:
            sample_paths.append(path)

    cagrs_arr  = np.array(cagrs)
    dds_arr    = np.array(max_dds)
    finals_arr = np.array(finals)

    p = [5, 25, 50, 75, 95]
    return {
        "n_sims":         n_sims,
        "n_trades":       n_trades,
        "initial_equity": initial_equity,
        "prob_ruin_pct":  round(ruined / n_sims * 100, 2),
        "cagr_pct": {
            "mean":  round(float(np.nanmean(cagrs_arr) * 100), 2),
            "std":   round(float(np.nanstd(cagrs_arr) * 100), 2),
            **{f"p{q}": round(float(np.nanpercentile(cagrs_arr * 100, q)), 2) for q in p},
        },
        "max_dd_pct": {
            "mean":  round(float(np.nanmean(dds_arr) * 100), 2),
            "std":   round(float(np.nanstd(dds_arr) * 100), 2),
            **{f"p{q}": round(float(np.nanpercentile(dds_arr * 100, q)), 2) for q in p},
        },
        "final_equity": {
            "mean":  round(float(np.nanmean(finals_arr)), 2),
            **{f"p{q}": round(float(np.nanpercentile(finals_arr, q)), 2) for q in p},
        },
        "sample_paths": sample_paths,
        "params": {
            "skip_prob": skip_prob,
            "cost_mult_range": list(cost_mult_range),
            "ruin_threshold": ruin_threshold,
            "seed": seed,
        },
    }


def format_mc_summary(result: Dict) -> str:
    if "error" in result:
        return f"ERROR: {result['error']}"
    lines = ["\n=== MONTE CARLO SIMULATION SUMMARY ==="]
    lines.append(f"Simulations: {result['n_sims']:,}  Trades: {result['n_trades']}  "
                 f"Initial equity: {result['initial_equity']:,.0f}")
    p = result["params"]
    lines.append(f"Params: skip_prob={p['skip_prob']:.0%}  "
                 f"cost_mult={p['cost_mult_range'][0]:.1f}×–{p['cost_mult_range'][1]:.1f}×  "
                 f"ruin_threshold={p['ruin_threshold']:.0%}")
    lines.append(f"\nProbability of ruin: {result['prob_ruin_pct']:.2f}%")

    cg = result["cagr_pct"]
    lines.append(f"\nCAGR distribution (%):")
    lines.append(f"  Mean={cg['mean']:+.2f}  StdDev={cg['std']:.2f}")
    lines.append(f"  P5={cg['p5']:+.2f}  P25={cg['p25']:+.2f}  P50={cg['p50']:+.2f}  "
                 f"P75={cg['p75']:+.2f}  P95={cg['p95']:+.2f}")

    dd = result["max_dd_pct"]
    lines.append(f"\nMax drawdown distribution (%):")
    lines.append(f"  Mean={dd['mean']:.2f}  StdDev={dd['std']:.2f}")
    lines.append(f"  P5={dd['p5']:.2f}  P25={dd['p25']:.2f}  P50={dd['p50']:.2f}  "
                 f"P75={dd['p75']:.2f}  P95={dd['p95']:.2f}")

    fe = result["final_equity"]
    lines.append(f"\nFinal equity (P5/P50/P95): "
                 f"{fe.get('p5',0):,.0f} / {fe.get('p50',0):,.0f} / {fe.get('p95',0):,.0f}")
    return "\n".join(lines)
