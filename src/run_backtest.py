"""
run_backtest.py
===============
CLI backtest runner.

Usage:
  python src/run_backtest.py
  python src/run_backtest.py --market US
  python src/run_backtest.py --market EU --start 2020-01-01 --years 5
  python src/run_backtest.py --market ALL --start 2021-01-01
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import ACCOUNT, WATCHLIST, DYNAMIC_UNIVERSE
from backtest import run_backtest
from report import backtest_report


def main() -> None:
    parser = argparse.ArgumentParser(description="Mastermind Pro — backtester")
    parser.add_argument("--market",      default="ALL", choices=["ALL", "US", "EU", "IN"],
                        help="Market to backtest (default: ALL)")
    parser.add_argument("--start",       default=None,
                        help="Start date YYYY-MM-DD")
    parser.add_argument("--end",         default=None,
                        help="End date YYYY-MM-DD (default: today)")
    parser.add_argument("--years",       type=int, default=3,
                        help="Years of history when --start not given (default: 3)")
    parser.add_argument("--equity",      type=float, default=None,
                        help="Initial equity (default: from config)")
    parser.add_argument("--slippage",   type=float, default=None,
                        help="Slippage fraction (default: 0.001)")
    parser.add_argument("--commission", type=float, default=None,
                        help="Commission fraction (default: 0.001)")
    parser.add_argument("--no-dynamic", action="store_true",
                        help="Use hardcoded watchlist instead of dynamic index constituents")
    args = parser.parse_args()

    equity = args.equity or ACCOUNT["equity"]
    label  = f"{args.market}_only" if args.market != "ALL" else "ALL_MARKETS"

    active_markets = ([args.market] if args.market != "ALL"
                      else list(WATCHLIST.keys()))

    # Build watchlist (dynamic index constituents or hardcoded fallback)
    use_dyn = DYNAMIC_UNIVERSE.get("ENABLED", False) and not args.no_dynamic
    if use_dyn:
        from universe import get_dynamic_watchlist
        score_top_n = DYNAMIC_UNIVERSE.get("SCORE_TOP_N", {})
        print(f"\nBuilding dynamic universe "
              f"(US={score_top_n.get('US','—')}, "
              f"EU={score_top_n.get('EU','—')}, "
              f"IN={score_top_n.get('IN','—')})…")
        watchlist_override = get_dynamic_watchlist(
            active_markets, score_top_n,
            max_age_days=DYNAMIC_UNIVERSE.get("MAX_AGE_DAYS", 7),
        )
    else:
        watchlist_override = {m: WATCHLIST[m] for m in active_markets if m in WATCHLIST}

    total_tickers = sum(len(v) for v in watchlist_override.values())
    print(f"\nBacktest: market={args.market}  equity={equity:,.0f}  "
          f"tickers={total_tickers}  dynamic={'yes' if use_dyn else 'no'}")

    result = run_backtest(
        market=args.market,
        start=args.start,
        end=args.end,
        years=args.years,
        initial_equity=equity,
        slippage=args.slippage,
        commission=args.commission,
        watchlist_override=watchlist_override,
    )

    if "error" in result:
        print(f"ERROR: {result['error']}")
        sys.exit(1)

    backtest_report(result, market_label=label)


if __name__ == "__main__":
    main()
