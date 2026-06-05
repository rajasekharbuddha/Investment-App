"""
decision_engine.py
==================
DecisionEngine.run_day() — single source of truth for live scan and backtest.

Phase 0   Universe refresh (optional — if dynamic universe enabled)
Phase 0.5 Quality pre-filter (stock_selector scoring)
Phase 1   Read tuner state
Phase 2   Portfolio review (peak, breakeven, trailing stop, exits)
Phase 3   Universe scan (evaluate 5-gates per non-held ticker)
Phase 4   Sizing (R-based)
Phase 5   Replacement scan (same-market replacements for exits)
Phase 6   Fill remaining open slots
Phase 7   Update tuner
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from config import (ACCOUNT, GATE_DEFAULTS, RISK, QUALITY_FILTER, MOMENTUM_EXIT,
                    RANKING, get_market, get_sector)
from adaptive_tuner import AdaptiveTuner
from rules import evaluate_gates
from ranking import momentum_score


def update_trailing_stop(
    current_price: float,
    highest_price: float,
    atr_val: float,
    current_stop: float,
) -> float:
    """Calculates a dynamic macro-trend trailing stop using a wide ATR multiplier floor."""
    if np.isnan(atr_val) or atr_val <= 0:
        return current_stop

    # 5.5x ATR tighter lock — captures IN trend gains before mean reversion fires
    dynamic_buffer = atr_val * 5.5
    new_stop_floor = highest_price - dynamic_buffer

    # Ensure the trailing stop only moves upward to lock in profits
    return max(current_stop, new_stop_floor)


class DecisionEngine:
    def __init__(self, tuner: Optional[AdaptiveTuner] = None) -> None:
        self.tuner          = tuner or AdaptiveTuner()
        self.peak_equity    = ACCOUNT["equity"]
        self._circuit_scale = 1.0
        self._circuit_base_equity: Optional[float] = None
        # Quality scores cache: {ticker: composite_score}
        self._quality_scores: Dict[str, float] = {}

    # =========================================================================
    # Public entry point
    # =========================================================================

    def run_day(
        self,
        today: pd.Timestamp,
        data_map: Dict[str, pd.DataFrame],
        portfolio: List[Dict],
        equity: float,
        context: str = "live",
        watchlist: Optional[Dict[str, List[str]]] = None,
        quality_scores: Optional[Dict[str, float]] = None,
    ) -> Dict[str, Any]:
        """
        Execute all engine phases for a single trading day.
        Returns comprehensive result dict used by run_daily and backtest.

        quality_scores: optional pre-computed {ticker: composite_score} from
                        stock_selector. If None, no quality filtering applied.
        """
        from config import WATCHLIST as DEFAULT_WL
        if watchlist is None:
            watchlist = DEFAULT_WL

        if quality_scores is not None:
            self._quality_scores = quality_scores

        # Phase 1: read tuner — capture mode before Phase 7 updates it
        loaded_tuner_mode = self.tuner.mode
        tuner_params = self.tuner.get_params()

        # Flat set of all tickers in today's active universe — used in the
        # adaptive escape clause to detect rank drops during the immunity window.
        watchlist_tickers = {t for tks in watchlist.values() for t in tks}

        # Phase 2: portfolio review
        held, exits, portfolio_actions = self._review_portfolio(
            portfolio, data_map, today, context, watchlist_tickers
        )

        if equity > self.peak_equity:
            self.peak_equity = equity
        risk_scale = self._circuit_breaker(equity)

        # Phase 3: universe scan
        held_tickers = {p["ticker"] for p in held}
        candidates: Dict[str, Any] = {}
        market_density: Dict[str, Dict[str, int]] = {}
        quality_filtered: List[str] = []

        min_score = QUALITY_FILTER.get("MIN_SCORE", 0) if QUALITY_FILTER.get("ENABLED") and self._quality_scores else 0

        # Compute market breadth once per market before the scan loop
        market_breadth: Dict[str, Tuple[Optional[float], Optional[float]]] = {
            mkt: self._market_breadth(mkt, tickers, data_map, today)
            for mkt, tickers in watchlist.items()
        }

        for market, tickers in watchlist.items():
            market_density[market] = {"enter": 0, "wait": 0, "skip": 0, "near": 0}
            ix_close, ix_sma200 = market_breadth.get(market, (None, None))
            for ticker in tickers:
                if ticker in held_tickers:
                    continue
                if ticker not in data_map:
                    continue

                # Quality pre-filter: skip Drag stocks
                if min_score > 0 and ticker in self._quality_scores:
                    score = self._quality_scores[ticker]
                    if score < min_score:
                        quality_filtered.append(ticker)
                        continue

                df = data_map[ticker]
                rows = df[df.index <= today]
                if len(rows) < 3:
                    continue

                rows = rows.copy()
                rows["SMA_50_20AGO"] = rows["SMA_50"].shift(
                    GATE_DEFAULTS["sma_rising_lookback"]
                )

                row   = rows.iloc[-1]
                prev  = rows.iloc[-2] if len(rows) >= 2 else None
                prev2 = rows.iloc[-3] if len(rows) >= 3 else None

                tqs = self._quality_scores.get(ticker)
                # Column-level index signal takes precedence over computed breadth
                col_ix_close = float(row["Index_Close"]) if "Index_Close" in row.index else None
                col_ix_sma   = float(row["Index_SMA200"]) if "Index_SMA200" in row.index else None
                eff_ix_close = col_ix_close if col_ix_close is not None else ix_close
                eff_ix_sma   = col_ix_sma   if col_ix_sma   is not None else ix_sma200
                result = evaluate_gates(
                    ticker, row, prev, prev2, tuner_params, market,
                    trend_quality_score=tqs,
                    index_close=eff_ix_close,
                    index_sma200=eff_ix_sma,
                )
                result["ticker"] = ticker

                # Attach quality score if available
                if tqs is not None:
                    result["quality_score"] = tqs

                candidates[ticker] = result

                d = result["decision"]
                if d == "ENTER":
                    market_density[market]["enter"] += 1
                elif d == "WAIT":
                    market_density[market]["wait"] += 1
                elif d == "NEAR":
                    market_density[market]["near"] += 1
                else:
                    market_density[market]["skip"] += 1

        # Phase 4: sizing (ENTER and WAIT candidates)
        sizing = self._size_candidates(candidates, equity, risk_scale)

        # Phase 4.5: sector momentum (used for rotation in phases 5 & 6)
        sector_mom = self._sector_momentum(data_map, watchlist)

        # Phase 5: replacement scan
        # Skip positions still within the 21-day immunity window — whipsaw exits
        # should park cash rather than immediately rotate into a new position.
        virtual_pending: List[Dict] = []
        replacement_queue: List[Dict] = []
        for exit_pos in exits:
            if not exit_pos.get("can_be_replaced", True):
                continue
            repl = self._find_replacement(
                exit_pos, candidates, sizing, held, virtual_pending, sector_mom
            )
            if repl:
                replacement_queue.append(repl)
                virtual_pending.append(repl)

        # Phase 6: fill remaining open slots
        new_entries = self._fill_slots(
            candidates, sizing, held, virtual_pending, replacement_queue, sector_mom
        )

        # Phase 7: update tuner
        tuner_updates: Dict[str, Any] = {}
        for market, counts in market_density.items():
            upd = self.tuner.update(
                market, counts["enter"], counts["wait"], counts["skip"]
            )
            tuner_updates[market] = upd

        return {
            "date":               today.strftime("%Y-%m-%d"),
            "portfolio_actions":  portfolio_actions,
            "held":               held,
            "exits":              exits,
            "replacement_queue":  replacement_queue,
            "new_entries":        new_entries,
            "candidates":         candidates,
            "sizing":             sizing,
            "tuner_updates":      tuner_updates,
            "tuner_mode":         self.tuner.mode,       # post-scan (next run's mode)
            "loaded_tuner_mode":  loaded_tuner_mode,     # mode used for THIS scan
            "risk_scale":         risk_scale,
            "market_density":     market_density,
            "quality_filtered":   quality_filtered,
            "quality_scores":     dict(self._quality_scores),
        }

    # =========================================================================
    # Phase 2 — Portfolio review
    # =========================================================================

    def _review_portfolio(
        self,
        portfolio: List[Dict],
        data_map: Dict[str, pd.DataFrame],
        today: pd.Timestamp,
        context: str,
        watchlist_tickers: Optional[set] = None,
    ) -> Tuple[List[Dict], List[Dict], List[Dict]]:
        held, exits, actions = [], [], []

        for pos in portfolio:
            ticker = pos["ticker"]
            action: Dict[str, Any] = {
                "ticker":   ticker,
                "action":   "HOLD",
                "reason":   "",
                "old_stop": pos.get("stop_loss"),
                "new_stop": pos.get("stop_loss"),
            }

            if ticker not in data_map:
                held.append(pos)
                action["reason"] = "no data"
                actions.append(action)
                continue

            df   = data_map[ticker]
            rows = df[df.index <= today]
            if rows.empty:
                held.append(pos)
                action["reason"] = "no data for date"
                actions.append(action)
                continue

            row    = rows.iloc[-1]
            close  = float(row["Close"])
            high   = float(row["High"])
            low    = float(row["Low"])
            atr    = float(row.get("ATR", pos.get("atr_at_entry", 1.0)))
            if np.isnan(atr) or atr <= 0:
                atr = pos.get("atr_at_entry", 1.0)

            stop         = float(pos["stop_loss"])
            peak         = float(pos.get("peak_price", pos["entry_price"]))
            entry        = float(pos["entry_price"])
            stop_initial = float(pos.get("stop_loss_initial", stop))

            # Update peak
            if high > peak:
                peak = high
                pos["peak_price"] = peak

            # Breakeven floor: +1R → move stop to entry
            one_r_price = entry + (entry - stop_initial)
            if close >= one_r_price and stop < entry:
                stop = entry
                action["action"] = "MOVE_STOP"
                action["reason"] = "+1R breakeven"

            # Trailing stop — 5.5× ATR floor (tighter lock-in for IN mean reversion)
            new_stop = update_trailing_stop(close, peak, atr, stop)
            if new_stop > stop:
                stop = new_stop
                action["action"] = "MOVE_STOP"
                action["reason"] = action.get("reason", "") or "trailing stop update"

            pos["stop_loss"]   = stop
            action["new_stop"] = stop

            # Exit conditions
            exit_triggered = False
            exit_price     = close
            exit_reason    = ""

            if low <= stop:
                open_px     = float(row.get("Open", close))
                exit_price  = max(stop, open_px) if open_px > stop else open_px
                exit_reason = f"Stop hit ({stop:.4f})"
                exit_triggered = True

            if not exit_triggered and context == "live":
                sma200 = row.get("SMA_200")
                if sma200 is not None and not np.isnan(float(sma200)) and close < float(sma200):
                    exit_price  = close
                    exit_reason = f"Close {close:.2f} < SMA200 {float(sma200):.2f}"
                    exit_triggered = True

            # Momentum exit: eject positions whose rolling momentum has turned negative.
            # Grace period prevents triggering on normal post-entry consolidation.
            if not exit_triggered and MOMENTUM_EXIT.get("ENABLED", False):
                entry_ts    = pd.Timestamp(pos.get("entry_date", today))
                grace_days  = MOMENTUM_EXIT.get("GRACE_DAYS", 10)
                past_grace  = (today - entry_ts).days >= grace_days
                ticker_df   = data_map.get(ticker)
                if past_grace and ticker_df is not None:
                    rows_to_date = ticker_df[ticker_df.index <= today]
                    min_bars     = max(RANKING.get("MOMENTUM_PERIODS", [252]))
                    if len(rows_to_date) >= min_bars:
                        score = momentum_score(rows_to_date,
                                               vol_penalty=RANKING.get("VOLATILITY_PENALTY", False))
                        threshold = MOMENTUM_EXIT.get("SCORE_THRESHOLD", 0.0)
                        if not np.isnan(score) and score < threshold:
                            exit_price  = close
                            exit_reason = f"Momentum exit (score {score:.3f} < {threshold})"
                            exit_triggered = True

            # Track calendar days held — used for immunity window and reporting
            entry_ts  = pd.Timestamp(pos.get("entry_date", today))
            days_held = max(0, (today - entry_ts).days)
            pos["days_held"] = days_held

            if exit_triggered:
                r_per_share = entry - stop_initial
                r_mult = (exit_price - entry) / r_per_share if r_per_share != 0 else 0.0
                pnl    = (exit_price - entry) * pos.get("shares", 0)

                # ── Adaptive Escape Clause ────────────────────────────────────
                # Default: hard 21-day immunity blocks replacements on whipsaw exits.
                # Override: allow early replacement if the position has structurally
                # broken down (Close < EMA_20) or dropped out of the ranked universe.
                structural_breach = False
                if "EMA_20" in row.index:
                    ema20 = float(row.get("EMA_20", float("nan")))
                    if not np.isnan(ema20) and close < ema20:
                        structural_breach = True

                still_in_universe = (
                    ticker in watchlist_tickers
                    if watchlist_tickers is not None
                    else True
                )

                if days_held < 21:
                    can_be_replaced = structural_breach or not still_in_universe
                    escape_reason   = (
                        "structural_breach" if structural_breach
                        else ("rank_escape" if not still_in_universe else None)
                    )
                else:
                    can_be_replaced = True
                    escape_reason   = None

                pos.update({"exit_date":        today.strftime("%Y-%m-%d"),
                            "exit_price":        exit_price,
                            "exit_reason":       exit_reason,
                            "pnl":               round(pnl, 2),
                            "r_multiple":        round(r_mult, 3),
                            "can_be_replaced":   can_be_replaced,
                            "escape_reason":     escape_reason})
                exits.append(pos)
                action.update({"action": "SELL", "reason": exit_reason,
                               "exit_price": exit_price, "pnl": pnl})
            else:
                held.append(pos)

            actions.append(action)

        return held, exits, actions

    # =========================================================================
    # Phase 4 — Sizing
    # =========================================================================

    def _size_candidates(
        self,
        candidates: Dict[str, Any],
        equity: float,
        risk_scale: float,
    ) -> Dict[str, Dict]:
        sizing: Dict[str, Dict] = {}
        for ticker, cand in candidates.items():
            if cand["decision"] not in ("ENTER", "WAIT"):
                continue
            regime    = cand.get("regime") or {}
            risk_pct  = regime.get("risk_pct", ACCOUNT.get("risk_per_trade", 0.05))
            if isinstance(risk_pct, (int, float)) and risk_pct > 1:
                risk_pct = risk_pct / 100
            stop_mult  = regime.get("stop_mult", 2.0)
            trail_mult = regime.get("trail_mult", 5.0)
            price      = cand.get("price", 0)
            atr        = cand.get("atr") or 0

            stop_price = price - stop_mult * atr if atr > 0 else price * 0.95
            sz = self._size(equity, risk_pct * risk_scale, price, stop_price)
            if sz["shares"] > 0:
                sizing[ticker] = {
                    **sz,
                    "stop_price":   stop_price,
                    "trail_mult":   trail_mult,
                    "regime_label": regime.get("label", "Normal"),
                    "is_high_vol":  regime.get("label", "") == "High Vol",
                }
        return sizing

    def _size(self, equity: float, risk_frac: float,
              entry: float, stop: float) -> Dict[str, Any]:
        risk_amount = equity * risk_frac
        risk_per_sh = entry - stop
        if risk_per_sh <= 0 or entry <= 0:
            return {"shares": 0, "cost": 0.0, "risk_amount": 0.0, "risk_frac": risk_frac}
        commission   = ACCOUNT.get("commission", 0.001)
        max_pos_size = ACCOUNT.get("max_position_size", 0.20)

        shares_by_risk  = int(risk_amount / risk_per_sh)
        max_shares_val  = int(equity * max_pos_size / (entry * (1 + commission)))
        shares          = min(shares_by_risk, max_shares_val)

        if shares <= 0:
            return {"shares": 0, "cost": 0.0, "risk_amount": 0.0, "risk_frac": risk_frac}
        cost = shares * entry * (1 + commission)
        return {"shares": shares, "cost": round(cost, 2),
                "risk_amount": round(risk_amount, 2), "risk_frac": round(risk_frac, 5)}

    # =========================================================================
    # Phase 5 — Replacement
    # =========================================================================

    def _find_replacement(
        self,
        exit_pos: Dict,
        candidates: Dict[str, Any],
        sizing: Dict[str, Dict],
        held: List[Dict],
        virtual_pending: List[Dict],
        sector_mom: Dict[str, float] = None,
    ) -> Optional[Dict]:
        exit_market  = exit_pos.get("market", "US")
        exit_sector  = exit_pos.get("sector", "Unknown")
        all_positions = held + virtual_pending
        sector_mom    = sector_mom or {}

        # Hysteresis hurdle: candidate must beat the exiting position's quality_score
        # by ≥15% to prevent marginal swaps that generate churn without real alpha lift.
        # When quality scores are not computed (backtest without quality filter), skip hurdle.
        exit_score = float(exit_pos.get("quality_score") or 0.0)
        has_scores = bool(self._quality_scores)
        # When quality scores not available (backtest), hold a fixed hurdle so
        # Phase-5 replacements are blocked and Phase-6 refills next day with the
        # freshest candidates (avoids same-day churn after a stop-loss exit).
        _hurdle    = abs(exit_score) * 0.15 if (exit_score and has_scores) else 5.0

        def _can(ticker, cand) -> Tuple[bool, str]:
            if cand.get("market") != exit_market:
                return False, "wrong_market"
            if cand["decision"] != "ENTER":
                return False, "not_ENTER"
            if ticker not in sizing:
                return False, "no_sizing"
            cand_score = float(cand.get("quality_score") or 0.0)
            if cand_score < (exit_score + _hurdle):
                return False, "hysteresis"
            return self._capacity_ok(ticker, cand, all_positions)

        # Sector rotation: if the exited sector has negative momentum, skip
        # same-sector replacement and look for a better sector immediately.
        exit_sector_declining = sector_mom.get(exit_sector, 0.0) < 0

        if not exit_sector_declining:
            for ticker, cand in candidates.items():
                ok, _ = _can(ticker, cand)
                if ok and cand.get("sector") == exit_sector:
                    return self._make_entry(ticker, cand, sizing)

        # Priority 2 — same market any sector (sector-momentum sorted)
        sorted_cands = sorted(
            candidates.items(),
            key=lambda kv: -(sector_mom.get(kv[1].get("sector", ""), 0.0)),
        )
        for ticker, cand in sorted_cands:
            ok, _ = _can(ticker, cand)
            if ok:
                return self._make_entry(ticker, cand, sizing)

        # Priority 3 — hold localized cash (no cross-market routing)
        return None

    # =========================================================================
    # Phase 6 — Fill remaining slots
    # =========================================================================

    def _fill_slots(
        self,
        candidates: Dict[str, Any],
        sizing: Dict[str, Dict],
        held: List[Dict],
        virtual_pending: List[Dict],
        replacement_queue: List[Dict],
        sector_mom: Dict[str, float] = None,
    ) -> List[Dict]:
        queued    = {r["ticker"] for r in replacement_queue}
        total_pos = len(held) + len(virtual_pending)
        available = RISK["MAX_OPEN_POSITIONS"] - total_pos
        sector_mom = sector_mom or {}

        # Sort ENTERs: positive sector momentum first, then quality, then lower vol
        enters = sorted(
            ((t, c) for t, c in candidates.items()
             if c["decision"] == "ENTER" and t not in queued and t in sizing),
            key=lambda x: (
                -(sector_mom.get(x[1].get("sector", ""), 0.0)),  # rising sectors first
                -(x[1].get("quality_score") or 0),               # higher quality next
                (x[1].get("atr_pct") or 999),                    # lower vol as tiebreaker
            ),
        )

        new_entries: List[Dict] = []
        all_pos = held + list(virtual_pending)

        for ticker, cand in enters:
            if available <= 0:
                break
            ok, _ = self._capacity_ok(ticker, cand, all_pos)
            if ok:
                entry = self._make_entry(ticker, cand, sizing)
                new_entries.append(entry)
                virtual_pending.append(entry)
                all_pos.append(entry)
                available -= 1

        return new_entries

    # =========================================================================
    # Market breadth helper (synthetic index regime signal)
    # =========================================================================

    def _market_breadth(
        self,
        market: str,
        tickers: List[str],
        data_map: Dict[str, pd.DataFrame],
        today: pd.Timestamp,
        sma_period: int = 200,
    ) -> Tuple[Optional[float], Optional[float]]:
        """
        Returns (pct_above_sma200, threshold=0.50) as a synthetic (index_close,
        index_sma200) pair for use in evaluate_gates.

        pct_above_sma200 > 0.50  → bullish breadth (elastic gates active)
        pct_above_sma200 <= 0.50 → bearish breadth (defensive gates tighten)

        Returns (None, None) when fewer than 10 tickers have sufficient data.
        """
        above = 0
        total = 0
        for ticker in tickers:
            df = data_map.get(ticker)
            if df is None:
                continue
            rows = df[df.index <= today]
            if len(rows) < sma_period:
                continue
            try:
                close = float(rows["Close"].iloc[-1])
                sma   = float(rows["Close"].iloc[-sma_period:].mean())
                if np.isnan(close) or np.isnan(sma) or sma == 0:
                    continue
                total += 1
                if close > sma:
                    above += 1
            except Exception:
                pass
        if total < 10:
            return None, None
        return above / total, 0.50

    # =========================================================================
    # Sector momentum helper
    # =========================================================================

    def _sector_momentum(
        self,
        data_map: Dict[str, pd.DataFrame],
        watchlist: Dict[str, List[str]],
        lookback: int = 63,
    ) -> Dict[str, float]:
        """
        Returns {sector: avg_pct_return} over the last `lookback` bars.
        Positive = sector trending up; negative = declining.
        Used for rotation: prefer entries in rising sectors; skip same-sector
        replacements when the exited sector is declining.
        """
        sector_rets: Dict[str, List[float]] = {}
        for tickers in watchlist.values():
            for ticker in tickers:
                df = data_map.get(ticker)
                if df is None or len(df) < lookback:
                    continue
                try:
                    ret = float(df["Close"].iloc[-1] / df["Close"].iloc[-lookback] - 1) * 100
                    sector = get_sector(ticker)
                    sector_rets.setdefault(sector, []).append(ret)
                except Exception:
                    pass
        return {s: sum(v) / len(v) for s, v in sector_rets.items() if v}

    # =========================================================================
    # Helpers
    # =========================================================================

    def _capacity_ok(
        self, ticker: str, cand: Dict, all_positions: List[Dict]
    ) -> Tuple[bool, str]:
        market   = cand.get("market", "US")
        sector   = cand.get("sector", "Unknown")
        is_hv    = cand.get("regime", {}).get("label", "") == "High Vol"
        max_open = RISK["MAX_OPEN_POSITIONS"]

        if len(all_positions) >= max_open:
            return False, "max_open_positions"

        def _resolve(limit_dict: dict, key: str, fallback) -> int:
            v = limit_dict.get(key, fallback)
            return int(max_open * v) if isinstance(v, float) and v <= 1.0 else int(v)

        mkt_count = sum(1 for p in all_positions if p.get("market") == market)
        if mkt_count >= _resolve(RISK["MAX_PER_MARKET"], market, 99):
            return False, "market_cap"

        sec_count = sum(1 for p in all_positions
                        if p.get("market") == market and p.get("sector") == sector)
        if sec_count >= _resolve(RISK["MAX_PER_SECTOR"], market, 99):
            return False, "sector_cap"

        if is_hv:
            hv_count = sum(1 for p in all_positions
                           if p.get("market") == market and p.get("is_high_vol", False))
            if hv_count >= RISK["MAX_HIGH_VOL_PER_MARKET"].get(market, 1):
                return False, "high_vol_cap"

        return True, ""

    def _make_entry(self, ticker: str, cand: Dict, sizing: Dict,
                    cross_market: bool = False) -> Dict:
        sz = sizing.get(ticker, {})
        reason = cand.get("reason", "")
        if cross_market:
            reason = f"[Cross-Market] {reason}"
        return {
            "ticker":      ticker,
            "market":      cand.get("market", "US"),
            "sector":      cand.get("sector", "Unknown"),
            "price":       cand.get("price", 0),
            "atr":         cand.get("atr", 0),
            "atr_pct":     cand.get("atr_pct", 0),
            "regime":      cand.get("regime", {}),
            "is_high_vol":  sz.get("is_high_vol", False),
            "stop_price":   sz.get("stop_price", 0),
            "trail_mult":   sz.get("trail_mult", 5.0),
            "shares":       sz.get("shares", 0),
            "cost":         sz.get("cost", 0),
            "risk_amount":  sz.get("risk_amount", 0),
            "decision":     "ENTER",
            "reason":       reason,
            "candidate":    cand,
            "sizing":       sz,
            "quality_score":  cand.get("quality_score"),
            "cross_market": cross_market,
        }

    def _circuit_breaker(self, equity: float) -> float:
        if self.peak_equity <= 0:
            return 1.0
        dd = (self.peak_equity - equity) / self.peak_equity

        scale = 1.0
        for band in sorted(RISK["DRAWDOWN_BANDS"], key=lambda x: -x["threshold"]):
            if dd >= band["threshold"]:
                scale = band["risk_scale"]
                break

        if self._circuit_scale < 1.0 and self._circuit_base_equity is not None:
            recovery = (equity - self._circuit_base_equity) / self._circuit_base_equity
            if recovery >= RISK["CIRCUIT_BREAKER_HYSTERESIS"]:
                scale = min(self._circuit_scale / min(b["risk_scale"]
                            for b in RISK["DRAWDOWN_BANDS"]), 1.0)

        if scale < self._circuit_scale:
            self._circuit_base_equity = equity
        self._circuit_scale = scale
        return scale
