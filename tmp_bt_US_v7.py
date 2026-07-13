"""US Wave4-v7: ultra-tight trailing (HIGH=2.5), 30% pos, dynamic."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
import config
# Ultra-tight trailing stop
config.MARKET_PARAMS["US"]["trail_mult"]["HIGH"]   = 2.5
config.MARKET_PARAMS["US"]["trail_mult"]["NORMAL"] = 3.5
config.MARKET_PARAMS["US"]["trail_mult"]["LOW"]    = 5.0
# Wide initial stop
config.MARKET_PARAMS["US"]["stop_mult"]["HIGH"]    = 4.0
config.MARKET_PARAMS["US"]["stop_mult"]["NORMAL"]  = 3.0
config.MARKET_PARAMS["US"]["stop_mult"]["LOW"]     = 2.5
# Larger positions
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
backtest_report(result, market_label="US_v7_trail2.5_stop4.0_pos30pct_dynamic")
