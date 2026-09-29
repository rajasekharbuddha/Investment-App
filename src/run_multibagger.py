"""
run_multibagger.py
==================
CLI entry-point for the multi-bagger screener.

Usage examples
--------------
  python src/run_multibagger.py --market IN
  python src/run_multibagger.py --market US --min-score 55 --gap-only
  python src/run_multibagger.py --market IN --top-n 20 --no-tech-gate
  python src/run_multibagger.py --market EU --no-dynamic
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from fundamental import fetch_all_fundamentals
from multibagger_screener import screen_multibaggers, multibagger_report
from sector_gaps import gap_summary

# ── Default universe per market ───────────────────────────────────────────────
# In production these come from the dynamic universe builder (top-250 quality).
# For standalone runs we include a representative starter set.
_STARTER_UNIVERSE: dict[str, list[str]] = {
    "IN": [
        "HAL.NS",  "BEL.NS",  "PARAS.NS", "DIXONSCS.NS", "MFSL.NS",
        "DCMSHRIRAM.NS", "ALKYLAMINE.NS", "TATACHEM.NS", "AAVAS.NS",
        "APLAPOLLO.NS", "SUNTV.NS", "LALPATHLAB.NS", "POLYMED.NS",
        "APOLLOHOSP.NS", "IRCTC.NS", "CDSL.NS", "ROUTE.NS",
        "KPITTECH.NS", "ZENTEC.NS", "TATAELXSI.NS",
    ],
    "US": [
        "NVDA", "AMD", "SMCI", "VRT", "AXON", "RKLB", "LUNR",
        "NNE",  "OKLO", "BWX",  "LDOS", "HEICO", "CACI",
        "IRTC", "NTRA", "EXAS", "SRPT",
        "AEHR", "ONTO", "AMBA",
    ],
    "EU": [
        "RHM.DE",  "LDO.MI", "DASSAULT.PA", "BALN.SW",
        "SIE.DE",  "SGRO.L", "FERG.L",
        "VIE.PA",  "SAAB-B.ST", "KNOS.ST",
    ],
}


def _build_price_data(tickers: list[str]) -> dict:
    """
    Fetch price history and compute SMA_50 / SMA_200 for the technical gate.
    Returns dict ticker → DataFrame (or None on failure).
    """
    try:
        import yfinance as yf
        import pandas as pd
    except ImportError:
        print("  [warn] yfinance not available — technical gate disabled")
        return {}

    price_data: dict = {}
    for ticker in tickers:
        try:
            df = yf.download(ticker, period="300d", progress=False, auto_adjust=True)
            if df is None or df.empty:
                price_data[ticker] = None
                continue
            df = df[["Close"]].copy()
            df["SMA_50"]  = df["Close"].rolling(50).mean()
            df["SMA_200"] = df["Close"].rolling(200).mean()
            price_data[ticker] = df
        except Exception:
            price_data[ticker] = None
    return price_data


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Multi-Bagger Screener — find small/mid-cap growth stocks in structural demand gaps",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--market", default="IN",
        choices=["IN", "US", "EU"],
        help="Market to screen (default: IN)",
    )
    parser.add_argument(
        "--min-score", type=float, default=35.0,
        help="Minimum MB-score to include (default: 35)",
    )
    parser.add_argument(
        "--gap-only", action="store_true",
        help="Return only tickers matching a structural demand gap",
    )
    parser.add_argument(
        "--no-tech-gate", action="store_true",
        help="Skip the SMA technical gate (useful for early-stage breakouts)",
    )
    parser.add_argument(
        "--top-n", type=int, default=None,
        help="Limit output to top-N by MB-score",
    )
    parser.add_argument(
        "--no-dynamic", action="store_true",
        help="Use the fixed starter universe instead of the dynamic top-250",
    )
    parser.add_argument(
        "--tickers", nargs="+", default=None,
        help="Override universe: provide an explicit list of tickers",
    )
    parser.add_argument(
        "--gaps", action="store_true",
        help="Print the structural gap catalogue for the market and exit",
    )
    args = parser.parse_args()

    market = args.market.upper()

    if args.gaps:
        print(gap_summary(market))
        return

    # ── Resolve universe ──────────────────────────────────────────────────────
    if args.tickers:
        tickers = [t.upper() for t in args.tickers]
        print(f"\n  Using {len(tickers)} user-supplied tickers")
    elif not args.no_dynamic:
        # Try to import the dynamic universe builder
        try:
            from dynamic_universe import build_universe
            tickers = build_universe(market, top_n=250)
            print(f"\n  Dynamic universe: {len(tickers)} tickers")
        except ImportError:
            print("  [warn] dynamic_universe not available — falling back to starter set")
            tickers = _STARTER_UNIVERSE.get(market, [])
    else:
        tickers = _STARTER_UNIVERSE.get(market, [])
        print(f"\n  Starter universe: {len(tickers)} tickers")

    if not tickers:
        print(f"  No tickers to screen for market '{market}'. Use --tickers to specify.")
        sys.exit(1)

    # ── Fetch fundamentals ────────────────────────────────────────────────────
    print(f"  Fetching fundamentals...")
    fundamentals = fetch_all_fundamentals(tickers)

    # ── Fetch price data for technical gate ───────────────────────────────────
    price_data = None
    if not args.no_tech_gate:
        print("  Fetching price history for technical gate...")
        price_data = _build_price_data(tickers)

    # ── Run screener ──────────────────────────────────────────────────────────
    results = screen_multibaggers(
        tickers     = tickers,
        market      = market,
        fundamentals= fundamentals,
        price_data  = price_data,
        min_score   = args.min_score,
        gap_only    = args.gap_only,
    )

    if args.top_n:
        results = results[:args.top_n]

    # ── Print report ──────────────────────────────────────────────────────────
    print(multibagger_report(results, market))


if __name__ == "__main__":
    main()
