"""
run_walkforward.py
==================
CLI walk-forward optimisation runner.

Usage:
  python src/run_walkforward.py
  python src/run_walkforward.py --market US --train 504 --test 126
  python src/run_walkforward.py --anchored
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd
from config import WATCHLIST, ACCOUNT
from data import fetch_and_cache
from indicators import calculate_all
from walk_forward import walk_forward, format_wfo_summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Mastermind Pro — walk-forward optimisation")
    parser.add_argument("--market",   default="ALL", choices=["ALL", "US", "EU", "IN"])
    parser.add_argument("--years",    type=int, default=5)
    parser.add_argument("--train",    type=int, default=504,
                        help="Train window in trading days (default: 504 ~2yr)")
    parser.add_argument("--test",     type=int, default=126,
                        help="Test window in trading days (default: 126 ~6mo)")
    parser.add_argument("--anchored", action="store_true",
                        help="Use anchored (expanding) train window")
    parser.add_argument("--equity",   type=float, default=None)
    args = parser.parse_args()

    active      = ["US", "EU", "IN"] if args.market == "ALL" else [args.market]
    wl          = {m: WATCHLIST[m] for m in active if m in WATCHLIST}
    all_tickers = [t for tl in wl.values() for t in tl]

    print(f"\nWalk-forward optimisation: market={args.market}  "
          f"train={args.train}d  test={args.test}d")
    print("Fetching data...")
    data_map_raw, stats = fetch_and_cache(all_tickers, years=args.years)
    print(f"  {stats['succeeded']}/{stats['attempted']} tickers ok")

    print("Computing indicators...")
    data_map = {t: calculate_all(df) for t, df in data_map_raw.items()}

    all_dates_sorted = sorted(set(d for df in data_map.values() for d in df.index))

    result = walk_forward(
        data_map=data_map,
        watchlist=wl,
        all_dates=all_dates_sorted,
        train_size=args.train,
        test_size=args.test,
        anchored=args.anchored,
        initial_equity=args.equity or ACCOUNT["equity"],
        verbose=True,
    )

    print(format_wfo_summary(result))

    ROOT     = Path(__file__).parent.parent
    out_path = ROOT / "reports" / "wfo_result.json"
    out_path.write_text(json.dumps(result, indent=2, default=str))
    print(f"\nSaved WFO result: {out_path}")


if __name__ == "__main__":
    main()
