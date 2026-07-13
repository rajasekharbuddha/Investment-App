"""IN Wave1-v2: trail=11/12, risk=11%/13% — aggressive hold + sizing."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
import config
config.MARKET_PARAMS["IN"]["trail_mult"]["NORMAL"] = 11.0
config.MARKET_PARAMS["IN"]["trail_mult"]["LOW"]    = 12.0
config.MARKET_PARAMS["IN"]["risk_pct"]["NORMAL"]   = 0.11
config.MARKET_PARAMS["IN"]["risk_pct"]["LOW"]      = 0.13

from config import DYNAMIC_UNIVERSE, ACCOUNT
from universe import get_dynamic_watchlist
from backtest import run_backtest
from report import backtest_report

score_top_n = DYNAMIC_UNIVERSE.get("SCORE_TOP_N", {})
wl = get_dynamic_watchlist(["IN"], score_top_n, max_age_days=7)
result = run_backtest(market="IN", start="2016-01-01", end="2026-06-06",
                      initial_equity=100_000, watchlist_override=wl)
if "error" in result:
    print("ERROR:", result["error"]); sys.exit(1)
backtest_report(result, market_label="IN_v2_trail11_risk11")
