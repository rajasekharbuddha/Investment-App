"""IN Wave3-v5: tight trailing (HIGH=3.5), wide initial stop (HIGH=4.5), dynamic universe."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
import config
# Tight trailing stop — lock profits fast in high-vol regime
config.MARKET_PARAMS["IN"]["trail_mult"]["HIGH"]   = 3.5
config.MARKET_PARAMS["IN"]["trail_mult"]["NORMAL"] = 4.5
config.MARKET_PARAMS["IN"]["trail_mult"]["LOW"]    = 5.5
# Wider initial stop — let trades breathe, fewer premature exits
config.MARKET_PARAMS["IN"]["stop_mult"]["HIGH"]    = 4.0
config.MARKET_PARAMS["IN"]["stop_mult"]["NORMAL"]  = 3.0
config.MARKET_PARAMS["IN"]["stop_mult"]["LOW"]     = 2.5

from config import DYNAMIC_UNIVERSE
from universe import get_dynamic_watchlist
from backtest import run_backtest
from report import backtest_report

score_top_n = DYNAMIC_UNIVERSE.get("SCORE_TOP_N", {})
wl = get_dynamic_watchlist(["IN"], score_top_n, max_age_days=7)
result = run_backtest(market="IN", start="2016-01-01", end="2026-06-06",
                      initial_equity=100_000, watchlist_override=wl)
if "error" in result:
    print("ERROR:", result["error"]); sys.exit(1)
backtest_report(result, market_label="IN_v5_trail3.5_stop4.0_dynamic")
