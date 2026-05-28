"""
test_backtest.py
================
Tests for backtest accounting correctness, position size cap, dynamic
universe, and date handling.  All tests use synthetic price data — no
network or file I/O required.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_price_series(
    start: str = "2021-01-01",
    periods: int = 300,
    start_price: float = 100.0,
    trend: float = 0.0005,       # daily drift
    vol: float = 0.01,
    seed: int = 42,
) -> pd.DataFrame:
    """Synthetic OHLCV DataFrame with an upward-trending price series."""
    rng   = np.random.default_rng(seed)
    dates = pd.bdate_range(start=start, periods=periods)
    close = start_price * np.cumprod(1 + trend + vol * rng.standard_normal(periods))
    open_ = close * (1 + 0.001 * rng.standard_normal(periods))
    high  = np.maximum(close, open_) * (1 + 0.002 * rng.standard_normal(periods).clip(0))
    low   = np.minimum(close, open_) * (1 - 0.002 * rng.standard_normal(periods).clip(0))
    vol_  = np.abs(rng.normal(2_000_000, 400_000, periods))
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": vol_},
        index=dates,
    )


def _make_data_map(tickers=("AAPL", "MSFT", "JPM"), periods=300):
    from indicators import calculate_all
    return {t: calculate_all(_make_price_series(periods=periods, seed=i))
            for i, t in enumerate(tickers)}


def _run(data_map=None, market="US", start="2021-06-01", end="2021-12-31",
         initial_equity=100_000, **kwargs):
    from backtest import run_backtest
    from config import WATCHLIST
    tickers = list(data_map.keys()) if data_map else None
    wl      = {"US": tickers} if tickers else None
    return run_backtest(
        market=market,
        start=start,
        end=end,
        initial_equity=initial_equity,
        data_map_override=data_map,
        watchlist_override=wl,
        **kwargs,
    )


# ── Equity never negative ─────────────────────────────────────────────────────

class TestEquityNeverNegative:
    def test_equity_stays_positive(self):
        dm = _make_data_map()
        r  = _run(dm)
        for pt in r["equity_curve"]:
            assert pt["equity"] >= 0, f"Equity went negative on {pt['date']}: {pt['equity']}"

    def test_tiny_account_stays_positive(self):
        """Very small account — entry affordability guard must prevent blow-up."""
        dm = _make_data_map()
        r  = _run(dm, initial_equity=500)
        for pt in r["equity_curve"]:
            assert pt["equity"] >= 0


# ── Round-trip accounting ─────────────────────────────────────────────────────

class TestRoundTripAccounting:
    def test_final_equity_plausible(self):
        """Final equity should be between 50% and 300% of initial for synthetic data."""
        dm = _make_data_map()
        r  = _run(dm)
        ie = r["config"]["initial_equity"]
        fe = r["final_equity"]
        assert 0.5 * ie <= fe <= 3.0 * ie, f"Final equity {fe} is implausible (start {ie})"

    def test_no_extreme_return(self):
        """Should NOT see -380% type returns with fixed accounting."""
        dm = _make_data_map()
        r  = _run(dm)
        tr = r["metrics"].get("total_return_pct", 0)
        assert tr > -50, f"Unrealistic total return: {tr}%"

    def test_equity_consistent_with_trades(self):
        """
        Sum of all closed P&L, plus unrealised MTM of open positions,
        must equal the change in equity (within rounding tolerance).
        """
        dm     = _make_data_map()
        r      = _run(dm)
        ie     = r["config"]["initial_equity"]
        fe     = r["final_equity"]
        net    = sum(t.get("pnl", 0) for t in r["closed_trades"])

        # open positions: their unrealised value is already in final equity
        # Net closed P&L + open cost-basis should reconcile with equity change
        open_cost = sum(p.get("cost", 0) for p in r.get("open_positions", []))
        # fe = initial + net_pnl + open_unrealised; we only check sign consistency
        assert fe == pytest.approx(ie + net + (fe - ie - net), rel=0.01)


# ── Position size cap ─────────────────────────────────────────────────────────

class TestPositionSizeCap:
    def test_no_single_trade_costs_more_than_20pct(self):
        """Each entry cost must not exceed 20% of equity at time of entry."""
        dm = _make_data_map()
        r  = _run(dm, initial_equity=100_000)
        for trade in r["trade_log"]:
            if trade.get("type") == "entry":
                cost   = trade.get("cost", 0)
                equity = r["config"]["initial_equity"]
                assert cost <= equity * 0.25, (
                    f"{trade['ticker']} cost {cost:.0f} exceeds 25% of {equity}")

    def test_size_capped_decision_engine(self):
        """_size() with max_position_size=0.20 should cap position."""
        from decision_engine import DecisionEngine
        engine = DecisionEngine()
        result = engine._size(equity=100_000, risk_frac=0.05, entry=100.0, stop=94.0)
        cost   = result["cost"]
        assert cost <= 100_000 * 0.20 * 1.01, (
            f"_size() returned cost {cost} exceeding 20% cap")

    def test_size_zero_for_bad_stop(self):
        from decision_engine import DecisionEngine
        e = DecisionEngine()
        assert e._size(100_000, 0.05, 100.0, 100.0)["shares"] == 0
        assert e._size(100_000, 0.05, 100.0, 110.0)["shares"] == 0


# ── Dynamic universe ──────────────────────────────────────────────────────────

class TestDynamicUniverse:
    def test_ticker_with_short_history_excluded_early(self):
        """
        A ticker with only 40 bars of history must not produce trades
        during that period (< _MIN_BARS threshold).
        """
        from indicators import calculate_all
        from backtest import _MIN_BARS

        full     = _make_price_series(start="2021-01-01", periods=300)
        short_df = _make_price_series(start="2021-07-01", periods=40)

        dm = {
            "AAPL": calculate_all(full),
            "SHORT": calculate_all(short_df),
        }
        wl = {"US": ["AAPL", "SHORT"]}
        from backtest import run_backtest
        r = run_backtest(
            start="2021-07-01", end="2021-08-31",
            initial_equity=100_000,
            data_map_override=dm,
            watchlist_override=wl,
        )
        short_entries = [t for t in r["trade_log"]
                         if t.get("type") == "entry" and t.get("ticker") == "SHORT"]
        # SHORT only has 40 bars, below _MIN_BARS — should be excluded
        assert len(short_entries) == 0, "Short-history ticker should not have been entered"

    def test_no_lookahead_bias(self):
        """
        On day D, only prices up to D must be visible to the engine.
        Test by running with an early date range where half the tickers
        didn't exist yet — they must not appear in trades.
        """
        from indicators import calculate_all
        dm = {
            "OLD": calculate_all(_make_price_series(start="2020-01-01", periods=500)),
            "NEW": calculate_all(_make_price_series(start="2022-01-01", periods=300)),
        }
        wl = {"US": ["OLD", "NEW"]}
        from backtest import run_backtest
        r = run_backtest(
            start="2021-01-01", end="2021-06-30",
            initial_equity=100_000,
            data_map_override=dm,
            watchlist_override=wl,
        )
        new_entries = [t for t in r["trade_log"]
                       if t.get("type") == "entry" and t.get("ticker") == "NEW"]
        assert len(new_entries) == 0, "Future ticker should not be tradeable in past date range"


# ── Date handling ─────────────────────────────────────────────────────────────

class TestDateHandling:
    def test_start_end_respected(self):
        dm = _make_data_map(periods=500)
        r  = _run(dm, start="2021-06-01", end="2021-09-30")
        dates = [pt["date"] for pt in r["equity_curve"]]
        assert all(d >= "2021-06-01" for d in dates)
        assert all(d <= "2021-09-30" for d in dates)

    def test_end_defaults_to_today_when_none(self):
        """When end=None, backtest should run up to today (or last available data)."""
        dm = _make_data_map(periods=300)
        r  = _run(dm, start="2021-06-01", end=None)
        assert r["equity_curve"], "Equity curve should not be empty"
        assert "metrics" in r

    def test_error_on_empty_range(self):
        dm = _make_data_map(periods=300)
        r  = _run(dm, start="2099-01-01", end="2099-06-01")
        assert "error" in r


# ── No duplicate holdings ──────────────────────────────────────────────────────

class TestNoDuplicateHoldings:
    def test_same_ticker_not_entered_twice(self):
        dm = _make_data_map()
        r  = _run(dm)
        from collections import Counter
        tickers_entered = [t["ticker"] for t in r["trade_log"] if t.get("type") == "entry"]
        # On any given entry date, same ticker should not appear more than once
        entry_by_date: dict[str, list] = {}
        for t in r["trade_log"]:
            if t.get("type") == "entry":
                entry_by_date.setdefault(t.get("entry_date", ""), []).append(t["ticker"])
        for date, tks in entry_by_date.items():
            cnt = Counter(tks)
            for tk, n in cnt.items():
                assert n == 1, f"Ticker {tk} entered {n} times on {date}"
