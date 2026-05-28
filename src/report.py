"""
report.py
=========
Daily report, backtest report, portfolio review report.
Enhanced: shows NEAR signals, quality scores, and grade tiers.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from config import MARKETS, REPORT

REPORT_DIR = Path(__file__).parent.parent / REPORT["OUTPUT_DIR"]
REPORT_DIR.mkdir(exist_ok=True)

DISCLAIMER = REPORT["DISCLAIMER"]


class C:
    R = "\033[91m"; G = "\033[92m"; Y = "\033[93m"; B = "\033[94m"
    M = "\033[95m"; CY = "\033[96m"; W = "\033[97m"; X = "\033[0m"; BOLD = "\033[1m"


def _plain(s: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", s)


def _fmt(v: float, sym: str) -> str:
    if sym == "Rs ":
        return f"{sym}{v:,.0f}"
    return f"{sym}{v:,.2f}"


def _grade_color(grade: str) -> str:
    return {"Excellent": C.G, "Good": C.CY, "Borderline": C.Y, "Drag": C.R}.get(grade, C.W)


# ===========================================================================
# Daily scan report
# ===========================================================================

def daily_report(
    decisions: List[Dict],
    account_eur: float,
    watchlist,
    markets: Optional[Dict] = None,
    tuner_mode: str = "BASE",
    risk_scale: float = 1.0,
    quality_filtered: Optional[List[str]] = None,
    quality_scores: Optional[Dict[str, float]] = None,
) -> str:
    markets = markets or MARKETS
    quality_filtered = quality_filtered or []
    quality_scores   = quality_scores   or {}

    flat_info: Dict[str, Dict] = {}
    if watchlist and isinstance(next(iter(watchlist.values())), list):
        for mkt, tickers in watchlist.items():
            for t in tickers:
                from config import get_sector
                flat_info[t] = {"market": mkt, "sector": get_sector(t)}
    else:
        flat_info = watchlist

    for d in decisions:
        tk  = d.get("ticker", "")
        inf = flat_info.get(tk, {})
        d.setdefault("market",  inf.get("market", d.get("market", "US")))
        d.setdefault("sector",  inf.get("sector", d.get("sector", "?")))
        mkt = d["market"]
        d["currency_symbol"] = markets.get(mkt, {}).get("symbol", "$")
        d["currency_code"]   = markets.get(mkt, {}).get("currency", "USD")

    by_market = {"US": [], "EU": [], "IN": []}
    for d in decisions:
        by_market.get(d.get("market", "US"), []).append(d)

    out = []
    out.append(f"\n{C.BOLD}{C.B}{'='*72}{C.X}")
    out.append(f"{C.BOLD}{C.W}  MASTERMIND PRO — DAILY REPORT  [{datetime.now():%Y-%m-%d %H:%M}]{C.X}")
    out.append(f"{C.BOLD}{C.B}{'='*72}{C.X}")
    out.append(f"  Account: {account_eur:,.0f}  |  Tuner: {C.CY}{tuner_mode}{C.X}  "
               f"|  Risk scale: {C.Y}{risk_scale:.0%}{C.X}")
    if quality_filtered:
        out.append(f"  Quality-filtered (Drag): {C.R}{len(quality_filtered)} tickers excluded{C.X}")
    out.append("")

    # Summary
    out.append(f"{C.BOLD}  Summary:{C.X}")
    for mk in ["US", "EU", "IN"]:
        m    = markets.get(mk, {})
        decs = by_market.get(mk, [])
        ne   = sum(1 for d in decs if d["decision"] == "ENTER")
        nw   = sum(1 for d in decs if d["decision"] == "WAIT")
        nn   = sum(1 for d in decs if d["decision"] == "NEAR")
        ns   = sum(1 for d in decs if d["decision"] == "SKIP")
        out.append(f"    {m.get('label','[?]')} {m.get('name','?'):<14} "
                   f"{len(decs):>3} stocks  "
                   f"{C.G}{ne:>2} enter{C.X}  "
                   f"{C.M}{nn:>2} near{C.X}  "
                   f"{C.Y}{nw:>2} wait{C.X}  "
                   f"{C.R}{ns:>2} skip{C.X}")
    out.append("")

    for mk in ["US", "EU", "IN"]:
        decs = by_market.get(mk, [])
        if not decs:
            continue
        m   = markets.get(mk, {})
        sym = m.get("symbol", "$")
        out.append(f"\n{C.BOLD}{C.CY}{'='*72}{C.X}")
        out.append(f"{C.BOLD}{C.W}  {m.get('label','[?]')} {m.get('name','?').upper()}  ({m.get('currency','?')}){C.X}")
        out.append(f"{C.BOLD}{C.CY}{'='*72}{C.X}")

        enters = [d for d in decs if d["decision"] == "ENTER"]
        nears  = [d for d in decs if d["decision"] == "NEAR"]
        waits  = [d for d in decs if d["decision"] == "WAIT"]
        skips  = [d for d in decs if d["decision"] == "SKIP"]

        if enters:
            out.append(f"\n  {C.BOLD}{C.G}--- ENTER ({len(enters)}) ---{C.X}")
            for d in enters:
                qs_str = _quality_str(d, quality_scores)
                out.append(f"\n  {C.BOLD}{C.G}* {d['ticker']:<16}{C.X}  "
                           f"{C.W}price={_fmt(d['price'], sym)}  "
                           f"ATR%={d.get('atr_pct',0):.2f}%  "
                           f"regime={_regime_str(d)}  "
                           f"sector={d.get('sector','?')}  "
                           f"{qs_str}{C.X}")
                out.append(f"    {C.CY}{d.get('reason','')}{C.X}")
                if "sizing" in d and d["sizing"].get("shares", 0) > 0:
                    sz = d["sizing"]
                    out.append(f"    Sizing: {sz['shares']:.0f} shares  "
                               f"cost={_fmt(sz['cost'], sym)}  "
                               f"stop={_fmt(sz['stop_price'], sym)}  "
                               f"risk={_fmt(sz['risk_amount'], sym)}")

        if nears:
            out.append(f"\n  {C.BOLD}{C.M}--- NEAR ({len(nears)}) — monitor closely ---{C.X}")
            for d in nears:
                qs_str = _quality_str(d, quality_scores)
                out.append(f"  {C.M}* {d['ticker']:<16}{C.X}  "
                           f"{_fmt(d['price'], sym)}  {qs_str}  "
                           f"gates={d.get('gates_passed','?')}/5  {d.get('reason','')}")

        if waits:
            out.append(f"\n  {C.BOLD}{C.Y}--- WAIT ({len(waits)}) ---{C.X}")
            for d in waits:
                out.append(f"  {C.Y}* {d['ticker']:<16}{C.X}  {_fmt(d['price'], sym)}  {d.get('reason','')}")

        if skips:
            out.append(f"\n  {C.BOLD}{C.R}--- SKIP ({len(skips)}) ---{C.X}")
            for d in skips:
                out.append(f"  {C.R}* {d['ticker']:<16}{C.X}  "
                           f"{_fmt(d['price'], sym)}  {d.get('reason','')}")

    out.append(f"\n{C.BOLD}{C.B}{'='*72}{C.X}")
    out.append(f"  {C.Y}{DISCLAIMER}{C.X}")
    out.append(f"{C.BOLD}{C.B}{'='*72}{C.X}\n")

    text = "\n".join(out)
    print(text)
    fname = REPORT_DIR / f"daily-{datetime.now():%Y-%m-%d}.txt"
    fname.write_text(_plain(text), encoding="utf-8")
    return text


# ===========================================================================
# Backtest report
# ===========================================================================

def backtest_report(summary: Dict, market_label: str = "ALL") -> str:
    m = summary.get("metrics", summary)
    out = []
    out.append(f"\n{C.BOLD}{C.B}{'='*68}{C.X}")
    out.append(f"{C.BOLD}{C.W}  MASTERMIND PRO -- BACKTEST  ({market_label}){C.X}")
    out.append(f"{C.BOLD}{C.B}{'='*68}{C.X}")

    cfg = summary.get("config", {})
    start_str = cfg.get("start", "")
    end_str   = cfg.get("end",   "")
    if cfg:
        out.append(f"  Period:  {start_str} to {end_str}")
        out.append(f"  Capital: {cfg.get('initial_equity', 0):,.0f}  |  "
                   f"Slippage: {cfg.get('slippage',0)*100:.2f}%  "
                   f"Commission: {cfg.get('commission',0)*100:.2f}%")
        out.append("")

    def line(label, val, col=C.W):
        out.append(f"  {label:<30} {col}{val}{C.X}")

    line("Final equity",     f"{summary.get('final_equity', 0):>12,.2f}")
    line("Total return",     f"{m.get('total_return_pct',0):>+10.2f}%",
         C.G if m.get("total_return_pct", 0) > 0 else C.R)
    line("CAGR",             f"{m.get('cagr_pct',0):>+10.2f}%",
         C.G if m.get("cagr_pct", 0) > 0 else C.R)
    line("Max drawdown",     f"{m.get('max_drawdown_pct',0):>10.2f}%", C.R)
    line("Sharpe ratio",     f"{m.get('sharpe_ratio',0):>10.3f}")
    line("Sortino ratio",    f"{m.get('sortino_ratio',0):>10.3f}")
    out.append("")
    line("Total trades",     f"{m.get('total_trades',0):>10}")
    line("Win rate",         f"{m.get('win_rate_pct',0):>10.1f}%")
    line("Avg R-multiple",   f"{m.get('avg_r_multiple',0):>+10.3f}R")
    line("Profit factor",    f"{m.get('profit_factor',0):>10.3f}")
    line("Ann. volatility",  f"{m.get('ann_vol_pct',0):>10.2f}%")

    # ── Index benchmark comparison ──────────────────────────────────────────
    if start_str and end_str:
        out.append(f"\n{C.BOLD}{C.B}{'='*68}{C.X}")
        out.append(f"{C.BOLD}{C.W}  INDEX BENCHMARK COMPARISON{C.X}")
        out.append(f"{C.BOLD}{C.B}{'='*68}{C.X}")

        # Determine which benchmarks to show
        lbl_up = market_label.upper()
        benchmarks = []
        if "US" in lbl_up or lbl_up in ("ALL", "ALL_MARKETS"):
            benchmarks.append(("S&P 500",  "^GSPC",  "US"))
        if "EU" in lbl_up or lbl_up in ("ALL", "ALL_MARKETS"):
            benchmarks.append(("DAX",      "^GDAXI", "EU"))
        if "IN" in lbl_up or lbl_up in ("ALL", "ALL_MARKETS"):
            benchmarks.append(("Nifty 50", "^NSEI",  "IN"))

        strat_cagr = m.get("cagr_pct", 0)

        hdr = f"  {'Index':<14} {'CAGR':>8} {'MaxDD':>8} {'Sharpe':>8} {'Alpha':>8}"
        out.append(hdr)
        out.append("  " + "-" * 50)

        for name, symbol, region in benchmarks:
            bm = _index_metrics(symbol, start_str, end_str)
            if bm is None:
                out.append(f"  {name:<14}  (data unavailable)")
                continue
            alpha     = strat_cagr - bm["cagr_pct"]
            col_cagr  = C.G if bm["cagr_pct"] > 0 else C.R
            col_alpha = C.G if alpha > 0 else C.R
            out.append(
                f"  {C.CY}{name:<14}{C.X}"
                f"  {col_cagr}{bm['cagr_pct']:>+7.2f}%{C.X}"
                f"  {C.R}{bm['max_dd_pct']:>7.2f}%{C.X}"
                f"  {bm['sharpe']:>8.3f}"
                f"  {col_alpha}{alpha:>+7.2f}%{C.X}"
            )

        out.append("")
        out.append(f"  {C.Y}Alpha = Strategy CAGR minus Index CAGR (same period){C.X}")
        out.append(f"  {C.Y}Note: multi-market result mixes USD/EUR/INR vs single-currency indices{C.X}")

    out.append(f"\n{C.BOLD}{C.B}{'='*68}{C.X}")
    out.append(f"  {C.Y}{DISCLAIMER}{C.X}")
    out.append(f"{C.BOLD}{C.B}{'='*68}{C.X}\n")

    text = "\n".join(out)
    print(text)

    today      = datetime.now().strftime("%Y-%m-%d")
    safe_label = market_label.replace(" ", "_")
    (REPORT_DIR / f"backtest-{today}-{safe_label}.txt").write_text(_plain(text), encoding="utf-8")

    trades = summary.get("closed_trades", summary.get("trades", []))
    if trades:
        pd.DataFrame(trades).to_csv(
            REPORT_DIR / f"backtest-{today}-{safe_label}-trades.csv", index=False
        )
    return text


# ===========================================================================
# Portfolio review report
# ===========================================================================

def portfolio_review_report(
    result: Dict[str, Any],
    equity: float,
    market_label: str = "ALL",
) -> str:
    out = []
    out.append(f"\n{C.BOLD}{C.CY}  PORTFOLIO REVIEW  [{result.get('date','')}]{C.X}")
    out.append(f"  Equity: {equity:,.2f}  |  Tuner: {result.get('tuner_mode','?')}  "
               f"|  Risk scale: {result.get('risk_scale',1):.0%}")

    exits   = result.get("exits", [])
    held    = result.get("held", [])

    if exits:
        out.append(f"\n  {C.BOLD}{C.R}  Exits ({len(exits)}):{C.X}")
        for e in exits:
            pnl = e.get("pnl", 0)
            col = C.G if pnl > 0 else C.R
            out.append(f"    {C.R}EXIT{C.X} {e['ticker']:<12}  "
                       f"{col}P&L={pnl:+,.2f}  R={e.get('r_multiple',0):+.2f}{C.X}  "
                       f"{e.get('exit_reason','')}")

    new_ent = result.get("new_entries", [])
    repl    = result.get("replacement_queue", [])
    if new_ent or repl:
        out.append(f"\n  {C.BOLD}{C.G}  New entries D+1 ({len(new_ent) + len(repl)}):{C.X}")
        for e in new_ent + repl:
            qs = e.get("quality_score")
            qs_str = f"  Q={qs:.0f}" if qs is not None else ""
            out.append(f"    {C.G}ENTER{C.X} {e['ticker']:<12}  "
                       f"price~{e.get('price',0):.2f}  "
                       f"stop={e.get('stop_price',0):.2f}  "
                       f"shares={e.get('shares',0)}{qs_str}")

    if held:
        out.append(f"\n  Held ({len(held)}):")
        for p in held:
            out.append(f"    HOLD {p['ticker']:<12}  entry={p.get('entry_price',0):.2f}  "
                       f"stop={p.get('stop_loss',0):.2f}")

    qf = result.get("quality_filtered", [])
    if qf:
        out.append(f"\n  {C.R}Quality-filtered (Drag): {', '.join(qf[:10])}"
                   f"{'...' if len(qf) > 10 else ''}{C.X}")

    out.append(f"\n  {C.Y}{DISCLAIMER}{C.X}\n")
    text = "\n".join(out)
    print(text)
    return text


# ===========================================================================
# Quality universe report
# ===========================================================================

def quality_report(scores_df, top_n: int = 20) -> str:
    """Print and return a quality scoring summary table."""
    from stock_selector import grade
    out = []
    out.append(f"\n{C.BOLD}{C.B}{'='*72}{C.X}")
    out.append(f"{C.BOLD}{C.W}  MASTERMIND PRO — UNIVERSE QUALITY SCORES{C.X}")
    out.append(f"{C.BOLD}{C.B}{'='*72}{C.X}")
    out.append(f"  {'Ticker':<16} {'Score':>6} {'Grade':<12} {'Hurst':>6} {'ADX':>6} "
               f"{'SMA200%':>8} {'Mom%':>7} {'Trend':>7}")
    out.append("  " + "-" * 68)

    df = scores_df.dropna(subset=["composite"]).head(top_n)
    for _, row in df.iterrows():
        g      = row.get("grade", grade(row.get("composite", 0)))
        gcol   = _grade_color(g)
        out.append(
            f"  {str(row.get('ticker','')):<16} "
            f"{gcol}{row.get('composite',0):>6.1f}  {g:<12}{C.X} "
            f"{row.get('hurst',0):>6.3f} "
            f"{row.get('adx_avg',0):>6.1f} "
            f"{row.get('time_above_sma200_pct',0):>8.1f} "
            f"{row.get('momentum_12_1_pct',0):>7.1f} "
            f"{row.get('trend_score',0):>7.1f}"
        )

    out.append(f"\n{C.BOLD}{C.B}{'='*72}{C.X}\n")
    text = "\n".join(out)
    print(text)
    return text


# ===========================================================================
# Helpers
# ===========================================================================

def _regime_str(d: Dict) -> str:
    reg = d.get("regime")
    if isinstance(reg, dict):
        return reg.get("label", "?")
    return str(reg) if reg else "?"


def _quality_str(d: Dict, quality_scores: Dict[str, float]) -> str:
    from stock_selector import grade as _grade
    ticker = d.get("ticker", "")
    qs = d.get("quality_score") or quality_scores.get(ticker)
    if qs is None:
        return ""
    g    = _grade(qs)
    gcol = _grade_color(g)
    return f"{gcol}Q={qs:.0f} {g}{C.X}"


def _index_metrics(symbol: str, start: str, end: str) -> Optional[Dict]:
    """Fetch index OHLCV and return CAGR, MaxDD, Sharpe for the period."""
    try:
        import yfinance as yf
        import numpy as np
        df = yf.download(symbol, start=start, end=end,
                         auto_adjust=True, progress=False)
        if df is None or len(df) < 20:
            return None
        close_col = df["Close"] if isinstance(df["Close"], pd.Series) else df["Close"].iloc[:, 0]
        prices = close_col.dropna()
        if len(prices) < 20:
            return None

        total_ret = float(prices.iloc[-1] / prices.iloc[0] - 1)
        days      = (prices.index[-1] - prices.index[0]).days
        years     = max(days / 365.25, 0.1)
        cagr      = ((1 + total_ret) ** (1 / years) - 1) * 100

        rolling_max = prices.cummax()
        dd          = (prices - rolling_max) / rolling_max
        max_dd      = float(dd.min()) * 100

        rets    = prices.pct_change().dropna()
        ann_ret = float(rets.mean()) * 252
        ann_vol = float(rets.std()) * (252 ** 0.5)
        sharpe  = ann_ret / ann_vol if ann_vol > 0 else 0.0

        return {"cagr_pct": round(cagr, 2),
                "max_dd_pct": round(max_dd, 2),
                "sharpe": round(sharpe, 3)}
    except Exception:
        return None
