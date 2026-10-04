"""
test_graham.py
==============
Unit tests for Graham defensive-investor checks, the yfinance D/E unit
conversion, and legacy D/E exit thresholds. No network — yfinance is stubbed.
"""

import sys
import types
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from fundamental import (
    GRAHAM, _CACHE_SCHEMA, _is_fresh, fetch_fundamentals,
    graham_check, graham_number,
)
from run_longterm import check_lt_exit, compute_exit_thresholds


def _data(**kwargs) -> dict:
    """A stock that passes every Graham test."""
    d = dict(
        sector="Industrials",
        pe=12.0, pb=1.5,                     # P/E x P/B = 18
        current_ratio=2.5,
        current_assets=500.0, current_liabilities=200.0,  # NCA = 300
        long_term_debt=100.0,
        eps_ttm=10.0, book_value_ps=80.0,    # Graham number = 134.16
        price=100.0,
    )
    d.update(kwargs)
    return d


class TestGrahamNumber:
    def test_formula(self):
        assert graham_number(_data()) == pytest.approx((22.5 * 10 * 80) ** 0.5)

    def test_negative_eps_is_none(self):
        assert graham_number(_data(eps_ttm=-1.0)) is None

    def test_missing_book_is_none(self):
        assert graham_number(_data(book_value_ps=None)) is None


class TestGrahamCheck:
    def test_all_pass(self):
        g = graham_check(_data())
        assert g["passed"] is True
        assert all(g["tests"].values())
        assert g["margin_of_safety"] == pytest.approx((134.164 - 100) / 134.164, rel=1e-3)

    def test_low_current_ratio_fails(self):
        g = graham_check(_data(current_ratio=1.5))
        assert g["tests"]["current_ratio"] is False
        assert g["passed"] is False

    def test_debt_above_net_current_assets_fails(self):
        g = graham_check(_data(long_term_debt=301.0))
        assert g["tests"]["debt_vs_nca"] is False
        assert g["passed"] is False

    def test_expensive_fails(self):
        g = graham_check(_data(pe=20.0, pb=2.0))   # 40 > 22.5
        assert g["tests"]["pe_x_pb"] is False
        assert g["passed"] is False

    def test_threshold_is_inclusive(self):
        assert graham_check(_data(pe=15.0, pb=1.5))["tests"]["pe_x_pb"] is True
        assert graham_check(_data(current_ratio=GRAHAM["min_current_ratio"]))["passed"] is True

    def test_missing_data_fails(self):
        g = graham_check(_data(long_term_debt=None))
        assert g["tests"]["debt_vs_nca"] is None
        assert g["passed"] is False

    def test_financials_skip_balance_sheet_tests(self):
        g = graham_check(_data(sector="Financial Services",
                               current_ratio=None, long_term_debt=None))
        assert g["tests"]["current_ratio"] is None
        assert g["tests"]["debt_vs_nca"] is None
        assert g["applicable"] == ["pe_x_pb"]
        assert g["passed"] is True

    def test_price_above_graham_number_negative_mos(self):
        g = graham_check(_data(price=200.0))
        assert g["margin_of_safety"] < 0


class _FakeTicker:
    info = {
        "debtToEquity": 45.3, "trailingPE": 12.0, "priceToBook": 1.5,
        "currentPrice": 100.0, "trailingEps": 10.0, "bookValue": 80.0,
        "sector": "Industrials",
    }
    financials = pd.DataFrame()
    balance_sheet = pd.DataFrame(
        {pd.Timestamp("2025-03-31"): [500.0, 200.0, 100.0],
         pd.Timestamp("2024-03-31"): [1.0, 1.0, 1.0]},
        index=["Current Assets", "Current Liabilities", "Long Term Debt"],
    )

    def __init__(self, ticker):
        pass


class TestFetchFundamentals:
    @pytest.fixture(autouse=True)
    def _stub_yfinance(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "yfinance",
                            types.SimpleNamespace(Ticker=_FakeTicker))

    def test_debt_equity_converted_from_percent(self):
        fd = fetch_fundamentals("TEST.NS", use_cache=False)
        assert fd["debt_equity"] == pytest.approx(0.453)

    def test_balance_sheet_uses_latest_column(self):
        fd = fetch_fundamentals("TEST.NS", use_cache=False)
        assert fd["current_assets"] == 500.0
        assert fd["current_liabilities"] == 200.0
        assert fd["long_term_debt"] == 100.0
        assert fd["current_ratio"] == pytest.approx(2.5)   # derived: no currentRatio in info
        assert graham_check(fd)["passed"] is True


    def test_debt_free_without_ltd_row(self, monkeypatch):
        class DebtFree(_FakeTicker):
            info = {**_FakeTicker.info, "totalDebt": 0, "debtToEquity": 0.0}
            balance_sheet = _FakeTicker.balance_sheet.drop("Long Term Debt")
        monkeypatch.setitem(sys.modules, "yfinance",
                            types.SimpleNamespace(Ticker=DebtFree))
        fd = fetch_fundamentals("TEST.NS", use_cache=False)
        assert fd["long_term_debt"] == 0.0
        assert graham_check(fd)["tests"]["debt_vs_nca"] is True

    def test_missing_ltd_row_with_debt_stays_unknown(self, monkeypatch):
        class NoRow(_FakeTicker):
            info = {**_FakeTicker.info, "totalDebt": 5e9}
            balance_sheet = _FakeTicker.balance_sheet.drop("Long Term Debt")
        monkeypatch.setitem(sys.modules, "yfinance",
                            types.SimpleNamespace(Ticker=NoRow))
        fd = fetch_fundamentals("TEST.NS", use_cache=False)
        assert fd["long_term_debt"] is None


class TestCacheSchema:
    def test_old_entries_are_stale(self):
        from datetime import datetime
        now = datetime.now().isoformat()
        assert _is_fresh({"_cached_at": now}) is False
        assert _is_fresh({"_cached_at": now, "_schema": _CACHE_SCHEMA}) is True


class TestLegacyDeThreshold:
    def _pos(self, th):
        return {"exit_thresholds": th}

    def test_new_threshold_in_ratio_units(self):
        th = compute_exit_thresholds({"debt_equity": 1.5})
        assert th["de_max"] == 3.0 and th["de_units"] == "ratio"
        assert check_lt_exit(self._pos(th), fund_data={"debt_equity": 3.1})["verdict"] == "SELL"
        assert check_lt_exit(self._pos(th), fund_data={"debt_equity": 2.9})["verdict"] == "HOLD"

    def test_legacy_percent_threshold_converted(self):
        # Entered at D/E 150 (%), old code stored de_max = 300
        pos = self._pos({"de_max": 300.0})
        assert check_lt_exit(pos, fund_data={"debt_equity": 3.1})["verdict"] == "SELL"
        assert check_lt_exit(pos, fund_data={"debt_equity": 2.9})["verdict"] == "HOLD"
        assert pos["exit_thresholds"]["de_max"] == 300.0   # stored position not mutated

    def test_legacy_floor_kept(self):
        pos = self._pos({"de_max": 2.0})
        assert check_lt_exit(pos, fund_data={"debt_equity": 1.9})["verdict"] == "HOLD"
        assert check_lt_exit(pos, fund_data={"debt_equity": 2.1})["verdict"] == "SELL"
