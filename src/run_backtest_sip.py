"""
run_backtest_sip.py
===================
CLI runner for the SIP strategy historical backtest.

Usage
-----
  python src/run_backtest_sip.py                           # US+EU, €2000/mo, 2016-now
  python src/run_backtest_sip.py --budget 3000             # custom monthly budget
  python src/run_backtest_sip.py --markets US              # US only
  python src/run_backtest_sip.py --start 2018-01-01        # shorter window
  python src/run_backtest_sip.py --max-picks 3             # fewer positions per month
  python src/run_backtest_sip.py --top-n 50                # smaller universe (faster)

Note: fetches 11 years of history — first run takes ~10–20 min for a full universe.
      Subsequent runs use the parquet cache and are much faster.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Ensure UTF-8 output on Windows (needed for ₹, €, etc.)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).parent.parent
YEARS_HISTORY = 11   # covers 2016-2026 plus SMA-200 warmup


def main() -> None:
    parser = argparse.ArgumentParser(description="SIP strategy historical backtest")
    parser.add_argument("--start",      default="2016-01-01", help="Backtest start date (YYYY-MM-DD)")
    parser.add_argument("--end",        default=None,          help="Backtest end date (YYYY-MM-DD); default: today")
    parser.add_argument("--budget-us",  type=float, default=2000.0,  help="US monthly budget in USD (default: 2000)")
    parser.add_argument("--budget-eu",  type=float, default=2000.0,  help="EU monthly budget in EUR (default: 2000)")
    parser.add_argument("--budget-in",  type=float, default=20000.0, help="IN monthly budget in INR (default: 20000)")
    parser.add_argument("--markets",    default="US,EU,IN",    help="Comma-separated markets (default: US,EU,IN)")
    parser.add_argument("--max-picks",  type=int,   default=5,       help="Max stocks per month (default: 5)")
    parser.add_argument("--top-n",      type=int,   default=100,     help="Universe size per market (default: 100)")
    parser.add_argument("--commission",      type=float, default=0.001, help="One-way commission (default: 0.001)")
    parser.add_argument("--slippage",        type=float, default=0.001, help="One-way slippage (default: 0.001)")
    parser.add_argument("--regime-reserve",  type=float, default=0.10,  help="Regime reserve fraction (default: 0.10)")
    args = parser.parse_args()

    markets = [m.strip().upper() for m in args.markets.split(",")]
    region_budget = {"US": args.budget_us, "EU": args.budget_eu, "IN": args.budget_in}

    print(f"\n{'='*60}")
    print(f"  SIP Backtest -- {args.start} to {args.end or 'today'}")
    print(f"  Markets : {', '.join(markets)}")
    print(f"  Budgets : US ${args.budget_us:,.0f}  EU EUR{args.budget_eu:,.0f}  IN Rs{args.budget_in:,.0f}  |  Max picks: {args.max_picks}")
    print(f"{'='*60}")

    # ── 1. Universe ──────────────────────────────────────────────────────────
    print("\n[1/4] Building universe...")
    from universe import get_dynamic_watchlist
    top_n_map = {m: args.top_n for m in markets}
    watchlist  = get_dynamic_watchlist(markets, top_n_map=top_n_map)
    all_tickers = [t for m in markets for t in watchlist.get(m, [])]
    print(f"      {len(all_tickers)} tickers")

    # ── 2. Price data (11-year history) ──────────────────────────────────────
    print(f"\n[2/4] Fetching {YEARS_HISTORY}-year price history + computing indicators...")
    print("      Cached files shorter than the start date are re-downloaded automatically.")
    from data import fetch_history, CACHE_DIR
    from indicators import calculate_all
    import pandas as pd

    start_ts = pd.Timestamp(args.start)
    data_map: dict = {}
    failed = 0
    for i, ticker in enumerate(all_tickers):
        if i % 10 == 0:
            print(f"      {i}/{len(all_tickers)}...", end="\r", flush=True)
        try:
            # Check if cached data covers the backtest start date
            safe   = ticker.replace("/", "_")
            cache  = CACHE_DIR / f"{safe}.parquet"
            use_cache = True
            if cache.exists():
                try:
                    cached_df = pd.read_parquet(cache)
                    if cached_df.empty or cached_df.index[0] > start_ts:
                        use_cache = False   # cache too short — force full re-download
                except Exception:
                    use_cache = False

            df = fetch_history(ticker, years=YEARS_HISTORY, use_cache=use_cache)
            if df is not None and len(df) >= 250:
                data_map[ticker] = calculate_all(df)
        except Exception:
            failed += 1
    print(f"      {len(data_map)} tickers ready ({failed} failed)          ")

    # ── 3. Per-region benchmarks ─────────────────────────────────────────────
    print("\n[3/4] Fetching per-region benchmarks...")
    _BENCH_TICKERS = {"US": "^GSPC", "EU": "^STOXX50E", "IN": "^NSEI"}
    benchmark_dfs: dict = {}
    for mkt, bticker in _BENCH_TICKERS.items():
        if mkt not in markets:
            continue
        try:
            bdf = fetch_history(bticker, years=YEARS_HISTORY)
            if bdf is not None:
                benchmark_dfs[mkt] = bdf
                print(f"      {bticker} ({mkt}) loaded — {len(bdf)} days")
            else:
                print(f"      {bticker} ({mkt}) unavailable — benchmark skipped for {mkt}")
        except Exception as e:
            print(f"      {bticker} ({mkt}) failed: {e}")

    # ── 4. Run backtest ──────────────────────────────────────────────────────
    print("\n[4/4] Running SIP backtest simulation...")
    from backtest_sip import run_sip_backtest
    result = run_sip_backtest(
        data_map=data_map,
        benchmark_dfs=benchmark_dfs,
        start=args.start,
        end=args.end,
        region_budget=region_budget,
        max_picks=args.max_picks,
        commission=args.commission,
        slippage=args.slippage,
        markets=markets,
        regime_reserve_pct=args.regime_reserve,
    )

    if "error" in result:
        print(f"\n  ERROR: {result['error']}")
        return

    print(result["report_text"])

    # ── Save report ──────────────────────────────────────────────────────────
    reports_dir = ROOT / "reports"
    reports_dir.mkdir(exist_ok=True)
    ts   = datetime.now().strftime("%Y-%m-%d")
    name = f"sip-backtest-{ts}-{'_'.join(markets)}.txt"
    path = reports_dir / name
    path.write_text(result["report_text"], encoding="utf-8")
    print(f"  Report saved → {path}\n")


if __name__ == "__main__":
    main()
