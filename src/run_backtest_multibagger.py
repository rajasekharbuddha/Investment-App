"""
run_backtest_multibagger.py
===========================
CLI runner for the multi-bagger backtest from 2008.

Downloads price history starting 2007-06-01 (6 months pre-start to warm up
indicators), computes SMA_50 / SMA_200, then simulates from 2008-01-01.

Usage
-----
  python src/run_backtest_multibagger.py                          # India, default universe
  python src/run_backtest_multibagger.py --market US              # US market
  python src/run_backtest_multibagger.py --market IN --slots 12   # 12 positions
  python src/run_backtest_multibagger.py --market US --start 2010 # from 2010
  python src/run_backtest_multibagger.py --no-accel               # disable accel filter

Universe note
-------------
Default universes cover liquid, well-known tickers that existed in 2008.
This introduces survivorship bias — stocks that failed are not included.
The report prints a prominent warning.  For real research, use a point-in-time
index membership database.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

from backtest_multibagger import run_mb_backtest, mb_backtest_report


# ── Default universes ─────────────────────────────────────────────────────────
# These were trading continuously since at least 2007.
_UNIVERSE: dict[str, list[str]] = {
    "IN": [
        # Large-cap India (Nifty 50 constituents, long history)
        "RELIANCE.NS", "TCS.NS",      "HDFCBANK.NS",  "INFY.NS",
        "ICICIBANK.NS","HINDUNILVR.NS","KOTAKBANK.NS", "SBIN.NS",
        "BHARTIARTL.NS","ITC.NS",      "AXISBANK.NS",  "LT.NS",
        "ASIANPAINT.NS","MARUTI.NS",   "TITAN.NS",     "BAJFINANCE.NS",
        "WIPRO.NS",    "HCLTECH.NS",   "SUNPHARMA.NS", "DRREDDY.NS",
        # Mid-cap with long history
        "PIDILITIND.NS","BERGEPAINT.NS","GODREJCP.NS",  "MARICO.NS",
        "DABUR.NS",    "LUPIN.NS",     "CIPLA.NS",     "DIVISLAB.NS",
        "MCDOWELL-N.NS","HAVELLS.NS",  "VOLTAS.NS",    "WHIRLPOOL.NS",
        "NMDC.NS",     "COALINDIA.NS", "NTPC.NS",      "POWERGRID.NS",
        "HAL.NS",      "BEL.NS",       "BHEL.NS",      "ABB.NS",
    ],
    "US": [
        # S&P 500 large-caps with data from 2007
        "AAPL", "MSFT", "GOOGL", "AMZN", "META",
        "BRK-B","JPM",  "JNJ",   "V",    "PG",
        "UNH",  "HD",   "MA",    "DIS",  "PYPL",
        "NVDA", "AMD",  "INTC",  "QCOM", "TXN",
        "XOM",  "CVX",  "COP",   "SLB",  "HAL",
        "BA",   "LMT",  "RTX",   "NOC",  "GD",
        "UNP",  "UPS",  "FDX",   "CSX",  "NSC",
        "WMT",  "TGT",  "COST",  "LOW",  "NKE",
    ],
    "EU": [
        "SAP.DE",  "ASML.AS", "LVMH.PA","SIE.DE",  "ALV.DE",
        "SAN.PA",  "BAS.DE",  "BAYN.DE","AIR.PA",  "TTE.PA",
        "RHM.DE",  "LDO.MI",  "ABI.BR", "DSM.AS",  "PHG.AS",
    ],
}


def _download_price_data(
    tickers: list[str],
    fetch_start: str,
    fetch_end: str,
) -> dict[str, pd.DataFrame]:
    """
    Download OHLCV + compute SMA_50 / SMA_200 for each ticker.
    Returns {ticker: DataFrame} — missing tickers are silently skipped.
    """
    try:
        import yfinance as yf
    except ImportError:
        print("  ERROR: yfinance is required.  pip install yfinance")
        sys.exit(1)

    data_map: dict[str, pd.DataFrame] = {}
    failed = 0

    print(f"  Downloading {len(tickers)} tickers ({fetch_start} → {fetch_end})...")

    for i, ticker in enumerate(tickers):
        try:
            df = yf.download(
                ticker,
                start=fetch_start,
                end=fetch_end,
                progress=False,
                auto_adjust=True,
            )
            if df is None or df.empty or len(df) < 210:
                failed += 1
                continue

            # Flatten MultiIndex columns if present
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            df = df[["Close"]].copy()
            df["SMA_50"]  = df["Close"].rolling(50).mean()
            df["SMA_200"] = df["Close"].rolling(200).mean()
            df.dropna(subset=["SMA_200"], inplace=True)

            if len(df) > 0:
                data_map[ticker] = df

        except Exception:
            failed += 1

        # Brief rate-limit delay
        if i > 0 and i % 10 == 0:
            time.sleep(0.5)
            print(f"    {i}/{len(tickers)} done, {failed} failed so far...")

    print(f"  Ready: {len(data_map)} tickers ({failed} failed/skipped)")
    return data_map


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Multi-Bagger Backtest from 2008",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--market",   default="IN", choices=["IN", "US", "EU"])
    parser.add_argument("--start",    default="2008", help="Backtest start year or date (default: 2008)")
    parser.add_argument("--end",      default=None,   help="Backtest end date (default: today)")
    parser.add_argument("--equity",   type=float, default=100_000, help="Starting capital")
    parser.add_argument("--slots",    type=int,   default=8,       help="Max positions (default: 8)")
    parser.add_argument("--review",   type=int,   default=63,      help="Entry review interval in days (default: 63)")
    parser.add_argument("--min-rank", type=float, default=0.40,    help="Entry rank cutoff — bottom X% excluded (default: 0.40)")
    parser.add_argument("--no-accel", action="store_true",          help="Disable the acceleration entry filter")
    parser.add_argument("--tickers",  nargs="+",  default=None,     help="Override default universe")
    args = parser.parse_args()

    market = args.market.upper()

    # Parse start date
    start_str = args.start.strip()
    if len(start_str) == 4:
        start_str = f"{start_str}-01-01"
    end_str = args.end or pd.Timestamp.now().strftime("%Y-%m-%d")

    # Download starts 7 months before backtest start to warm up SMA_200
    fetch_start = (pd.Timestamp(start_str) - pd.DateOffset(months=7)).strftime("%Y-%m-%d")
    fetch_end   = end_str

    tickers = args.tickers or _UNIVERSE.get(market, [])
    if not tickers:
        print(f"  No tickers for market '{market}'. Use --tickers to supply a list.")
        sys.exit(1)

    print(f"\n  Multi-Bagger Backtest  ──  {market}")
    print(f"  Universe: {len(tickers)} tickers")
    print(f"  Backtest period: {start_str} → {end_str}")
    print(f"  Slots: {args.slots}  |  Review every {args.review}d  |  Min rank: {args.min_rank:.0%}")
    print()

    data_map = _download_price_data(tickers, fetch_start, fetch_end)

    if len(data_map) < 3:
        print("  ERROR: Fewer than 3 tickers have valid price data. Aborting.")
        sys.exit(1)

    print(f"\n  Running backtest from {start_str}...")
    result = run_mb_backtest(
        market              = market,
        data_map            = data_map,
        start               = start_str,
        end                 = end_str,
        equity              = args.equity,
        max_positions       = args.slots,
        review_days         = args.review,
        min_entry_rank      = args.min_rank,
        require_acceleration= not args.no_accel,
    )

    print(mb_backtest_report(result))


if __name__ == "__main__":
    main()
