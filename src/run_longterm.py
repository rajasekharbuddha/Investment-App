"""
run_longterm.py
===============
Long-term investment screener.

Pipeline
--------
1. Technical pre-screen  -- reuses existing gates + Q-score to eliminate
   structurally broken stocks (only ENTER / NEAR with Q >= min_q pass)
2. Fundamental analysis  -- fetches P/E, P/B, ROE, revenue growth, D/E,
   margins, FCF yield from yfinance (7-day cache)
3. Combined scoring      -- 65% fundamental + 35% technical
4. Tiered report         -- Tier 1 (high conviction) -> Tier 2 -> Tier 3

Usage
-----
  python src/run_longterm.py                         # IN market, Q>=55
  python src/run_longterm.py --markets IN            # explicit
  python src/run_longterm.py --min-q 60              # stricter technical filter
  python src/run_longterm.py --no-near               # ENTER signals only
  python src/run_longterm.py --refresh-cache         # force re-fetch fundamentals
  python src/run_longterm.py --top-n-in 250          # universe size (default 250)
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# -- Constants ----------------------------------------------------------------
FUND_WEIGHT   = 0.65   # fundamentals dominate for long-term
TECH_WEIGHT   = 0.35

TIER1 = 75   # High conviction -- buy on confirmation
TIER2 = 60   # Watch & accumulate on dips
TIER3 = 45   # Watchlist -- monitor for improvement

MIN_Q_DEFAULT = 55   # technical quality gate

ROOT       = Path(__file__).parent.parent
TUNER_FILE = ROOT / "tuner_state.json"


# -- Formatting helpers -------------------------------------------------------

def _pct(val, dec=1) -> str:
    return f"{val*100:.{dec}f}%" if val is not None else "N/A"


def _x(val, dec=2) -> str:
    return f"{val:.{dec}f}x" if val is not None else "N/A"


def _pe(val, dec=1) -> str:
    return f"{val:.{dec}f}" if val is not None else "N/A"


def _bar(score: float, width: int = 10) -> str:
    """ASCII progress bar for a 0-100 score."""
    filled = round(score / 100 * width)
    return "[" + "#" * filled + "." * (width - filled) + "]"


def _c(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m"


# -- Main pipeline -------------------------------------------------------------

def run_longterm_screen(
    markets: str       = "IN",
    min_q: int         = MIN_Q_DEFAULT,
    include_near: bool = True,
    refresh_cache: bool= False,
    top_n_in: int      = 250,
) -> None:
    import pandas as pd

    from config import WATCHLIST, MARKETS, QUALITY_FILTER, DYNAMIC_UNIVERSE, ACCOUNT
    from data import fetch_all
    from indicators import calculate_all
    from adaptive_tuner import AdaptiveTuner
    from decision_engine import DecisionEngine
    from select_stocks import quality_score_all, dynamic_watchlist
    from fundamental import (
        fetch_all_fundamentals, score_fundamentals,
        fundamental_grade, red_flags, WEIGHTS,
    )

    active_markets = [m.strip().upper() for m in markets.split(",")]
    today          = pd.Timestamp.today().normalize()

    _hdr = "\033[1m\033[94m"
    _rst = "\033[0m"
    _sep = "-" * 72

    print(f"\n{_hdr}{'='*72}{_rst}")
    print(f"\033[1m\033[97m  MASTERMIND PRO -- LONG-TERM SCREENER{_rst}")
    print(f"  {today.strftime('%Y-%m-%d')}  |  Markets: {', '.join(active_markets)}"
          f"  |  Min-Q: {min_q}  |  Signals: ENTER+{'NEAR' if include_near else 'only'}")
    print(f"{_hdr}{'='*72}{_rst}")

    # -- 1. Build universe & fetch price data ---------------------------------
    print("\n[1/4] Building universe & fetching price data...")
    top_n_map = {"US": 200, "EU": 200, "IN": top_n_in}

    if DYNAMIC_UNIVERSE.get("ENABLED", False):
        from universe import get_dynamic_watchlist
        score_top_n = {m: top_n_map.get(m, 200) for m in active_markets}
        active_wl = get_dynamic_watchlist(
            active_markets, score_top_n,
            max_age_days=DYNAMIC_UNIVERSE.get("MAX_AGE_DAYS", 7),
        )
    else:
        active_wl = {m: t for m, t in WATCHLIST.items() if m in active_markets}

    raw_data = fetch_all(active_wl, years=3)
    print(f"  Loaded {len(raw_data)} tickers")

    # -- 2. Technical pre-screen -----------------------------------------------
    print(f"\n[2/4] Technical pre-screen  (Q >= {min_q}, structural gates)...")
    data_map = {t: calculate_all(df) for t, df in raw_data.items()}

    if DYNAMIC_UNIVERSE.get("ENABLED", False):
        active_wl = dynamic_watchlist(data_map, top_n_map, watchlist=active_wl)

    quality_scores = quality_score_all(data_map)

    tuner  = AdaptiveTuner.load(str(TUNER_FILE)) if TUNER_FILE.exists() else AdaptiveTuner()
    engine = DecisionEngine(tuner=tuner)

    result = engine.run_day(
        today=today,
        data_map=data_map,
        portfolio=[],      # no positions -- want all fresh signals
        equity=ACCOUNT["equity"],
        context="live",
        watchlist=active_wl,
        quality_scores=quality_scores,
    )

    # Collect candidates passing the technical bar
    shortlist: list[dict] = []
    for ticker, cand in result["candidates"].items():
        decision = cand.get("decision", "")
        q        = quality_scores.get(ticker, 0)
        if q < min_q:
            continue
        if decision == "ENTER" or (include_near and decision == "NEAR"):
            # Grab latest SMA values for exit-watch display
            df     = data_map.get(ticker)
            sma50  = float(df["SMA_50"].iloc[-1])  if df is not None and "SMA_50"  in df.columns else None
            sma200 = float(df["SMA_200"].iloc[-1]) if df is not None and "SMA_200" in df.columns else None
            shortlist.append({
                "ticker":   ticker,
                "decision": decision,
                "q_score":  q,
                "price":    cand.get("price"),
                "atr_pct":  cand.get("atr_pct"),
                "market":   cand.get("market", active_markets[0]),
                "gates":    cand.get("gates_passed", 5),
                "sma50":    sma50,
                "sma200":   sma200,
            })

    shortlist.sort(key=lambda x: x["q_score"], reverse=True)
    n_enter = sum(1 for s in shortlist if s["decision"] == "ENTER")
    n_near  = sum(1 for s in shortlist if s["decision"] == "NEAR")
    print(f"  {len(shortlist)} candidates passed  "
          f"(ENTER: {n_enter}  NEAR: {n_near}  Q>={min_q})")

    if not shortlist:
        print("  No candidates passed the technical screen -- try lowering --min-q")
        return

    # -- 3. Fundamental analysis -----------------------------------------------
    tickers = [s["ticker"] for s in shortlist]
    cache_note = "  (refreshing cache)" if refresh_cache else "  (using 7-day cache)"
    print(f"\n[3/4] Fundamental analysis for {len(tickers)} stocks...{cache_note}")

    fund_data = fetch_all_fundamentals(tickers, use_cache=not refresh_cache)

    scored: list[dict] = []
    for s in shortlist:
        t  = s["ticker"]
        fd = fund_data.get(t, {})
        if fd.get("_error"):
            print(f"  \033[93mWarning: {t} fetch failed -- {fd['_error']}\033[0m")

        f_score, comps = score_fundamentals(fd)
        t_score        = float(s["q_score"])
        combined       = FUND_WEIGHT * f_score + TECH_WEIGHT * t_score
        flags          = red_flags(fd)
        data_pts       = sum(1 for v in comps.values() if v is not None)

        scored.append({
            **s,
            "fund_data":   fd,
            "fund_score":  f_score,
            "tech_score":  t_score,
            "combined":    combined,
            "components":  comps,
            "flags":       flags,
            "grade":       fundamental_grade(f_score),
            "data_pts":    data_pts,
        })

    scored.sort(key=lambda x: x["combined"], reverse=True)

    # -- 4. Tiered report ------------------------------------------------------
    print(f"\n[4/4] Generating report...\n")

    tier1 = [s for s in scored if s["combined"] >= TIER1]
    tier2 = [s for s in scored if TIER2 <= s["combined"] < TIER1]
    tier3 = [s for s in scored if TIER3 <= s["combined"] < TIER2]
    below = [s for s in scored if s["combined"] < TIER3]

    print(f"{_hdr}{'='*72}{_rst}")
    print(f"\033[1m\033[97m  LONG-TERM SCREENER RESULTS  --  {today.strftime('%Y-%m-%d')}{_rst}")
    print(f"{_hdr}{'='*72}{_rst}")
    print(f"  Score = {FUND_WEIGHT*100:.0f}% Fundamental + {TECH_WEIGHT*100:.0f}% Technical Q-score")
    print(f"  Screened: {len(scored)} stocks  |"
          f"  Tier 1: {len(tier1)}  |  Tier 2: {len(tier2)}  |  Tier 3: {len(tier3)}")

    _CURR = {"IN": "Rs", "US": "$", "EU": "€"}

    def _print_tier(stocks: list[dict], heading: str, color: str) -> None:
        if not stocks:
            return
        print(f"\n\033[1m\033[{color}m{_sep}\033[0m")
        print(f"\033[1m\033[{color}m  {heading}\033[0m")
        print(f"\033[{color}m{_sep}\033[0m")

        for rank, s in enumerate(stocks, 1):
            fd     = s["fund_data"]
            ticker = s["ticker"]
            name   = (fd.get("name") or ticker)[:38]
            signal = "* ENTER" if s["decision"] == "ENTER" else "o NEAR"
            bar    = _bar(s["combined"])
            curr   = _CURR.get(s.get("market", "US"), "$")

            # - Rank header -
            print(f"\n  \033[1m\033[{color}m{rank}. {ticker:<18}\033[0m"
                  f"  Combined \033[1m{s['combined']:.1f}\033[0m  [{bar}]  {signal}")
            print(f"     \033[90m{name}\033[0m"
                  f"  |  F={s['fund_score']:.0f} ({s['grade']})"
                  f"  |  T-Q={s['tech_score']:.0f}"
                  f"  |  {fd.get('sector','Unknown')}")

            # - Price & ATR -
            price = s.get("price")
            atr   = s.get("atr_pct")
            mc    = fd.get("market_cap")
            mc_str    = (f"{curr}{mc/1e9:.0f}B" if mc and mc > 1e9
                         else f"{curr}{mc/1e6:.0f}M" if mc else "N/A")
            p_fmt     = f"{curr}{{:,.0f}}" if s.get("market") == "IN" else f"{curr}{{:,.2f}}"
            price_str = p_fmt.format(price) if price else "N/A"
            atr_str   = f"{atr:.2f}%" if atr else "N/A"   # already in % form
            print(f"     Price={price_str}  ATR%={atr_str}  Mkt Cap={mc_str}")

            # - Valuation -
            pe = fd.get("pe");  pb = fd.get("pb");  ev = fd.get("ev_ebitda")
            eg = fd.get("eps_growth")
            peg_str = "N/A"
            if pe and eg and eg > 0.02:
                peg_str = f"{pe/(eg*100):.2f}"
            print(f"     P/E={_pe(pe)}  P/B={_pe(pb)}  EV/EBITDA={_pe(ev)}  PEG={peg_str}")

            # - Profitability & Growth -
            roe = fd.get("roe");  rg = fd.get("revenue_cagr_3yr") or fd.get("revenue_growth")
            om  = fd.get("operating_margin"); nm = fd.get("net_margin")
            rg_label = "3yr CAGR" if fd.get("revenue_cagr_3yr") else "YoY"
            print(f"     ROE={_pct(roe)}  Rev Growth={_pct(rg)} ({rg_label})"
                  f"  EPS Growth={_pct(eg)}")
            print(f"     OpMargin={_pct(om)}  NetMargin={_pct(nm)}"
                  f"  FCF Yield={_pct(fd.get('fcf_yield'))}  D/E={_x(fd.get('debt_equity'))}")

            # - Component scores -
            comps = s["components"]
            score_line = "  ".join(
                f"{m[:4].upper()}={v}/10" if v is not None else f"{m[:4].upper()}=N/A"
                for m, v in comps.items()
            )
            print(f"     \033[90mScores: {score_line}\033[0m")

            # - Red flags -
            for flag in s["flags"]:
                print(f"     \033[93m!  {flag}\033[0m")

            # - Data availability warning -
            if s["data_pts"] < 5:
                print(f"     \033[91m!  Only {s['data_pts']}/9 fundamental metrics available"
                      f" -- score may be unreliable\033[0m")

            # - Exit Watch -------------------------------------------------------
            print(f"     \033[90m{'- '*34}\033[0m")
            print(f"     \033[1m\033[93m  EXIT WATCH\033[0m")

            # Technical exit: SMA_200 level
            price  = s.get("price")
            sma50  = s.get("sma50")
            sma200 = s.get("sma200")
            if price and sma200:
                gap_pct   = (price - sma200) / sma200 * 100
                sma50_200 = ((sma50 - sma200) / sma200 * 100) if sma50 else None
                gap_color = "\033[92m" if gap_pct > 10 else ("\033[93m" if gap_pct > 5 else "\033[91m")
                print(f"     \033[93m  Technical  :\033[0m"
                      f"  SMA_200 = {curr}{sma200:,.0f}"
                      f"  ({gap_color}{gap_pct:+.1f}% below current price\033[0m)"
                      + (f"  |  SMA_50 gap = {sma50_200:+.1f}%" if sma50_200 is not None else ""))
                print(f"     \033[90m  -> Sell signal if SMA_50 crosses below SMA_200 "
                      f"(confirmed 2-3 weeks)\033[0m")
            else:
                print(f"     \033[93m  Technical  : price/SMA data unavailable\033[0m")

            # Fundamental exit thresholds based on current values
            exits = []
            roe = fd.get("roe")
            if roe is not None:
                thresh = max(0.10, round(roe * 0.5, 2))  # exit if ROE halves or drops below 10%
                exits.append(f"ROE < {thresh*100:.0f}%  (now {roe*100:.1f}%)")

            rg = fd.get("revenue_cagr_3yr") or fd.get("revenue_growth")
            if rg is not None and rg > 0:
                exits.append(f"Revenue growth turns negative for 2 consecutive years  (now {rg*100:.1f}%)")

            de = fd.get("debt_equity")
            if de is not None:
                thresh_de = max(2.0, round(de * 2, 1))
                exits.append(f"D/E > {thresh_de:.1f}x  (now {de:.2f}x)")

            fcf = fd.get("fcf_yield")
            if fcf is not None and fcf > 0:
                exits.append(f"FCF yield turns negative  (now {fcf*100:.1f}%)")

            if pe and pe > 0:
                exits.append(f"P/E > {pe*2:.0f}x without growth acceleration  (now {pe:.1f}x)")

            if exits:
                print(f"     \033[93m  Fundamental:\033[0m")
                for ex in exits:
                    print(f"     \033[90m  -> {ex}\033[0m")

            print(f"     \033[90m{'- '*34}\033[0m")

        print()

    _print_tier(tier1, f"TIER 1 -- HIGH CONVICTION  (Combined >= {TIER1})", "92")
    _print_tier(tier2, f"TIER 2 -- WATCH & ACCUMULATE  ({TIER2}-{TIER1-1})", "96")
    _print_tier(tier3, f"TIER 3 -- WATCHLIST  ({TIER3}-{TIER2-1})", "93")

    if below:
        print(f"\033[90m  Below threshold (<{TIER3}): "
              + ", ".join(s["ticker"] for s in below) + "\033[0m")

    # - Summary table -
    print(f"\n{_hdr}{'='*72}{_rst}")
    print(f"\033[1m\033[97m  SUMMARY TABLE\033[0m")
    print(f"{_hdr}{'='*72}{_rst}")
    print(f"  {'Ticker':<18}  {'Combined':>8}  {'Fund':>6}  {'Tech':>5}  "
          f"{'Grade':<8}  {'Signal':<7}  P/E   D/E   ROE")
    print(f"  {'-'*18}  {'-'*8}  {'-'*6}  {'-'*5}  {'-'*8}  {'-'*7}  {'-'*4}  {'-'*4}  {'-'*5}")
    for s in scored:
        fd   = s["fund_data"]
        tier = ("T1" if s["combined"] >= TIER1 else
                "T2" if s["combined"] >= TIER2 else
                "T3" if s["combined"] >= TIER3 else "--")
        print(f"  {s['ticker']:<18}  {s['combined']:>8.1f}  {s['fund_score']:>6.1f}"
              f"  {s['tech_score']:>5.0f}  {s['grade']:<8}  {s['decision']:<7}"
              f"  {_pe(fd.get('pe'),0):>4}"
              f"  {_x(fd.get('debt_equity'),1):>5}"
              f"  {_pct(fd.get('roe'),0):>5}")

    print(f"\n{_hdr}{'='*72}{_rst}")
    print(f"\033[93m  DISCLAIMER: Research use only. Not financial advice.\033[0m")
    print(f"  Fundamental data: Yahoo Finance  |  Cache TTL: 7 days")
    print(f"  Weights: ROE 20%  RevGrowth 15%  D/E 15%  EPS 12%  "
          f"OpMargin 10%  PEG 10%  FCF 8%  P/B 5%  NM 5%")
    print(f"{_hdr}{'='*72}{_rst}\n")


# -- CLI -----------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Mastermind Pro -- Long-term investment screener",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--markets",       default="IN",
                        help="Comma-separated markets (default: IN)")
    parser.add_argument("--min-q",         type=int, default=MIN_Q_DEFAULT,
                        help=f"Min technical Q score (default: {MIN_Q_DEFAULT})")
    parser.add_argument("--no-near",       action="store_true",
                        help="Exclude NEAR signals -- ENTER only")
    parser.add_argument("--refresh-cache", action="store_true",
                        help="Force re-fetch fundamental data (ignore 7-day cache)")
    parser.add_argument("--top-n-in",      type=int, default=250,
                        help="Dynamic universe size for IN market (default: 250)")
    args = parser.parse_args()

    run_longterm_screen(
        markets       = args.markets,
        min_q         = args.min_q,
        include_near  = not args.no_near,
        refresh_cache = args.refresh_cache,
        top_n_in      = args.top_n_in,
    )


if __name__ == "__main__":
    main()
