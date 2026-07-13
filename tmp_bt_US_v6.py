"""US Wave3-v6: tight trailing (HIGH=3.0) + bigger positions (30%), dynamic."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
import config
# Tight trailing stop
config.MARKET_PARAMS["US"]["trail_mult"]["HIGH"]   = 3.0
config.MARKET_PARAMS["US"]["trail_mult"]["NORMAL"] = 4.5
config.MARKET_PARAMS["US"]["trail_mult"]["LOW"]    = 6.5
# Wider initial stop
config.MARKET_PARAMS["US"]["stop_mult"]["HIGH"]    = 4.0
config.MARKET_PARAMS["US"]["stop_mult"]["NORMAL"]  = 3.0
config.MARKET_PARAMS["US"]["stop_mult"]["LOW"]     = 2.5
# Bigger positions
config.RISK["MAX_POSITION_SIZE_PCT"]       = 0.30
config.RISK["MAX_TOTAL_CONCENTRATION_PCT"] = 0.40

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
backtest_report(result, market_label="US_v6_trail3.0_stop4.0_pos30pct_dynamic")
