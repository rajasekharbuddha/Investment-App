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

T_SCAN, T_BT, T_LTB, T_LTS, T_WF, T_ST, T_MC, T_PORT, T_REP = st.tabs([
    "📊 Daily Scan",
    "📈 ST Backtest",
    "🏦 LT Backtest",
    "🔭 LT Screener",
    "🔄 Walk-Forward",
    "💪 Stress Tests",
    "🎲 Monte Carlo",
    "💼 Portfolio",
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

                today_ts = (pd.Timestamp(scan_asof.strip()).normalize()
                            if scan_asof.strip()
                            else pd.Timestamp.today().normalize())

                st.write(f"⚙ Calculating indicators for {len(raw_data)} tickers…")
                with contextlib.redirect_stdout(buf):
                    data_map_full = {t: calculate_all(df) for t, df in raw_data.items()}
                    # Slice to as-of date — matches desktop app behaviour, prevents lookahead
                    data_map = {
                        t: df[df.index <= today_ts]
                        for t, df in data_map_full.items()
                        if not df[df.index <= today_ts].empty
                    }

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

                    # Wire sidebar momentum-exit toggle into config before engine runs
                    import config as _cfg
                    _cfg.MOMENTUM_EXIT["ENABLED"] = momentum_exit_s

                    tuner  = AdaptiveTuner.load(str(TUNER_FILE))
                    engine = DecisionEngine(tuner=tuner)

                    result = engine.run_day(
                        today=today_ts,
                        data_map=data_map,
                        portfolio=portfolio_data,
                        equity=equity_s,
                        context="live",
                        watchlist=active_wl,
                        quality_scores=quality_scores if quality_filter_s else None,
                    )

                # Save updated portfolio (held positions + new entries)
                new_portfolio = list(result["held"])
                for entry_info in result["new_entries"] + result["replacement_queue"]:
                    ticker = entry_info["ticker"]
                    close  = (float(data_map[ticker].iloc[-1]["Close"])
                              if ticker in data_map else entry_info.get("price", 0))
                    if entry_info.get("shares", 0) > 0:
                        regime = entry_info.get("regime", {})
                        new_portfolio.append({
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
                            "risk_pct":          (regime.get("risk_pct", 0.05)
                                                  if isinstance(regime, dict) else 0.05),
                            "regime":            (regime.get("label", "Normal")
                                                  if isinstance(regime, dict) else "Normal"),
                            "is_high_vol":       entry_info.get("is_high_vol", False),
                            "cost":              entry_info.get("cost", 0),
                        })
                PORTFOLIO_FILE.parent.mkdir(exist_ok=True)
                PORTFOLIO_FILE.write_text(json.dumps(new_portfolio, indent=2))
                _n_held = len(result["held"])
                _n_new  = len(result["new_entries"])
                _n_repl = len(result["replacement_queue"])
                _port_msg = f"Portfolio saved: {_n_held} held"
                if _n_new:  _port_msg += f", +{_n_new} new"
                if _n_repl: _port_msg += f", +{_n_repl} queued"
                st.write(f"💾 {_port_msg}")

                st.write("📝 Generating report…")
                with contextlib.redirect_stdout(buf):
                    candidates = list(result["candidates"].values())
                    for c in candidates:
                        sz = result["sizing"].get(c["ticker"])
                        if sz:
                            c["sizing"] = sz

                    loaded_mode = result.get("loaded_tuner_mode", result["tuner_mode"])
                    report_text = daily_report(
                        decisions=candidates,
                        account_eur=equity_s,
                        watchlist=active_wl,
                        markets=MARKETS,
                        tuner_mode=loaded_mode,
                        risk_scale=result["risk_scale"],
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
            loaded_mode = result.get("loaded_tuner_mode", result["tuner_mode"])
            next_mode   = result["tuner_mode"]
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
                    st.line_chart(chart_df, width="stretch")
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
# TAB 8 — Portfolio
# ══════════════════════════════════════════════════════════════════════════════

_CURR_SYM = {"IN": "Rs ", "US": "$", "EU": "€"}
_PORT_FILE = ROOT / "portfolio" / "positions.json"


def _load_positions() -> list:
    if not _PORT_FILE.exists():
        return []
    try:
        return json.loads(_PORT_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


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
    st.caption("Live P&L, stop levels, and days held. Prices fetched from Yahoo Finance.")

    positions = _load_positions()

    if not positions:
        st.info("No open positions found in portfolio/positions.json. "
                "Run a Daily Scan to populate the portfolio.")
    else:
        # ── Refresh controls ─────────────────────────────────────────────────
        btn_col, ts_col = st.columns([1, 4])
        with btn_col:
            do_refresh = st.button("🔄 Refresh Prices", type="primary", key="port_refresh")
        with ts_col:
            if "port_fetched_at" in st.session_state:
                st.caption(f"Last fetched: {st.session_state['port_fetched_at']}")

        tickers = [p["ticker"] for p in positions]

        if do_refresh or "port_prices" not in st.session_state:
            with st.spinner(f"Fetching live prices for {len(tickers)} tickers…"):
                st.session_state["port_prices"]     = _fetch_live_prices(tickers)
                st.session_state["port_fetched_at"] = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M")

        price_map: dict = st.session_state.get("port_prices", {})

        # ── Build rows ───────────────────────────────────────────────────────
        today = pd.Timestamp.today().normalize()
        rows  = []
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
            strategy   = p.get("strategy", "")
            days_held  = max((today - entry_date).days, 0)

            cur_px = price_map.get(ticker)

            if cur_px is not None:
                cur_val   = shares * cur_px
                pnl       = cur_val - cost
                pnl_pct   = (cur_px - entry_px) / entry_px * 100 if entry_px else 0.0
                init_risk = entry_px - stop_init
                r_mult    = (cur_px - entry_px) / init_risk if init_risk > 0 else 0.0
                stop_dist = (cur_px - stop) / cur_px * 100 if cur_px else 0.0

                if cur_px <= stop:
                    status = "🔴 STOP HIT"
                elif stop_dist < 5.0:
                    status = "🟡 Near stop"
                else:
                    status = "🟢 Safe"
            else:
                cur_val = pnl = pnl_pct = r_mult = stop_dist = None
                status = "⚪ No price"

            rows.append({
                "status":     status,
                "ticker":     ticker,
                "market":     market,
                "curr":       curr,
                "sector":     p.get("sector", "Unknown"),
                "entry_date": entry_date.strftime("%Y-%m-%d"),
                "days_held":  days_held,
                "entry_px":   entry_px,
                "cur_px":     cur_px,
                "stop":       stop,
                "stop_dist":  stop_dist,
                "shares":     shares,
                "cost":       cost,
                "cur_val":    cur_val,
                "pnl":        pnl,
                "pnl_pct":   pnl_pct,
                "r_mult":     r_mult,
                "regime":     regime,
                "peak_px":    peak_px,
                "atr":        atr,
                "trail_mult": trail_mult,
                "strategy":   strategy,
                "lt_combined": p.get("lt_combined"),
                "lt_grade":    p.get("lt_grade"),
            })

        # ── Global alerts (shown above all tabs) ─────────────────────────────
        stop_hits = [r for r in rows if "STOP HIT" in r["status"]]
        near_stps = [r for r in rows if "Near stop" in r["status"]]
        if stop_hits:
            st.error("🔴 **Stop breached — review immediately:** "
                     + ", ".join(r["ticker"] for r in stop_hits))
        if near_stps:
            st.warning("🟡 **Within 5% of stop:** "
                       + ", ".join(f"{r['ticker']} ({r['stop_dist']:.1f}%)"
                                   for r in near_stps))

        # ── Sub-tabs by region ────────────────────────────────────────────────
        def _render_port_tab(tab_rows: list, market: str | None = None) -> None:
            """Render summary + table + expanders for a given set of rows."""
            if not tab_rows:
                st.info(f"No open positions{f' in {market}' if market else ''}.")
                return

            curr = _CURR_SYM.get(market, "$") if market else None
            priced = [r for r in tab_rows if r["pnl"] is not None]

            # Summary metrics
            total_cost = sum(r["cost"] for r in tab_rows)
            total_val  = sum(r["cur_val"] for r in priced) if priced else None
            total_pnl  = sum(r["pnl"]  for r in priced)  if priced else None
            pnl_pct_total = (total_pnl / total_cost * 100
                             if total_pnl is not None and total_cost else None)

            if market:
                # Single currency — show clean totals
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Positions", len(tab_rows))
                c2.metric(f"Invested ({curr})",
                          f"{curr}{total_cost:,.0f}")
                c3.metric(f"Unrealised P&L ({curr})",
                          f"{curr}{total_pnl:+,.0f}" if total_pnl is not None else "—",
                          delta=f"{pnl_pct_total:+.2f}%" if pnl_pct_total is not None else None,
                          delta_color="normal" if (total_pnl or 0) >= 0 else "inverse")
                c4.metric("Current Value",
                          f"{curr}{total_val:,.0f}" if total_val is not None else "—")
            else:
                # Overview: break down by market
                mkt_groups: dict = {}
                for r in tab_rows:
                    mkt_groups.setdefault(r["market"], []).append(r)

                c1, c2, c3 = st.columns(3)
                c1.metric("Total Positions", len(tab_rows))
                c2.metric("Markets", ", ".join(sorted(mkt_groups.keys())))
                c3.metric("Alerts",
                          f"🔴 {len(stop_hits)}  🟡 {len(near_stps)}"
                          if (stop_hits or near_stps) else "✅ All safe")

                for mk, mk_rows in sorted(mkt_groups.items()):
                    mk_curr  = _CURR_SYM.get(mk, "$")
                    mk_cost  = sum(r["cost"] for r in mk_rows)
                    mk_priced = [r for r in mk_rows if r["pnl"] is not None]
                    mk_pnl   = sum(r["pnl"] for r in mk_priced) if mk_priced else None
                    pnl_str  = f"{mk_curr}{mk_pnl:+,.0f}" if mk_pnl is not None else "—"
                    st.caption(
                        f"**{mk}** — Invested: {mk_curr}{mk_cost:,.0f}  ·  "
                        f"P&L: {pnl_str}  ·  {len(mk_rows)} position(s)"
                    )

            st.markdown("---")

            # Table
            table_rows = []
            for r in tab_rows:
                table_rows.append({
                    "Status":      r["status"],
                    "Ticker":      r["ticker"] + (" 📈" if r["strategy"] == "longterm" else ""),
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
                })
            tbl_df = pd.DataFrame(table_rows)
            st.dataframe(
                tbl_df, width="stretch", hide_index=True,
                column_config={
                    "Status":      st.column_config.TextColumn("Status",   width="small"),
                    "Ticker":      st.column_config.TextColumn("Ticker",   width="small"),
                    "Days":        st.column_config.NumberColumn("Days",   format="%d"),
                    "Entry Px":    st.column_config.NumberColumn("Entry Px",  format="%.2f"),
                    "Live Px":     st.column_config.NumberColumn("Live Px",   format="%.2f"),
                    "Stop":        st.column_config.NumberColumn("Stop",      format="%.2f"),
                    "Stop Dist %": st.column_config.ProgressColumn(
                                       "Stop Dist %", min_value=0, max_value=30,
                                       format="%.1f%%"),
                    "Shares":      st.column_config.NumberColumn("Shares", format="%d"),
                    "P&L":         st.column_config.NumberColumn("P&L",    format="%+.0f"),
                    "P&L %":       st.column_config.NumberColumn("P&L %",  format="%+.2f%%"),
                    "R-Mult":      st.column_config.NumberColumn("R-Mult", format="%+.2fR"),
                },
            )

            # Per-position expanders
            st.subheader("Position Detail")
            for r in tab_rows:
                pnl_label = f"{r['curr']}{r['pnl']:+,.0f}" if r["pnl"] is not None else "—"
                r_label   = f"{r['r_mult']:+.2f}R" if r["r_mult"] is not None else "—"
                lt_badge  = "  📈 LT" if r["strategy"] == "longterm" else ""
                with st.expander(
                    f"**{r['ticker']}**{lt_badge}  ·  {r['status']}  ·  "
                    f"P&L {pnl_label}  ·  {r_label}  ·  {r['days_held']}d"
                ):
                    c1, c2, c3 = st.columns(3)
                    with c1:
                        st.metric("Entry Price", f"{r['curr']}{r['entry_px']:,.2f}")
                        st.metric("Live Price",  f"{r['curr']}{r['cur_px']:,.2f}"
                                                 if r["cur_px"] else "—")
                        st.metric("Entry Date",  r["entry_date"])
                    with c2:
                        st.metric("Stop Loss",    f"{r['curr']}{r['stop']:,.2f}")
                        st.metric("Stop Cushion", f"{r['stop_dist']:.1f}%"
                                                  if r["stop_dist"] is not None else "—")
                        st.metric("Peak Price",   f"{r['curr']}{r['peak_px']:,.2f}")
                    with c3:
                        st.metric("ATR at Entry", f"{r['curr']}{r['atr']:.2f}")
                        st.metric("Trail Stop",   f"{r['trail_mult']}× ATR")
                        st.metric("Regime",       r["regime"])

                    st.caption(
                        f"Shares: {r['shares']}  ·  Cost: {r['curr']}{r['cost']:,.0f}"
                        + (f"  ·  Value: {r['curr']}{r['cur_val']:,.0f}" if r["cur_val"] else "")
                    )
                    if r["strategy"] == "longterm":
                        st.caption(
                            f"📈 Long-Term position  ·  "
                            f"Combined score: {r['lt_combined']}  ·  Grade: {r['lt_grade']}  ·  "
                            f"Exit trigger: SMA_200 cross"
                        )

        tab_ov, tab_us, tab_eu, tab_in = st.tabs(["🌍 Overview", "🇺🇸 US", "🇪🇺 EU", "🇮🇳 IN"])
        with tab_ov:
            _render_port_tab(rows, None)
        with tab_us:
            _render_port_tab([r for r in rows if r["market"] == "US"], "US")
        with tab_eu:
            _render_port_tab([r for r in rows if r["market"] == "EU"], "EU")
        with tab_in:
            _render_port_tab([r for r in rows if r["market"] == "IN"], "IN")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 9 — Reports
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
