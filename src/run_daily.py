"""
run_daily.py
============
CLI daily scan — enhanced with quality scoring and NEAR signals.

Usage:
  python src/run_daily.py
  python src/run_daily.py --markets US,EU,IN
  python src/run_daily.py --skip-journal
  python src/run_daily.py --dynamic --top-n US=80,EU=40,IN=60
  python src/run_daily.py --quality-filter        # exclude Drag stocks
  python src/run_daily.py --score-universe        # print quality scores table
  python src/run_daily.py --update-pnl
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import WATCHLIST, WATCHLIST_FLAT, MARKETS, ACCOUNT, QUALITY_FILTER, DYNAMIC_UNIVERSE
from data import fetch_all
from indicators import calculate_all
from adaptive_tuner import AdaptiveTuner
from decision_engine import DecisionEngine
from report import daily_report, portfolio_review_report
from journal import update_journal
from post_trade import run as run_post_trade

ROOT           = Path(__file__).parent.parent
PORTFOLIO_FILE = ROOT / "portfolio" / "positions.json"
TUNER_FILE     = ROOT / "tuner_state.json"
STATE_FILE     = ROOT / "state" / "last_decisions.json"


def _load_portfolio() -> list:
    if PORTFOLIO_FILE.exists():
        try:
            return json.loads(PORTFOLIO_FILE.read_text())
        except Exception:
            pass
    return []


def _save_portfolio(portfolio: list) -> None:
    PORTFOLIO_FILE.parent.mkdir(exist_ok=True)
    PORTFOLIO_FILE.write_text(json.dumps(portfolio, indent=2))


def _parse_top_n(raw: str) -> dict:
    out = {}
    for part in raw.split(","):
        if "=" in part:
            k, v = part.strip().split("=", 1)
            try:
                out[k.strip().upper()] = int(v.strip())
            except ValueError:
                pass
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Mastermind Pro — daily scan")
    parser.add_argument("--markets",        default="US,EU,IN",
                        help="Comma-separated markets (default: US,EU,IN)")
    parser.add_argument("--skip-journal",   action="store_true",
                        help="Skip Excel journal update")
    parser.add_argument("--dynamic",        action="store_true",
                        help="Use dynamic top-N universe selection")
    parser.add_argument("--top-n",          default="",
                        help="Top-N per market e.g. US=80,EU=40,IN=60")
    parser.add_argument("--quality-filter", action="store_true",
                        help="Enable quality scoring pre-filter (exclude Drag stocks)")
    parser.add_argument("--score-universe", action="store_true",
                        help="Print quality scores table before scan")
    parser.add_argument("--update-pnl",    action="store_true",
                        help="Run post-trade enrichment after scan")
    parser.add_argument("--years",          type=int, default=3,
                        help="Years of history to fetch (default: 3)")
    parser.add_argument("--asof",           default="",
                        help="Simulate scan as of a past date YYYY-MM-DD (no lookahead)")
    args = parser.parse_args()

    active_markets = [m.strip().upper() for m in args.markets.split(",")]
    top_n_map      = _parse_top_n(args.top_n) if args.top_n else {}

    use_quality  = args.quality_filter or QUALITY_FILTER.get("ENABLED", False)
    use_dyn_univ = args.dynamic or DYNAMIC_UNIVERSE.get("ENABLED", False)

    # ── Step 1: Build universe & fetch EOD data ───────────────────────────────
    print("\n[1/5] Fetching EOD data from Yahoo Finance...")

    if use_dyn_univ:
        from universe import get_dynamic_watchlist
        score_top_n = {
            m: (top_n_map.get(m) or DYNAMIC_UNIVERSE["SCORE_TOP_N"].get(m, 200))
            for m in active_markets
        }
        print(f"  [universe] Downloading index constituents "
              f"(US={score_top_n.get('US','—')}, "
              f"EU={score_top_n.get('EU','—')}, "
              f"IN={score_top_n.get('IN','—')})…")
        active_wl = get_dynamic_watchlist(active_markets, score_top_n,
                                          max_age_days=DYNAMIC_UNIVERSE.get("MAX_AGE_DAYS", 7))
    else:
        active_wl = {m: tickers for m, tickers in WATCHLIST.items() if m in active_markets}

    raw_data = fetch_all(active_wl, years=args.years)

    # ── Step 2: Calculate indicators ─────────────────────────────────────────
    print(f"\n[2/5] Calculating indicators for {len(raw_data)} tickers...")
    data_map = {t: calculate_all(df) for t, df in raw_data.items()}

    # Dynamic quality-ranking: select top-N from full universe
    if use_dyn_univ:
        from select_stocks import dynamic_watchlist
        print("  [dynamic] Ranking universe by momentum + quality...")
        score_top_n = {
            m: (top_n_map.get(m) or DYNAMIC_UNIVERSE["SCORE_TOP_N"].get(m, 200))
            for m in active_markets
        }
        active_wl = dynamic_watchlist(data_map, score_top_n, watchlist=active_wl)
        total_sel  = sum(len(v) for v in active_wl.values())
        print(f"  [dynamic] Selected {total_sel} tickers after ranking")

    # ── Quality scoring ───────────────────────────────────────────────────────
    quality_scores: dict = {}
    quality_filtered: list = []

    if use_quality:
        print("\n  [quality] Scoring universe...")
        from select_stocks import quality_score_all, filter_by_quality
        quality_scores = quality_score_all(data_map)
        min_score = QUALITY_FILTER.get("MIN_SCORE", 35)
        all_tickers = [t for tl in active_wl.values() for t in tl]
        _, quality_filtered = filter_by_quality(all_tickers, quality_scores, min_score=min_score)
        if quality_filtered:
            print(f"  [quality] {len(quality_filtered)} Drag stocks filtered out: "
                  f"{', '.join(quality_filtered[:8])}{'…' if len(quality_filtered) > 8 else ''}")

    if args.score_universe and quality_scores:
        from select_stocks import quality_score_all
        from stock_selector import score_all
        import pandas as pd
        from report import quality_report
        print("\n  [quality] Universe quality scores:")
        all_tickers = [t for tl in active_wl.values() for t in tl]
        scores_df = score_all(data_map)
        quality_report(scores_df)

    # ── Step 3: Run engine.run_day() ─────────────────────────────────────────
    print("\n[3/5] Running DecisionEngine.run_day()...")
    portfolio = _load_portfolio()
    tuner     = AdaptiveTuner.load(str(TUNER_FILE))
    engine    = DecisionEngine(tuner=tuner)

    import pandas as pd
    if args.asof:
        try:
            today = pd.Timestamp(args.asof).normalize()
            print(f"  [asof] Historical scan as of {today.date()} — data sliced, no lookahead")
            data_map = {t: df[df.index <= today] for t, df in data_map.items()
                        if not df[df.index <= today].empty}
        except Exception as e:
            print(f"  [asof] Invalid date '{args.asof}': {e} — using today")
            today = pd.Timestamp.today().normalize()
    else:
        today = pd.Timestamp.today().normalize()

    result = engine.run_day(
        today=today,
        data_map=data_map,
        portfolio=portfolio,
        equity=ACCOUNT["equity"],
        context="live",
        watchlist=active_wl,
        quality_scores=quality_scores if use_quality else None,
    )

    # Update in-memory portfolio
    new_portfolio = list(result["held"])
    for entry_info in result["new_entries"] + result["replacement_queue"]:
        ticker = entry_info["ticker"]
        close  = float(data_map[ticker].iloc[-1]["Close"]) if ticker in data_map else entry_info.get("price", 0)
        if entry_info.get("shares", 0) > 0:
            regime = entry_info.get("regime", {})
            new_portfolio.append({
                "ticker":              ticker,
                "market":              entry_info.get("market", "US"),
                "sector":              entry_info.get("sector", "Unknown"),
                "entry_price":         close,
                "entry_date":          today.strftime("%Y-%m-%d"),
                "shares":              entry_info.get("shares", 0),
                "stop_loss":           entry_info.get("stop_price", close * 0.95),
                "stop_loss_initial":   entry_info.get("stop_price", close * 0.95),
                "trail_mult":          entry_info.get("trail_mult", 5.0),
                "peak_price":          close,
                "atr_at_entry":        entry_info.get("atr", 0),
                "risk_pct":            (regime.get("risk_pct", 0.05) if isinstance(regime, dict) else 0.05),
                "regime":              (regime.get("label", "Normal") if isinstance(regime, dict) else "Normal"),
                "is_high_vol":         entry_info.get("is_high_vol", False),
                "cost":                entry_info.get("cost", 0),
            })

    _save_portfolio(new_portfolio)

    # ── Step 4: Report ───────────────────────────────────────────────────────
    print("\n[4/5] Generating report...")
    candidates = list(result["candidates"].values())
    for c in candidates:
        sz = result["sizing"].get(c["ticker"])
        if sz:
            c["sizing"] = sz

    loaded_mode = result.get("loaded_tuner_mode", result["tuner_mode"])
    next_mode   = result["tuner_mode"]
    if loaded_mode != next_mode:
        print(f"  [tuner] Mode shifted: {loaded_mode} → {next_mode} (applies next scan)")
    daily_report(
        decisions=candidates,
        account_eur=ACCOUNT["equity"],
        watchlist=active_wl,
        markets=MARKETS,
        tuner_mode=loaded_mode,
        risk_scale=result["risk_scale"],
        quality_filtered=result.get("quality_filtered", quality_filtered),
        quality_scores=result.get("quality_scores", quality_scores),
    )

    portfolio_review_report(result, ACCOUNT["equity"])

    # Save last decisions
    STATE_FILE.parent.mkdir(exist_ok=True)
    STATE_FILE.write_text(
        json.dumps({"date": today.strftime("%Y-%m-%d"),
                    "decisions": candidates, "tuner_mode": result["tuner_mode"]},
                   indent=2, default=str)
    )

    # ── Step 5: Journal & post-trade ─────────────────────────────────────────
    if not args.skip_journal:
        print("\n[5/5] Updating journal...")
        status = update_journal(candidates, WATCHLIST_FLAT, MARKETS)
        print(status)
    else:
        print("\n[5/5] Journal update skipped.")

    tuner.save(str(TUNER_FILE))

    if args.update_pnl:
        print("\n  Running post-trade P&L enrichment...")
        run_post_trade()

    print()


if __name__ == "__main__":
    main()
