"""
semimap_prices.py
=================
Refresh the point-in-time prices in semimap/nodes.json from yfinance and
stamp meta.pricesAsOf, so the Semiconductor Dependency Map shows an honest
"as of" date instead of pretending to be real-time.

Usage
-----
  python src/semimap_prices.py            # update semimap/nodes.json in place
  python src/semimap_prices.py --dry-run  # print the changes only
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Callable, Optional

NODES_FILE = Path(__file__).parent.parent / "semimap" / "nodes.json"


def _yf_last_close(ticker: str) -> Optional[float]:
    import yfinance as yf
    hist = yf.Ticker(ticker).history(period="5d", auto_adjust=False)
    if hist is None or hist.empty:
        return None
    return float(hist["Close"].dropna().iloc[-1])


def refresh_prices(
    data: dict,
    fetch: Callable[[str], Optional[float]] = _yf_last_close,
    today: Optional[date] = None,
) -> list[str]:
    """
    Update each listed node's price in place. Nodes sharing a ticker
    (e.g. Samsung brand + foundry) are fetched once. Returns a list of
    tickers that could not be refreshed; their old price is kept.
    """
    quotes: dict[str, Optional[float]] = {}
    for n in data["nodes"]:
        t = n.get("ticker")
        if t and t not in quotes:
            try:
                quotes[t] = fetch(t)
            except Exception:
                quotes[t] = None

    failed = sorted(t for t, q in quotes.items() if q is None)
    for n in data["nodes"]:
        q = quotes.get(n.get("ticker") or "")
        if q is not None:
            n["price"] = round(q, 2)
            n.pop("priceApprox", None)

    if len(failed) < len(quotes):
        data["meta"]["pricesAsOf"] = (today or date.today()).isoformat()
        data["meta"]["pricesSource"] = "yfinance close" + (
            f", {len(failed)} kept from earlier" if failed else "")
    return failed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Print changes, don't write")
    args = parser.parse_args()

    data   = json.loads(NODES_FILE.read_text(encoding="utf-8"))
    before = {n["id"]: n.get("price") for n in data["nodes"]}
    failed = refresh_prices(data)

    for n in data["nodes"]:
        if n.get("ticker") and before[n["id"]] != n.get("price"):
            print(f"  {n['ticker']:<10} {before[n['id']]} -> {n['price']} {n['currency']}")
    if failed:
        print(f"  Not refreshed (old price kept): {', '.join(failed)}")

    if args.dry_run:
        print("  Dry run: nodes.json not written")
        return
    NODES_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"  Wrote {NODES_FILE}  (as of {data['meta']['pricesAsOf']})")


if __name__ == "__main__":
    main()
