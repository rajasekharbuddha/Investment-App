"""
run_sip.py
==========
CLI runner for the monthly SIP strategy (US + EU, default €2,000/month).

Usage
-----
  python src/run_sip.py                       # US + EU, €2,000, today
  python src/run_sip.py --budget 3000         # custom budget
  python src/run_sip.py --markets US          # US only
  python src/run_sip.py --min-q 60            # stricter quality gate
  python src/run_sip.py --top-n 100           # smaller universe per market
  python src/run_sip.py --dry-run             # preview without saving state
  python src/run_sip.py --refresh-cache       # force re-fetch fundamentals
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ROOT = Path(__file__).parent.parent


def main() -> None:
    parser = argparse.ArgumentParser(description="Monthly SIP runner — US + EU equity accumulation")
    parser.add_argument("--budget",        type=float, default=2000.0, help="Monthly budget in EUR (default: 2000)")
    parser.add_argument("--markets",       default="US,EU",            help="Comma-separated markets (default: US,EU)")
    parser.add_argument("--min-q",         type=float, default=55.0,   help="Minimum Q-score for entry (default: 55)")
    parser.add_argument("--top-n",         type=int,   default=200,    help="Universe size per market (default: 200)")
    parser.add_argument("--dry-run",       action="store_true",        help="Preview allocation without saving state")
    parser.add_argument("--refresh-cache", action="store_true",        help="Force re-fetch fundamental data")
    args = parser.parse_args()

    markets = [m.strip().upper() for m in args.markets.split(",")]

    print(f"\n{'='*60}")
    print(f"  SIP Monthly Run — {datetime.now().strftime('%Y-%m-%d')}")
    print(f"  Markets : {', '.join(markets)}")
    print(f"  Budget  : €{args.budget:,.0f}")
    print(f"  Min Q   : {args.min_q}")
    print(f"{'='*60}")

    # 1. Build universe
    print("\n[1/4] Building universe...")
    from universe import get_dynamic_watchlist
    top_n_map = {m: args.top_n for m in markets}
    watchlist = get_dynamic_watchlist(markets, top_n_map=top_n_map)
    all_tickers = [t for m in markets for t in watchlist.get(m, [])]
    print(f"      {len(all_tickers)} tickers loaded")

    # 2. Fetch price data + compute indicators
    print("\n[2/4] Fetching price data and computing indicators...")
    from data import fetch_ticker
    from indicators import calculate_all

    data_map: dict = {}
    failed = 0
    for i, ticker in enumerate(all_tickers):
        if i % 20 == 0:
            print(f"      {i}/{len(all_tickers)}...", end="\r", flush=True)
        try:
            df = fetch_ticker(ticker, lookback_days=300)
            if df is not None and len(df) >= 60:
                data_map[ticker] = calculate_all(df)
        except Exception:
            failed += 1
    print(f"      {len(data_map)} tickers ready ({failed} failed)          ")

    # 3. Fundamental Q-scores
    print("\n[3/4] Fetching fundamental Q-scores...")
    from fundamental import fetch_fundamentals, score_fundamentals

    q_scores: dict = {}
    for i, ticker in enumerate(all_tickers):
        if i % 20 == 0:
            print(f"      {i}/{len(all_tickers)}...", end="\r", flush=True)
        try:
            raw = fetch_fundamentals(ticker, use_cache=not args.refresh_cache)
            score, _ = score_fundamentals(raw)
            q_scores[ticker] = score
        except Exception:
            q_scores[ticker] = 0.0
    print(f"      {len(q_scores)} Q-scores computed                        ")

    # 4. Run SIP cycle
    print("\n[4/4] Running SIP cycle...")
    from sip_strategy import run_sip_cycle, SIP_CONFIG
    SIP_CONFIG["min_q_entry"] = args.min_q

    result = run_sip_cycle(
        data_map=data_map,
        q_scores=q_scores,
        override_budget=args.budget,
        override_min_q=args.min_q,
        dry_run=args.dry_run,
    )

    print(result["report_text"])

    # Save report
    if not args.dry_run:
        reports_dir = ROOT / "reports"
        reports_dir.mkdir(exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d")
        path = reports_dir / f"sip-{ts}.txt"
        path.write_text(result["report_text"], encoding="utf-8")
        print(f"  Report saved → {path}\n")
    else:
        print("  [Dry run] No state saved.\n")

    alloc = result["allocation"]
    if alloc:
        print("  Quick summary:")
        for ticker, eur in alloc.items():
            c = next((x for x in result["candidates"] if x["ticker"] == ticker), {})
            print(f"    BUY {ticker:<12} €{eur:,.0f}  (Q={c.get('q_score', 0):.0f}, Mom={c.get('momentum', 0)*100:+.1f}%)")
    else:
        print("  No buys this month — no candidates passed all gates.")


if __name__ == "__main__":
    main()
