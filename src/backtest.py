"""
backtest.py
===========
Historical simulation calling DecisionEngine.run_day() per trading day.

Accounting model (correct):
  - cash tracks available funds; positions hold unrealised cost basis
  - entry:  cash -= cost        (no equity change — cash converts to stock)
  - exit:   cash += proceeds    (returns capital; equity changes only by P&L)
  - daily equity = cash + mark-to-market of open positions

Key fixes vs prior version:
  - Exit adds full proceeds (not just P&L)
  - Entry checks affordability; caps shares to available cash
  - Dynamic universe: tickers with < 50 bars on that day are excluded
  - No duplicate entries on same ticker
  - Position value cap enforced via _size() (max_position_size in config)
  - Multi-currency note: returns are computed in raw price units; run
    single-market backtests for currency-pure results (US/EUR/INR mixed
    together will produce figures in units of the dominant market)
"""

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from config import ACCOUNT, REPORT, RISK, WATCHLIST
from indicators import calculate_all
from adaptive_tuner import AdaptiveTuner
from decision_engine import DecisionEngine

_MIN_BARS = 50  # minimum indicator history required before a ticker is tradeable


def _top_velocity_tickers(
    data_today: Dict[str, pd.DataFrame],
    watchlist: Dict[str, List[str]],
    top_n: int = 3,
) -> set:
    """Return the top-N cross-sectional momentum leaders (used for breakout-scaling at entry)."""
    from ranking import momentum_score
    scores: Dict[str, float] = {}
    for tickers in watchlist.values():
        for ticker in tickers:
            df = data_today.get(ticker)
            if df is None or len(df) < 63:
                continue
            s = momentum_score(df, vol_penalty=False)
            if s is not None and np.isfinite(s):
                scores[ticker] = float(s)
    top = sorted(scores, key=scores.__getitem__, reverse=True)[:top_n]
    return set(top)


def run_backtest(
    market: str = "ALL",
    start: Optional[str] = None,
    end: Optional[str] = None,
    years: int = 3,
    initial_equity: Optional[float] = None,
    slippage: Optional[float] = None,
    commission: Optional[float] = None,
    tuner_path: Optional[str] = None,
    watchlist_override: Optional[Dict[str, List[str]]] = None,
    data_map_override: Optional[Dict[str, pd.DataFrame]] = None,
    param_overrides: Optional[Dict[str, Any]] = None,
    quality_scores: Optional[Dict[str, float]] = None,
    debug: bool = False,
) -> Dict[str, Any]:
    from data import fetch_all

    initial_equity = initial_equity or ACCOUNT["equity"]
    slippage       = slippage   if slippage   is not None else ACCOUNT.get("slippage",   0.001)
    commission     = commission if commission is not None else ACCOUNT.get("commission", 0.001)

    end_dt   = pd.Timestamp(end).normalize()   if end   else pd.Timestamp.today().normalize()
    start_dt = pd.Timestamp(start).normalize() if start else end_dt - pd.DateOffset(years=years)

    # auto-calculate years of history needed from the date range
    years_needed = max(int((end_dt - start_dt).days / 365) + 2, 4)

    if watchlist_override:
        watchlist = watchlist_override
    elif market == "ALL":
        watchlist = WATCHLIST
    else:
        watchlist = {market: WATCHLIST.get(market, [])}

    all_tickers = [t for tlist in watchlist.values() for t in tlist]

    if data_map_override:
        data_map = data_map_override
    else:
        print(f"Fetching {len(all_tickers)} tickers ({years_needed} yrs of history)...")
        data_map_raw = fetch_all(watchlist, years=years_needed)
        print("Computing indicators...")
        data_map = {t: calculate_all(df) for t, df in data_map_raw.items()}

    if len(watchlist) > 1:
        print(
            "  NOTE: multi-market backtest mixes USD/EUR/INR — results are in "
            "mixed units. For currency-pure analysis run single-market."
        )

    if param_overrides:
        _apply_param_overrides(param_overrides)

    # Build sorted list of actual trading days in range
    all_dates: set = set()
    for df in data_map.values():
        mask = (df.index >= start_dt) & (df.index <= end_dt)
        all_dates.update(df[mask].index)
    trading_days = sorted(all_dates)

    if not trading_days:
        return {"error": "No trading days in range", "metrics": {}}

    print(f"Backtesting {len(trading_days)} days "
          f"[{trading_days[0].date()} to {trading_days[-1].date()}]")

    tuner  = AdaptiveTuner.load(tuner_path) if tuner_path else AdaptiveTuner()
    engine = DecisionEngine(tuner=tuner)
    engine.peak_equity = initial_equity

    cash: float          = initial_equity
    portfolio: List[Dict] = []
    equity_curve: List[Dict] = [
        {"date": trading_days[0].strftime("%Y-%m-%d"), "equity": cash}
    ]
    trade_log: List[Dict] = []

    for i, today in enumerate(trading_days):
        # ── Dynamic universe: slice each ticker's history up to today ──────
        data_today: Dict[str, pd.DataFrame] = {}
        for t, df in data_map.items():
            sliced = df[df.index <= today]
            if len(sliced) >= _MIN_BARS:   # enough history for reliable signals
                data_today[t] = sliced

        # Build active watchlist from tickers that have data today
        active_wl = {
            mkt: [t for t in tks if t in data_today]
            for mkt, tks in watchlist.items()
        }

        # Mark-to-market equity at start of day (for engine sizing decisions)
        current_equity = _mark_to_market(cash, portfolio, data_today)

        result = engine.run_day(
            today=today,
            data_map=data_today,
            portfolio=portfolio,
            equity=current_equity,
            context="backtest",
            watchlist=active_wl,
            quality_scores=quality_scores,
        )

        next_day = trading_days[i + 1] if i + 1 < len(trading_days) else None

        # Top-3 momentum leaders get 1.5× baseline sizing at entry (breakout-scaling)
        velocity_leaders = _top_velocity_tickers(data_today, active_wl, top_n=3)

        # ── Process exits at next-day open ───────────────────────────────
        for exit_pos in result["exits"]:
            ticker    = exit_pos["ticker"]
            fill_px   = _next_open(data_map, ticker, next_day, slippage, "sell")
            if fill_px is None:
                # No next-day open; use today's close as fallback fill
                fill_px = (float(data_today[ticker].iloc[-1]["Close"])
                           if ticker in data_today
                           else exit_pos.get("entry_price", 0))

            shares    = exit_pos.get("shares", 0)
            entry_px  = float(exit_pos["entry_price"])
            comm_out  = commission * fill_px * shares
            proceeds  = fill_px * shares - comm_out
            cost_basis = float(exit_pos.get("cost", entry_px * shares))
            pnl       = proceeds - cost_basis

            stop_init = float(exit_pos.get("stop_loss_initial", entry_px * 0.95))
            r_per_sh  = entry_px - stop_init
            r_mult    = (fill_px - entry_px) / r_per_sh if r_per_sh != 0 else 0.0

            cash += proceeds  # return capital + realised P&L

            exit_pos.update({
                "exit_price": round(fill_px, 6),
                "pnl":        round(pnl, 2),
                "r_multiple": round(r_mult, 3),
            })
            trade_log.append({**exit_pos, "type": "exit"})

            if debug:
                print(f"  EXIT  {ticker:12s} {shares:5d}sh @ {fill_px:.4f} "
                      f"pnl={pnl:+.2f}  cash={cash:.2f}")

        # ── Process new entries at next-day open ─────────────────────────
        new_portfolio = list(result["held"])
        entered_today = {p["ticker"] for p in new_portfolio}
        all_new       = result["new_entries"] + result["replacement_queue"]

        for entry_info in all_new:
            ticker = entry_info["ticker"]
            if ticker in entered_today:
                continue  # prevent duplicates

            fill_px = _next_open(data_map, ticker, next_day, slippage, "buy")
            if fill_px is None or fill_px <= 0:
                continue

            # Breakout-scaling: velocity leaders get 1.5× baseline allocation
            baseline_pct = RISK.get("MAX_POSITION_SIZE_PCT", 0.12)
            scale        = 1.5 if ticker in velocity_leaders else 1.0
            target_value = min(
                current_equity * baseline_pct * scale,
                current_equity * RISK.get("MAX_TOTAL_CONCENTRATION_PCT", 0.18),
            )
            shares = math.floor(target_value / (fill_px * (1 + commission)))
            if shares <= 0:
                continue

            comm_in = commission * fill_px * shares
            cost    = fill_px * shares + comm_in

            # Affordability guard: reduce to what cash allows
            if cost > cash:
                max_sh = int(cash / (fill_px * (1 + commission)))
                if max_sh <= 0:
                    if debug:
                        print(f"  SKIP  {ticker:12s} -- insufficient cash "
                              f"(need {cost:.0f}, have {cash:.0f})")
                    continue
                shares = max_sh
                cost   = fill_px * shares * (1 + commission)

            regime     = entry_info.get("regime", {})
            stop_mult  = regime.get("stop_mult", 2.0)  if isinstance(regime, dict) else 2.0
            trail_mult = entry_info.get("trail_mult", 5.0)
            atr_entry  = entry_info.get("atr", 0) or 0
            stop_loss  = fill_px - stop_mult * atr_entry
            risk_pct   = regime.get("risk_pct", 0.05) if isinstance(regime, dict) else 0.05

            cash -= cost  # deploy capital (no net equity change — cash→stock)

            pos = {
                "ticker":            ticker,
                "market":            entry_info.get("market", "US"),
                "sector":            entry_info.get("sector", "Unknown"),
                "entry_price":       fill_px,
                "entry_date":        (next_day.strftime("%Y-%m-%d")
                                      if next_day else today.strftime("%Y-%m-%d")),
                "shares":            shares,
                "stop_loss":         stop_loss,
                "stop_loss_initial": stop_loss,
                "trail_mult":        trail_mult,
                "peak_price":        fill_px,
                "atr_at_entry":      atr_entry,
                "risk_pct":          risk_pct,
                "regime":            (regime.get("label", "Normal")
                                      if isinstance(regime, dict) else "Normal"),
                "is_high_vol":       entry_info.get("is_high_vol", False),
                "cost":              round(cost, 2),
                "quality_score":     entry_info.get("quality_score"),
            }
            new_portfolio.append(pos)
            entered_today.add(ticker)
            trade_log.append({**pos, "type": "entry"})

            if debug:
                print(f"  ENTRY {ticker:12s} {shares:5d}sh @ {fill_px:.4f} "
                      f"cost={cost:.2f}  cash={cash:.2f}")

        portfolio = new_portfolio

        # Daily equity snapshot (mark-to-market)
        day_equity = _mark_to_market(cash, portfolio, data_today)
        equity_curve.append({
            "date":   today.strftime("%Y-%m-%d"),
            "equity": round(day_equity, 2),
        })

    if param_overrides:
        _restore_param_overrides()

    eq_series = pd.Series(
        [e["equity"] for e in equity_curve],
        index=pd.to_datetime([e["date"] for e in equity_curve]),
    )
    closed  = [t for t in trade_log if t.get("type") == "exit"]
    metrics = compute_metrics(eq_series, closed, equity_curve[0]["equity"])

    # Final equity: last mark-to-market value
    final_eq = equity_curve[-1]["equity"]

    return {
        "equity_curve":  equity_curve,
        "trade_log":     trade_log,
        "closed_trades": closed,
        "open_positions": portfolio,
        "metrics":        metrics,
        "final_equity":   final_eq,
        "config": {
            "market":          market,
            "start":           start_dt.strftime("%Y-%m-%d"),
            "end":             end_dt.strftime("%Y-%m-%d"),
            "initial_equity":  equity_curve[0]["equity"],
            "slippage":        slippage,
            "commission":      commission,
        },
    }


def compute_metrics(
    equity_series: pd.Series,
    closed_trades: List[Dict],
    initial_equity: float,
) -> Dict[str, Any]:
    if len(equity_series) < 2:
        return {}

    returns = equity_series.pct_change().dropna()

    total_ret = equity_series.iloc[-1] / equity_series.iloc[0] - 1
    days      = max(1, (equity_series.index[-1] - equity_series.index[0]).days)
    years     = days / 365.25
    cagr      = (1 + total_ret) ** (1 / years) - 1 if years > 0 and total_ret > -1 else 0.0

    rolling_max = equity_series.cummax()
    drawdown    = (equity_series - rolling_max) / rolling_max
    max_dd      = float(drawdown.min())

    ann_ret = float(returns.mean()) * 252
    ann_vol = float(returns.std())  * np.sqrt(252)
    sharpe  = ann_ret / ann_vol if ann_vol > 0 else 0.0

    neg     = returns[returns < 0]
    dn_vol  = float(neg.std()) * np.sqrt(252) if len(neg) > 1 else 0.0
    sortino = ann_ret / dn_vol if dn_vol > 0 else 0.0

    n_trades = len(closed_trades)
    win_rate = profit_factor = avg_r = 0.0
    if n_trades:
        winners  = [t for t in closed_trades if t.get("pnl", 0) > 0]
        losers   = [t for t in closed_trades if t.get("pnl", 0) <= 0]
        win_rate = len(winners) / n_trades
        gp       = sum(t.get("pnl", 0) for t in winners)
        gl       = abs(sum(t.get("pnl", 0) for t in losers))
        profit_factor = gp / gl if gl > 0 else float("inf")
        avg_r    = float(np.mean([t.get("r_multiple", 0) for t in closed_trades]))

    return {
        "cagr_pct":         round(cagr * 100,      2),
        "total_return_pct": round(total_ret * 100,  2),
        "max_drawdown_pct": round(max_dd * 100,     2),
        "sharpe_ratio":     round(sharpe,            3),
        "sortino_ratio":    round(sortino,           3),
        "profit_factor":    round(profit_factor,     3),
        "win_rate_pct":     round(win_rate * 100,    1),
        "avg_r_multiple":   round(avg_r,             3),
        "total_trades":     n_trades,
        "ann_vol_pct":      round(ann_vol * 100,     2),
        "years":            round(years,             2),
    }


# ===========================================================================
# Internal helpers
# ===========================================================================

def _mark_to_market(cash: float, portfolio: List[Dict],
                    data_today: Dict[str, pd.DataFrame]) -> float:
    """Cash + current close value of all open positions."""
    mtm = cash
    for pos in portfolio:
        t      = pos["ticker"]
        shares = pos.get("shares", 0)
        if shares <= 0:
            continue
        if t in data_today:
            close = float(data_today[t].iloc[-1]["Close"])
            if np.isfinite(close) and close > 0:
                mtm += close * shares
                continue
        mtm += pos.get("cost", 0)   # fallback to cost basis
    return mtm


_PARAM_BACKUP: Dict = {}

def _apply_param_overrides(overrides: Dict) -> None:
    import config
    for k, v in overrides.items():
        _PARAM_BACKUP[k] = getattr(config, k, None)
        setattr(config, k, v)

def _restore_param_overrides() -> None:
    import config
    for k, v in _PARAM_BACKUP.items():
        setattr(config, k, v)
    _PARAM_BACKUP.clear()

def _next_open(
    data_map: Dict[str, pd.DataFrame],
    ticker: str,
    next_day: Optional[pd.Timestamp],
    slippage: float,
    side: str,
) -> Optional[float]:
    """Return next-day open with slippage, or None if no data."""
    if next_day is None or ticker not in data_map:
        return None
    df   = data_map[ticker]
    rows = df[df.index == next_day]
    if rows.empty:
        return None
    px = float(rows.iloc[0]["Open"])
    if not np.isfinite(px) or px <= 0:
        return None
    return round(px * (1 + slippage) if side == "buy" else px * (1 - slippage), 6)


# ===========================================================================
# Legacy Backtest class (backward compat)
# ===========================================================================

class Backtest:
    def __init__(
        self,
        starting_capital: float = 10_000,
        max_positions: int = 8,
        max_per_sector: int = 2,
    ) -> None:
        self.starting_capital = starting_capital
        self.max_positions    = max_positions
        self.max_per_sector   = max_per_sector
        self._result: Optional[Dict] = None

    def run(
        self,
        data: Dict[str, pd.DataFrame],
        sectors: Dict[str, str],
        start_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        import config
        orig_max = config.RISK["MAX_OPEN_POSITIONS"]
        orig_sec = config.RISK["MAX_PER_SECTOR"].copy()
        orig_mkt = config.RISK["MAX_PER_MARKET"].copy()
        config.RISK["MAX_OPEN_POSITIONS"] = self.max_positions
        for mk in config.RISK["MAX_PER_SECTOR"]:
            config.RISK["MAX_PER_SECTOR"][mk] = self.max_per_sector
            config.RISK["MAX_PER_MARKET"][mk]  = self.max_positions

        wl: Dict[str, List[str]] = {}
        for ticker in data:
            from config import get_market as gm
            mk = gm(ticker)
            wl.setdefault(mk, []).append(ticker)

        result = run_backtest(
            market="ALL",
            start=start_date,
            initial_equity=self.starting_capital,
            watchlist_override=wl,
            data_map_override=data,
        )

        config.RISK["MAX_OPEN_POSITIONS"] = orig_max
        config.RISK["MAX_PER_SECTOR"]     = orig_sec
        config.RISK["MAX_PER_MARKET"]     = orig_mkt

        self._result = result
        m = result.get("metrics", {})
        return {
            "starting_capital": self.starting_capital,
            "final_equity":     result.get("final_equity", self.starting_capital),
            "total_return_pct": m.get("total_return_pct", 0),
            "cagr_pct":         m.get("cagr_pct", 0),
            "max_drawdown_pct": m.get("max_drawdown_pct", 0),
            "sharpe_ratio":     m.get("sharpe_ratio", 0),
            "total_trades":     m.get("total_trades", 0),
            "win_rate_pct":     m.get("win_rate_pct", 0),
            "avg_r_multiple":   m.get("avg_r_multiple", 0),
            "expectancy_eur":   0.0,
            "trades":           result.get("closed_trades", []),
            "equity_curve":     result.get("equity_curve", []),
        }
