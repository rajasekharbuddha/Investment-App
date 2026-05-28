"""
run_backtest_longterm.py
========================
CLI runner for the long-term portfolio backtest.

Strategy
--------
  At each rebalance: score tickers on structural gates (SMA_50 > SMA_200,
  Close > SMA_200, SMA_50 rising) + momentum. Hold top-N equal-weight.
  Optional: exit immediately on structural breakdown.

Usage
-----
  python src/run_backtest_longterm.py
  python src/run_backtest_longterm.py --market IN --start 2015-01-01
  python src/run_backtest_longterm.py --market IN --start 2015-01-01 --end 2024-12-31
  python src/run_backtest_longterm.py --slots 15 --rebalance 63
  python src/run_backtest_longterm.py --no-breakdown   # disable SMA breakdown exit
  python src/run_backtest_longterm.py --equity 500000
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ROOT = Path(__file__).parent.parent


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Mastermind Pro -- Long-term portfolio backtest",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--market",       default="IN",
                        help="Market to backtest: IN | US | EU (default: IN)")
    parser.add_argument("--start",        default="2015-01-01",
                        help="Start date YYYY-MM-DD (default: 2015-01-01)")
    parser.add_argument("--end",          default="",
                        help="End date YYYY-MM-DD (default: today)")
    parser.add_argument("--equity",       type=float, default=100_000,
                        help="Starting capital (default: 100000)")
    parser.add_argument("--slots",        type=int, default=10,
                        help="Max portfolio positions / equal-weight slots (default: 10)")
    parser.add_argument("--rebalance",    type=int, default=63,
                        help="Rebalance interval in trading days: "
                             "21=monthly, 63=quarterly, 126=semi-annual, 252=annual "
                             "(default: 63)")
    parser.add_argument("--no-breakdown", action="store_true",
                        help="Disable daily breakdown exit (SMA_50 < SMA_200)")
    parser.add_argument("--commission",   type=float, default=0.001,
                        help="One-way commission rate (default: 0.001 = 0.10%%)")
    parser.add_argument("--slippage",     type=float, default=0.001,
                        help="One-way slippage rate (default: 0.001 = 0.10%%)")
    parser.add_argument("--top-n-in",       type=int, default=250,
                        help="Universe size for IN dynamic watchlist (default: 250)")
    parser.add_argument("--momentum-floor", type=float, default=-5.0,
                        help="Exit-watch proxy: exit if avg momentum score < N%% "
                             "(default: -5 = -5%%).  Use -99 to disable.")
    args = parser.parse_args()

    import pandas as pd
    from config import WATCHLIST, DYNAMIC_UNIVERSE
    from data import fetch_all
    from indicators import calculate_all
    from backtest_longterm import run_longterm_backtest, longterm_backtest_report

    market    = args.market.upper()
    end_date  = args.end.strip() or pd.Timestamp.today().strftime("%Y-%m-%d")
    start_date= args.start.strip()

    print(f"\n  Long-Term Backtest  |  Market: {market}  |  {start_date} to {end_date}")
    mf_val  = args.momentum_floor / 100.0
    mf_disp = f"{args.momentum_floor:.0f}%" if mf_val > -1.0 else "OFF"
    print(f"  Slots: {args.slots}  |  Rebalance: {args.rebalance}d"
          f"  |  Breakdown exit: {'OFF' if args.no_breakdown else 'ON'}"
          f"  |  Mom.floor: {mf_disp}")
    print(f"  Capital: {args.equity:,.0f}  |  Commission: {args.commission*100:.2f}%"
          f"  |  Slippage: {args.slippage*100:.2f}%\n")

    # Build watchlist
    use_dyn = DYNAMIC_UNIVERSE.get("ENABLED", False)
    if use_dyn:
        from universe import get_dynamic_watchlist
        score_top_n = {market: args.top_n_in if market == "IN" else
                       DYNAMIC_UNIVERSE.get("SCORE_TOP_N", {}).get(market, 200)}
        print(f"[1/3] Building dynamic universe (top-{score_top_n[market]})...")
        wl = get_dynamic_watchlist([market], score_top_n,
                                   max_age_days=DYNAMIC_UNIVERSE.get("MAX_AGE_DAYS", 7))
    else:
        wl = {market: WATCHLIST.get(market, [])}

    total_tickers = sum(len(v) for v in wl.values())
    print(f"[1/3] Universe: {total_tickers} tickers")

    # Fetch price data (need enough history before start date for indicators)
    # Fetch 3 extra years before start for warm-up
    start_ts = pd.Timestamp(start_date)
    fetch_start = (start_ts - pd.DateOffset(years=3)).strftime("%Y-%m-%d")
    fetch_end   = end_date

    print(f"[2/3] Fetching price data ({fetch_start} to {fetch_end})...")
    raw_data = fetch_all(wl, years=max(3, (pd.Timestamp(fetch_end) - start_ts).days // 365 + 3))
    print(f"      Loaded {len(raw_data)} tickers")

    print(f"[3/3] Calculating indicators + running backtest...")
    data_map = {t: calculate_all(df) for t, df in raw_data.items()}

    result = run_longterm_backtest(
        market              = market,
        data_map            = data_map,
        start               = start_date,
        end                 = end_date,
        equity              = args.equity,
        max_positions       = args.slots,
        rebalance_days      = args.rebalance,
        exit_on_breakdown   = not args.no_breakdown,
        momentum_floor      = mf_val,
        commission          = args.commission,
        slippage            = args.slippage,
    )

    print(longterm_backtest_report(result))


if __name__ == "__main__":
    main()
