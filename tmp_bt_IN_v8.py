"""IN Wave4-v8: tight trailing (HIGH=3.5), TOP-100 scored universe, 30% pos."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
import config
# Tight trailing stop
config.MARKET_PARAMS["IN"]["trail_mult"]["HIGH"]   = 3.5
config.MARKET_PARAMS["IN"]["trail_mult"]["NORMAL"] = 4.5
config.MARKET_PARAMS["IN"]["trail_mult"]["LOW"]    = 5.5
# Wide initial stop
config.MARKET_PARAMS["IN"]["stop_mult"]["HIGH"]    = 4.0
config.MARKET_PARAMS["IN"]["stop_mult"]["NORMAL"]  = 3.0
config.MARKET_PARAMS["IN"]["stop_mult"]["LOW"]     = 2.5
# Bigger positions
config.RISK["MAX_POSITION_SIZE_PCT"]       = 0.30
config.RISK["MAX_TOTAL_CONCENTRATION_PCT"] = 0.40
# Restrict to top-100 scored stocks (higher quality entry pool)
config.DYNAMIC_UNIVERSE["SCORE_TOP_N"]["IN"] = 100

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
backtest_report(result, market_label="IN_v8_trail3.5_stop4.0_top100_pos30pct")
