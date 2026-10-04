"""
run_backtest_sim.py
===================
Run the multi-bagger backtest with synthetic data calibrated to
documented historical market returns (2008–2026).

Use this when live market data is unavailable.  Results are directional
indicators of strategy behaviour — not a guarantee of live performance.

Usage
-----
  python src/run_backtest_sim.py                      # India
  python src/run_backtest_sim.py --market US
  python src/run_backtest_sim.py --market IN --slots 10 --no-accel
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

from synthetic_market_data import generate_market_data, _MARKET_ANNUAL
from backtest_multibagger    import run_mb_backtest, mb_backtest_report
from run_backtest_multibagger import _UNIVERSE


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Multi-Bagger Backtest (Synthetic Data, 2008–2026)"
    )
    parser.add_argument("--market",   default="IN", choices=["IN", "US", "EU"])
    parser.add_argument("--start",    default="2008", help="Start year (default: 2008)")
    parser.add_argument("--end",      default="2026-10-04")
    parser.add_argument("--equity",   type=float, default=100_000)
    parser.add_argument("--slots",    type=int,   default=8)
    parser.add_argument("--review",   type=int,   default=21)
    parser.add_argument("--min-rank", type=float, default=0.10)
    parser.add_argument("--stop-loss", type=float, default=0.0, help="Hard stop below entry (0 = off)")
    parser.add_argument("--no-accel", action="store_true")
    parser.add_argument("--seed",     type=int,   default=42)
    args = parser.parse_args()

    market = args.market.upper()
    start_str = args.start.strip()
    if len(start_str) == 4:
        start_str = f"{start_str}-01-01"

    tickers = _UNIVERSE.get(market, [])
    print(f"\n  Multi-Bagger Backtest  ──  {market}  (SYNTHETIC DATA)")
    print(f"  Calibrated to documented historical returns  (see synthetic_market_data.py)")
    print(f"  Universe: {len(tickers)} tickers  |  Period: {start_str} → {args.end}")
    print(f"  Slots: {args.slots}  |  Review: {args.review}d  |  Min rank: {args.min_rank:.0%}")
    print()
    print(f"  Generating synthetic price series (seed={args.seed})...")

    data_map, benchmark = generate_market_data(
        market    = market,
        tickers   = tickers,
        start     = "2007-06-01",
        end       = args.end,
        seed      = args.seed,
    )

    print(f"  Generated {len(data_map)} stock series + benchmark")
    print(f"  Running backtest from {start_str}...")

    # Inject benchmark into data_map for the backtest engine (uses as buy-hold comparison)
    bench_df = pd.DataFrame({"Close": benchmark})
    bench_df["SMA_50"]  = bench_df["Close"].rolling(50).mean()
    bench_df["SMA_200"] = bench_df["Close"].rolling(200).mean()

    # Run backtest
    result = run_mb_backtest(
        market               = market,
        data_map             = data_map,
        start                = start_str,
        end                  = args.end,
        equity               = args.equity,
        max_positions        = args.slots,
        review_days          = args.review,
        min_entry_rank       = args.min_rank,
        require_acceleration = not args.no_accel,
        stop_loss            = args.stop_loss,
    )

    # Inject synthetic benchmark into result for comparison section of report
    if "error" not in result:
        ann_rets = _MARKET_ANNUAL.get(market, {})
        bench_series = benchmark.loc[start_str:args.end].dropna()
        if len(bench_series) > 1:
            n_yrs = max((bench_series.index[-1] - bench_series.index[0]).days / 365.25, 0.01)
            b_tot  = float(bench_series.iloc[-1] / bench_series.iloc[0]) - 1
            b_cagr = (1 + b_tot) ** (1 / n_yrs) - 1
            from backtest_multibagger import _max_dd, _sharpe
            b_dd  = _max_dd(bench_series)
            b_sh  = _sharpe(bench_series.pct_change().dropna())
            b_ann: dict[int, float] = {}
            for yr, grp in bench_series.groupby(bench_series.index.year):
                b_ann[int(yr)] = float(grp.iloc[-1] / grp.iloc[0] - 1)
            result["benchmark"] = {
                "ticker":         {"IN": "^NSEI (synthetic)", "US": "^GSPC (synthetic)",
                                   "EU": "^STOXX50E (synthetic)"}.get(market, "Benchmark"),
                "cagr":           b_cagr,
                "total_return":   b_tot,
                "max_dd":         b_dd,
                "sharpe":         b_sh,
                "annual_returns": b_ann,
            }

    print(mb_backtest_report(result))


if __name__ == "__main__":
    main()
