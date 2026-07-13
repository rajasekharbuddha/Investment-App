"""US Wave2-v3: boost ALL regimes, static watchlist."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
import config
config.MARKET_PARAMS["US"]["trail_mult"]["HIGH"]   = 10.0
config.MARKET_PARAMS["US"]["trail_mult"]["NORMAL"] = 12.0
config.MARKET_PARAMS["US"]["trail_mult"]["LOW"]    = 14.0
config.MARKET_PARAMS["US"]["risk_pct"]["HIGH"]     = 0.09
config.MARKET_PARAMS["US"]["risk_pct"]["NORMAL"]   = 0.10
config.MARKET_PARAMS["US"]["risk_pct"]["LOW"]      = 0.12

from backtest import run_backtest
from report import backtest_report

result = run_backtest(market="US", start="2016-01-01", end="2026-06-06",
                      initial_equity=100_000)
if "error" in result:
    print("ERROR:", result["error"]); sys.exit(1)
backtest_report(result, market_label="US_v3_static_high10_risk9")
