"""
run_replacement_list.py
=======================
CLI replacement / bench list generator.

Usage:
  python src/run_replacement_list.py
  python src/run_replacement_list.py --market EU
  python src/run_replacement_list.py --market US --top-n 20
  python src/run_replacement_list.py --quality-filter
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import WATCHLIST, ACCOUNT, QUALITY_FILTER
from data import fetch_all
from indicators import calculate_all
from adaptive_tuner import AdaptiveTuner
from replacement_list import build_replacement_list, format_bench_table


def main() -> None:
    parser = argparse.ArgumentParser(description="Mastermind Pro — replacement list")
    parser.add_argument("--market",        default="ALL", choices=["ALL", "US", "EU", "IN"])
    parser.add_argument("--top-n",         type=int, default=None)
    parser.add_argument("--years",         type=int, default=3)
    parser.add_argument("--quality-filter", action="store_true",
                        help="Score and sort candidates by quality")
    args = parser.parse_args()

    active = ["US", "EU", "IN"] if args.market == "ALL" else [args.market]
    wl     = {m: WATCHLIST[m] for m in active if m in WATCHLIST}

    print(f"\nFetching data for replacement list ({args.market})...")
    raw      = fetch_all(wl, years=args.years)
    data_map = {t: calculate_all(df) for t, df in raw.items()}

    # Optional quality scoring
    quality_scores: dict = {}
    use_quality = args.quality_filter or QUALITY_FILTER.get("ENABLED", False)
    if use_quality:
        from select_stocks import quality_score_all
        print("  [quality] Scoring candidates...")
        quality_scores = quality_score_all(data_map)

    ROOT  = Path(__file__).parent.parent
    tuner = AdaptiveTuner.load(str(ROOT / "tuner_state.json"))

    for market in active:
        bench = build_replacement_list(
            market, data_map,
            tuner_mode=tuner.mode,
            top_n=args.top_n,
            quality_scores=quality_scores,
        )
        print(f"\n{'='*88}")
        print(f"  REPLACEMENT LIST -- {market}  (tuner: {tuner.mode})  {len(bench)} candidates")
        print(f"{'='*88}")
        print(format_bench_table(bench))

    print()


if __name__ == "__main__":
    main()
