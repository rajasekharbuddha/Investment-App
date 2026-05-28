"""
run_montecarlo.py
=================
CLI Monte Carlo robustness analysis.

Usage:
  python src/run_montecarlo.py
  python src/run_montecarlo.py --trades reports/backtest-2024-01-01-ALL_MARKETS-trades.csv
  python src/run_montecarlo.py --n-sims 10000 --skip-prob 0.05
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd
from config import ACCOUNT
from monte_carlo import run_monte_carlo, format_mc_summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Mastermind Pro — Monte Carlo")
    parser.add_argument("--trades",    default=None,
                        help="Path to trade CSV (default: latest backtest trades)")
    parser.add_argument("--n-sims",    type=int, default=10_000)
    parser.add_argument("--skip-prob", type=float, default=0.05)
    parser.add_argument("--equity",    type=float, default=None)
    parser.add_argument("--seed",      type=int, default=42)
    args = parser.parse_args()

    ROOT = Path(__file__).parent.parent

    if args.trades:
        trades_path = Path(args.trades)
    else:
        candidates = sorted((ROOT / "reports").glob("*-trades.csv"), reverse=True)
        if not candidates:
            print("No trades CSV found. Run run_backtest.py first.")
            sys.exit(1)
        trades_path = candidates[0]

    print(f"Loading trades from: {trades_path}")
    df     = pd.read_csv(trades_path)
    trades = df.to_dict("records")
    print(f"  {len(trades)} trades loaded")

    equity = args.equity or ACCOUNT["equity"]
    print(f"\nRunning {args.n_sims:,} Monte Carlo simulations (equity={equity:,.0f})...")

    result = run_monte_carlo(
        trades=trades,
        initial_equity=equity,
        n_sims=args.n_sims,
        skip_prob=args.skip_prob,
        seed=args.seed,
    )

    print(format_mc_summary(result))

    out = ROOT / "reports" / "monte_carlo_result.json"
    result_serializable = {k: v for k, v in result.items() if k != "sample_paths"}
    out.write_text(json.dumps(result_serializable, indent=2, default=str))
    print(f"\nSaved: {out}")


if __name__ == "__main__":
    main()
