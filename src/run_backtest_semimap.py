"""
run_backtest_semimap.py
=======================
Backtest the Semiconductor Dependency Map stocks, in EUR.

  --mode portfolio   model portfolio (core ETF + satellites) vs core ETF only
  --mode short       short-term ATR-Dynamic strategy on the map's stocks
  --mode long        long-term momentum strategy on the map's stocks
  --mode all         all three (default)

Usage
-----
  python src/run_backtest_semimap.py
  python src/run_backtest_semimap.py --mode portfolio --start 2018-01-01 --rebalance 21
  python src/run_backtest_semimap.py --mode long --slots 5
  python src/run_backtest_semimap.py --synthetic      # offline random-walk data, for testing only

Saves the report to reports/semimap-backtest-<date>.txt and the equity curves
to reports/semimap-backtest-<date>.csv.
"""

from __future__ import annotations

import argparse
import io
import os
import re
import sys
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd

from semimap_backtest import (
    load_map, portfolio_spec, stock_universe, fetch_eur_data, splice_core,
    run_portfolio_backtest, run_short_term, run_long_term, synthetic_fetcher,
)

ROOT = Path(__file__).parent.parent

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


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--mode",        default="all", choices=["all", "portfolio", "short", "long"])
    p.add_argument("--start",       default="2016-01-01", help="Start date (default 2016-01-01)")
    p.add_argument("--end",         default="", help="End date (default today)")
    p.add_argument("--equity",      type=float, default=10_000, help="Starting capital in EUR (default 10,000)")
    p.add_argument("--portfolio",   default=None, help="Portfolio name from semimap/portfolios.json (default: its default)")
    p.add_argument("--rebalance",   type=int, default=63, help="Model portfolio rebalance interval in trading days (default 63 = quarterly)")
    p.add_argument("--slots",       type=int, default=6, help="Long-term strategy positions (default 6)")
    p.add_argument("--lt-rebalance", type=int, default=63, help="Long-term strategy rebalance in calendar days (default 63)")
    p.add_argument("--commission",  type=float, default=0.001)
    p.add_argument("--slippage",    type=float, default=0.001)
    p.add_argument("--synthetic",   action="store_true", help="Use random-walk data instead of Yahoo (offline testing only)")
    a = p.parse_args()

    end   = a.end.strip() or pd.Timestamp.today().strftime("%Y-%m-%d")
    years = max(int((pd.Timestamp(end) - pd.Timestamp(a.start)).days / 365) + 2, 3)

    nodes, ports = load_map()
    spec   = portfolio_spec(nodes, ports, a.portfolio)
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
           f"  {a.start} to {end}  |  Start capital {a.equity:,.0f} EUR"
           f"  |  Costs {a.commission*100:.2f}% + {a.slippage*100:.2f}% slippage",
           f"{B}\033[94m{SEP}{X}"]
    if a.synthetic:
        out.append(f"  {R}{B}SYNTHETIC DATA: random walks, not market prices. Use only to test the pipeline.{X}")

    print(f"\n  Loading {len(tickers)} tickers + FX ({years} years)...")
    fetcher = synthetic_fetcher() if a.synthetic else None
    data, missing = fetch_eur_data(tickers, years, fetcher)
    if missing:
        out.append(f"  {Y}No prices for: {', '.join(missing)} (left out){X}")

    core_ser, proxy_until = splice_core(
        data[spec["core"]["ticker"]]["Close"] if spec["core"]["ticker"] in data else None,
        data[spec["core"]["proxy"]]["Close"] if spec["core"]["proxy"] in data else None,
    )
    closes = {t: df["Close"] for t, df in data.items()}
    closes[spec["core"]["ticker"]] = core_ser

    core_res = run_portfolio_backtest(closes, {spec["core"]["ticker"]: 1.0}, a.start, end,
                                      a.equity, a.rebalance, a.commission, a.slippage)
    curves = {"core_etf_only": core_res["equity_curve"]}

    if a.mode in ("all", "portfolio"):
        res = run_portfolio_backtest(closes, spec["weights"], a.start, end,
                                     a.equity, a.rebalance, a.commission, a.slippage)
        if "error" in res:
            out.append(f"  {R}Portfolio backtest: {res['error']}{X}")
        else:
            out += portfolio_section(res, core_res, spec, labels, proxy_until)
            curves["model_portfolio"] = res["equity_curve"]

    strat_data = {t: df for t, df in data.items() if t in stocks}
    if a.mode in ("all", "short"):
        print("  Running short-term strategy...")
        buf = io.StringIO()
        with redirect_stdout(buf):
            st = run_short_term(strat_data, a.start, end, a.equity, a.commission, a.slippage)
        out += short_term_section(st, core_res)
        if "equity_curve" in st:
            curves["short_term"] = pd.Series([e["equity"] for e in st["equity_curve"]],
                                             index=pd.to_datetime([e["date"] for e in st["equity_curve"]]))

    if a.mode in ("all", "long"):
        print("  Running long-term strategy...")
        from backtest_longterm import longterm_backtest_report
        lt = run_long_term(strat_data, a.start, end, a.equity, a.slots, a.lt_rebalance,
                           a.commission, a.slippage, core_ser, spec["core"]["ticker"])
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
    print(text)

    rdir = ROOT / "reports"
    rdir.mkdir(exist_ok=True)
    stem = f"semimap-backtest-{datetime.now():%Y-%m-%d}" + ("-synthetic" if a.synthetic else "")
    (rdir / f"{stem}.txt").write_text(re.sub(r"\x1b\[[0-9;]*m", "", text), encoding="utf-8")
    pd.DataFrame(curves).to_csv(rdir / f"{stem}.csv", index_label="date")
    print(f"  Saved reports/{stem}.txt and reports/{stem}.csv")


if __name__ == "__main__":
    main()
