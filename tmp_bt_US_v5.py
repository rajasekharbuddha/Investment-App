"""US Wave3-v5: tight trailing (HIGH=3.5), wider initial stop, dynamic universe."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
import config
# Tight trailing stop
config.MARKET_PARAMS["US"]["trail_mult"]["HIGH"]   = 3.5
config.MARKET_PARAMS["US"]["trail_mult"]["NORMAL"] = 5.0
config.MARKET_PARAMS["US"]["trail_mult"]["LOW"]    = 7.0
# Wider initial stop — avoid premature stop-out on volatile entries
config.MARKET_PARAMS["US"]["stop_mult"]["HIGH"]    = 3.5
config.MARKET_PARAMS["US"]["stop_mult"]["NORMAL"]  = 2.5
config.MARKET_PARAMS["US"]["stop_mult"]["LOW"]     = 2.5

from config import DYNAMIC_UNIVERSE
from universe import get_dynamic_watchlist
from backtest import run_backtest
from report import backtest_report

score_top_n = DYNAMIC_UNIVERSE.get("SCORE_TOP_N", {})
wl = get_dynamic_watchlist(["US"], score_top_n, max_age_days=7)
result = run_backtest(market="US", start="2016-01-01", end="2026-06-06",
                      initial_equity=100_000, watchlist_override=wl)
if "error" in result:
    print("ERROR:", result["error"]); sys.exit(1)
backtest_report(result, market_label="US_v5_trail3.5_stop3.5_dynamic")
