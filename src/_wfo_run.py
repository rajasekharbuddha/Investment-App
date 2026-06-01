"""WFO run — 8-combo grid, real-time fold logging, ~30-40 min on IN fixed watchlist."""
import os, sys, time, builtins as _bt
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "reports", "wfo_progress.txt")

_orig_print = _bt.print
def _live(msg="", *args, **kwargs):
    line = str(msg) + (" " + " ".join(str(a) for a in args) if args else "")
    _orig_print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")

_bt.print = _live
open(LOG, "w", encoding="utf-8").close()

_live(f"=== WFO START {time.strftime('%H:%M:%S')} ===")

from config import WATCHLIST, ACCOUNT
from data import fetch_and_cache
from indicators import calculate_all
from walk_forward import walk_forward, format_wfo_summary
import json

# 8-combo grid — 3 most impactful gate levers only
# Keeps total evals to ~64 (4 folds x 8 combos x 2) = ~33 min
GRID = {
    "sma_dist_min":  [0.003, 0.010],   # looser / tighter trend distance
    "volume_mult":   [0.45,  0.70],    # looser / tighter liquidity
    "macd_hist_eps": [-0.001, 0.0],    # allow slight neg MACD / strict positive
}

wl = {"IN": WATCHLIST["IN"]}
all_tickers = WATCHLIST["IN"]

_live(f"Fetching data for {len(all_tickers)} IN tickers...")
t0 = time.time()
data_map_raw, stats = fetch_and_cache(all_tickers, years=3)
_live(f"  {stats['succeeded']}/{stats['attempted']} tickers ok in {time.time()-t0:.0f}s")

_live("Computing indicators...")
data_map = {t: calculate_all(df) for t, df in data_map_raw.items()}
all_dates = sorted({d for df in data_map.values() for d in df.index})
_live(f"  {len(all_dates)} trading days  ({all_dates[0].date()} to {all_dates[-1].date()})")

n_combos = 2**3
n_folds  = (len(all_dates) - 252) // 252  # train=1yr, test=1yr
_live(f"\nWFO config: {n_combos} combos  train=252d(~1yr)  test=252d(~1yr)  ~{n_folds} folds")
_live(f"Estimated runtime: ~{n_combos * n_folds * 2 * 31 // 60} min")
_live("=" * 60)

result = walk_forward(
    data_map=data_map,
    watchlist=wl,
    all_dates=all_dates,
    train_size=252,
    test_size=252,
    anchored=False,
    grid=GRID,
    initial_equity=ACCOUNT["equity"],
    verbose=True,
)

summary = format_wfo_summary(result)
_live(summary)
_live(f"\n=== WFO DONE {time.strftime('%H:%M:%S')} ===")

out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "reports", "wfo_result.json")
with open(out, "w", encoding="utf-8") as f:
    json.dump(result, f, indent=2, default=str)
_live(f"Saved: {out}")
