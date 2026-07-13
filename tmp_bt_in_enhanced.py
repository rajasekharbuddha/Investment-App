"""Temporary: IN backtest with enhanced MARKET_PARAMS (trail_mult 7→9, risk_pct 7%→9%, volume loosened)."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

import config

# IN ENHANCED: hold India's sustained PSU/defense trends longer, more conviction
config.MARKET_PARAMS["IN"]["trail_mult"]["NORMAL"] = 9.0
config.MARKET_PARAMS["IN"]["trail_mult"]["LOW"]    = 10.0
config.MARKET_PARAMS["IN"]["risk_pct"]["NORMAL"]   = 0.09
config.MARKET_PARAMS["IN"]["risk_pct"]["LOW"]      = 0.11
config.MARKET_PARAMS["IN"]["volume_mult"]          = 0.45

sys.argv = [
    "run_backtest.py",
    "--market", "IN",
    "--start",  "2016-01-01",
    "--end",    "2026-06-05",
]
from run_backtest import main
main()
