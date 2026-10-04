"""
test_semimap_backtest.py
========================
Unit tests for the Semiconductor Map backtest: EUR conversion, core-ETF
splicing, the fixed-weight portfolio simulator, and the long-term engine's
supplied-benchmark option. No network — all data is synthetic.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from semimap_backtest import (
    fetch_eur_data, load_map, portfolio_spec, run_long_term,
    run_portfolio_backtest, splice_core, stock_universe, synthetic_fetcher, to_eur,
)

IDX = pd.bdate_range("2024-01-01", periods=10)


def _flat(v=100.0, idx=IDX):
    return pd.Series(v, index=idx)


class TestToEur:
    def test_usd_converted(self):
        df = pd.DataFrame({"Open": 110.0, "High": 110.0, "Low": 110.0, "Close": 110.0, "Volume": 5.0}, index=IDX)
        out = to_eur(df, "USD", {"USD": _flat(1.1)})
        assert out["Close"].iloc[0] == pytest.approx(100.0)
        assert out["Volume"].iloc[0] == 5.0

    def test_eur_unchanged(self):
        df = pd.DataFrame({"Close": 50.0}, index=IDX)
        assert to_eur(df, "EUR", {})["Close"].iloc[-1] == 50.0

    def test_missing_fx_raises(self):
        with pytest.raises(KeyError):
            to_eur(pd.DataFrame({"Close": 1.0}, index=IDX), "JPY", {})


class TestSpliceCore:
    def test_proxy_before_core(self):
        proxy = pd.Series(np.linspace(100, 109, 10), index=IDX)
        core  = pd.Series([50.0, 51.0, 52.0, 53.0, 54.0], index=IDX[5:])
        s, first = splice_core(core, proxy)
        assert first == IDX[5]
        assert s.loc[IDX[5:]].tolist() == pytest.approx(core.tolist())
        # pre-splice returns are the proxy's returns
        assert (s.loc[IDX[1]] / s.loc[IDX[0]]) == pytest.approx(proxy.iloc[1] / proxy.iloc[0])

    def test_no_proxy_needed(self):
        core = _flat(10.0)
        s, first = splice_core(core, _flat(5.0, IDX[3:]))
        assert first is None and s.equals(core)

    def test_proxy_only(self):
        s, first = splice_core(None, _flat(5.0))
        assert first is None and len(s) == 10


class TestPortfolioBacktest:
    def test_flat_prices_lose_only_costs(self):
        r = run_portfolio_backtest({"A": _flat(), "B": _flat()}, {"A": 0.5, "B": 0.5},
                                   "2024-01-01", "2024-12-31", equity=1000, rebalance_days=3,
                                   commission=0.001, slippage=0.001)
        assert r["costs"] == pytest.approx(2.0)            # one full buy, later rebalances free
        assert r["final_equity"] == pytest.approx(998.0)

    def test_buy_and_hold_weights(self):
        a = pd.Series(np.linspace(100, 200, 10), index=IDX)
        r = run_portfolio_backtest({"A": a, "B": _flat()}, {"A": 0.5, "B": 0.5},
                                   "2024-01-01", "2024-12-31", equity=1000, rebalance_days=999,
                                   commission=0, slippage=0)
        assert r["final_equity"] == pytest.approx(1500.0)
        assert r["contribution"]["A"] == pytest.approx(500.0)
        assert r["contribution"]["B"] == pytest.approx(0.0)

    def test_late_joiner_waits_for_rebalance(self):
        b = pd.Series([np.nan] * 4 + [100.0] * 6, index=IDX)
        r = run_portfolio_backtest({"A": _flat(), "B": b}, {"A": 0.5, "B": 0.5},
                                   "2024-01-01", "2024-12-31", equity=1000, rebalance_days=3,
                                   commission=0, slippage=0)
        assert r["joined_late"] == {"B": str(IDX[4].date())}
        assert r["rebalances"] == 4                         # days 0, 3, 6, 9
        assert r["final_equity"] == pytest.approx(1000.0)

    def test_annual_returns_chain(self):
        idx = pd.bdate_range("2023-12-25", periods=10)
        a = pd.Series(np.linspace(100, 110, 10), index=idx)
        r = run_portfolio_backtest({"A": a}, {"A": 1.0}, "2023-01-01", "2024-12-31",
                                   equity=100, commission=0, slippage=0)
        total = np.prod([1 + v for v in r["annual_returns"].values()]) - 1
        assert total == pytest.approx(0.10)


class TestMapPipeline:
    def test_spec_weights_sum_to_one(self):
        nodes, ports = load_map()
        spec = portfolio_spec(nodes, ports)
        assert sum(spec["weights"].values()) == pytest.approx(1.0)
        assert spec["weights"][spec["core"]["ticker"]] == pytest.approx(0.5)
        assert spec["weights"]["005930.KS"] == pytest.approx(0.05)

    def test_unknown_portfolio(self):
        nodes, ports = load_map()
        with pytest.raises(ValueError):
            portfolio_spec(nodes, ports, "nope")

    def test_synthetic_end_to_end(self):
        nodes, ports = load_map()
        spec = portfolio_spec(nodes, ports)
        tickers = dict(stock_universe(nodes))
        tickers[spec["core"]["ticker"]] = "EUR"
        tickers[spec["core"]["proxy"]] = "USD"
        data, missing = fetch_eur_data(tickers, 4, synthetic_fetcher())
        assert missing == []
        core, first = splice_core(data["VVSM.DE"]["Close"], data["SMH"]["Close"])
        closes = {t: df["Close"] for t, df in data.items()}
        closes["VVSM.DE"] = core
        start = str((pd.Timestamp.today() - pd.DateOffset(years=2)).date())
        r = run_portfolio_backtest(closes, spec["weights"], start)
        assert "error" not in r and r["final_equity"] > 0
        assert "CRWV" in r["joined_late"]

        stocks = {t: df for t, df in data.items() if t in stock_universe(nodes)}
        lt = run_long_term(stocks, start, str(pd.Timestamp.today().date()), 10_000, 5, 63,
                           0.001, 0.001, core, "VVSM.DE")
        assert lt["benchmark"]["ticker"] == "VVSM.DE"
        assert "cagr" in lt["benchmark"]


class TestRunSemimapBacktest:
    def test_portfolio_mode_report_and_curves(self, tmp_path):
        from semimap_backtest import run_semimap_backtest, save_semimap_report
        start = str((pd.Timestamp.today() - pd.DateOffset(years=2)).date())
        r = run_semimap_backtest(mode="portfolio", start=start, synthetic=True)
        assert set(r["curves"]) == {"core_etf_only", "model_portfolio"}
        assert "MODEL PORTFOLIO" in r["text"] and "SYNTHETIC DATA" in r["text"]
        assert "SHORT-TERM" not in r["text"]
        txt, csv = save_semimap_report(r, tmp_path)
        assert txt.name.endswith("-synthetic.txt") and "\x1b[" not in txt.read_text()
        assert list(pd.read_csv(csv).columns) == ["date", "core_etf_only", "model_portfolio"]

    def test_unknown_mode(self):
        from semimap_backtest import run_semimap_backtest
        with pytest.raises(ValueError):
            run_semimap_backtest(mode="weekly", synthetic=True)
