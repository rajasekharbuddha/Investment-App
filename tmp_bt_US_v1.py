"""US Wave1-v1: trail=12/14, risk=10%/12% — extend holds, more capital per trade."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
import config
config.MARKET_PARAMS["US"]["trail_mult"]["NORMAL"] = 12.0
config.MARKET_PARAMS["US"]["trail_mult"]["LOW"]    = 14.0
config.MARKET_PARAMS["US"]["risk_pct"]["NORMAL"]   = 0.10
config.MARKET_PARAMS["US"]["risk_pct"]["LOW"]      = 0.12

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
backtest_report(result, market_label="US_v1_trail12_risk10")
