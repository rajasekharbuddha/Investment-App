"""IN Wave2-v3: boost ALL regimes (HIGH+NORMAL+LOW), static watchlist."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
import config
# HIGH regime is ATR 2-4% — where most IN trades live
config.MARKET_PARAMS["IN"]["trail_mult"]["HIGH"]   = 9.0
config.MARKET_PARAMS["IN"]["trail_mult"]["NORMAL"] = 10.0
config.MARKET_PARAMS["IN"]["trail_mult"]["LOW"]    = 11.0
config.MARKET_PARAMS["IN"]["risk_pct"]["HIGH"]     = 0.09
config.MARKET_PARAMS["IN"]["risk_pct"]["NORMAL"]   = 0.10
config.MARKET_PARAMS["IN"]["risk_pct"]["LOW"]      = 0.12

from config import WATCHLIST
from backtest import run_backtest
from report import backtest_report

# Use static curated watchlist (previously gave 14.05% CAGR)
result = run_backtest(market="IN", start="2016-01-01", end="2026-06-06",
                      initial_equity=100_000)
if "error" in result:
    print("ERROR:", result["error"]); sys.exit(1)
backtest_report(result, market_label="IN_v3_static_high9_risk9")
