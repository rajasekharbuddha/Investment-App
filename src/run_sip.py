"""
run_sip.py
==========
CLI runner for the monthly SIP strategy (C2 regime-reserve variant).

Usage
-----
  python src/run_sip.py                       # US + EU + IN, per-region budgets, today
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

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).parent.parent


def main() -> None:
    parser = argparse.ArgumentParser(description="Monthly SIP runner — regime-reserve C2 strategy")
    parser.add_argument("--markets",       default="US,EU,IN",       help="Comma-separated markets (default: US,EU,IN)")
    parser.add_argument("--min-q",         type=float, default=55.0, help="Minimum Q-score for entry (default: 55)")
    parser.add_argument("--top-n",         type=int,   default=100,  help="Universe size per market (default: 100)")
    parser.add_argument("--dry-run",       action="store_true",      help="Preview allocation without saving state")
    parser.add_argument("--refresh-cache", action="store_true",      help="Force re-fetch fundamental data")
    args = parser.parse_args()

    markets = [m.strip().upper() for m in args.markets.split(",")]

    from sip_strategy import SIP_CONFIG
    rb  = SIP_CONFIG["region_budget"]
    sym = SIP_CONFIG["region_symbol"]
    rsv = SIP_CONFIG["regime_reserve_pct"]

    budget_str = "  |  ".join(f"{sym.get(m,'')}{rb.get(m,0):,.0f}/mo" for m in markets)

    print(f"\n{'='*64}")
    print(f"  SIP Monthly Run — {datetime.now().strftime('%Y-%m-%d')}")
    print(f"  Markets       : {', '.join(markets)}")
    print(f"  Budgets       : {budget_str}")
    print(f"  Regime reserve: {rsv*100:.0f}% held/mo, released on index < SMA_200")
    print(f"  Min Q-score   : {args.min_q}")
    print(f"{'='*64}")

    # ── 1. Universe ──────────────────────────────────────────────────────────
    print("\n[1/4] Building universe...")
    from universe import get_dynamic_watchlist
    watchlist   = get_dynamic_watchlist(markets, top_n_map={m: args.top_n for m in markets})
    all_tickers = [t for m in markets for t in watchlist.get(m, [])]
    print(f"      {len(all_tickers)} tickers")

    # ── 2. Price data ────────────────────────────────────────────────────────
    print("\n[2/4] Fetching 1-year price data + computing indicators...")
    from data import fetch_history
    from indicators import calculate_all

    data_map: dict = {}
    failed = 0
    for i, ticker in enumerate(all_tickers):
        if i % 20 == 0:
            print(f"      {i}/{len(all_tickers)}...", end="\r", flush=True)
        try:
            df = fetch_history(ticker, years=1)
            if df is not None and len(df) >= 60:
                data_map[ticker] = calculate_all(df)
        except Exception:
            failed += 1
    print(f"      {len(data_map)} tickers ready ({failed} failed)          ")

    # ── 3. Fundamental Q-scores ──────────────────────────────────────────────
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
    print(f"      {len(q_scores)} Q-scores computed          ")

    # ── 4. Regime benchmarks ─────────────────────────────────────────────────
    print("\n[4/4] Fetching regime benchmarks + running SIP cycle...")
    _BENCH = {"US": "^GSPC", "EU": "^STOXX50E", "IN": "^NSEI"}
    benchmark_dfs: dict = {}
    for mkt in markets:
        bt = _BENCH.get(mkt)
        if bt:
            try:
                bdf = fetch_history(bt, years=1)
                if bdf is not None:
                    benchmark_dfs[mkt] = bdf
                    print(f"      {bt} ({mkt}) — {len(bdf)} days")
            except Exception as e:
                print(f"      {bt} ({mkt}) failed: {e}")

    # ── 5. Run SIP cycle ─────────────────────────────────────────────────────
    from sip_strategy import run_sip_cycle

    result = run_sip_cycle(
        data_map=data_map,
        q_scores=q_scores,
        override_min_q=args.min_q,
        benchmark_dfs=benchmark_dfs,
        dry_run=args.dry_run,
    )

    print(result["report_text"])

    # ── Regime status ────────────────────────────────────────────────────────
    regime_st = result.get("regime_status", {})
    dip_rsv   = result.get("dip_reserve", {})
    print("  REGIME RESERVE STATUS")
    print("  " + "-" * 44)
    for mkt in markets:
        s = sym.get(mkt, "")
        print(f"  {mkt}: {regime_st.get(mkt, '—')}  "
              f"(reserve now: {s}{dip_rsv.get(mkt, 0):,.0f})")

    # ── Save report ──────────────────────────────────────────────────────────
    reports_dir = ROOT / "reports"
    reports_dir.mkdir(exist_ok=True)
    ts   = datetime.now().strftime("%Y-%m-%d")
    path = reports_dir / f"sip-{ts}.txt"
    if not args.dry_run:
        path.write_text(result["report_text"], encoding="utf-8")
        print(f"\n  Report saved: {path}\n")
    else:
        print("\n  [Dry run] No state saved.\n")

    # ── Quick allocation summary ─────────────────────────────────────────────
    alloc      = result["allocation"]
    region_dep = result.get("region_deploy", {})
    if alloc:
        print("  BUYS THIS MONTH:")
        for ticker, amt in alloc.items():
            c: dict = next((x for x in result["candidates"] if x["ticker"] == ticker), {})
            mkt = c.get("market", "")
            s   = sym.get(mkt, "")
            print(f"    BUY {ticker:<12} {s}{amt:,.0f}  "
                  f"(Q={c.get('q_score', 0):.0f}  Mom={c.get('momentum', 0)*100:+.1f}%  [{mkt}])")
    else:
        print("  No buys this month — no candidates passed all gates.")


if __name__ == "__main__":
    main()
