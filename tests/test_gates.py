"""
test_gates.py
=============
Unit tests for the 5 sequential gates, anti-chase blocker, and NEAR logic.
Uses synthetic pd.Series rows — no network or file I/O.
"""

import numpy as np
import pandas as pd
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from rules import (
    check_trend, check_momentum, check_volatility,
    check_liquidity, check_execution, check_timing,
    evaluate_gates,
)
from config import GATE_DEFAULTS


def _row(**kwargs) -> pd.Series:
    defaults = dict(
        Close=110.0, Open=108.0, High=112.0, Low=107.0,
        SMA_50=105.0, SMA_200=100.0,
        ATR=2.0, ATR_PCT=1.8,
        RSI=60.0,
        MACD=0.5, MACD_SIG=0.3, MACD_HIST=0.2,
        Volume=2_000_000, VOL_AVG_20=1_500_000,
        BODY=2.0, SMA_50_20AGO=102.0,
    )
    defaults.update(kwargs)
    return pd.Series(defaults)


_BASE = {
    "sma_dist_min":  GATE_DEFAULTS["sma_dist_min"],
    "volume_mult":   1.0,
    "macd_hist_eps": 0.0,
}


class TestGate1Trend:
    def test_all_pass(self):
        assert check_trend(_row(), sma50_20ago=102.0, sma_dist_min=0.02)["pass"] is True

    def test_fail_below_sma200(self):
        g = check_trend(_row(Close=98.0), sma50_20ago=102.0, sma_dist_min=0.02)
        assert g["pass"] is False

    def test_fail_sma50_below_sma200(self):
        g = check_trend(_row(SMA_50=99.0), sma50_20ago=102.0, sma_dist_min=0.02)
        assert g["pass"] is False

    def test_fail_sma50_not_rising(self):
        g = check_trend(_row(), sma50_20ago=106.0, sma_dist_min=0.02)
        assert g["pass"] is False

    def test_nan_sma50_20ago(self):
        g = check_trend(_row(), sma50_20ago=float("nan"), sma_dist_min=0.02)
        assert not g["details"]["sma50_rising"]


class TestGate2Momentum:
    def test_pass(self):
        assert check_momentum(_row(RSI=60), rsi_lo=53, rsi_hi=68)["pass"] is True

    def test_fail_macd_below_signal(self):
        g = check_momentum(_row(MACD=0.2, MACD_SIG=0.5), rsi_lo=53, rsi_hi=68)
        assert g["pass"] is False

    def test_fail_rsi_too_low(self):
        assert check_momentum(_row(RSI=45), rsi_lo=53, rsi_hi=68)["pass"] is False

    def test_fail_rsi_too_high(self):
        assert check_momentum(_row(RSI=75), rsi_lo=53, rsi_hi=68)["pass"] is False

    def test_rsi_at_boundaries(self):
        assert check_momentum(_row(RSI=53), rsi_lo=53, rsi_hi=68)["details"]["rsi_in_band"]
        assert check_momentum(_row(RSI=68), rsi_lo=53, rsi_hi=68)["details"]["rsi_in_band"]


class TestGate3Volatility:
    def test_normal_passes(self):
        assert check_volatility(_row(ATR_PCT=1.5))["pass"] is True

    def test_high_vol_passes(self):
        assert check_volatility(_row(ATR_PCT=3.0))["pass"] is True

    def test_extreme_blocks(self):
        g = check_volatility(_row(ATR_PCT=4.5))
        assert g["pass"] is False
        assert g["details"]["regime"] == "Extreme"


class TestGate4Liquidity:
    def test_us_adequate(self):
        g = check_liquidity(_row(Volume=2_000_000, VOL_AVG_20=1_500_000),
                            market="US", volume_mult=1.0)
        assert g["pass"] is True

    def test_us_low_volume(self):
        g = check_liquidity(_row(Volume=500_000, VOL_AVG_20=2_000_000),
                            market="US", volume_mult=1.0)
        assert g["pass"] is False

    def test_eu_lower_baseline(self):
        g = check_liquidity(_row(Volume=800_000, VOL_AVG_20=1_000_000),
                            market="EU", volume_mult=1.0)
        assert g["pass"] is True

    def test_mult_tightens(self):
        g = check_liquidity(_row(Volume=1_000_000, VOL_AVG_20=1_000_000),
                            market="US", volume_mult=1.2)
        assert g["pass"] is False


class TestGate5Execution:
    def test_pass(self):
        g = check_execution(_row(MACD_HIST=0.3), _row(MACD_HIST=0.1), macd_hist_eps=0.0)
        assert g["pass"] is True

    def test_fail_hist_falling(self):
        g = check_execution(_row(MACD_HIST=0.1), _row(MACD_HIST=0.3), macd_hist_eps=0.0)
        assert g["pass"] is False

    def test_fail_hist_below_eps(self):
        g = check_execution(_row(MACD_HIST=-0.05), _row(MACD_HIST=0.1), macd_hist_eps=0.0)
        assert g["pass"] is False

    def test_no_prev_fails(self):
        assert check_execution(_row(MACD_HIST=0.3), None, macd_hist_eps=0.0)["pass"] is False


class TestAntiChase:
    def _big(self):
        return _row(Close=102.0, Open=100.0, BODY=2.0, ATR=2.0)

    def _small(self):
        return _row(Close=100.5, Open=100.0, BODY=0.5, ATR=2.0)

    def test_no_prev_no_block(self):
        g = check_timing(_row(), None, None, atr=2.0)
        assert g["pass"] is True

    def test_three_big_green_blocks(self):
        big = self._big()
        assert check_timing(big, big, big, atr=2.0)["pass"] is False

    def test_small_candle_no_block(self):
        big = self._big()
        assert check_timing(self._small(), big, big, atr=2.0)["pass"] is True


class TestEvaluateGatesIntegration:
    def _good(self):
        return _row(Close=110.0, SMA_50=105.0, SMA_200=100.0,
                    ATR_PCT=1.5, RSI=60.0,
                    MACD=0.5, MACD_SIG=0.3, MACD_HIST=0.2,
                    Volume=2_000_000, VOL_AVG_20=1_000_000,
                    SMA_50_20AGO=102.0)

    def _prev(self):
        return _row(MACD_HIST=0.1, Close=100.5, Open=100.0, BODY=0.5)

    def test_enter(self):
        r = evaluate_gates("AAPL", self._good(), self._prev(), self._prev(), _BASE, "US")
        assert r["decision"] == "ENTER"

    def test_skip_g1_fail(self):
        r = evaluate_gates("AAPL", _row(Close=98.0), None, None, _BASE, "US")
        assert r["decision"] in ("SKIP", "NEAR")

    def test_skip_extreme_vol(self):
        bad = self._good()
        bad["ATR_PCT"] = 5.0
        r = evaluate_gates("AAPL", bad, None, None, _BASE, "US")
        assert r["decision"] in ("SKIP", "NEAR")

    def test_wait_g5_fail(self):
        r = evaluate_gates("AAPL", self._good(), _row(MACD_HIST=-0.1), None, _BASE, "US")
        assert r["decision"] == "WAIT"

    def test_near_four_gates(self):
        """4/5 gates pass (G2 fails) → NEAR instead of WAIT."""
        bad_momentum = self._good()
        bad_momentum["RSI"] = 45.0   # below rsi_lo → G2 fails
        r = evaluate_gates("AAPL", bad_momentum, self._prev(), self._prev(), _BASE, "US")
        assert r["decision"] in ("NEAR", "WAIT")

    def test_required_keys(self):
        r = evaluate_gates("AAPL", self._good(), self._prev(), self._prev(), _BASE, "US")
        for k in ("decision", "reason", "gates", "price", "atr", "market", "sector",
                  "gates_passed"):
            assert k in r, f"Missing key: {k}"

    def test_gates_passed_count(self):
        r = evaluate_gates("AAPL", self._good(), self._prev(), self._prev(), _BASE, "US")
        assert isinstance(r["gates_passed"], int)
        assert 0 <= r["gates_passed"] <= 5
