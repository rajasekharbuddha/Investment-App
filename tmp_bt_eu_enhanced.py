"""Temporary: EU backtest with enhanced MARKET_PARAMS (trail_mult 7→9, risk_pct 5%→7%, sma_dist_min loosened)."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

import config

# EU ENHANCED: hold trends longer, more capital per signal, earlier entries
config.MARKET_PARAMS["EU"]["trail_mult"]["NORMAL"] = 9.0
config.MARKET_PARAMS["EU"]["trail_mult"]["LOW"]    = 9.0
config.MARKET_PARAMS["EU"]["risk_pct"]["NORMAL"]   = 0.07
config.MARKET_PARAMS["EU"]["risk_pct"]["LOW"]      = 0.08
config.MARKET_PARAMS["EU"]["sma_dist_min"]         = 0.005

sys.argv = [
    "run_backtest.py",
    "--market", "EU",
    "--start",  "2016-01-01",
    "--end",    "2026-06-05",
]
from run_backtest import main
main()
