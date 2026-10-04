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


# ── Full run + report (shared by the CLI and both apps) ─────────────────────

MODES = ("all", "portfolio", "short", "long")

CURVE_LABELS = {
    "model_portfolio": "Model portfolio",
    "core_etf_only":   "Core ETF only",
    "short_term":      "Short-term strategy",
    "long_term":       "Long-term strategy",
}

B, W, G, R, Y, C, D, X = ("\033[1m", "\033[97m", "\033[92m", "\033[91m",
                          "\033[93m", "\033[96m", "\033[90m", "\033[0m")
SEP = "=" * 72


def _pct(v: float, signed: bool = True) -> str:
    return f"{v:+.1f}%" if signed else f"{v:.1f}%"


def _col(v: float) -> str:
    return G if v >= 0 else R


def _heading(title: str) -> list[str]:
    return [f"\n{B}\033[94m{SEP}{X}", f"{B}{W}  {title}{X}", f"{B}\033[94m{SEP}{X}"]


def portfolio_section(res: dict, core: dict, spec: dict, labels: dict, proxy_until) -> list[str]:
    out = _heading(f"MODEL PORTFOLIO  --  {spec['name']}")
    out.append(f"  {res['start']} to {res['end']}  |  Rebalance every {res['rebalance_days']} trading days"
               f"  |  {res['rebalances']} rebalances  |  Costs {res['costs']:,.0f} EUR")
    out.append("")
    pm, cm = res["metrics"], core["metrics"]
    rows = [
        ("Final equity (EUR)", f"{res['final_equity']:,.0f}", f"{core['final_equity']:,.0f}", None),
        ("Total return",  _pct(pm["total_return_pct"]), _pct(cm["total_return_pct"]), pm["total_return_pct"] - cm["total_return_pct"]),
        ("CAGR",          _pct(pm["cagr_pct"]),         _pct(cm["cagr_pct"]),         pm["cagr_pct"] - cm["cagr_pct"]),
        ("Max drawdown",  _pct(pm["max_drawdown_pct"]), _pct(cm["max_drawdown_pct"]), pm["max_drawdown_pct"] - cm["max_drawdown_pct"]),
        ("Ann. volatility", _pct(pm["ann_vol_pct"], False), _pct(cm["ann_vol_pct"], False), None),
        ("Sharpe (rf 0%)", f"{pm['sharpe_ratio']:.2f}", f"{cm['sharpe_ratio']:.2f}", None),
    ]
    out.append(f"  {'':<20} {'Portfolio':>12} {'Core ETF only':>14} {'Difference':>11}")
    out.append(f"  {'-'*20} {'-'*12} {'-'*14} {'-'*11}")
    for name, a, b, diff in rows:
        d = f"{_col(diff)}{diff:+.1f} pts{X}" if diff is not None else ""
        out.append(f"  {name:<20} {a:>12} {b:>14}  {d}")

    out.append(f"\n  {B}Year by year{X}")
    out.append(f"  {'Year':<6} {'Portfolio':>10} {'Core only':>10}")
    for yr, v in res["annual_returns"].items():
        c = core["annual_returns"].get(yr)
        out.append(f"  {yr:<6} {_col(v)}{v*100:>+9.1f}%{X} {(_col(c) + f'{c*100:>+9.1f}%' + X) if c is not None else '':>10}")

    out.append(f"\n  {B}What each holding contributed{X}  (EUR gain on {res['initial_equity']:,.0f} start)")
    contrib = sorted(res["contribution"].items(), key=lambda kv: kv[1], reverse=True)
    for t, v in contrib:
        share = v / res["initial_equity"] * 100
        out.append(f"  {labels.get(t, t):<28} {res['weights'][t]*100:>5.1f}%  {_col(v)}{v:>+12,.0f}{X}  ({share:+.0f}% of start)")

    notes = []
    if proxy_until is not None:
        notes.append(f"Core ETF: {spec['core']['proxy']} returns (in EUR) stand in before {proxy_until.date()},"
                     f" when {spec['core']['ticker']} starts trading.")
    for t, d in res["joined_late"].items():
        if t != spec["core"]["ticker"]:
            notes.append(f"{labels.get(t, t)} has prices only from {d}; its weight was spread over"
                         f" the other holdings until the next rebalance after that.")
    if notes:
        out.append("")
        out += [f"  {Y}{n}{X}" for n in notes]
    return out


def short_term_section(st: dict, core: dict) -> list[str]:
    out = _heading("SHORT-TERM STRATEGY (ATR-Dynamic) ON THE MAP'S STOCKS")
    if "error" in st:
        return out + [f"  {R}{st['error']}{X}"]
    m, cm = st["metrics"], core["metrics"]
    cfg = st["config"]
    out.append(f"  {cfg['start']} to {cfg['end']}  |  EU market parameters  |  prices in EUR")
    out.append("")
    out.append(f"  {'':<20} {'Strategy':>12} {'Core ETF only':>14}")
    out.append(f"  {'-'*20} {'-'*12} {'-'*14}")
    out.append(f"  {'Final equity (EUR)':<20} {st['final_equity']:>12,.0f} {core['final_equity']:>14,.0f}")
    for name, k in [("Total return", "total_return_pct"), ("CAGR", "cagr_pct"), ("Max drawdown", "max_drawdown_pct")]:
        out.append(f"  {name:<20} {_pct(m[k]):>12} {_pct(cm[k]):>14}")
    out.append(f"  {'Sharpe (rf 0%)':<20} {m['sharpe_ratio']:>12.2f} {cm['sharpe_ratio']:>14.2f}")
    out.append("")
    out.append(f"  Trades {m['total_trades']}  |  Win rate {m['win_rate_pct']:.1f}%  |  Avg R {m['avg_r_multiple']:+.2f}"
               f"  |  Profit factor {m['profit_factor']:.2f}")
    by_ticker: dict[str, float] = {}
    for t in st["closed_trades"]:
        by_ticker[t["ticker"]] = by_ticker.get(t["ticker"], 0.0) + float(t.get("pnl", 0))
    if by_ticker:
        best = sorted(by_ticker.items(), key=lambda kv: kv[1], reverse=True)
        out.append("  P&L by stock (EUR): " + ", ".join(f"{t} {v:+,.0f}" for t, v in best))
    return out


def run_semimap_backtest(
    mode: str = "all",
    start: str = "2016-01-01",
    end: str = "",
    equity: float = 10_000,
    portfolio: Optional[str] = None,
    rebalance: int = 63,
    slots: int = 6,
    lt_rebalance: int = 63,
    commission: float = 0.001,
    slippage: float = 0.001,
    synthetic: bool = False,
) -> dict:
    """
    Run the requested backtests and build the ANSI-coloured report.

    Returns {"text": report, "curves": {name: EUR equity Series}, "synthetic": bool}.
    Progress lines are printed as it goes.
    """
    import io
    from contextlib import redirect_stdout

    if mode not in MODES:
        raise ValueError(f"Unknown mode '{mode}'")

    end   = end.strip() or pd.Timestamp.today().strftime("%Y-%m-%d")
    years = max(int((pd.Timestamp(end) - pd.Timestamp(start)).days / 365) + 2, 3)

    nodes, ports = load_map()
    spec   = portfolio_spec(nodes, ports, portfolio)
    stocks = stock_universe(nodes)
    labels: dict[str, str] = {}
    for n in nodes["nodes"]:          # first node wins: "Samsung", not "Samsung Foundry"
        if n.get("ticker"):
            labels.setdefault(n["ticker"], n["name"])
    labels[spec["core"]["ticker"]] = "Core ETF " + spec["core"]["ticker"]

    tickers = dict(stocks)
    tickers[spec["core"]["ticker"]] = spec["core"]["currency"]
    if spec["core"]["proxy"]:
        tickers[spec["core"]["proxy"]] = spec["core"]["proxy_currency"]

    out = [f"\n{B}\033[94m{SEP}{X}",
           f"{B}{W}  SEMICONDUCTOR MAP BACKTEST  (EUR){X}",
           f"  {start} to {end}  |  Start capital {equity:,.0f} EUR"
           f"  |  Costs {commission*100:.2f}% + {slippage*100:.2f}% slippage",
           f"{B}\033[94m{SEP}{X}"]
    if synthetic:
        out.append(f"  {R}{B}SYNTHETIC DATA: random walks, not market prices. Use only to test the pipeline.{X}")

    print(f"\n  Loading {len(tickers)} tickers + FX ({years} years)...")
    fetcher = synthetic_fetcher() if synthetic else None
    data, missing = fetch_eur_data(tickers, years, fetcher)
    if missing:
        out.append(f"  {Y}No prices for: {', '.join(missing)} (left out){X}")

    core_ser, proxy_until = splice_core(
        data[spec["core"]["ticker"]]["Close"] if spec["core"]["ticker"] in data else None,
        data[spec["core"]["proxy"]]["Close"] if spec["core"]["proxy"] in data else None,
    )
    closes = {t: df["Close"] for t, df in data.items()}
    closes[spec["core"]["ticker"]] = core_ser

    core_res = run_portfolio_backtest(closes, {spec["core"]["ticker"]: 1.0}, start, end,
                                      equity, rebalance, commission, slippage)
    curves = {"core_etf_only": core_res["equity_curve"]}

    if mode in ("all", "portfolio"):
        res = run_portfolio_backtest(closes, spec["weights"], start, end,
                                     equity, rebalance, commission, slippage)
        if "error" in res:
            out.append(f"  {R}Portfolio backtest: {res['error']}{X}")
        else:
            out += portfolio_section(res, core_res, spec, labels, proxy_until)
            curves["model_portfolio"] = res["equity_curve"]

    strat_data = {t: df for t, df in data.items() if t in stocks}
    if mode in ("all", "short"):
        print("  Running short-term strategy...")
        buf = io.StringIO()
        with redirect_stdout(buf):
            st = run_short_term(strat_data, start, end, equity, commission, slippage)
        out += short_term_section(st, core_res)
        if "equity_curve" in st:
            curves["short_term"] = pd.Series([e["equity"] for e in st["equity_curve"]],
                                             index=pd.to_datetime([e["date"] for e in st["equity_curve"]]))

    if mode in ("all", "long"):
        print("  Running long-term strategy...")
        from backtest_longterm import longterm_backtest_report
        lt = run_long_term(strat_data, start, end, equity, slots, lt_rebalance,
                           commission, slippage, core_ser, spec["core"]["ticker"])
        out += _heading("LONG-TERM STRATEGY (momentum rotation) ON THE MAP'S STOCKS")
        if "error" in lt:
            out.append(f"  {R}{lt['error']}{X}")
        else:
            lt["market"], lt["benchmark_name"] = "MAP STOCKS (EUR)", "Core ETF"
            out.append(longterm_backtest_report(lt))
            curves["long_term"] = lt["equity_curve"]

    out.append(f"\n{Y}  Research only, not investment advice. Past results don't predict future returns."
               f" The universe is today's map, so it has survivorship bias.{X}\n")
    text = "\n".join(out)
    return {"text": text, "curves": curves, "synthetic": synthetic}


def save_semimap_report(result: dict, reports_dir: Path = ROOT / "reports") -> tuple[Path, Path]:
    """Write the plain-text report and the equity-curve CSV; return both paths."""
    import re
    from datetime import datetime
    reports_dir.mkdir(exist_ok=True)
    stem = f"semimap-backtest-{datetime.now():%Y-%m-%d}" + ("-synthetic" if result["synthetic"] else "")
    txt, csv = reports_dir / f"{stem}.txt", reports_dir / f"{stem}.csv"
    txt.write_text(re.sub(r"\x1b\[[0-9;]*m", "", result["text"]), encoding="utf-8")
    pd.DataFrame(result["curves"]).to_csv(csv, index_label="date")
    return txt, csv
