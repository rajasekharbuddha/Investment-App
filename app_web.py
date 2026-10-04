#!/usr/bin/env python3
"""
Mastermind Pro — Browser Edition
Run: streamlit run app.py
Original Tkinter desktop app preserved as app_tkinter.py
"""

import contextlib
import io
import json
import math
import re
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).parent
SRC  = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


# ── Helpers ────────────────────────────────────────────────────────────────────

def _strip(text: str) -> str:
    """Remove ANSI colour codes so text renders cleanly in st.code()."""
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def _capture(fn, *args, **kwargs):
    """Run fn(*args, **kwargs), capturing all stdout. Returns (result, log)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        result = fn(*args, **kwargs)
    return result, buf.getvalue()


# ── Page config ────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Mastermind Pro",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
  /* tighten code blocks */
  .stCode { font-size: 12px; }
  /* status boxes */
  [data-testid="stStatusWidget"] { font-size: 13px; }
</style>
""", unsafe_allow_html=True)


# ── Sidebar ────────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("📈 Mastermind Pro")
    st.caption("ATR-Dynamic + Fundamental System")
    st.markdown("---")

    st.subheader("Account")
    equity_s     = st.number_input("Equity",       value=100_000, step=10_000, min_value=1_000)
    commission_s = st.number_input("Commission %", value=0.10, step=0.01, format="%.2f") / 100
    slippage_s   = st.number_input("Slippage %",   value=0.10, step=0.01, format="%.2f") / 100

    st.markdown("---")
    st.subheader("Strategy Flags")
    dynamic_universe_s = st.toggle("Dynamic Universe", value=True)
    quality_filter_s   = st.toggle("Quality Filter",   value=True)
    momentum_exit_s    = st.toggle("Momentum Exit",    value=True)

    st.markdown("---")
    st.caption("⚠ Research use only. Not financial advice.")


# ── Tabs ───────────────────────────────────────────────────────────────────────

(T_SCAN, T_UNIV, T_PT, T_BT, T_LTB, T_LTS, T_SEMI, T_WF, T_ST, T_MC, T_SIP, T_SIM,
 T_PORT, T_REP, T_BENCH, T_SET) = st.tabs([
    "📊 Daily Scan",
    "🌌 Universe",
    "🧾 Post-Trade",
    "📈 ST Backtest",
    "🏦 LT Backtest",
    "🔭 LT Screener",
    "🔬 Semis Backtest",
    "🔄 Walk-Forward",
    "💪 Stress Tests",
    "🎲 Monte Carlo",
    "💰 SIP Plan",
    "🧮 Compounding Sim",
    "💼 Portfolio",
    "📁 Reports",
    "🪑 Bench List",
    "⚙ Settings",
])


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — Daily Scan
# ══════════════════════════════════════════════════════════════════════════════

with T_SCAN:
    st.header("Daily Signal Scan")
    st.caption("5-gate ATR-Dynamic entry engine with NEAR signals and quality scoring.")

    c1, c2, c3 = st.columns([2, 1, 1])
    with c1:
        scan_markets = st.multiselect("Markets", ["IN", "US", "EU"], default=["IN"])
    with c2:
        scan_asof = st.text_input("As-of date", placeholder="YYYY-MM-DD (blank = today)")
    with c3:
        scan_skip_journal = st.checkbox("Skip journal update", value=True)

    if st.button("▶ Run Daily Scan", type="primary", key="btn_scan"):
        if not scan_markets:
            st.warning("Select at least one market.")
            st.stop()

        buf = io.StringIO()
        try:
            from config import (WATCHLIST, WATCHLIST_FLAT, MARKETS, ACCOUNT,
                                QUALITY_FILTER, DYNAMIC_UNIVERSE)
            from data import fetch_all
            from indicators import calculate_all
            from adaptive_tuner import AdaptiveTuner
            from decision_engine import DecisionEngine
            from report import daily_report

            # Per-region portfolio files (st = short-term, lt = long-term)
            def _port_file(strategy: str, market: str):
                return ROOT / "portfolio" / f"{strategy}_{market}.json"

            def _load_port(strategy: str, market: str) -> list:
                f = _port_file(strategy, market)
                if not f.exists():
                    return []
                try:
                    return json.loads(f.read_text(encoding="utf-8"))
                except Exception:
                    return []

            def _save_port(strategy: str, market: str, positions: list) -> None:
                f = _port_file(strategy, market)
                f.parent.mkdir(exist_ok=True)
                f.write_text(json.dumps(positions, indent=2))

            def _build_new_port(held, new_entries, replacement_queue, data_map, today_ts):
                new_port = list(held)
                for entry_info in new_entries + replacement_queue:
                    ticker = entry_info["ticker"]
                    close  = (float(data_map[ticker].iloc[-1]["Close"])
                              if ticker in data_map else entry_info.get("price", 0))
                    if entry_info.get("shares", 0) > 0:
                        regime = entry_info.get("regime", {})
                        new_port.append({
                            "ticker":            ticker,
                            "market":            entry_info.get("market", "US"),
                            "sector":            entry_info.get("sector", "Unknown"),
                            "entry_price":       close,
                            "entry_date":        today_ts.strftime("%Y-%m-%d"),
                            "shares":            entry_info.get("shares", 0),
                            "stop_loss":         entry_info.get("stop_price", close * 0.95),
                            "stop_loss_initial": entry_info.get("stop_price", close * 0.95),
                            "trail_mult":        entry_info.get("trail_mult", 5.0),
                            "peak_price":        close,
                            "atr_at_entry":      entry_info.get("atr", 0),
                            "risk_pct":          (regime.get("risk_pct", 0.05) if isinstance(regime, dict) else 0.05),
                            "regime":            (regime.get("label", "Normal") if isinstance(regime, dict) else "Normal"),
                            "is_high_vol":       entry_info.get("is_high_vol", False),
                            "cost":              entry_info.get("cost", 0),
                        })
                return new_port

            TUNER_FILE  = ROOT / "tuner_state.json"
            STATE_FILE  = ROOT / "state" / "last_decisions.json"
            PER_REGION_EQUITY = 100_000   # fixed 100k per region

            with st.status("Running daily scan…", expanded=True) as status:

                st.write("⚙ Building universe…")
                with contextlib.redirect_stdout(buf):
                    if dynamic_universe_s:
                        from universe import get_dynamic_watchlist
                        score_top_n = DYNAMIC_UNIVERSE["SCORE_TOP_N"]
                        active_wl = get_dynamic_watchlist(
                            scan_markets, score_top_n,
                            max_age_days=DYNAMIC_UNIVERSE.get("MAX_AGE_DAYS", 7))
                    else:
                        active_wl = {m: WATCHLIST[m] for m in scan_markets if m in WATCHLIST}

                st.write("📥 Fetching price data…")
                with contextlib.redirect_stdout(buf):
                    raw_data = fetch_all(active_wl, years=3)

                today_ts = (pd.Timestamp(scan_asof.strip()).normalize()
                            if scan_asof.strip()
                            else pd.Timestamp.today().normalize())

                st.write(f"⚙ Calculating indicators for {len(raw_data)} tickers…")
                with contextlib.redirect_stdout(buf):
                    data_map_full = {t: calculate_all(df) for t, df in raw_data.items()}
                    data_map = {
                        t: df[df.index <= today_ts]
                        for t, df in data_map_full.items()
                        if not df[df.index <= today_ts].empty
                    }

                    if dynamic_universe_s:
                        from select_stocks import dynamic_watchlist as _dyn_wl
                        active_wl = _dyn_wl(data_map, DYNAMIC_UNIVERSE["SCORE_TOP_N"],
                                             watchlist=active_wl)

                quality_scores, quality_filtered = {}, []
                if quality_filter_s:
                    st.write("🔎 Quality scoring universe…")
                    with contextlib.redirect_stdout(buf):
                        from select_stocks import quality_score_all, filter_by_quality
                        quality_scores = quality_score_all(data_map)
                        all_tickers = [t for tl in active_wl.values() for t in tl]
                        _, quality_filtered = filter_by_quality(
                            all_tickers, quality_scores,
                            min_score=QUALITY_FILTER.get("MIN_SCORE", 35))

                st.write("🤖 Running DecisionEngine — per-region 100k…")
                with contextlib.redirect_stdout(buf):
                    import config as _cfg
                    _cfg.MOMENTUM_EXIT["ENABLED"] = momentum_exit_s

                    tuner = AdaptiveTuner.load(str(TUNER_FILE))

                    # ── Per-region independent scan (100k equity each) ──────────
                    all_candidates, all_sizing = {}, {}
                    total_held = total_new = total_repl = 0
                    last_result = None

                    for market in scan_markets:
                        mkt_port = _load_port("st", market)
                        mkt_wl   = {market: active_wl.get(market, [])}

                        engine = DecisionEngine(tuner=tuner)
                        engine.peak_equity = PER_REGION_EQUITY

                        mkt_result = engine.run_day(
                            today=today_ts,
                            data_map=data_map,
                            portfolio=mkt_port,
                            equity=PER_REGION_EQUITY,
                            context="live",
                            watchlist=mkt_wl,
                            quality_scores=quality_scores if quality_filter_s else None,
                        )

                        new_port = _build_new_port(
                            mkt_result["held"],
                            mkt_result["new_entries"],
                            mkt_result["replacement_queue"],
                            data_map, today_ts,
                        )
                        _save_port("st", market, new_port)

                        total_held += len(mkt_result["held"])
                        total_new  += len(mkt_result["new_entries"])
                        total_repl += len(mkt_result["replacement_queue"])
                        all_candidates.update(mkt_result["candidates"])
                        all_sizing.update(mkt_result["sizing"])
                        last_result = mkt_result

                _port_msg = f"Portfolios saved: {total_held} held"
                if total_new:  _port_msg += f", +{total_new} new entries"
                if total_repl: _port_msg += f", +{total_repl} queued"
                st.write(f"💾 {_port_msg}  (100k per region)")

                st.write("📝 Generating report…")
                with contextlib.redirect_stdout(buf):
                    candidates = list(all_candidates.values())
                    for c in candidates:
                        sz = all_sizing.get(c["ticker"])
                        if sz:
                            c["sizing"] = sz

                    loaded_mode = last_result.get("loaded_tuner_mode", last_result["tuner_mode"])
                    report_text = daily_report(
                        decisions=candidates,
                        account_eur=PER_REGION_EQUITY,
                        watchlist=active_wl,
                        markets=MARKETS,
                        tuner_mode=loaded_mode,
                        risk_scale=last_result["risk_scale"],
                        quality_filtered=quality_filtered,
                        quality_scores=quality_scores,
                    )
                    tuner.save(str(TUNER_FILE))

                    STATE_FILE.parent.mkdir(exist_ok=True)
                    STATE_FILE.write_text(
                        json.dumps({"date": today_ts.strftime("%Y-%m-%d"),
                                    "decisions": candidates,
                                    "tuner_mode": loaded_mode},
                                   indent=2, default=str))

                status.update(label="Scan complete!", state="complete")

            # Metric bar
            enters = [c for c in candidates if c.get("decision") == "ENTER"]
            nears  = [c for c in candidates if c.get("decision") == "NEAR"]
            loaded_mode = last_result.get("loaded_tuner_mode", last_result["tuner_mode"])
            next_mode   = last_result["tuner_mode"]
            mode_label  = loaded_mode if loaded_mode == next_mode else f"{loaded_mode}→{next_mode}"
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("ENTER signals",   len(enters))
            m2.metric("NEAR signals",    len(nears))
            m3.metric("Tuner mode (used)", mode_label)
            m4.metric("Tickers scanned", len(candidates))

            st.code(_strip(report_text), language=None)

            with st.expander("📋 Progress log"):
                st.text(_strip(buf.getvalue()))

        except Exception as exc:
            st.error(f"Scan failed: {exc}")
            with st.expander("📋 Progress log"):
                st.text(_strip(buf.getvalue()))
            st.exception(exc)


# ══════════════════════════════════════════════════════════════════════════════
# TAB — Universe
# ══════════════════════════════════════════════════════════════════════════════

with T_UNIV:
    st.header("Universe Scoring")
    st.caption("Score all tickers by momentum velocity, SMA50 trend distance, and composite grade.")

    if st.button("▶ Score Universe", type="primary", key="btn_univ"):
        buf = io.StringIO()
        try:
            from config import WATCHLIST, DYNAMIC_UNIVERSE
            from data import fetch_all
            from indicators import calculate_all
            from stock_selector import score_all
            from report import quality_report

            with st.status("Scoring universe…", expanded=True) as status:
                with contextlib.redirect_stdout(buf):
                    if dynamic_universe_s:
                        from universe import get_dynamic_watchlist
                        score_top_n = DYNAMIC_UNIVERSE.get("SCORE_TOP_N", {})
                        active_wl = get_dynamic_watchlist(
                            None, score_top_n,
                            max_age_days=DYNAMIC_UNIVERSE.get("MAX_AGE_DAYS", 7))
                    else:
                        active_wl = WATCHLIST

                total = sum(len(v) for v in active_wl.values())
                st.write(f"📥 Fetching EOD data ({total} tickers)…")
                with contextlib.redirect_stdout(buf):
                    raw = fetch_all(active_wl, years=3)

                st.write(f"⚙ Calculating indicators for {len(raw)} tickers…")
                with contextlib.redirect_stdout(buf):
                    data_map = {t: calculate_all(df) for t, df in raw.items()}

                st.write("🔎 Scoring universe (momentum velocity + SMA50 trend)…")
                with contextlib.redirect_stdout(buf):
                    scores_df = score_all(data_map)
                    report_text = quality_report(scores_df, top_n=len(scores_df))

                status.update(label="Done!", state="complete")

            st.code(_strip(report_text), language=None)
            with st.expander("📋 Progress log"):
                st.text(_strip(buf.getvalue()))

        except Exception as exc:
            st.error(f"Universe scoring failed: {exc}")
            with st.expander("📋 Progress log"):
                st.text(_strip(buf.getvalue()))
            st.exception(exc)


# ══════════════════════════════════════════════════════════════════════════════
# TAB — Post-Trade
# ══════════════════════════════════════════════════════════════════════════════

with T_PT:
    st.header("Post-Trade Analysis")
    st.caption("Enrich today's WAIT/ENTER/NEAR journal rows with sizing, "
               "market-behaviour and Tier 3 reflection.")

    if st.button("▶ Run Post-Trade Analysis", type="primary", key="btn_pt"):
        buf = io.StringIO()
        try:
            from post_trade import run as pt_run

            with contextlib.redirect_stdout(buf):
                pt_run()

            st.code(_strip(buf.getvalue()), language=None)

        except Exception as exc:
            st.error(f"Post-trade analysis failed: {exc}")
            st.code(_strip(buf.getvalue()), language=None)
            st.exception(exc)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — Short-Term Backtest
# ══════════════════════════════════════════════════════════════════════════════

with T_BT:
    st.header("Short-Term Backtest  (ATR-Dynamic)")
    st.caption("Tick-by-tick simulation using the same DecisionEngine as the live scan.")

    c1, c2, c3 = st.columns(3)
    with c1:
        bt_market     = st.selectbox("Market", ["IN", "US", "EU", "ALL"], key="bt_market")
        bt_start      = st.date_input("Start", value=date(2016, 1, 1), key="bt_start")
        bt_end        = st.date_input("End",   value=date.today(),      key="bt_end")
    with c2:
        bt_equity     = st.number_input("Equity",       value=equity_s,             key="bt_equity")
        bt_commission = st.number_input("Commission %", value=commission_s * 100,
                                         step=0.01, format="%.2f", key="bt_comm") / 100
        bt_slippage   = st.number_input("Slippage %",   value=slippage_s * 100,
                                         step=0.01, format="%.2f", key="bt_slip") / 100
    with c3:
        bt_no_dyn     = st.checkbox("Use hardcoded watchlist (skip dynamic)", value=False)

    if st.button("▶ Run ST Backtest", type="primary", key="btn_bt"):
        buf = io.StringIO()
        try:
            from config import WATCHLIST, DYNAMIC_UNIVERSE
            from backtest import run_backtest
            from report import backtest_report

            with st.status("Running short-term backtest…", expanded=True) as status:
                active_markets = ([bt_market] if bt_market != "ALL" else list(WATCHLIST.keys()))
                use_dyn = dynamic_universe_s and not bt_no_dyn

                st.write("⚙ Building universe…")
                with contextlib.redirect_stdout(buf):
                    if use_dyn:
                        from universe import get_dynamic_watchlist
                        score_top_n = DYNAMIC_UNIVERSE.get("SCORE_TOP_N", {})
                        watchlist_override = get_dynamic_watchlist(
                            active_markets, score_top_n,
                            max_age_days=DYNAMIC_UNIVERSE.get("MAX_AGE_DAYS", 7))
                    else:
                        watchlist_override = {m: WATCHLIST[m] for m in active_markets if m in WATCHLIST}

                total_t = sum(len(v) for v in watchlist_override.values())
                st.write(f"📥 Fetching & simulating {total_t} tickers  "
                         f"({bt_start} → {bt_end})…")
                with contextlib.redirect_stdout(buf):
                    result = run_backtest(
                        market=bt_market,
                        start=str(bt_start),
                        end=str(bt_end),
                        initial_equity=bt_equity,
                        commission=bt_commission,
                        slippage=bt_slippage,
                        watchlist_override=watchlist_override,
                    )

                if "error" in result:
                    status.update(label="Error", state="error")
                    st.error(result["error"])
                else:
                    st.write("📝 Generating report…")
                    with contextlib.redirect_stdout(buf):
                        label = f"{bt_market}_only" if bt_market != "ALL" else "ALL_MARKETS"
                        report_text = backtest_report(result, market_label=label)
                    status.update(label="Backtest complete!", state="complete")

            if "error" not in result:
                m = result.get("metrics", {})
                m1, m2, m3, m4, m5 = st.columns(5)
                m1.metric("CAGR",        f"{m.get('cagr_pct', 0):+.2f}%")
                m2.metric("Total Return", f"{m.get('total_return_pct', 0):+.2f}%")
                m3.metric("Max DD",      f"{m.get('max_drawdown_pct', 0):.2f}%")
                m4.metric("Sharpe",      f"{m.get('sharpe_ratio', 0):.3f}")
                m5.metric("Trades",      m.get("total_trades", 0))

                ec = result.get("equity_curve", [])
                if ec:
                    ec_df = pd.DataFrame(ec).set_index("date")
                    ec_df.index = pd.to_datetime(ec_df.index)
                    st.subheader("Equity Curve")
                    st.area_chart(ec_df["equity"], width="stretch")

                st.code(_strip(report_text), language=None)

                trades = result.get("closed_trades", result.get("trades", []))
                if trades:
                    st.download_button(
                        "⬇ Download Trades CSV",
                        data=pd.DataFrame(trades).to_csv(index=False),
                        file_name=f"trades_{bt_market}_{bt_start}.csv",
                        mime="text/csv",
                    )

            with st.expander("📋 Progress log"):
                st.text(_strip(buf.getvalue()))

        except Exception as exc:
            st.error(f"Backtest failed: {exc}")
            with st.expander("📋 Progress log"):
                st.text(_strip(buf.getvalue()))
            st.exception(exc)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — Long-Term Backtest
# ══════════════════════════════════════════════════════════════════════════════

with T_LTB:
    st.header("Long-Term Backtest  (Quarterly Momentum Rebalancing)")
    st.caption("Three independent exit triggers: SMA breakdown · momentum floor · rotation.")

    c1, c2, c3 = st.columns(3)
    with c1:
        ltb_market    = st.selectbox("Market", ["IN", "US", "EU"], key="ltb_market")
        ltb_start     = st.date_input("Start", value=date(2015, 1, 1), key="ltb_start")
        ltb_end       = st.date_input("End",   value=date.today(),      key="ltb_end")
    with c2:
        ltb_equity    = st.number_input("Equity", value=equity_s, key="ltb_equity")
        ltb_slots     = st.number_input("Slots",  value=10, min_value=1, max_value=30, key="ltb_slots")
        ltb_rebalance = st.selectbox(
            "Rebalance interval",
            options=[21, 63, 126, 252],
            index=1,
            format_func=lambda x: {21: "Monthly (21d)", 63: "Quarterly (63d)",
                                    126: "Semi-Annual (126d)", 252: "Annual (252d)"}[x],
        )
    with c3:
        ltb_no_breakdown = st.checkbox("Disable SMA breakdown exit", value=False)
        ltb_mom_floor    = st.number_input("Momentum floor %", value=-5.0, step=1.0,
                                            help="Exit at rebalance if avg momentum < N%. -99 = OFF.")
        ltb_commission   = st.number_input("Commission %", value=commission_s * 100,
                                            step=0.01, format="%.2f", key="ltb_comm") / 100

    if st.button("▶ Run LT Backtest", type="primary", key="btn_ltb"):
        buf = io.StringIO()
        try:
            from config import WATCHLIST, DYNAMIC_UNIVERSE
            from data import fetch_all
            from indicators import calculate_all
            from backtest_longterm import run_longterm_backtest, longterm_backtest_report

            market = ltb_market.upper()
            with st.status("Running long-term backtest…", expanded=True) as status:

                st.write("⚙ Building universe…")
                with contextlib.redirect_stdout(buf):
                    if dynamic_universe_s:
                        from universe import get_dynamic_watchlist
                        score_top_n = {market: DYNAMIC_UNIVERSE.get("SCORE_TOP_N", {}).get(market, 250)}
                        wl = get_dynamic_watchlist([market], score_top_n,
                                                    max_age_days=DYNAMIC_UNIVERSE.get("MAX_AGE_DAYS", 7))
                    else:
                        wl = {market: WATCHLIST.get(market, [])}

                total_t = sum(len(v) for v in wl.values())
                years_needed = max(4, (date.today().year - ltb_start.year) + 3)
                st.write(f"📥 Fetching {total_t} tickers ({years_needed} yrs of history)…")
                with contextlib.redirect_stdout(buf):
                    raw_data = fetch_all(wl, years=years_needed)

                st.write(f"⚙ Calculating indicators for {len(raw_data)} tickers…")
                with contextlib.redirect_stdout(buf):
                    data_map = {t: calculate_all(df) for t, df in raw_data.items()}

                st.write("🏃 Running rebalancing simulation…")
                with contextlib.redirect_stdout(buf):
                    result = run_longterm_backtest(
                        market=market,
                        data_map=data_map,
                        start=str(ltb_start),
                        end=str(ltb_end),
                        equity=ltb_equity,
                        max_positions=int(ltb_slots),
                        rebalance_days=ltb_rebalance,
                        exit_on_breakdown=not ltb_no_breakdown,
                        momentum_floor=ltb_mom_floor / 100.0,
                        commission=ltb_commission,
                        slippage=slippage_s,
                    )
                    report_text = longterm_backtest_report(result)

                status.update(label="Done!", state="complete")

            m = result.get("metrics", {})
            m1, m2, m3, m4, m5 = st.columns(5)
            m1.metric("CAGR",         f"{m.get('cagr_pct', 0):+.2f}%")
            m2.metric("Total Return",  f"{m.get('total_return_pct', 0):+.2f}%")
            m3.metric("Max DD",        f"{m.get('max_drawdown_pct', 0):.2f}%")
            m4.metric("Sharpe",        f"{m.get('sharpe_ratio', 0):.3f}")
            m5.metric("Rebalances",    m.get("total_rebalances", "—"))

            ec = result.get("equity_curve")
            if ec is not None and len(ec) > 0:
                try:
                    if isinstance(ec, pd.Series):
                        # longterm backtest returns a pd.Series with datetime index
                        chart_data = ec
                    elif isinstance(ec, list) and isinstance(ec[0], dict):
                        tmp = pd.DataFrame(ec).set_index("date")
                        tmp.index = pd.to_datetime(tmp.index)
                        chart_data = tmp["equity"] if "equity" in tmp.columns else tmp.iloc[:, 0]
                    else:
                        chart_data = pd.Series(ec)
                    st.subheader("Equity Curve")
                    st.area_chart(chart_data, width="stretch")
                except Exception:
                    pass

            st.code(_strip(report_text), language=None)

            with st.expander("📋 Progress log"):
                st.text(_strip(buf.getvalue()))

        except Exception as exc:
            st.error(f"LT backtest failed: {exc}")
            with st.expander("📋 Progress log"):
                st.text(_strip(buf.getvalue()))
            st.exception(exc)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 4 — Long-Term Screener
# ══════════════════════════════════════════════════════════════════════════════

with T_LTS:
    st.header("Long-Term Fundamental Screener")
    st.caption("65% fundamental (Q-score) + 35% technical. Tiered BUY / NEAR / WATCH output.")

    c1, c2, c3 = st.columns(3)
    with c1:
        lts_markets = st.multiselect("Markets", ["IN", "US", "EU"], default=["IN"], key="lts_mkts")
    with c2:
        lts_min_q   = st.slider("Min technical Q-score", 30, 80, 55)
        lts_no_near = st.checkbox("ENTER signals only (exclude NEAR)", value=False)
    with c3:
        lts_refresh = st.checkbox("Refresh fundamental cache (force re-fetch)", value=False)
        lts_top_n   = st.number_input("Universe size (IN)", value=250, step=50, key="lts_topn")

    lts_c1, lts_c2 = st.columns(2)
    with lts_c1:
        lts_equity = st.number_input("Equity (for equal-weight sizing)", value=equity_s, key="lts_equity")
    with lts_c2:
        lts_slots  = st.number_input("Slots (equal-weight portfolio)", value=10, min_value=1, max_value=30, key="lts_slots")

    if st.button("▶ Run Screener", type="primary", key="btn_lts"):
        if not lts_markets:
            st.warning("Select at least one market.")
            st.stop()

        buf = io.StringIO()
        try:
            from run_longterm import run_longterm_screen

            with st.status("Running fundamental screener…", expanded=True) as status:
                with contextlib.redirect_stdout(buf):
                    run_longterm_screen(
                        markets=",".join(lts_markets),
                        min_q=lts_min_q,
                        include_near=not lts_no_near,
                        refresh_cache=lts_refresh,
                        top_n_in=int(lts_top_n),
                        equity=float(lts_equity),
                        max_positions=int(lts_slots),
                    )
                status.update(label="Screen complete!", state="complete")

            output = _strip(buf.getvalue())
            if output.strip():
                st.code(output, language=None)
            else:
                st.info("No output — check that universe tickers are downloadable.")

        except Exception as exc:
            st.error(f"Screener failed: {exc}")
            st.text(_strip(buf.getvalue()))
            st.exception(exc)


# ══════════════════════════════════════════════════════════════════════════════
# TAB — Semis Backtest (Semiconductor Map stocks, EUR)
# ══════════════════════════════════════════════════════════════════════════════

with T_SEMI:
    st.header("Semiconductor Map Backtest")
    st.caption("Stocks from semimap/ with every price converted to EUR: your model portfolio vs its "
               "core ETF, plus the short-term and long-term strategies run on the same stocks.")

    from semimap_backtest import MODES, CURVE_LABELS, load_map

    try:
        _, _sm_ports = load_map()
        _sm_port_ids, _sm_port_default = list(_sm_ports["portfolios"]), _sm_ports["default"]
    except Exception:
        _sm_port_ids, _sm_port_default = ["model"], "model"

    _sm_mode_labels = {"all": "All three", "portfolio": "Model portfolio",
                       "short": "Short-term strategy", "long": "Long-term strategy"}
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        sm_mode  = st.selectbox("Backtest", list(MODES), format_func=_sm_mode_labels.get, key="sm_mode")
        sm_port  = st.selectbox("Portfolio", _sm_port_ids, index=_sm_port_ids.index(_sm_port_default),
                                key="sm_port")
    with c2:
        sm_start = st.date_input("Start", value=pd.Timestamp("2016-01-01"), key="sm_start")
        sm_end   = st.date_input("End", value=pd.Timestamp.today(), key="sm_end")
    with c3:
        sm_equity = st.number_input("Starting capital (EUR)", value=10_000, min_value=100, step=1_000, key="sm_equity")
        sm_rebal  = st.number_input("Portfolio rebalance (trading days)", value=63, min_value=1, step=21, key="sm_rebal")
    with c4:
        sm_slots    = st.number_input("LT slots", value=6, min_value=1, max_value=19, key="sm_slots")
        sm_lt_rebal = st.number_input("LT rebalance (days)", value=63, min_value=1, step=21, key="sm_lt_rebal")
    sm_synth = st.checkbox("Offline test data (random walks, not market prices)", value=False, key="sm_synth")

    if st.button("▶ Run Semis Backtest", type="primary", key="btn_semis"):
        if sm_start >= sm_end:
            st.warning("Start must be before End.")
            st.stop()
        buf = io.StringIO()
        try:
            from semimap_backtest import run_semimap_backtest, save_semimap_report
            with st.status("Running Semis backtest…", expanded=True) as status:
                with contextlib.redirect_stdout(buf):
                    _sm_res = run_semimap_backtest(
                        mode=sm_mode, start=str(sm_start), end=str(sm_end),
                        equity=float(sm_equity), portfolio=sm_port,
                        rebalance=int(sm_rebal), slots=int(sm_slots),
                        lt_rebalance=int(sm_lt_rebal), synthetic=sm_synth,
                    )
                _sm_txt, _sm_csv = save_semimap_report(_sm_res)
                status.update(label="Semis backtest complete!", state="complete")
            st.session_state["semis_result"] = {**_sm_res, "saved": (_sm_txt.name, _sm_csv.name)}
        except Exception as exc:
            st.error(f"Semis backtest failed: {exc}")
            st.text(_strip(buf.getvalue()))
            st.exception(exc)

    _sm_last = st.session_state.get("semis_result")
    if _sm_last:
        import plotly.graph_objects as go
        if _sm_last["synthetic"]:
            st.warning("These results use offline test data (random walks), not market prices.")
        _sm_colors = {"model_portfolio": "#4e79a7", "core_etf_only": "#9c9c9c",
                      "short_term": "#59a14f", "long_term": "#f28e2b"}
        _sm_fig = go.Figure()
        for _k, _ser in _sm_last["curves"].items():
            _sm_fig.add_trace(go.Scatter(
                x=_ser.index, y=_ser.values, mode="lines", name=CURVE_LABELS.get(_k, _k),
                line=dict(color=_sm_colors.get(_k), width=2.5 if _k == "model_portfolio" else 1.6,
                          dash="dot" if _k == "core_etf_only" else "solid"),
            ))
        _sm_fig.update_layout(yaxis_title="Equity (EUR)", height=420,
                              margin=dict(l=10, r=10, t=30, b=10), hovermode="x unified")
        st.plotly_chart(_sm_fig, use_container_width=True)
        st.code(_strip(_sm_last["text"]), language=None)
        st.caption(f"Saved to reports/{_sm_last['saved'][0]} and reports/{_sm_last['saved'][1]}")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 5 — Walk-Forward
# ══════════════════════════════════════════════════════════════════════════════

with T_WF:
    st.header("Walk-Forward Optimisation")
    st.caption("Optimises gate parameters on rolling in-sample windows; evaluates out-of-sample.")

    c1, c2, c3 = st.columns(3)
    with c1:
        wf_market   = st.selectbox("Market", ["IN", "US", "EU", "ALL"], key="wf_market")
        wf_years    = st.number_input("Years of history", value=5, min_value=2, max_value=15, key="wf_years")
    with c2:
        wf_train    = st.number_input("Train window (trading days)", value=504, step=63, key="wf_train",
                                       help="504 ≈ 2 years")
        wf_test     = st.number_input("Test window  (trading days)", value=126, step=21,  key="wf_test",
                                       help="126 ≈ 6 months")
    with c3:
        wf_anchored = st.checkbox("Anchored (expanding) train window", value=False)
        wf_equity   = st.number_input("Equity", value=equity_s, key="wf_equity")

    if st.button("▶ Run Walk-Forward", type="primary", key="btn_wf"):
        buf = io.StringIO()
        try:
            from config import WATCHLIST, ACCOUNT
            from data import fetch_and_cache
            from indicators import calculate_all
            from walk_forward import walk_forward, format_wfo_summary

            with st.status("Running walk-forward…", expanded=True) as status:
                active = (["US", "EU", "IN"] if wf_market == "ALL" else [wf_market])
                wl = {m: WATCHLIST[m] for m in active if m in WATCHLIST}
                all_tickers = [t for tl in wl.values() for t in tl]

                st.write(f"📥 Fetching {len(all_tickers)} tickers ({wf_years} yrs)…")
                with contextlib.redirect_stdout(buf):
                    data_map_raw, stats = fetch_and_cache(all_tickers, years=wf_years)

                st.write(f"⚙ {stats['succeeded']}/{stats['attempted']} tickers ok. Computing indicators…")
                with contextlib.redirect_stdout(buf):
                    data_map   = {t: calculate_all(df) for t, df in data_map_raw.items()}
                    all_dates  = sorted({d for df in data_map.values() for d in df.index})

                st.write("🔄 Running walk-forward folds… (this may take several minutes)")
                with contextlib.redirect_stdout(buf):
                    result = walk_forward(
                        data_map=data_map,
                        watchlist=wl,
                        all_dates=all_dates,
                        train_size=int(wf_train),
                        test_size=int(wf_test),
                        anchored=wf_anchored,
                        initial_equity=wf_equity,
                        verbose=True,
                    )
                    report_text = format_wfo_summary(result)

                status.update(label="Done!", state="complete")

            st.code(_strip(report_text), language=None)
            with st.expander("📋 Progress log"):
                st.text(_strip(buf.getvalue()))

        except Exception as exc:
            st.error(f"Walk-forward failed: {exc}")
            with st.expander("📋 Progress log"):
                st.text(_strip(buf.getvalue()))
            st.exception(exc)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 6 — Stress Tests
# ══════════════════════════════════════════════════════════════════════════════

with T_ST:
    st.header("Stress Tests")
    st.caption("Historical windows (2008, 2020, 2022) + synthetic shocks (vol spike, liquidity collapse, gaps, correlation crisis).")

    c1, c2 = st.columns(2)
    with c1:
        st_market  = st.selectbox("Market", ["IN", "US", "EU", "ALL"], key="st_market")
        st_years   = st.number_input("Years of history", value=5, min_value=2, key="st_years")
        st_equity  = st.number_input("Equity", value=equity_s, key="st_equity")
    with c2:
        st_mode = st.radio("Scenarios to run", ["All", "Historical only", "Synthetic only"],
                            horizontal=True)

    if st.button("▶ Run Stress Tests", type="primary", key="btn_st"):
        buf = io.StringIO()
        try:
            from config import WATCHLIST
            from data import fetch_and_cache
            from indicators import calculate_all
            from stress_tests import (run_all_stress_tests, run_historical_stress,
                                       run_synthetic_stress, format_stress_summary)

            with st.status("Running stress tests…", expanded=True) as status:
                active      = (["US", "EU", "IN"] if st_market == "ALL" else [st_market])
                wl          = {m: WATCHLIST[m] for m in active if m in WATCHLIST}
                all_tickers = [t for tl in wl.values() for t in tl]

                st.write(f"📥 Fetching {len(all_tickers)} tickers…")
                with contextlib.redirect_stdout(buf):
                    data_map_raw, stats = fetch_and_cache(all_tickers, years=st_years)

                st.write(f"⚙ Computing indicators…")
                with contextlib.redirect_stdout(buf):
                    data_map = {t: calculate_all(df) for t, df in data_map_raw.items()}

                st.write(f"💪 Running {st_mode.lower()} scenarios…")
                with contextlib.redirect_stdout(buf):
                    if st_mode == "Historical only":
                        result = {"historical": run_historical_stress(data_map, wl, st_equity),
                                  "synthetic":  {}}
                    elif st_mode == "Synthetic only":
                        result = {"historical": {},
                                  "synthetic":  run_synthetic_stress(data_map, wl, st_equity)}
                    else:
                        result = run_all_stress_tests(data_map, wl, initial_equity=st_equity)

                    report_text = format_stress_summary(result)

                status.update(label="Done!", state="complete")

            st.code(_strip(report_text), language=None)
            with st.expander("📋 Progress log"):
                st.text(_strip(buf.getvalue()))

        except Exception as exc:
            st.error(f"Stress tests failed: {exc}")
            with st.expander("📋 Progress log"):
                st.text(_strip(buf.getvalue()))
            st.exception(exc)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 7 — Monte Carlo
# ══════════════════════════════════════════════════════════════════════════════

with T_MC:
    st.header("Monte Carlo Robustness Analysis")
    st.caption("Bootstraps the backtest trade log N times. Run ST Backtest first to generate a trades CSV.")

    reports_dir  = ROOT / "reports"
    trade_files  = sorted(reports_dir.glob("*-trades.csv"), reverse=True) if reports_dir.exists() else []
    file_options = {f.name: f for f in trade_files}

    c1, c2, c3 = st.columns(3)
    with c1:
        mc_n_sims    = st.number_input("Simulations", value=5_000, step=1_000, min_value=100)
    with c2:
        mc_skip_prob = st.number_input("Trade skip probability", value=0.05, step=0.01,
                                        format="%.2f", help="Randomly skip this fraction of trades.")
    with c3:
        mc_equity    = st.number_input("Equity", value=equity_s, key="mc_equity")

    mc_file = st.selectbox(
        "Trade CSV  (from a previous ST Backtest)",
        options=["(use latest)"] + list(file_options.keys()),
        help="Run the ST Backtest tab first — it saves a trades CSV to reports/.",
    )

    if st.button("▶ Run Monte Carlo", type="primary", key="btn_mc"):
        if not trade_files:
            st.error("No trades CSV found in reports/. Run the ST Backtest tab first.")
            st.stop()

        trades_path = (trade_files[0] if mc_file == "(use latest)"
                       else file_options[mc_file])

        buf = io.StringIO()
        try:
            from monte_carlo import run_monte_carlo, format_mc_summary

            with st.status("Running Monte Carlo…", expanded=True) as status:
                st.write(f"📥 Loading {trades_path.name}…")
                trades_df = pd.read_csv(trades_path)
                trades    = trades_df.to_dict("records")
                st.write(f"🎲 Running {mc_n_sims:,} simulations on {len(trades)} trades…")

                with contextlib.redirect_stdout(buf):
                    result = run_monte_carlo(
                        trades=trades,
                        initial_equity=mc_equity,
                        n_sims=int(mc_n_sims),
                        skip_prob=mc_skip_prob,
                        seed=42,
                    )
                    report_text = format_mc_summary(result)

                status.update(label="Done!", state="complete")

            # Percentile metrics
            pct = result.get("final_equity", {})
            if pct:
                cols = st.columns(5)
                labels = ["5th %ile", "25th %ile", "Median", "75th %ile", "95th %ile"]
                keys   = ["p5", "p25", "p50", "p75", "p95"]
                for col, key, lbl in zip(cols, keys, labels):
                    eq = pct.get(key)
                    col.metric(lbl, f"{eq:,.0f}" if isinstance(eq, (int, float)) else "—")

            # Percentile equity paths chart
            sample_paths = result.get("sample_paths", [])
            if sample_paths:
                # Paths can be shorter than others (random trade-skipping / early
                # ruin break in simulate_equity_curve), so forward-fill each path's
                # last value out to the longest path before stacking into an array.
                import numpy as np
                max_len = max(len(p) for p in sample_paths)
                arr = np.array([p + [p[-1]] * (max_len - len(p)) for p in sample_paths])
                chart_df = pd.DataFrame({
                    "p5":     np.percentile(arr, 5,  axis=0),
                    "p25":    np.percentile(arr, 25, axis=0),
                    "median": np.percentile(arr, 50, axis=0),
                    "p75":    np.percentile(arr, 75, axis=0),
                    "p95":    np.percentile(arr, 95, axis=0),
                })
                st.subheader("Percentile Equity Paths")
                st.line_chart(chart_df, width="stretch")

            st.code(_strip(report_text), language=None)

            with st.expander("📋 Progress log"):
                st.text(_strip(buf.getvalue()))

        except Exception as exc:
            st.error(f"Monte Carlo failed: {exc}")
            with st.expander("📋 Progress log"):
                st.text(_strip(buf.getvalue()))
            st.exception(exc)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 8 — SIP Plan
# ══════════════════════════════════════════════════════════════════════════════

with T_SIP:
    st.header("💰 Monthly SIP Plan — US · EU · India")
    sip_subtab_cycle, sip_subtab_bt = st.tabs(["📅 Monthly Cycle", "📊 SIP Backtest"])

    # ══════════════════════════════════════════════════════════════════════════
    # SUB-TAB A — Monthly Cycle
    # ══════════════════════════════════════════════════════════════════════════
    with sip_subtab_cycle:
        st.caption(
            "Deploy a fixed monthly budget per region into quality stocks. "
            "Selection: SMA uptrend + Q-score ≥ 55 + ranked by momentum. "
            "Exits: SMA breakdown (10d) or Q-score < 35."
        )

        # Per-region budgets in local currency
        st.markdown("**Monthly Budget per Region (local currency)**")
        _sbc1, _sbc2, _sbc3 = st.columns(3)
        with _sbc1:
            sip_budget_us = st.number_input("🇺🇸 US Budget ($)", min_value=100, max_value=100000,
                                            value=2000, step=100, key="sip_budget_us")
        with _sbc2:
            sip_budget_eu = st.number_input("🇪🇺 EU Budget (€)", min_value=100, max_value=100000,
                                            value=2000, step=100, key="sip_budget_eu")
        with _sbc3:
            sip_budget_in = st.number_input("🇮🇳 IN Budget (₹)", min_value=1000, max_value=2000000,
                                            value=20000, step=1000, key="sip_budget_in")

        sip_c2, sip_c3, sip_c4 = st.columns(3)
        with sip_c2:
            sip_markets_sel = st.multiselect("Markets", ["US", "EU", "IN"], default=["US", "EU", "IN"], key="sip_mkts")
        with sip_c3:
            sip_min_q = st.slider("Min Q-Score", 30, 80, 55, key="sip_minq")
        with sip_c4:
            sip_top_n = st.number_input("Universe size / market", min_value=20, max_value=500,
                                         value=100, step=10, key="sip_topn")

        _sip_region_budget = {"US": float(sip_budget_us), "EU": float(sip_budget_eu), "IN": float(sip_budget_in)}

        sip_dry = st.checkbox("Dry run (preview only — don't save state)", value=True, key="sip_dry")

        if st.button("▶ Run Monthly SIP Cycle", key="sip_run", type="primary"):
            if not sip_markets_sel:
                st.error("Select at least one market.")
            else:
                import sys as _sys
                _sys.path.insert(0, str(ROOT / "src"))

                with st.spinner("Building universe…"):
                    try:
                        from universe import get_dynamic_watchlist
                        top_n_map = {m: int(sip_top_n) for m in sip_markets_sel}
                        watchlist = get_dynamic_watchlist(sip_markets_sel, top_n_map=top_n_map)
                        all_tickers = [t for m in sip_markets_sel for t in watchlist.get(m, [])]
                    except Exception as _e:
                        st.error(f"Universe build failed: {_e}")
                        all_tickers = []

                if all_tickers:
                    _prog = st.progress(0, text="Fetching price data…")
                    with st.spinner("Fetching price data and indicators…"):
                        from data import fetch_history
                        from indicators import calculate_all
                        _data_map: dict = {}
                        for _i, _t in enumerate(all_tickers):
                            _prog.progress(int(_i / len(all_tickers) * 40), text=f"Price data {_i}/{len(all_tickers)}…")
                            try:
                                _df = fetch_history(_t, years=1)
                                if _df is not None and len(_df) >= 60:
                                    _data_map[_t] = calculate_all(_df)
                            except Exception:
                                pass

                    with st.spinner("Fetching fundamental Q-scores…"):
                        from fundamental import fetch_fundamentals, score_fundamentals
                        _q_scores: dict = {}
                        for _i, _t in enumerate(all_tickers):
                            _prog.progress(40 + int(_i / len(all_tickers) * 55), text=f"Q-scores {_i}/{len(all_tickers)}…")
                            try:
                                _raw = fetch_fundamentals(_t, use_cache=True)
                                _sc, _ = score_fundamentals(_raw)
                                _q_scores[_t] = _sc
                            except Exception:
                                _q_scores[_t] = 0.0

                    _prog.progress(95, text="Fetching regime benchmarks…")
                    _bench_tickers = {"US": "^GSPC", "EU": "^STOXX50E", "IN": "^NSEI"}
                    _bench_dfs_cyc: dict = {}
                    for _bm, _bt in _bench_tickers.items():
                        if _bm in sip_markets_sel:
                            try:
                                _bdf = fetch_history(_bt, years=1)
                                if _bdf is not None:
                                    _bench_dfs_cyc[_bm] = _bdf
                            except Exception:
                                pass

                    _prog.progress(100, text="Running SIP cycle…")
                    with st.spinner("Running SIP selection…"):
                        from sip_strategy import run_sip_cycle, SIP_CONFIG as _SC
                        _result = run_sip_cycle(
                            data_map=_data_map,
                            q_scores=_q_scores,
                            override_min_q=float(sip_min_q),
                            benchmark_dfs=_bench_dfs_cyc,
                            dry_run=bool(sip_dry),
                        )

                    _prog.empty()
                    st.success(f"Cycle complete — {datetime.now().strftime('%Y-%m-%d')}")

                    # ── Regime reserve status
                    _regime_st = _result.get("regime_status", {})
                    _dip_rsv   = _result.get("dip_reserve", {})
                    _r_sym     = _SC.get("region_symbol", {"US":"$","EU":"€","IN":"₹"})
                    _rsv_pct   = _SC.get("regime_reserve_pct", 0.10)
                    st.info(
                        f"**Regime Reserve ({_rsv_pct*100:.0f}%/mo):** "
                        + "  |  ".join(
                            f"{m}: {_regime_st.get(m,'—')}  (reserve: {_r_sym.get(m,'')}{_dip_rsv.get(m,0):,.0f})"
                            for m in sip_markets_sel
                        )
                    )

                    _exits = _result["exits"]
                    st.subheader(f"Exit / Trim Signals ({len(_exits)})")
                    if _exits:
                        st.dataframe([{
                            "Action": _e["action"], "Ticker": _e["ticker"],
                            "Shares": _e["shares"], "Price": round(_e["current_price"], 2),
                            "Gain %": _e["gain_pct"], "Reason": _e["reason"],
                        } for _e in _exits], use_container_width=True)
                    else:
                        st.info("No exit signals — all holdings healthy.")

                    # ── Per-region allocation tabs
                    _alloc      = _result["allocation"]
                    _candidates = _result["candidates"]
                    _r_sym      = _SC.get("region_symbol", {"US":"$","EU":"€","IN":"₹"})
                    _r_cur      = _SC.get("region_currency", {"US":"USD","EU":"EUR","IN":"INR"})
                    _r_bgt      = _sip_region_budget

                    st.subheader("This Month's Buys — by Region")
                    _cyc_tabs = st.tabs(["🌍 All"] + [
                        f"{'🇺🇸' if m=='US' else '🇪🇺' if m=='EU' else '🇮🇳'} {m} ({_r_cur.get(m,m)})"
                        for m in sip_markets_sel
                    ])
                    # All tab
                    with _cyc_tabs[0]:
                        if _alloc:
                            _all_rows = []
                            for _tk, _amt in _alloc.items():
                                _c  = next((x for x in _candidates if x["ticker"] == _tk), {})
                                _mk = _c.get("market", "")
                                _s  = _r_sym.get(_mk, "")
                                _all_rows.append({
                                    "Ticker": _tk, "Market": _mk, "Sector": _c.get("sector",""),
                                    "Amount": f"{_s}{_amt:,.0f}", "Currency": _r_cur.get(_mk,""),
                                    "~Shares": round(_amt/_c.get("price",1),1) if _c.get("price") else "—",
                                    "Q-Score": round(_c.get("q_score",0)),
                                    "Mom %":   round(_c.get("momentum",0)*100,1),
                                    "Score":   round(_c.get("composite",0),3),
                                })
                            st.dataframe(_all_rows, use_container_width=True)
                        else:
                            st.warning("No qualifying candidates this month — hold cash.")
                    # Per-region tabs
                    for _ri, _rm in enumerate(sip_markets_sel):
                        with _cyc_tabs[_ri + 1]:
                            _sym = _r_sym.get(_rm, "")
                            _bgt = _r_bgt.get(_rm, 0)
                            _mk_alloc = {t: v for t, v in _alloc.items()
                                         if next((x for x in _candidates if x["ticker"]==t),{}).get("market")==_rm}
                            st.caption(f"Budget: {_sym}{_bgt:,.0f} {_r_cur.get(_rm,'')}  |  Deployed: {_sym}{sum(_mk_alloc.values()):,.0f}")
                            if _mk_alloc:
                                _rrows = []
                                for _tk, _amt in _mk_alloc.items():
                                    _c = next((x for x in _candidates if x["ticker"]==_tk), {})
                                    _rrows.append({
                                        "Ticker": _tk, "Sector": _c.get("sector",""),
                                        f"Amount ({_r_cur.get(_rm,'')})": round(_amt, 2),
                                        "~Shares": round(_amt/_c.get("price",1),2) if _c.get("price") else "—",
                                        "Price":   round(_c.get("price",0), 2),
                                        "Q-Score": round(_c.get("q_score",0)),
                                        "Mom %":   round(_c.get("momentum",0)*100,1),
                                        "Score":   round(_c.get("composite",0),3),
                                    })
                                st.dataframe(_rrows, use_container_width=True)
                            else:
                                st.info(f"No qualifying {_rm} candidates this month.")

                    with st.expander(f"All {len(_candidates)} passing candidates"):
                        st.dataframe([{
                            "Ticker": _c["ticker"], "Market": _c["market"],
                            "Sector": _c["sector"], "Q-Score": round(_c["q_score"]),
                            "Mom %":  round(_c["momentum"]*100,1),
                            "Score":  round(_c["composite"],3),
                            "Price":  round(_c["price"],2),
                            "Selected": "✓" if _c["ticker"] in _alloc else "",
                        } for _c in _candidates], use_container_width=True)

                    with st.expander("Full text report"):
                        st.code(_result["report_text"])

                    if not sip_dry:
                        _rpath = ROOT / "reports" / f"sip-{datetime.now().strftime('%Y-%m-%d')}.txt"
                        _rpath.write_text(_result["report_text"], encoding="utf-8")
                        st.caption(f"Report saved → {_rpath}")

        with st.expander("Strategy Rules Reference"):
            st.markdown("""
**Entry criteria (all must pass):**
1. **Uptrend gate**: SMA_50 > SMA_200 — only buy stocks in confirmed long-term uptrend
2. **Quality gate**: Fundamental Q-score ≥ 55 (ROE, revenue growth, EPS growth, D/E, margins, FCF yield, PEG, P/B, net margin)
3. **Ranking**: composite = 40% Q-score + 60% momentum (avg of 1M / 3M / 6M / 12M returns)

**Allocation:** Equal weight across top 5 picks · Min €200/stock · Sector cap 25% of portfolio

**Exit signals:** SMA_50 < SMA_200 for ≥10 days → EXIT · Q-score < 35 → EXIT · Position > 15% → TRIM to 10%
            """)

        st.subheader("Current SIP Holdings")
        from sip_strategy import load_sip_holdings
        _sip_state = load_sip_holdings()
        _holdings  = _sip_state.get("holdings", {})
        st.caption(
            f"Total deployed: €{_sip_state.get('total_deployed', 0):,.0f}  |  "
            f"Positions: {len(_holdings)}  |  Started: {_sip_state.get('start_date', '—')}"
        )
        if _holdings:
            st.dataframe([{
                "Ticker": _t, "Market": _h.get("market", ""), "Sector": _h.get("sector", ""),
                "Shares": _h.get("shares", 0), "Avg Cost €": round(_h.get("avg_cost", 0), 2),
                "Invested €": round(_h.get("total_cost_eur", 0), 2),
                "First Bought": _h.get("first_bought", ""), "Last Added": _h.get("last_added", ""),
            } for _t, _h in _holdings.items()], use_container_width=True)
        else:
            st.info("No SIP positions yet. Run the first monthly cycle above.")

    # ══════════════════════════════════════════════════════════════════════════
    # SUB-TAB B — SIP Backtest
    # ══════════════════════════════════════════════════════════════════════════
    with sip_subtab_bt:
        st.caption(
            "Simulate the SIP strategy over a historical period. "
            "Each region uses its own local-currency budget. "
            "Performance measured by XIRR per region — no FX conversion."
        )

        _sbt_c1, _sbt_c2, _sbt_c3 = st.columns(3)
        with _sbt_c1:
            _sbt_start = st.text_input("Start date", value="2016-01-01", key="sbt_start")
        with _sbt_c2:
            _sbt_picks = st.slider("Max picks / month", 1, 10, 5, key="sbt_picks")
        with _sbt_c3:
            _sbt_topn = st.number_input("Universe size / market", min_value=20, max_value=500,
                                         value=50, step=10, key="sbt_topn")

        st.markdown("**Monthly Budget per Region (local currency)**")
        _sbtb1, _sbtb2, _sbtb3 = st.columns(3)
        with _sbtb1:
            _sbt_bgt_us = st.number_input("🇺🇸 US Budget ($)", min_value=100, max_value=100000,
                                          value=2000, step=100, key="sbt_bgt_us")
        with _sbtb2:
            _sbt_bgt_eu = st.number_input("🇪🇺 EU Budget (€)", min_value=100, max_value=100000,
                                          value=2000, step=100, key="sbt_bgt_eu")
        with _sbtb3:
            _sbt_bgt_in = st.number_input("🇮🇳 IN Budget (₹)", min_value=1000, max_value=2000000,
                                          value=20000, step=1000, key="sbt_bgt_in")

        _sbt_region_budget = {"US": float(_sbt_bgt_us), "EU": float(_sbt_bgt_eu), "IN": float(_sbt_bgt_in)}
        _sbt_markets = st.multiselect("Markets", ["US", "EU", "IN"], default=["US", "EU", "IN"], key="sbt_mkts")

        st.info(
            "⏱ First run fetches 11 years of price history (~10–20 min for large universe). "
            "Subsequent runs use the parquet cache and are much faster. "
            "Set universe size to 50 for a quick test run."
        )

        if st.button("▶ Run SIP Backtest", key="sbt_run", type="primary"):
            if not _sbt_markets:
                st.error("Select at least one market.")
            else:
                _sbt_prog = st.progress(0, text="Building universe…")

                # Universe
                try:
                    from universe import get_dynamic_watchlist
                    _sbt_wl = get_dynamic_watchlist(_sbt_markets, top_n_map={m: int(_sbt_topn) for m in _sbt_markets})
                    _sbt_tickers = [t for m in _sbt_markets for t in _sbt_wl.get(m, [])]
                except Exception as _e:
                    st.error(f"Universe failed: {_e}")
                    _sbt_tickers = []

                if _sbt_tickers:
                    # Price data — 11-year history
                    _sbt_prog.progress(5, text=f"Fetching price history for {len(_sbt_tickers)} tickers…")
                    from data import fetch_history
                    from indicators import calculate_all
                    _sbt_data: dict = {}
                    for _si, _stt in enumerate(_sbt_tickers):
                        _sbt_prog.progress(5 + int(_si / len(_sbt_tickers) * 70),
                                           text=f"Price data {_si}/{len(_sbt_tickers)}…")
                        try:
                            _sdf = fetch_history(_stt, years=11)
                            if _sdf is not None and len(_sdf) >= 250:
                                _sbt_data[_stt] = calculate_all(_sdf)
                        except Exception:
                            pass

                    # Per-region benchmarks
                    _sbt_bench_tickers = {"US": "^GSPC", "EU": "^STOXX50E", "IN": "^NSEI"}
                    _sbt_bench_dfs: dict = {}
                    for _bm, _bt in _sbt_bench_tickers.items():
                        if _bm in _sbt_markets:
                            _sbt_prog.progress(77, text=f"Fetching benchmark {_bt}…")
                            try:
                                _bdf = fetch_history(_bt, years=11)
                                if _bdf is not None:
                                    _sbt_bench_dfs[_bm] = _bdf
                            except Exception:
                                pass

                    # Run backtest with C2 regime reserve
                    _sbt_prog.progress(90, text="Simulating…")
                    from backtest_sip import run_sip_backtest
                    _sbt_result = run_sip_backtest(
                        data_map=_sbt_data,
                        benchmark_dfs=_sbt_bench_dfs,
                        start=_sbt_start,
                        region_budget=_sbt_region_budget,
                        max_picks=int(_sbt_picks),
                        markets=_sbt_markets,
                        regime_reserve_pct=0.10,
                    )
                    _sbt_prog.progress(100, text="Done")
                    _sbt_prog.empty()

                    if "error" in _sbt_result:
                        st.error(_sbt_result["error"])
                    else:
                        st.caption(
                            f"Cycles: {_sbt_result['n_cycles']}  |  "
                            f"Max DD: {_sbt_result['max_drawdown']:+.1f}%  |  "
                            f"Avg positions: {_sbt_result['avg_positions']:.1f}"
                        )

                        # ── Regional sub-tabs (primary results view)
                        _r_sym   = _sbt_result.get("region_symbol",    {"US":"$","EU":"€","IN":"₹"})
                        _r_cur   = _sbt_result.get("region_currency",  {"US":"USD","EU":"EUR","IN":"INR"})
                        _r_xirr  = _sbt_result.get("region_xirr",       {})
                        _r_bxirr = _sbt_result.get("region_benchmark_xirr", {})
                        _r_inv   = _sbt_result.get("region_invested",   {})
                        _r_fin   = _sbt_result.get("region_final",      {})
                        _r_gain  = _sbt_result.get("region_gain",       {})
                        _r_nav   = _sbt_result.get("region_nav_hist",   {})
                        _r_yr    = _sbt_result.get("region_year_returns",{})
                        _r_bgt   = _sbt_result.get("region_budget",     {})
                        _active_mkts = _sbt_result.get("markets", _sbt_markets)

                        _flag   = {"US":"🇺🇸","EU":"🇪🇺","IN":"🇮🇳"}
                        _rlabels = [f"{_flag.get(m,m)} {m} ({_r_cur.get(m,m)})" for m in _active_mkts]
                        if _rlabels:
                            _rtabs = st.tabs(_rlabels)
                            for _ri, _rm in enumerate(_active_mkts):
                                with _rtabs[_ri]:
                                    _sym  = _r_sym.get(_rm, "")
                                    _cur  = _r_cur.get(_rm, "")
                                    _xi   = _r_xirr.get(_rm, float("nan"))
                                    _bxi  = _r_bxirr.get(_rm, float("nan"))
                                    _inv  = _r_inv.get(_rm, 0.0)
                                    _fin  = _r_fin.get(_rm, 0.0)
                                    _gn   = _r_gain.get(_rm, 0.0)
                                    _bgt  = _r_bgt.get(_rm, 0.0)
                                    _alpha = _xi - _bxi if not math.isnan(_xi) and not math.isnan(_bxi) else float("nan")

                                    st.caption(f"Budget: {_sym}{_bgt:,.0f}/month {_cur}  |  Total invested: {_sym}{_inv:,.0f}  →  Final: {_sym}{_fin:,.0f}")
                                    _rc1, _rc2, _rc3, _rc4 = st.columns(4)
                                    _rc1.metric("XIRR (portfolio)",
                                                f"{_xi*100:.2f}%" if not math.isnan(_xi) else "N/A")
                                    _rc2.metric("Benchmark XIRR",
                                                f"{_bxi*100:.2f}%" if not math.isnan(_bxi) else "N/A")
                                    _rc3.metric("Alpha",
                                                f"{_alpha*100:+.2f}%" if not math.isnan(_alpha) else "N/A")
                                    _rc4.metric("Total Gain",
                                                f"{_sym}{_gn:,.0f} ({_gn/_inv*100:+.0f}%)" if _inv else "—")

                                    # NAV chart in local currency
                                    _rnav_list = _r_nav.get(_rm, [])
                                    if _rnav_list:
                                        _rnav_df = pd.DataFrame(_rnav_list)
                                        _rnav_df["date"] = pd.to_datetime(_rnav_df["date"])
                                        _rnav_df = _rnav_df.set_index("date")
                                        st.subheader(f"NAV vs Invested ({_cur})")
                                        st.line_chart(_rnav_df[["nav", "invested"]])

                                    # Year-by-year for this region
                                    _yr_rows = _r_yr.get(_rm, [])
                                    if _yr_rows:
                                        st.subheader(f"Year-by-Year Returns ({_cur}, Modified Dietz)")
                                        st.dataframe([{
                                            "Year": r["year"],
                                            f"NAV Start ({_sym})": f"{r['nav_start']:,.0f}",
                                            f"Contributed ({_sym})": f"{r['contributions']:,.0f}",
                                            f"NAV End ({_sym})": f"{r['nav_end']:,.0f}",
                                            "Return %": f"{r['return_pct']:+.1f}%",
                                        } for r in _yr_rows], use_container_width=True, hide_index=True)

                        # ── Full report
                        with st.expander("Full text report"):
                            st.code(_sbt_result["report_text"])

                        # ── Save report
                        _sbt_rpath = (ROOT / "reports" /
                                      f"sip-backtest-{datetime.now().strftime('%Y-%m-%d')}-"
                                      f"{'_'.join(_sbt_markets)}.txt")
                        _sbt_rpath.write_text(_sbt_result["report_text"], encoding="utf-8")
                        st.caption(f"Report saved → {_sbt_rpath}")

        with st.expander("ℹ Backtest Limitations"):
            st.markdown("""
- **Survivorship bias**: Uses current universe. Stocks that were delisted, went bankrupt, or were removed from indices are absent — real results would be lower.
- **No historical Q-scores**: Fundamental gate (Q ≥ 55) is omitted. The backtest uses technical momentum ranking only. Live trading with Q-score filter may perform differently.
- **Index-drift**: Current S&P 500 / DAX / FTSE constituents are used throughout. A live strategy tracks constituent changes monthly.
- **XIRR vs CAGR**: XIRR measures the annualised return on each euro of capital from the date it was deployed. It is the correct metric for SIP evaluation. Standard CAGR applied to total invested would overstate performance.
            """)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 9 — Compounding Simulator
# ══════════════════════════════════════════════════════════════════════════════

with T_SIM:
    st.header("🧮 Financial Compounding Simulator")
    st.caption(
        "Two-phase wealth model — Phase 1 'Matching Velocity' (active monthly contributions matching "
        "the lump sum's organic growth) followed by Phase 2 'Pure Compounding' (contributions drop to zero). "
        "Independent of the trading strategies in the other tabs — pure compound-interest mathematics."
    )

    import sys as _sys
    _sys.path.insert(0, str(ROOT / "src"))
    from compounding_simulator import REGION_CONFIG as _SIM_CCY
    from compounding_simulator import format_amount as _fmt_amount, format_amount_abbrev as _fmt_amount_abbrev

    def _sim_on_region_change():
        _r = st.session_state["sim_region"]
        _c = _SIM_CCY[_r]
        st.session_state["sim_capital"] = _c["default_capital"]
        st.session_state["sim_p1_contribution"] = _c["default_contribution"]

    sim_region = st.selectbox(
        "Currency / Region", ["IN", "US", "EU"],
        format_func=lambda r: f"{_SIM_CCY[r]['flag']} {_SIM_CCY[r]['name']} ({_SIM_CCY[r]['symbol']})",
        key="sim_region", on_change=_sim_on_region_change,
    )
    _ccy = _SIM_CCY[sim_region]

    def _esc_md(s: str) -> str:
        """Escape $ so Streamlit's markdown/KaTeX renderer doesn't mangle it
        (a bare $ opens inline math mode in st.caption/markdown/info/help)."""
        return s.replace("$", "\\$")

    def _fmt_full(amount: float) -> str:
        """Full-precision amount, locale-grouped, markdown-safe (for captions/help text)."""
        return _esc_md(_fmt_amount(amount, _ccy))

    # ── Inputs
    _sim_c1, _sim_c2, _sim_c3, _sim_c4 = st.columns(4)
    with _sim_c1:
        if sim_region == "IN":
            sim_capital_raw = st.number_input(
                f"Initial capital ({_ccy['symbol']} Cr)", min_value=0.01, max_value=_ccy["capital_max"],
                value=_ccy["default_capital"], step=_ccy["capital_step"], format=_ccy["capital_fmt"],
                key="sim_capital",
            )
            sim_initial_capital = sim_capital_raw * _ccy["big_unit"]
        else:
            sim_capital_raw = st.number_input(
                f"Initial capital ({_ccy['symbol']})", min_value=1000.0, max_value=_ccy["capital_max"],
                value=_ccy["default_capital"], step=_ccy["capital_step"], format=_ccy["capital_fmt"],
                key="sim_capital",
            )
            sim_initial_capital = sim_capital_raw
    with _sim_c2:
        sim_roi_pct = st.slider("Target annual ROI (%)", min_value=8.0, max_value=15.0,
                                 value=12.0, step=0.5, key="sim_roi")
    with _sim_c3:
        sim_p1_years = st.number_input("Phase 1 years", min_value=1, max_value=20, value=6, step=1, key="sim_p1y")
    with _sim_c4:
        sim_p2_years = st.number_input("Phase 2 years", min_value=1, max_value=30, value=10, step=1, key="sim_p2y")

    sim_target_roi = sim_roi_pct / 100.0
    _sim_auto_contribution = sim_initial_capital * sim_target_roi / 12.0

    def _sim_use_auto_contribution():
        st.session_state["sim_p1_contribution"] = round(_sim_auto_contribution, -2)

    _sim_cc1, _sim_cc2 = st.columns([3, 1])
    with _sim_cc1:
        sim_monthly_contribution = st.number_input(
            f"Phase 1 monthly contribution ({_ccy['symbol']})", min_value=0.0, max_value=1_00_00_000.0,
            value=_ccy["default_contribution"], step=_ccy["contribution_step"], format="%.0f",
            key="sim_p1_contribution",
            help="Active amount you manually contribute each month during Phase 1. Held flat for the whole phase.",
        )
    with _sim_cc2:
        st.markdown("<div style='height: 1.85em'></div>", unsafe_allow_html=True)
        st.button(
            "↺ Match auto", key="sim_p1_auto_btn", on_click=_sim_use_auto_contribution,
            help=f"Set to {_fmt_full(_sim_auto_contribution)}/month — matches the lump sum's initial annual "
                 f"organic growth ({_fmt_full(sim_initial_capital * sim_target_roi)}/yr ÷ 12) at the current "
                 f"capital and ROI.",
        )

    st.caption(
        f"Held flat at **{_fmt_full(sim_monthly_contribution)}/month** for all {int(sim_p1_years)} years of Phase 1. "
        f"Auto-match value at current capital/ROI: {_fmt_full(_sim_auto_contribution)}/month."
    )

    sim_mode = st.radio(
        "Mode", ["Flat ROI (deterministic)", "Stress Test (volatility / sequence-of-returns risk)"],
        horizontal=True, key="sim_mode",
    )
    _sim_stress = sim_mode.startswith("Stress")

    if _sim_stress:
        _sim_v1, _sim_v2 = st.columns(2)
        with _sim_v1:
            sim_volatility_pct = st.slider("Annual return volatility (std dev, %)", min_value=5.0, max_value=30.0,
                                            value=15.0, step=1.0, key="sim_vol")
        with _sim_v2:
            sim_n_sims = st.number_input("Monte Carlo simulations", min_value=50, max_value=5000,
                                          value=500, step=50, key="sim_nsims")

    if st.button("▶ Run Simulation", type="primary", key="sim_run"):
        import sys as _sys
        _sys.path.insert(0, str(ROOT / "src"))
        from compounding_simulator import (
            simulate, rule_of_72_check, milestone_crossings, monte_carlo, executive_summary,
        )

        # Deterministic baseline always computed — used for the ledger, chart, and Rule-of-72 check,
        # and as a comparison point even in stress-test mode.
        _sim_df = simulate(
            initial_capital=sim_initial_capital,
            target_roi=sim_target_roi,
            phase1_years=int(sim_p1_years),
            phase2_years=int(sim_p2_years),
            phase1_monthly_contribution=float(sim_monthly_contribution),
        )
        _milestones_hit = milestone_crossings(_sim_df, _ccy["milestones"])
        _r72 = rule_of_72_check(sim_target_roi)

        _mc = None
        if _sim_stress:
            with st.spinner(f"Running {int(sim_n_sims)} randomized simulations…"):
                _goal_amount = list(_ccy["milestones"].values())[-1]
                _mc = monte_carlo(
                    initial_capital=sim_initial_capital,
                    target_roi=sim_target_roi,
                    phase1_years=int(sim_p1_years),
                    phase2_years=int(sim_p2_years),
                    phase1_monthly_contribution=float(sim_monthly_contribution),
                    volatility=sim_volatility_pct / 100.0,
                    n_sims=int(sim_n_sims),
                    goal=_goal_amount,
                )

        # Stash everything needed to render, keyed by the inputs active at run time — so
        # later widget interactions (e.g. toggling the ledger's Yearly/Monthly view) just
        # rerun the script without wiping these results or re-running the simulation.
        st.session_state["sim_results"] = {
            "df": _sim_df, "milestones_hit": _milestones_hit, "r72": _r72, "mc": _mc,
            "ccy": _ccy, "region": sim_region, "roi_pct": sim_roi_pct,
            "p1_years": int(sim_p1_years), "p2_years": int(sim_p2_years),
            "stress": _sim_stress, "volatility_pct": sim_volatility_pct if _sim_stress else None,
            "n_sims": int(sim_n_sims) if _sim_stress else None,
        }

    # ── Render last-run results (persisted in session_state so widget interactions below,
    # like the ledger view toggle, don't clear the results the way rerunning inside the
    # button block would).
    if "sim_results" in st.session_state:
        _res = st.session_state["sim_results"]
        _sim_df, _r_ccy = _res["df"], _res["ccy"]

        def _r_fmt_full(amount: float) -> str:
            return _fmt_amount(amount, _r_ccy)

        def _r_fmt_big(amount: float) -> str:
            """Abbreviated amount for tables/metrics/prose. INR always shows in Cr
            (the idiomatic unit regardless of magnitude); USD/EUR switch between K
            and M so a $100K capital doesn't read as the awkward '$0.10 M'."""
            return _fmt_amount_abbrev(amount, _r_ccy)

        def _r_esc_md(s: str) -> str:
            """Escape $ for markdown/KaTeX contexts (st.caption/markdown/info/metric label)."""
            return s.replace("$", "\\$")

        import sys as _sys
        _sys.path.insert(0, str(ROOT / "src"))
        import plotly.graph_objects as go
        from compounding_simulator import executive_summary as _executive_summary

        # ── Executive Summary
        st.subheader("Executive Summary")
        _exec_df = _executive_summary(_sim_df)
        st.dataframe(
            [{"Year": int(r["Year"]), "Balance": _r_fmt_big(r["Balance"])} for _, r in _exec_df.iterrows()],
            use_container_width=True, hide_index=True,
        )

        # ── Milestones
        _ms_cols = st.columns(len(_r_ccy["milestones"]))
        for _col, (_label, _yr) in zip(_ms_cols, _res["milestones_hit"].items()):
            with _col:
                st.metric(_r_esc_md(f"{_r_ccy['symbol']}{_label}"), f"{_yr:.2f} yr" if _yr is not None else "not reached")

        # ── Rule of 72 validation
        _r72 = _res["r72"]
        st.info(
            f"**Rule of 72 validation** — theoretical doubling time at {_res['roi_pct']:.1f}% ROI: "
            f"72 ÷ {_res['roi_pct']:.1f} = **{_r72['theoretical_years']:.2f} years**. "
            f"Actual monthly-compounded doubling time (no contributions): **{_r72['actual_years']:.2f} years** "
            f"({_r72['difference_years']:+.2f} yr vs. the Rule-of-72 approximation — monthly compounding at a "
            f"fixed annual rate doesn't land exactly on the simplified rule)."
        )

        # ── Chart — Plotly, phase-colour-coded with milestone lines
        _p1_df = _sim_df[_sim_df["Phase"] == 1]
        _p2_df = _sim_df[_sim_df["Phase"] == 2]

        _fig = go.Figure()
        _fig.add_trace(go.Scatter(
            x=_p1_df["GlobalMonth"] / 12.0, y=_p1_df["EndingBalance"] / _r_ccy["big_unit"],
            mode="lines", name="Phase 1 — Matching Velocity",
            line=dict(color="#e15759", width=2.5),
        ))
        _fig.add_trace(go.Scatter(
            x=_p2_df["GlobalMonth"] / 12.0, y=_p2_df["EndingBalance"] / _r_ccy["big_unit"],
            mode="lines", name="Phase 2 — Pure Compounding",
            line=dict(color="#4e79a7", width=2.5),
        ))
        for _label, _amount in _r_ccy["milestones"].items():
            _fig.add_hline(
                y=_amount / _r_ccy["big_unit"], line_dash="dot", line_color="gray", opacity=0.6,
                annotation_text=f"{_r_ccy['symbol']}{_label}", annotation_position="right",
            )
        _fig.update_layout(
            xaxis_title="Years", yaxis_title=f"Portfolio Value ({_r_ccy['symbol']} {_r_ccy['big_label']})",
            height=480, margin=dict(l=10, r=10, t=30, b=10),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        )
        st.plotly_chart(_fig, use_container_width=True)

        # ── Ledger (yearly aggregation by default, monthly on request)
        st.subheader("Ledger")
        _ledger_view = st.radio("View", ["Yearly", "Monthly"], horizontal=True, key="sim_ledger_view")
        if _ledger_view == "Yearly":
            _ledger = _sim_df.groupby("Year").agg(
                Phase=("Phase", "first"),
                StartingBalance=("StartingBalance", "first"),
                ActiveContribution=("Contribution", "sum"),
                GrowthEarned=("GrowthEarned", "sum"),
                EndingBalance=("EndingBalance", "last"),
            ).reset_index()
        else:
            _ledger = _sim_df.rename(columns={"Contribution": "ActiveContribution"})[
                ["Year", "Month", "Phase", "StartingBalance", "ActiveContribution", "GrowthEarned", "EndingBalance"]
            ]
        _money_cols = ["StartingBalance", "ActiveContribution", "GrowthEarned", "EndingBalance"]
        _ledger_display = _ledger.copy()
        _ledger_display["Phase"] = _ledger_display["Phase"].map({1: "1 — Matching Velocity", 2: "2 — Pure Compounding"})
        for _col in _money_cols:
            _ledger_display[_col] = _ledger_display[_col].apply(_r_fmt_full)
        st.dataframe(_ledger_display, use_container_width=True, hide_index=True)
        st.download_button(
            "⬇ Download Ledger CSV (raw numbers)", _ledger.round(2).to_csv(index=False).encode("utf-8"),
            file_name="compounding_ledger.csv", mime="text/csv", key="sim_dl_ledger",
        )

        # ── Stress test / Monte Carlo
        if _res["stress"] and _res["mc"] is not None:
            _mc = _res["mc"]
            _goal_label, _goal_amount = list(_r_ccy["milestones"].items())[-1]

            st.subheader("Stress Test — Sequence-of-Returns Risk")
            _mc_c1, _mc_c2, _mc_c3 = st.columns(3)
            with _mc_c1:
                st.metric(_r_esc_md(f"Success rate (reach {_r_ccy['symbol']}{_goal_label} in window)"),
                           f"{_mc['success_rate']*100:.1f}%")
            with _mc_c2:
                st.metric("Median final balance", _r_fmt_big(_mc["percentiles"]["p50"]))
            with _mc_c3:
                st.metric(_r_esc_md(f"Median years to {_r_ccy['symbol']}{_goal_label}"),
                           f"{_mc['median_years_to_goal']:.1f} yr" if _mc["median_years_to_goal"] else "—")

            _pct_df = pd.DataFrame([
                {"Percentile": k, "Final Balance": _r_fmt_big(v)}
                for k, v in _mc["percentiles"].items()
            ])
            st.dataframe(_pct_df, use_container_width=True, hide_index=True)

            _hist_fig = go.Figure()
            _hist_fig.add_trace(go.Histogram(
                x=_mc["final_balances"] / _r_ccy["big_unit"], nbinsx=40,
                marker_color="#4e79a7", opacity=0.85,
            ))
            _hist_fig.add_vline(x=_goal_amount / _r_ccy["big_unit"], line_dash="dash", line_color="#e15759",
                                 annotation_text=f"{_r_ccy['symbol']}{_goal_label} goal", annotation_position="top")
            _hist_fig.add_vline(x=_sim_df["EndingBalance"].iloc[-1] / _r_ccy["big_unit"],
                                 line_dash="dot", line_color="gray",
                                 annotation_text="Flat-ROI baseline", annotation_position="top left")
            _hist_fig.update_layout(
                xaxis_title=f"Final Portfolio Value ({_r_ccy['symbol']} {_r_ccy['big_label']})",
                yaxis_title="Simulations",
                height=380, margin=dict(l=10, r=10, t=30, b=10),
            )
            st.plotly_chart(_hist_fig, use_container_width=True)

            _median_delta = _mc["percentiles"]["p50"] - _sim_df["EndingBalance"].iloc[-1]
            st.markdown(_r_esc_md(
                f"**Strategic insight:** across {_res['n_sims']} randomized {_res['volatility_pct']:.0f}%-volatility "
                f"paths, only **{_mc['success_rate']*100:.1f}%** reach the {_r_ccy['symbol']}{_goal_label} goal "
                f"within the {_res['p1_years']+_res['p2_years']}-year window, versus the flat-ROI baseline of "
                f"{_r_fmt_big(_sim_df['EndingBalance'].iloc[-1])}. The median outcome "
                f"({_r_fmt_big(_mc['percentiles']['p50'])}) sits "
                f"{'below' if _median_delta < 0 else 'above'} the deterministic baseline by "
                f"{_r_fmt_big(abs(_median_delta))} — a classic **volatility drag** effect: a return stream with the "
                f"*same average* annual return as the flat case compounds to a *lower typical* outcome once "
                f"year-to-year variance is introduced, because losses require proportionally larger gains to "
                f"recover (a −20% year needs +25% to break even). The p5–p95 spread "
                f"({_r_fmt_big(_mc['percentiles']['p5'])} – {_r_fmt_big(_mc['percentiles']['p95'])}) shows how wide "
                f"the range of outcomes is for an identical strategy under realistic equity-market volatility."
            ))

    with st.expander("ℹ Methodology & Limitations"):
        st.markdown("""
- **Monthly compounding**: every rate is converted from an annual figure via `(1 + annual_rate) ** (1/12) - 1`, not a naive `annual_rate / 12`.
- **Three regions**: pick India (₹, Crore-denominated), United States (\\$), or Europe (€) from the Currency/Region selector. Switching regions resets the capital and contribution fields to sensible defaults for that currency, and rescales the milestone goalposts accordingly (₹1/3/6/10 Cr for India; \\$100K/300K/600K/1M or €100K/300K/600K/1M for US/EU).
- **Phase 1 contribution is a fixed monthly amount you choose** (defaults to ₹1,00,000 / \\$1,000 / €1,000 depending on region), held flat for the whole phase — not a moving target that increases as the balance grows. The **"↺ Match auto"** button sets it to `initial_capital × ROI ÷ 12`, i.e. exactly matching the lump sum's *initial* annual organic growth — the original "match the growth" baseline math from the spec.
- **Growth is computed on the pre-contribution balance** each month (an "ordinary annuity" convention — the month's contribution itself doesn't earn that month's growth). This is the standard convention behind the `FV = pmt × [(1+r)^n − 1] / r` annuity formula.
- **Stress-test mode** draws one random annual return per year from a Normal distribution (mean = target ROI, std = your chosen volatility), applied uniformly across that year's 12 months. This models year-to-year market regime risk, not intra-year noise — a reasonable simplification for a long-horizon SIP-style strategy.
- **Not investment advice.** This is a pure compound-interest / Monte Carlo mathematics tool, independent of the stock-picking engines elsewhere in this app. Real returns depend on fund selection, expense ratios, taxes, and the actual sequence of market returns.
        """)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 10 — Portfolio
# ══════════════════════════════════════════════════════════════════════════════

_CURR_SYM   = {"IN": "Rs ", "US": "$", "EU": "€"}
_PORT_ROOT  = ROOT / "portfolio"
_STRATEGIES = {"st": "Short-Term Momentum", "lt": "Long-Term"}
_MARKETS    = ["US", "EU", "IN"]


def _load_strategy_positions(strategy: str) -> list:
    """Load all positions for a given strategy across all regions."""
    positions = []
    for mkt in _MARKETS:
        f = _PORT_ROOT / f"{strategy}_{mkt}.json"
        if f.exists():
            try:
                rows = json.loads(f.read_text(encoding="utf-8"))
                for r in rows:
                    r.setdefault("market", mkt)
                positions.extend(rows)
            except Exception:
                pass
    return positions


def _fetch_live_prices(tickers: list) -> dict:
    """Fetch latest close price for each ticker via yfinance."""
    import yfinance as yf
    price_map = {}
    if not tickers:
        return price_map
    try:
        data = yf.download(tickers, period="3d", auto_adjust=True,
                           progress=False, threads=True)
        close = data["Close"] if "Close" in data.columns else data
        for t in tickers:
            try:
                series = close[t] if t in close.columns else close
                val = series.dropna().iloc[-1]
                price_map[t] = float(val)
            except Exception:
                pass
    except Exception:
        # fallback: fetch one by one
        for t in tickers:
            try:
                hist = yf.Ticker(t).history(period="3d")
                if not hist.empty:
                    price_map[t] = float(hist["Close"].iloc[-1])
            except Exception:
                pass
    return price_map


with T_PORT:
    st.header("Portfolio — Open Positions")
    st.caption("Two independent strategies × three regions. Each region runs on a 100,000 equity base.")

    strategy_tab_st, strategy_tab_lt, strategy_tab_sip = st.tabs(
        ["📊 Short-Term Momentum", "🏦 Long-Term", "💰 SIP Plan"]
    )

    for _strat_key, _strat_tab in [("st", strategy_tab_st), ("lt", strategy_tab_lt)]:
      with _strat_tab:
        positions = _load_strategy_positions(_strat_key)
        _strat_label = _STRATEGIES[_strat_key]

        if not positions:
            st.info(
                f"No open positions in {_strat_label} portfolios.  "
                + ("Run a Daily Scan to populate." if _strat_key == "st"
                   else "Add positions via the Long-Term Screener tab.")
            )
        else:
            # ── Price refresh controls ────────────────────────────────────────
            _refresh_key  = f"port_refresh_{_strat_key}"
            _prices_key   = f"port_prices_{_strat_key}"
            _fetched_key  = f"port_fetched_at_{_strat_key}"

            btn_col, ts_col = st.columns([1, 4])
            with btn_col:
                do_refresh = st.button("🔄 Refresh Prices", type="primary", key=_refresh_key)
            with ts_col:
                if _fetched_key in st.session_state:
                    st.caption(f"Last fetched: {st.session_state[_fetched_key]}")

            tickers = [p["ticker"] for p in positions]
            if do_refresh or _prices_key not in st.session_state:
                with st.spinner(f"Fetching live prices for {len(tickers)} tickers…"):
                    st.session_state[_prices_key]  = _fetch_live_prices(tickers)
                    st.session_state[_fetched_key] = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M")

            price_map: dict = st.session_state.get(_prices_key, {})

            # Exit Watch (technical breakdown-confirmation + fundamental thresholds
            # captured at entry) for held long-term positions only.
            _exitwatch_key = f"port_exitwatch_{_strat_key}"
            if _strat_key == "lt" and (do_refresh or _exitwatch_key not in st.session_state):
                with st.spinner("Checking Exit Watch signals…"):
                    from data import fetch_history
                    from indicators import calculate_all
                    from fundamental import fetch_fundamentals
                    from run_longterm import check_lt_exit, _consecutive_days_breakdown

                    exit_watch: dict = {}
                    for p in positions:
                        t = p["ticker"]
                        sma50 = sma200 = None
                        days_bd = 0
                        try:
                            df = fetch_history(t, years=2)
                            if df is not None and len(df) >= 200:
                                ind    = calculate_all(df)
                                sma50  = float(ind["SMA_50"].iloc[-1])
                                sma200 = float(ind["SMA_200"].iloc[-1])
                                days_bd = _consecutive_days_breakdown(ind["SMA_50"], ind["SMA_200"])
                        except Exception:
                            pass
                        fund = None
                        try:
                            fund = fetch_fundamentals(t, use_cache=True)
                        except Exception:
                            pass
                        exit_watch[t] = check_lt_exit(
                            p, price=price_map.get(t), sma50=sma50, sma200=sma200,
                            days_below_sma200=days_bd, fund_data=fund,
                        )
                    st.session_state[_exitwatch_key] = exit_watch

            exit_watch_map: dict = st.session_state.get(_exitwatch_key, {})

            # ── Build rows ───────────────────────────────────────────────────
            today_p = pd.Timestamp.today().normalize()
            rows    = []
            for p in positions:
                ticker     = p["ticker"]
                market     = p.get("market", "US")
                curr       = _CURR_SYM.get(market, "$")
                entry_px   = float(p.get("entry_price", 0))
                entry_date = pd.Timestamp(p.get("entry_date", "2000-01-01"))
                shares     = int(p.get("shares", 0))
                stop       = float(p.get("stop_loss", 0))
                stop_init  = float(p.get("stop_loss_initial", stop))
                cost       = float(p.get("cost", entry_px * shares))
                peak_px    = float(p.get("peak_price", entry_px))
                atr        = float(p.get("atr_at_entry", 0))
                trail_mult = float(p.get("trail_mult", 5.0))
                regime     = p.get("regime", "Normal")
                days_held  = max((today_p - entry_date).days, 0)
                cur_px     = price_map.get(ticker)

                if cur_px is not None:
                    cur_val   = shares * cur_px
                    pnl       = cur_val - cost
                    pnl_pct   = (cur_px - entry_px) / entry_px * 100 if entry_px else 0.0
                    init_risk = entry_px - stop_init
                    r_mult    = (cur_px - entry_px) / init_risk if init_risk > 0 else 0.0
                    stop_dist = (cur_px - stop) / cur_px * 100 if cur_px else 0.0
                    if cur_px <= stop:
                        pstatus = "🔴 STOP HIT"
                    elif stop_dist < 5.0:
                        pstatus = "🟡 Near stop"
                    else:
                        pstatus = "🟢 Safe"
                else:
                    cur_val = pnl = pnl_pct = r_mult = stop_dist = None
                    pstatus = "⚪ No price"

                exit_watch = exit_watch_map.get(ticker) if _strat_key == "lt" else None
                if exit_watch and "STOP HIT" not in pstatus:
                    if exit_watch["verdict"] == "SELL":
                        pstatus = "🔴 Exit Signal"
                    elif exit_watch["verdict"] == "WATCH" and "Safe" in pstatus:
                        pstatus = "🟡 Watch"

                rows.append({
                    "status": pstatus, "ticker": ticker, "market": market,
                    "curr": curr, "sector": p.get("sector", "Unknown"),
                    "entry_date": entry_date.strftime("%Y-%m-%d"), "days_held": days_held,
                    "entry_px": entry_px, "cur_px": cur_px, "stop": stop,
                    "stop_dist": stop_dist, "shares": shares, "cost": cost,
                    "cur_val": cur_val, "pnl": pnl, "pnl_pct": pnl_pct,
                    "r_mult": r_mult, "regime": regime, "peak_px": peak_px,
                    "atr": atr, "trail_mult": trail_mult,
                    "lt_combined": p.get("lt_combined"), "lt_grade": p.get("lt_grade"),
                    "exit_watch": exit_watch,
                })

            # ── Global alerts ────────────────────────────────────────────────
            stop_hits  = [r for r in rows if "STOP HIT" in r["status"]]
            near_stps  = [r for r in rows if "Near stop" in r["status"]]
            exit_sigs  = [r for r in rows if "Exit Signal" in r["status"]]
            watch_sigs = [r for r in rows if r["status"] == "🟡 Watch"]
            if stop_hits:
                st.error("🔴 **Stop breached — review immediately:** "
                         + ", ".join(r["ticker"] for r in stop_hits))
            if near_stps:
                st.warning("🟡 **Within 5% of stop:** "
                           + ", ".join(f"{r['ticker']} ({r['stop_dist']:.1f}%)"
                                       for r in near_stps))
            if exit_sigs:
                st.error("🔴 **Exit Watch triggered — review:** "
                         + ", ".join(r["ticker"] for r in exit_sigs))
            if watch_sigs:
                st.warning("🟡 **Exit Watch — early warning:** "
                           + ", ".join(r["ticker"] for r in watch_sigs))

            # ── Per-region sub-tabs ───────────────────────────────────────────
            def _render_region(tab_rows, market):
                if not tab_rows:
                    st.info(f"No open positions in {market}.")
                    return

                curr   = _CURR_SYM.get(market, "$")
                priced = [r for r in tab_rows if r["pnl"] is not None]
                deployed  = sum(r["cost"] for r in tab_rows)
                cash      = max(100_000 - deployed, 0)
                total_val = sum(r["cur_val"] for r in priced) if priced else None
                total_pnl = sum(r["pnl"]    for r in priced) if priced else None
                pnl_pct   = (total_pnl / deployed * 100
                             if total_pnl is not None and deployed else None)

                # Equity breakdown row
                c1, c2, c3, c4, c5 = st.columns(5)
                c1.metric("Positions",  len(tab_rows))
                c2.metric(f"Equity ({curr})", f"{curr}100,000")
                c3.metric(f"Deployed ({curr})", f"{curr}{deployed:,.0f}")
                c4.metric(f"Cash ({curr})", f"{curr}{cash:,.0f}")
                c5.metric(f"Unrealised P&L",
                          f"{curr}{total_pnl:+,.0f}" if total_pnl is not None else "—",
                          delta=f"{pnl_pct:+.2f}%" if pnl_pct is not None else None,
                          delta_color="normal" if (total_pnl or 0) >= 0 else "inverse")

                st.markdown("---")

                tbl_df = pd.DataFrame([{
                    "Status":      r["status"],
                    "Ticker":      r["ticker"],
                    "Sector":      r["sector"],
                    "Days":        r["days_held"],
                    "Entry Px":    r["entry_px"],
                    "Live Px":     r["cur_px"],
                    "Stop":        r["stop"],
                    "Stop Dist %": r["stop_dist"],
                    "Shares":      r["shares"],
                    "P&L":         r["pnl"],
                    "P&L %":       r["pnl_pct"],
                    "R-Mult":      r["r_mult"],
                } for r in tab_rows])
                st.dataframe(tbl_df, use_container_width=True, hide_index=True,
                    column_config={
                        "Status":      st.column_config.TextColumn(width="small"),
                        "Days":        st.column_config.NumberColumn(format="%d"),
                        "Entry Px":    st.column_config.NumberColumn(format="%.2f"),
                        "Live Px":     st.column_config.NumberColumn(format="%.2f"),
                        "Stop":        st.column_config.NumberColumn(format="%.2f"),
                        "Stop Dist %": st.column_config.ProgressColumn(
                                           min_value=0, max_value=30, format="%.1f%%"),
                        "Shares":      st.column_config.NumberColumn(format="%d"),
                        "P&L":         st.column_config.NumberColumn(format="%+.0f"),
                        "P&L %":       st.column_config.NumberColumn(format="%+.2f%%"),
                        "R-Mult":      st.column_config.NumberColumn(format="%+.2fR"),
                    })

                st.subheader("Position Detail")
                for r in tab_rows:
                    pnl_label = f"{r['curr']}{r['pnl']:+,.0f}" if r["pnl"] is not None else "—"
                    r_label   = f"{r['r_mult']:+.2f}R" if r["r_mult"] is not None else "—"
                    with st.expander(
                        f"**{r['ticker']}**  ·  {r['status']}  ·  "
                        f"P&L {pnl_label}  ·  {r_label}  ·  {r['days_held']}d"
                    ):
                        c1, c2, c3 = st.columns(3)
                        with c1:
                            st.metric("Entry Price", f"{r['curr']}{r['entry_px']:,.2f}")
                            st.metric("Live Price",  f"{r['curr']}{r['cur_px']:,.2f}" if r["cur_px"] else "—")
                            st.metric("Entry Date",  r["entry_date"])
                        with c2:
                            st.metric("Stop Loss",    f"{r['curr']}{r['stop']:,.2f}")
                            st.metric("Stop Cushion", f"{r['stop_dist']:.1f}%" if r["stop_dist"] is not None else "—")
                            st.metric("Peak Price",   f"{r['curr']}{r['peak_px']:,.2f}")
                        with c3:
                            st.metric("ATR at Entry", f"{r['curr']}{r['atr']:.2f}")
                            st.metric("Trail Stop",   f"{r['trail_mult']}× ATR")
                            st.metric("Regime",       r["regime"])
                        st.caption(
                            f"Shares: {r['shares']}  ·  Cost: {r['curr']}{r['cost']:,.0f}"
                            + (f"  ·  Value: {r['curr']}{r['cur_val']:,.0f}" if r["cur_val"] else "")
                        )
                        if _strat_key == "lt" and r.get("lt_combined"):
                            st.caption(f"LT score: {r['lt_combined']}  ·  Grade: {r['lt_grade']}")
                        if _strat_key == "lt":
                            ew = r.get("exit_watch")
                            reasons = "; ".join(ew["reasons"]) if ew else "not yet checked"
                            st.caption(f"**Exit Watch:** {reasons}")

            # ── Overview row: 3 regions side-by-side ─────────────────────────
            tab_ov, tab_us, tab_eu, tab_in = st.tabs(["🌍 Overview", "🇺🇸 US", "🇪🇺 EU", "🇮🇳 IN"])
            with tab_ov:
                any_priced = [r for r in rows if r["pnl"] is not None]
                ov_cols = st.columns(3)
                for i, mk in enumerate(["US", "EU", "IN"]):
                    mk_rows   = [r for r in rows if r["market"] == mk]
                    mk_curr   = _CURR_SYM[mk]
                    deployed  = sum(r["cost"] for r in mk_rows)
                    cash      = max(100_000 - deployed, 0)
                    mk_priced = [r for r in mk_rows if r["pnl"] is not None]
                    pnl       = sum(r["pnl"] for r in mk_priced) if mk_priced else None
                    with ov_cols[i]:
                        st.markdown(f"**{mk}**")
                        st.metric("Positions", len(mk_rows))
                        st.metric(f"Deployed", f"{mk_curr}{deployed:,.0f}")
                        st.metric(f"Cash", f"{mk_curr}{cash:,.0f}")
                        st.metric(f"P&L",
                                  f"{mk_curr}{pnl:+,.0f}" if pnl is not None else "—",
                                  delta=f"{pnl/deployed*100:+.2f}%" if pnl and deployed else None)
                if stop_hits or near_stps:
                    st.warning(f"🔴 {len(stop_hits)} stop hit  ·  🟡 {len(near_stps)} near stop")
            with tab_us:
                _render_region([r for r in rows if r["market"] == "US"], "US")
            with tab_eu:
                _render_region([r for r in rows if r["market"] == "EU"], "EU")
            with tab_in:
                _render_region([r for r in rows if r["market"] == "IN"], "IN")


# ── SIP Plan sub-tab in Portfolio ────────────────────────────────────────────
with strategy_tab_sip:
    from sip_strategy import load_sip_holdings, SIP_CONFIG as _SIP_CFG
    _sip_state = load_sip_holdings()
    _sip_h     = _sip_state.get("holdings", {})
    _sip_rsym  = _SIP_CFG.get("region_symbol",   {"US":"$","EU":"€","IN":"₹"})
    _sip_rcur  = _SIP_CFG.get("region_currency",  {"US":"USD","EU":"EUR","IN":"INR"})
    _sip_rbgt  = _SIP_CFG.get("region_budget",    {"US":2000,"EU":2000,"IN":20000})

    st.caption(
        f"Positions: {len(_sip_h)}  |  "
        f"Total deployed: various currencies  |  "
        f"Started: {_sip_state.get('start_date','—')}"
    )

    if not _sip_h:
        st.info("No SIP positions yet. Run the first monthly SIP cycle in the SIP Plan tab.")
    else:
        # Group by region
        _sip_by_region: dict = {}
        for _tk, _th in _sip_h.items():
            _mk = _th.get("market", "US")
            _sip_by_region.setdefault(_mk, {})[_tk] = _th

        # Refresh live prices for SIP holdings
        _sip_tickers_all = list(_sip_h.keys())
        _sip_prices_key  = "sip_port_prices"
        _sc1, _sc2 = st.columns([1, 5])
        with _sc1:
            if st.button("🔄 Refresh Prices", key="sip_port_refresh"):
                with st.spinner("Fetching prices…"):
                    st.session_state[_sip_prices_key] = _fetch_live_prices(_sip_tickers_all)
        with _sc2:
            st.caption(f"{len(_sip_tickers_all)} SIP positions across {len(_sip_by_region)} regions")

        _sip_px = st.session_state.get(_sip_prices_key, {})

        # Per-region sub-tabs
        _sip_port_labels = [
            f"{'🇺🇸' if m=='US' else '🇪🇺' if m=='EU' else '🇮🇳'} {m} ({_sip_rcur.get(m,m)})"
            for m in ["US","EU","IN"] if m in _sip_by_region
        ]
        _sip_port_tabs = st.tabs(_sip_port_labels) if _sip_port_labels else []
        for _ti, _mk in enumerate([m for m in ["US","EU","IN"] if m in _sip_by_region]):
            with _sip_port_tabs[_ti]:
                _sym  = _sip_rsym.get(_mk, "")
                _cur  = _sip_rcur.get(_mk, "")
                _bgt  = _sip_rbgt.get(_mk, 0)
                _mpos = _sip_by_region[_mk]

                # Summary row
                _total_cost = sum(_th.get("total_cost_eur", _th.get("shares",0)*_th.get("avg_cost",0))
                                  for _th in _mpos.values())
                _total_val  = sum(_sip_px.get(_tk,0) * _th.get("shares",0)
                                  for _tk, _th in _mpos.items() if _sip_px.get(_tk))
                _total_pnl  = _total_val - _total_cost if _total_val else None

                _pc1, _pc2, _pc3, _pc4 = st.columns(4)
                _pc1.metric("Positions", len(_mpos))
                _pc2.metric(f"Budget/mo ({_cur})", f"{_sym}{_bgt:,.0f}")
                _pc3.metric(f"Cost Basis", f"{_sym}{_total_cost:,.0f}")
                _pc4.metric("Unrealised P&L",
                            f"{_sym}{_total_pnl:+,.0f}" if _total_pnl is not None else "—",
                            delta=f"{_total_pnl/_total_cost*100:+.1f}%" if _total_pnl and _total_cost else None)

                # Position table
                _rows_sip = []
                for _tk, _th in _mpos.items():
                    _live = _sip_px.get(_tk)
                    _avg  = _th.get("avg_cost", 0)
                    _sh   = _th.get("shares", 0)
                    _cost = _th.get("total_cost_eur", _avg * _sh)
                    _val  = _live * _sh if _live else None
                    _pnl  = _val - _cost if _val is not None else None
                    _pnl_pct = (_live/_avg - 1)*100 if _live and _avg else None
                    _rows_sip.append({
                        "Ticker":   _tk,
                        "Sector":   _th.get("sector",""),
                        "Shares":   round(_sh, 4),
                        f"Avg Cost ({_sym})":  round(_avg, 2),
                        f"Live Price ({_sym})": round(_live, 2) if _live else "—",
                        f"Cost ({_sym})":       round(_cost, 0),
                        f"Value ({_sym})":      round(_val, 0) if _val else "—",
                        "P&L %":  f"{_pnl_pct:+.1f}%" if _pnl_pct is not None else "—",
                        "First Bought": _th.get("first_bought",""),
                        "Last Added":   _th.get("last_added",""),
                    })
                st.dataframe(_rows_sip, use_container_width=True, hide_index=True)

    # SIP cycle history
    if _sip_state.get("cycles"):
        with st.expander(f"Cycle history ({len(_sip_state['cycles'])} cycles)"):
            st.dataframe(_sip_state["cycles"], use_container_width=True)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 11 — Reports
# ══════════════════════════════════════════════════════════════════════════════

with T_REP:
    st.header("Saved Reports")
    st.caption("All reports are auto-saved to reports/ after each scan or backtest.")

    reports_dir = ROOT / "reports"

    _rc1, _rc2 = st.columns([6, 1])
    with _rc2:
        if st.button("🔄 Refresh", key="rep_refresh"):
            st.rerun()
    with _rc1:
        _rep_filter = st.selectbox(
            "Filter by type",
            ["All", "Daily Scan", "Backtest", "Long-Term", "Walk-Forward", "Other"],
            key="rep_filter_type",
            label_visibility="collapsed",
        )

    _prefix_map = {
        "Daily Scan":   "daily",
        "Backtest":     "backtest",
        "Long-Term":    "longterm",
        "Walk-Forward": "wfo",
    }

    all_txt = sorted(reports_dir.glob("*.txt"), reverse=True) if reports_dir.exists() else []
    if _rep_filter != "All":
        _pfx = _prefix_map.get(_rep_filter, "")
        if _pfx:
            all_txt = [f for f in all_txt if f.name.startswith(_pfx)]
        else:
            known = set(_prefix_map.values())
            all_txt = [f for f in all_txt if not any(f.name.startswith(p) for p in known)]

    if not all_txt:
        st.info("No saved reports found. Run the Daily Scan or a Backtest to generate reports.")
    else:
        col_a, col_b = st.columns([1, 3])
        with col_a:
            st.caption(f"{len(all_txt)} report(s)")
            selected_name = st.radio(
                "Select report",
                options=[f.name for f in all_txt],
                key="rep_selected",
                label_visibility="collapsed",
            )
        with col_b:
            selected_path = reports_dir / selected_name
            content = selected_path.read_text(encoding="utf-8", errors="replace")

            file_col, dl_col = st.columns([3, 1])
            file_col.markdown(f"**{selected_name}**")
            dl_col.download_button(
                "⬇ Download",
                data=content,
                file_name=selected_name,
                mime="text/plain",
                key="rep_download",
            )
            st.code(content, language=None)


# ══════════════════════════════════════════════════════════════════════════════
# TAB — Bench List (Replacement Candidates)
# ══════════════════════════════════════════════════════════════════════════════

with T_BENCH:
    st.header("Bench List")
    st.caption("Replacement candidates per market, ranked ENTER → NEAR → WAIT → SKIP "
               "(quality desc, then ATR% asc within ENTER).")

    c1, c2, c3 = st.columns(3)
    with c1:
        bench_market = st.selectbox("Market", ["ALL", "US", "EU", "IN"], key="bench_market")
    with c2:
        bench_topn = st.number_input("Top N", value=20, min_value=1, step=5, key="bench_topn")
    with c3:
        bench_qf = st.checkbox("Quality sort", value=quality_filter_s, key="bench_qf")

    if st.button("▶ Build Bench List", type="primary", key="btn_bench"):
        buf = io.StringIO()
        try:
            from config import WATCHLIST, DYNAMIC_UNIVERSE
            from data import fetch_all
            from indicators import calculate_all
            from adaptive_tuner import AdaptiveTuner
            from replacement_list import build_replacement_list, format_bench_table

            active = ["US", "EU", "IN"] if bench_market == "ALL" else [bench_market]

            with st.status(f"Building bench list [{bench_market}]…", expanded=True) as status:
                with contextlib.redirect_stdout(buf):
                    if dynamic_universe_s:
                        from universe import get_dynamic_watchlist
                        score_top_n = {m: DYNAMIC_UNIVERSE["SCORE_TOP_N"].get(m, 200) for m in active}
                        wl = get_dynamic_watchlist(
                            active, score_top_n,
                            max_age_days=DYNAMIC_UNIVERSE.get("MAX_AGE_DAYS", 7))
                    else:
                        wl = {m: WATCHLIST[m] for m in active if m in WATCHLIST}

                st.write("📥 Fetching EOD data…")
                with contextlib.redirect_stdout(buf):
                    raw = fetch_all(wl, years=3)
                    data_map = {t: calculate_all(df) for t, df in raw.items()}

                quality_scores: dict = {}
                if bench_qf:
                    st.write("🔎 Scoring candidates…")
                    with contextlib.redirect_stdout(buf):
                        from select_stocks import quality_score_all
                        quality_scores = quality_score_all(data_map)

                with contextlib.redirect_stdout(buf):
                    tuner = AdaptiveTuner.load(str(ROOT / "tuner_state.json"))
                    report_parts = []
                    for mk in active:
                        bench = build_replacement_list(
                            mk, data_map, tuner_mode=tuner.mode,
                            top_n=int(bench_topn), quality_scores=quality_scores,
                        )
                        report_parts.append(
                            f"\n{'='*88}\n"
                            f"  REPLACEMENT LIST -- {mk}  (tuner: {tuner.mode})  {len(bench)} candidates\n"
                            f"{'='*88}\n"
                            f"{format_bench_table(bench)}\n"
                        )
                    report_text = "\n".join(report_parts)

                status.update(label="Done!", state="complete")

            st.code(_strip(report_text), language=None)
            with st.expander("📋 Progress log"):
                st.text(_strip(buf.getvalue()))

        except Exception as exc:
            st.error(f"Bench list build failed: {exc}")
            with st.expander("📋 Progress log"):
                st.text(_strip(buf.getvalue()))
            st.exception(exc)


# ══════════════════════════════════════════════════════════════════════════════
# TAB — Settings
# ══════════════════════════════════════════════════════════════════════════════

with T_SET:
    st.header("Account & Risk Settings")
    st.caption("Shared with the desktop app — both read/write the same app_settings.json.")

    from app_settings import load_settings as _load_web_settings, save_settings as _save_web_settings

    if "web_settings" not in st.session_state:
        st.session_state["web_settings"] = _load_web_settings()
    _cur = st.session_state["web_settings"]

    with st.form("settings_form"):
        st.subheader("Numeric")
        n1, n2, n3 = st.columns(3)
        with n1:
            set_account   = st.number_input("Account Size", value=int(_cur["account_size"]), step=10_000)
            set_max_pos   = st.number_input("Max Open Positions", value=int(_cur["max_positions"]), step=1)
            set_max_sec   = st.number_input("Max Per Sector (slots)", value=int(_cur["max_per_sector"]), step=1)
        with n2:
            set_max_hv    = st.number_input("Max High-Vol Per Market", value=int(_cur["max_high_vol"]), step=1)
            set_pos_pct   = st.number_input("Entry Size Cap (0-1 fraction)",
                                            value=float(_cur["max_position_size_pct"]), step=0.01, format="%.2f")
            set_conc_pct  = st.number_input("Max Concentration Cap (0-1 frac)",
                                            value=float(_cur["max_concentration_pct"]), step=0.01, format="%.2f")
        with n3:
            set_grace     = st.number_input("Momentum Exit Grace (days)", value=int(_cur["momentum_grace"]), step=1)
            set_periods   = st.text_input("Momentum Periods (csv)", value=str(_cur["momentum_periods"]))

        st.subheader("Universe / Ranking Top-N")
        u1, u2, u3 = st.columns(3)
        with u1:
            set_topn_us   = st.number_input("Top-N US (universe fetch)", value=int(_cur["top_n_us"]), step=10)
            set_ranktn_us = st.number_input("Ranking Top-N US", value=int(_cur["rank_top_n_us"]), step=1)
        with u2:
            set_topn_eu   = st.number_input("Top-N EU (universe fetch)", value=int(_cur["top_n_eu"]), step=10)
            set_ranktn_eu = st.number_input("Ranking Top-N EU", value=int(_cur["rank_top_n_eu"]), step=1)
        with u3:
            set_topn_in   = st.number_input("Top-N IN (universe fetch)", value=int(_cur["top_n_in"]), step=10)
            set_ranktn_in = st.number_input("Ranking Top-N IN", value=int(_cur["rank_top_n_in"]), step=1)

        st.subheader("Flags")
        b1, b2, b3, b4 = st.columns(4)
        with b1:
            set_qf  = st.checkbox("Quality filter", value=bool(_cur["quality_filter"]))
        with b2:
            set_dyn = st.checkbox("Dynamic universe", value=bool(_cur["dynamic_universe"]))
        with b3:
            set_mex = st.checkbox("Momentum exit", value=bool(_cur["momentum_exit"]))
        with b4:
            set_vp  = st.checkbox("Volatility penalty", value=bool(_cur["vol_penalty"]))

        if st.form_submit_button("💾 Save Settings", type="primary"):
            new_settings = {
                "account_size": int(set_account), "max_positions": int(set_max_pos),
                "max_per_sector": int(set_max_sec), "max_high_vol": int(set_max_hv),
                "max_position_size_pct": float(set_pos_pct), "max_concentration_pct": float(set_conc_pct),
                "momentum_grace": int(set_grace), "momentum_periods": set_periods,
                "top_n_us": int(set_topn_us), "top_n_eu": int(set_topn_eu), "top_n_in": int(set_topn_in),
                "rank_top_n_us": int(set_ranktn_us), "rank_top_n_eu": int(set_ranktn_eu),
                "rank_top_n_in": int(set_ranktn_in),
                "quality_filter": set_qf, "dynamic_universe": set_dyn,
                "momentum_exit": set_mex, "vol_penalty": set_vp,
            }
            _save_web_settings(new_settings)
            st.session_state["web_settings"] = new_settings
            st.success("Settings saved successfully.")

    st.caption("To add/remove stocks edit `src/config.py` directly.")

    st.subheader("Watchlist Preview")
    try:
        from config import WATCHLIST, MARKETS
        _wl_lines = []
        for mk in ["US", "EU", "IN"]:
            m = MARKETS.get(mk, {})
            note = "" if m.get("tradeable", True) else "  [analysis only]"
            _wl_lines.append(f"[{mk}] {m.get('name', '?')} ({m.get('currency', '?')}){note}")
            _wl_lines.extend(f"  {t}" for t in WATCHLIST.get(mk, []))
        st.code("\n".join(_wl_lines), language=None)
    except Exception as exc:
        st.error(f"Could not read config.py: {exc}")
