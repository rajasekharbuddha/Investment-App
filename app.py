#!/usr/bin/env python3
"""
Mastermind Pro — Browser Edition
Run: streamlit run app.py
Original Tkinter desktop app preserved as app_tkinter.py
"""

import contextlib
import io
import json
import re
import sys
from datetime import date
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

T_SCAN, T_BT, T_LTB, T_LTS, T_WF, T_ST, T_MC, T_REP = st.tabs([
    "📊 Daily Scan",
    "📈 ST Backtest",
    "🏦 LT Backtest",
    "🔭 LT Screener",
    "🔄 Walk-Forward",
    "💪 Stress Tests",
    "🎲 Monte Carlo",
    "📁 Reports",
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

            PORTFOLIO_FILE = ROOT / "portfolio" / "positions.json"
            TUNER_FILE     = ROOT / "tuner_state.json"
            STATE_FILE     = ROOT / "state" / "last_decisions.json"

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

                st.write(f"⚙ Calculating indicators for {len(raw_data)} tickers…")
                with contextlib.redirect_stdout(buf):
                    data_map = {t: calculate_all(df) for t, df in raw_data.items()}

                    if dynamic_universe_s:
                        from select_stocks import dynamic_watchlist as _dyn_wl
                        active_wl = _dyn_wl(data_map, DYNAMIC_UNIVERSE["SCORE_TOP_N"],
                                             watchlist=active_wl)

                # Quality scoring
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

                st.write("🤖 Running DecisionEngine…")
                with contextlib.redirect_stdout(buf):
                    portfolio_data = []
                    if PORTFOLIO_FILE.exists():
                        try:
                            portfolio_data = json.loads(PORTFOLIO_FILE.read_text())
                        except Exception:
                            pass

                    tuner  = AdaptiveTuner.load(str(TUNER_FILE))
                    engine = DecisionEngine(tuner=tuner)

                    today_ts = (pd.Timestamp(scan_asof.strip()).normalize()
                                if scan_asof.strip()
                                else pd.Timestamp.today().normalize())

                    result = engine.run_day(
                        today=today_ts,
                        data_map=data_map,
                        portfolio=portfolio_data,
                        equity=equity_s,
                        context="live",
                        watchlist=active_wl,
                        quality_scores=quality_scores if quality_filter_s else None,
                    )

                st.write("📝 Generating report…")
                with contextlib.redirect_stdout(buf):
                    candidates = list(result["candidates"].values())
                    for c in candidates:
                        sz = result["sizing"].get(c["ticker"])
                        if sz:
                            c["sizing"] = sz

                    report_text = daily_report(
                        decisions=candidates,
                        account_eur=equity_s,
                        watchlist=active_wl,
                        markets=MARKETS,
                        tuner_mode=result["tuner_mode"],
                        risk_scale=result["risk_scale"],
                        quality_filtered=quality_filtered,
                        quality_scores=quality_scores,
                    )
                    tuner.save(str(TUNER_FILE))

                    STATE_FILE.parent.mkdir(exist_ok=True)
                    STATE_FILE.write_text(
                        json.dumps({"date": today_ts.strftime("%Y-%m-%d"),
                                    "decisions": candidates,
                                    "tuner_mode": result["tuner_mode"]},
                                   indent=2, default=str))

                status.update(label="Scan complete!", state="complete")

            # Metric bar
            enters = [c for c in candidates if c.get("decision") == "ENTER"]
            nears  = [c for c in candidates if c.get("decision") == "NEAR"]
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("ENTER signals",   len(enters))
            m2.metric("NEAR signals",    len(nears))
            m3.metric("Tuner mode",      result["tuner_mode"])
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
                    st.area_chart(ec_df["equity"], use_container_width=True)

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

            ec = result.get("equity_curve", [])
            if ec:
                try:
                    if isinstance(ec[0], dict):
                        ec_df = pd.DataFrame(ec).set_index("date")
                        ec_df.index = pd.to_datetime(ec_df.index)
                        col = "equity" if "equity" in ec_df.columns else ec_df.columns[0]
                    else:
                        ec_df = pd.DataFrame({"equity": ec})
                    st.subheader("Equity Curve")
                    st.area_chart(ec_df[col] if isinstance(ec[0], dict) else ec_df["equity"],
                                  use_container_width=True)
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
            pct = result.get("percentiles", {})
            if pct:
                cols = st.columns(5)
                labels = ["5th %ile", "25th %ile", "Median", "75th %ile", "95th %ile"]
                keys   = ["p5", "p25", "p50", "p75", "p95"]
                for col, key, lbl in zip(cols, keys, labels):
                    val = pct.get(key, {})
                    eq  = val.get("final_equity", val) if isinstance(val, dict) else val
                    col.metric(lbl, f"{eq:,.0f}" if isinstance(eq, (int, float)) else str(eq))

            # Percentile equity paths chart
            sample_paths = result.get("sample_paths", [])
            if sample_paths:
                try:
                    import numpy as np
                    arr = np.array(sample_paths)
                    chart_df = pd.DataFrame({
                        "p5":     np.percentile(arr, 5,  axis=0),
                        "p25":    np.percentile(arr, 25, axis=0),
                        "median": np.percentile(arr, 50, axis=0),
                        "p75":    np.percentile(arr, 75, axis=0),
                        "p95":    np.percentile(arr, 95, axis=0),
                    })
                    st.subheader("Percentile Equity Paths")
                    st.line_chart(chart_df, use_container_width=True)
                except Exception:
                    pass

            st.code(_strip(report_text), language=None)

            with st.expander("📋 Progress log"):
                st.text(_strip(buf.getvalue()))

        except Exception as exc:
            st.error(f"Monte Carlo failed: {exc}")
            with st.expander("📋 Progress log"):
                st.text(_strip(buf.getvalue()))
            st.exception(exc)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 8 — Reports
# ══════════════════════════════════════════════════════════════════════════════

with T_REP:
    st.header("Saved Reports")
    st.caption("All reports are auto-saved to reports/ after each scan or backtest.")

    reports_dir = ROOT / "reports"
    txt_files   = sorted(reports_dir.glob("*.txt"), reverse=True) if reports_dir.exists() else []

    if not txt_files:
        st.info("No saved reports yet. Run the Daily Scan or a Backtest to generate reports.")
    else:
        col_a, col_b = st.columns([1, 3])
        with col_a:
            selected_name = st.radio(
                "Select report",
                options=[f.name for f in txt_files],
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
            )
            st.code(content, language=None)
