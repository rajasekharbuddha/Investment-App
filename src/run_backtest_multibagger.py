"""
run_backtest_multibagger.py
===========================
CLI runner for the multi-bagger backtest from 2008.

Loads daily closes from data/price_cache/<TICKER>.csv when present, otherwise
downloads them with yfinance and caches them. Fetches 14 months before the
start date to warm up SMA_200 and momentum. Cache CSV format: Date,Close.

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

from backtest_multibagger import BENCHMARK_TICKER, run_mb_backtest, mb_backtest_report


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


CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "price_cache"


def _load_closes(ticker: str, fetch_start: str, fetch_end: str) -> pd.Series | None:
    """Close prices from data/price_cache/<ticker>.csv, else yfinance (then cached)."""
    path = CACHE_DIR / f"{ticker}.csv"
    if path.exists():
        s = pd.read_csv(path, index_col=0, parse_dates=True).iloc[:, 0]
        if s.index.max() >= pd.Timestamp(fetch_end) - pd.Timedelta(days=5):
            return s.loc[fetch_start:fetch_end].dropna()
    try:
        import yfinance as yf
        df = yf.download(ticker, start=fetch_start, end=fetch_end,
                         progress=False, auto_adjust=True)
    except Exception:
        return None
    if df is None or df.empty:
        return None
    s = df["Close"].squeeze().dropna()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    s.rename("Close").to_csv(path)
    return s


def _download_price_data(
    tickers: list[str],
    fetch_start: str,
    fetch_end: str,
) -> dict[str, pd.DataFrame]:
    """{ticker: DataFrame[Close, SMA_50, SMA_200]}; tickers without data are skipped."""
    data_map: dict[str, pd.DataFrame] = {}
    failed: list[str] = []
    print(f"  Loading {len(tickers)} tickers ({fetch_start} → {fetch_end})...")
    for i, ticker in enumerate(tickers):
        s = _load_closes(ticker, fetch_start, fetch_end)
        if s is None or len(s) < 210:
            failed.append(ticker)
            continue
        df = pd.DataFrame({"Close": s})
        df["SMA_50"]  = df["Close"].rolling(50).mean()
        df["SMA_200"] = df["Close"].rolling(200).mean()
        df.dropna(subset=["SMA_200"], inplace=True)
        data_map[ticker] = df
        if i % 10 == 9:
            time.sleep(0.5)
    print(f"  Ready: {len(data_map)} tickers" + (f"  (no data: {', '.join(failed)})" if failed else ""))
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
    parser.add_argument("--review",   type=int,   default=21,      help="Entry review interval in days (default: 21)")
    parser.add_argument("--min-rank", type=float, default=0.10,    help="Entry rank cutoff — bottom X%% excluded (default: 0.10)")
    parser.add_argument("--stop-loss", type=float, default=0.0,    help="Hard stop below entry (0 = off)")
    parser.add_argument("--no-accel", action="store_true",          help="Disable the acceleration entry filter")
    parser.add_argument("--tickers",  nargs="+",  default=None,     help="Override default universe")
    args = parser.parse_args()

    market = args.market.upper()

    # Parse start date
    start_str = args.start.strip()
    if len(start_str) == 4:
        start_str = f"{start_str}-01-01"
    end_str = args.end or pd.Timestamp.now().strftime("%Y-%m-%d")

    # 14 months of history before the start: SMA_200 needs ~10 months, momentum 3 more
    fetch_start = (pd.Timestamp(start_str) - pd.DateOffset(months=14)).strftime("%Y-%m-%d")
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

    bench_ticker = BENCHMARK_TICKER.get(market, "^NSEI")
    benchmark = _load_closes(bench_ticker, fetch_start, fetch_end)

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
        stop_loss           = args.stop_loss,
        benchmark           = benchmark,
    )

    print(mb_backtest_report(result))


if __name__ == "__main__":
    main()
