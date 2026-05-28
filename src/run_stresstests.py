"""
run_stresstests.py
==================
CLI stress test runner.

Usage:
  python src/run_stresstests.py
  python src/run_stresstests.py --market US
  python src/run_stresstests.py --synthetic-only
  python src/run_stresstests.py --historical-only
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import WATCHLIST, ACCOUNT
from data import fetch_and_cache
from indicators import calculate_all
from stress_tests import (
    run_all_stress_tests, run_historical_stress,
    run_synthetic_stress, format_stress_summary,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Mastermind Pro — stress tests")
    parser.add_argument("--market",          default="ALL", choices=["ALL", "US", "EU", "IN"])
    parser.add_argument("--years",           type=int, default=5)
    parser.add_argument("--equity",          type=float, default=None)
    parser.add_argument("--historical-only", action="store_true")
    parser.add_argument("--synthetic-only",  action="store_true")
    args = parser.parse_args()

    active     = ["US", "EU", "IN"] if args.market == "ALL" else [args.market]
    wl         = {m: WATCHLIST[m] for m in active if m in WATCHLIST}
    all_tickers = [t for tl in wl.values() for t in tl]
    equity     = args.equity or ACCOUNT["equity"]

    print(f"\nStress tests: market={args.market}  equity={equity:,.0f}")
    print("Fetching data...")
    data_map_raw, stats = fetch_and_cache(all_tickers, years=args.years)
    print(f"  {stats['succeeded']}/{stats['attempted']} tickers ok")

    print("Computing indicators...")
    data_map = {t: calculate_all(df) for t, df in data_map_raw.items()}

    if args.historical_only:
        result = {"historical": run_historical_stress(data_map, wl, equity), "synthetic": {}}
    elif args.synthetic_only:
        result = {"historical": {}, "synthetic": run_synthetic_stress(data_map, wl, equity)}
    else:
        result = run_all_stress_tests(data_map, wl, initial_equity=equity)

    print(format_stress_summary(result))

    ROOT     = Path(__file__).parent.parent
    out_path = ROOT / "reports" / "stress_test_result.json"
    out_path.write_text(json.dumps(result, indent=2, default=str))
    print(f"\nSaved: {out_path}")


if __name__ == "__main__":
    main()
