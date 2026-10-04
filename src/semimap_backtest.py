"""
semimap_backtest.py
===================
Backtests for the stocks on the Semiconductor Dependency Map
(semimap/nodes.json + semimap/portfolios.json). Every price is converted to
EUR first, so results are in one currency for a EUR-based investor.

1. Model portfolio   the allocation in portfolios.json (core ETF + satellite
                     weights), rebalanced every N trading days, compared with
                     holding only the core ETF.
2. Short-term        the ATR-Dynamic DecisionEngine backtest (backtest.py) with
                     the map's stocks as the universe.
3. Long-term         the momentum-rebalancing backtest (backtest_longterm.py)
                     on the same universe.

The core ETF (VVSM.DE) only trades from late 2020. Before that the backtest
uses the returns of its proxy (SMH, the US-listed ETF on the same index),
converted to EUR, and the report says so.

CLI: src/run_backtest_semimap.py
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Dict, List, Optional

import numpy as np
import pandas as pd

ROOT        = Path(__file__).parent.parent
SEMIMAP_DIR = ROOT / "semimap"

# Yahoo FX pairs quoted as "units of currency per 1 EUR"
FX_TICKERS: Dict[str, str] = {
    "USD": "EURUSD=X",
    "KRW": "EURKRW=X",
    "JPY": "EURJPY=X",
    "GBP": "EURGBP=X",
}

OHLC = ["Open", "High", "Low", "Close"]


# ── Map data ────────────────────────────────────────────────────────────────

def load_map(directory: Path = SEMIMAP_DIR) -> tuple[dict, dict]:
    nodes = json.loads((directory / "nodes.json").read_text(encoding="utf-8"))
    ports = json.loads((directory / "portfolios.json").read_text(encoding="utf-8"))
    return nodes, ports


def stock_universe(nodes: dict) -> Dict[str, str]:
    """{yfinance ticker: currency} for every listed company (shared tickers once)."""
    out: Dict[str, str] = {}
    for n in nodes["nodes"]:
        if n.get("ticker"):
            out.setdefault(n["ticker"], n["currency"])
    return out


def portfolio_spec(nodes: dict, ports: dict, name: Optional[str] = None) -> dict:
    """
    Resolve a named portfolio into tickers and fractional weights.

    Returns {"name", "core": {"ticker", "currency", "proxy", "proxy_currency"},
             "weights": {ticker: fraction}}  — weights include the core ETF and sum to 1.
    """
    pid  = name or ports["default"]
    if pid not in ports["portfolios"]:
        raise ValueError(f"Unknown portfolio '{pid}'. Choices: {', '.join(ports['portfolios'])}")
    p    = ports["portfolios"][pid]
    core = p["core"]
    by_id = {n["id"]: n for n in nodes["nodes"]}

    weights: Dict[str, float] = {core["yfTicker"]: core["weight"] / 100}
    for node_id, w in p["weights"].items():
        t = by_id[node_id]["ticker"]
        weights[t] = weights.get(t, 0.0) + w / 100

    return {
        "id":      pid,
        "name":    p["name"],
        "core": {
            "ticker":         core["yfTicker"],
            "currency":       core["currency"],
            "name":           core["name"],
            "proxy":          (core.get("proxy") or {}).get("ticker"),
            "proxy_currency": (core.get("proxy") or {}).get("currency", "USD"),
        },
        "weights": weights,
    }


# ── Prices in EUR ───────────────────────────────────────────────────────────

def to_eur(df: pd.DataFrame, currency: str, fx: Dict[str, pd.Series]) -> pd.DataFrame:
    """Convert OHLC from `currency` to EUR with daily FX (units per EUR); volume unchanged."""
    if currency == "EUR":
        return df.copy()
    if currency not in fx:
        raise KeyError(f"No FX series for {currency}")
    rate = fx[currency].reindex(df.index).ffill()
    out  = df.copy()
    for c in OHLC:
        if c in out.columns:
            out[c] = out[c] / rate
    return out.dropna(subset=["Close"])


def fetch_eur_data(
    tickers: Dict[str, str],
    years: int,
    fetcher: Optional[Callable[[Dict[str, List[str]], int], Dict[str, pd.DataFrame]]] = None,
) -> tuple[Dict[str, pd.DataFrame], List[str]]:
    """
    Download (or read from the parquet cache) every ticker plus the FX pairs it
    needs, and return ({ticker: OHLCV in EUR}, [tickers that could not be loaded]).
    """
    if fetcher is None:
        from data import fetch_all
        fetcher = fetch_all

    fx_needed = sorted({FX_TICKERS[c] for c in tickers.values() if c != "EUR" and c in FX_TICKERS})
    raw = fetcher({"SEMI": list(tickers) + fx_needed}, years)

    fx = {ccy: raw[pair]["Close"] for ccy, pair in FX_TICKERS.items() if pair in raw}
    out: Dict[str, pd.DataFrame] = {}
    missing: List[str] = []
    for t, ccy in tickers.items():
        if t not in raw:
            missing.append(t)
            continue
        try:
            out[t] = to_eur(raw[t], ccy, fx)
        except KeyError:
            missing.append(t)
    return out, missing


def splice_core(core: Optional[pd.Series], proxy: Optional[pd.Series]) -> tuple[pd.Series, Optional[pd.Timestamp]]:
    """
    A continuous core-ETF price series: the proxy's daily returns before the
    core ETF's first price, the core ETF's own returns after. Returns
    (series, first real core date or None if the proxy was not needed).
    """
    if core is None or core.dropna().empty:
        if proxy is None:
            raise ValueError("Neither the core ETF nor its proxy has prices")
        return proxy.dropna(), None
    core = core.dropna()
    if proxy is None or proxy.dropna().empty or proxy.dropna().index[0] >= core.index[0]:
        return core, None

    proxy = proxy.dropna()
    first = core.index[0]
    idx   = proxy.index[proxy.index < first].union(core.index)
    rets  = pd.concat([proxy.pct_change()[proxy.index < first],
                       core.pct_change()]).reindex(idx).fillna(0.0)
    level = (1 + rets).cumprod()
    # Scale so the spliced series equals the real core price from `first` on
    level = level * (float(core.iloc[0]) / float(level.loc[first]))
    return level, first


# ── 1. Model portfolio ──────────────────────────────────────────────────────

def run_portfolio_backtest(
    closes: Dict[str, pd.Series],
    weights: Dict[str, float],
    start: str,
    end: Optional[str] = None,
    equity: float = 10_000.0,
    rebalance_days: int = 63,
    commission: float = 0.001,
    slippage: float = 0.001,
) -> dict:
    """
    Simulate a fixed-weight portfolio of EUR close series.

    On the first day and every `rebalance_days` trading days the portfolio is
    reset to its target weights, re-normalised over the assets that have a
    price that day (an asset that lists later joins at the next rebalance).
    Trading costs are (commission + slippage) on the turnover.
    """
    tickers = [t for t in weights if t in closes and weights[t] > 0]
    if not tickers:
        return {"error": "No priced assets with a weight"}

    start_ts = pd.Timestamp(start)
    end_ts   = pd.Timestamp(end) if end else pd.Timestamp.today().normalize()
    prices   = pd.DataFrame({t: closes[t] for t in tickers}).sort_index()
    prices   = prices[(prices.index >= start_ts) & (prices.index <= end_ts)].ffill()
    prices   = prices.dropna(how="all")
    if len(prices) < 2:
        return {"error": "Not enough prices in the date range"}

    rets       = prices.pct_change()
    cost_rate  = commission + slippage
    values     = pd.Series(0.0, index=tickers)
    cash       = equity
    contrib    = pd.Series(0.0, index=tickers)
    total_cost = 0.0
    curve: List[float] = []
    days_since = rebalance_days      # forces a rebalance on day one
    n_rebal    = 0

    for i, date in enumerate(prices.index):
        if i > 0:
            r = rets.loc[date].reindex(tickers).fillna(0.0)
            gain     = values * r
            contrib += gain
            values  += gain

        if days_since >= rebalance_days:
            avail = [t for t in tickers if pd.notna(prices.at[date, t])]
            total = float(values.sum()) + cash
            w     = pd.Series({t: weights[t] for t in avail})
            w     = w / w.sum()
            target = (w * total).reindex(tickers).fillna(0.0)
            turnover = float((target - values).abs().sum())
            fee      = turnover * cost_rate
            total_cost += fee
            values = (w * (total - fee)).reindex(tickers).fillna(0.0)
            cash   = 0.0
            days_since = 0
            n_rebal   += 1

        days_since += 1
        curve.append(float(values.sum()) + cash)

    eq = pd.Series(curve, index=prices.index, name="equity")
    first_price = {t: prices[t].first_valid_index() for t in tickers}
    late = {t: str(d.date()) for t, d in first_price.items()
            if d is not None and d > prices.index[0]}

    return {
        "equity_curve":   eq,
        "metrics":        _metrics(eq),
        "annual_returns": _annual_returns(eq),
        "contribution":   {t: float(contrib[t]) for t in tickers},
        "weights":        {t: weights[t] for t in tickers},
        "joined_late":    late,
        "costs":          total_cost,
        "rebalances":     n_rebal,
        "start":          str(eq.index[0].date()),
        "end":            str(eq.index[-1].date()),
        "initial_equity": equity,
        "final_equity":   float(eq.iloc[-1]),
        "rebalance_days": rebalance_days,
    }


def _metrics(eq: pd.Series) -> dict:
    from backtest import compute_metrics
    return compute_metrics(eq, [], float(eq.iloc[0]))


def _annual_returns(eq: pd.Series) -> Dict[int, float]:
    out: Dict[int, float] = {}
    prev = float(eq.iloc[0])
    for yr, grp in eq.groupby(eq.index.year):
        out[int(yr)] = float(grp.iloc[-1]) / prev - 1
        prev = float(grp.iloc[-1])
    return out


# ── 2 & 3. Strategies on the map universe ───────────────────────────────────

def run_short_term(data_eur: Dict[str, pd.DataFrame], start: str, end: Optional[str],
                   equity: float, commission: float, slippage: float) -> dict:
    from backtest import run_backtest
    from indicators import calculate_all
    data_map = {t: calculate_all(df) for t, df in data_eur.items()}
    # The engine takes market parameters from the watchlist key; EU is the EUR market.
    return run_backtest(
        market="EU", start=start, end=end, initial_equity=equity,
        commission=commission, slippage=slippage,
        watchlist_override={"EU": list(data_map)},
        data_map_override=data_map,
    )


def run_long_term(data_eur: Dict[str, pd.DataFrame], start: str, end: str,
                  equity: float, slots: int, rebalance_days: int,
                  commission: float, slippage: float,
                  benchmark: Optional[pd.Series], benchmark_label: str) -> dict:
    from backtest_longterm import run_longterm_backtest
    from indicators import calculate_all
    data_map = {t: calculate_all(df) for t, df in data_eur.items()}
    return run_longterm_backtest(
        "EU", data_map, start, end, equity=equity, max_positions=slots,
        rebalance_days=rebalance_days, commission=commission, slippage=slippage,
        benchmark_series=benchmark, benchmark_label=benchmark_label,
    )


# ── Offline test data ───────────────────────────────────────────────────────

# First trading days used by synthetic data, so splicing and late joiners get exercised
_SYNTH_LISTING = {"CRWV": "2025-03-28", "VVSM.DE": "2020-12-01"}


def synthetic_fetcher(seed: int = 7) -> Callable[[Dict[str, List[str]], int], Dict[str, pd.DataFrame]]:
    """
    A stand-in for data.fetch_all that makes random-walk OHLCV, for running the
    pipeline without network access. Results from it are NOT market data.
    """
    def fetch(watchlist: Dict[str, List[str]], years: int) -> Dict[str, pd.DataFrame]:
        idx = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=int(252 * years))
        out = {}
        for i, t in enumerate(sum(watchlist.values(), [])):
            rng = np.random.default_rng(seed + i)
            if t.startswith("EUR") and t.endswith("=X"):
                base  = {"EURUSD=X": 1.12, "EURKRW=X": 1400.0, "EURJPY=X": 135.0}.get(t, 1.0)
                close = base * np.exp(np.cumsum(rng.normal(0, 0.004, len(idx))))
                vol   = np.zeros(len(idx))
            else:
                close = 100 * np.exp(np.cumsum(rng.normal(0.0006, 0.02, len(idx))))
                vol   = rng.integers(1_000_000, 3_000_000, len(idx)).astype(float)
            df = pd.DataFrame({"Open": close * 0.997, "High": close * 1.012,
                               "Low": close * 0.988, "Close": close, "Volume": vol}, index=idx)
            if t in _SYNTH_LISTING:
                df = df[df.index >= pd.Timestamp(_SYNTH_LISTING[t])]
            out[t] = df
        return out
    return fetch
