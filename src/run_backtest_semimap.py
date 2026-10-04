"""
run_backtest_semimap.py
=======================
Backtest the Semiconductor Dependency Map stocks, in EUR.

  --mode portfolio   model portfolio (core ETF + satellites) vs core ETF only
  --mode short       short-term ATR-Dynamic strategy on the map's stocks
  --mode long        long-term momentum strategy on the map's stocks
  --mode all         all three (default)

Usage
-----
  python src/run_backtest_semimap.py
  python src/run_backtest_semimap.py --mode portfolio --start 2018-01-01 --rebalance 21
  python src/run_backtest_semimap.py --mode long --slots 5
  python src/run_backtest_semimap.py --synthetic      # offline random-walk data, for testing only

Saves the report to reports/semimap-backtest-<date>.txt and the equity curves
to reports/semimap-backtest-<date>.csv. The same backtest is on the
"Semis Backtest" tab of both apps.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from semimap_backtest import MODES, run_semimap_backtest, save_semimap_report


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--mode",        default="all", choices=list(MODES))
    p.add_argument("--start",       default="2016-01-01", help="Start date (default 2016-01-01)")
    p.add_argument("--end",         default="", help="End date (default today)")
    p.add_argument("--equity",      type=float, default=10_000, help="Starting capital in EUR (default 10,000)")
    p.add_argument("--portfolio",   default=None, help="Portfolio name from semimap/portfolios.json (default: its default)")
    p.add_argument("--rebalance",   type=int, default=63, help="Model portfolio rebalance interval in trading days (default 63 = quarterly)")
    p.add_argument("--slots",       type=int, default=6, help="Long-term strategy positions (default 6)")
    p.add_argument("--lt-rebalance", type=int, default=63, help="Long-term strategy rebalance in calendar days (default 63)")
    p.add_argument("--commission",  type=float, default=0.001)
    p.add_argument("--slippage",    type=float, default=0.001)
    p.add_argument("--synthetic",   action="store_true", help="Use random-walk data instead of Yahoo (offline testing only)")
    a = p.parse_args()

    result = run_semimap_backtest(
        mode=a.mode, start=a.start, end=a.end, equity=a.equity, portfolio=a.portfolio,
        rebalance=a.rebalance, slots=a.slots, lt_rebalance=a.lt_rebalance,
        commission=a.commission, slippage=a.slippage, synthetic=a.synthetic,
    )
    print(result["text"])
    txt, csv = save_semimap_report(result)
    print(f"  Saved reports/{txt.name} and reports/{csv.name}")


if __name__ == "__main__":
    main()
