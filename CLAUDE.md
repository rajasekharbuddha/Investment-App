# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Mastermind Pro: a systematic stock research/signal/backtesting platform for US, EU, and India equities, shipped as two GUIs (Tkinter desktop + Streamlit browser) that share one strategy engine in `src/`. Research/paper-trading only — not a live trading system.

## Commands

```bash
# Install deps
python -m pip install -r requirements.txt

# Run the desktop app (Tkinter, 14 tabs)
python app.py

# Run the browser app (Streamlit, 15 tabs) — opens http://localhost:8501
streamlit run app_web.py

# Run all tests
pytest tests/

# Run a single test file / class / test
pytest tests/test_gates.py
pytest tests/test_gates.py::TestGate1Trend
pytest tests/test_gates.py::TestGate1Trend::test_all_pass

# Type-check (see mypy.ini gotcha below — do not run bare `mypy .`)
mypy app.py src/*.py

# CLI tools (all live in src/, all support --help)
python src/run_daily.py --markets US,EU,IN --dynamic
python src/run_backtest.py --market IN --start 2016-01-01
python src/run_longterm.py --markets IN --no-near
python src/run_backtest_longterm.py --market IN --slots 10 --rebalance 63
python src/run_sip.py --dry-run
python src/run_backtest_sip.py --regime-reserve 0.20
python src/run_walkforward.py --market IN --years 5
python src/run_montecarlo.py --trades reports/some-trades.csv
python src/run_stresstests.py --market IN
python src/run_replacement_list.py --market IN
```

Three pre-existing test failures are unrelated to any recent work and not a regression signal by themselves: `test_backtest.py::TestPositionSizeCap::{test_no_single_trade_costs_more_than_20pct,test_size_capped_decision_engine}` and `test_gates.py::TestGate4Liquidity::test_mult_tightens`.

## Architecture

### One engine, two UIs, flat imports

`app.py` (Tkinter) and `app_web.py` (Streamlit) are both thin presentation layers over the same `src/` strategy engine — every feature must exist identically in both, and a change to any `src/` module must be reflected in both UIs' workers (see "GUI/parity discipline" below).

**Critical gotcha:** nothing under `src/` is imported as a package. Every entry point (`app.py`, `app_web.py`, `tests/*.py`, and every `src/run_*.py` / `src/*.py` module) does `sys.path.insert(0, ".../src")` and then imports flatly (`from data import fetch_all`, not `from src.data import fetch_all`). `src/__init__.py` exists but is not used for imports — it's just there for pytest's package-uniqueness bookkeeping. This means:
- A top-level `data/` directory (the parquet price cache) and `src/data.py` share the bare name `data`. Static analysis tools that don't know about the flat-import convention will resolve `data` to the wrong thing and produce bogus "module has no attribute" errors. **`mypy.ini` (`explicit_package_bases = True`, `mypy_path = src`) exists specifically to fix this** — always type-check via `mypy app.py src/*.py`, never a bare `mypy .`.
- When adding a new `src/` module, don't add package-relative imports (`from .foo import bar`) — follow the existing flat convention or every other module's imports break.

### GUI/parity discipline

Both apps must expose the same feature set through their own idioms:
- `app.py`: each tab is a `_tab_X` builder (widgets) + `_run_X` (validates live field values, spawns a `threading.Thread`) + `_worker_X` (does the work, writes ANSI-tagged text to a shared `queue.Queue` drained by `_poll()` into a `tk.Text` terminal widget via `_QWriter`/`_TeeWriter`). Charts are embedded matplotlib (`FigureCanvasTkAgg`) — the desktop app has no other charting dependency, so don't reach for a second charting library.
- `app_web.py`: each tab is a `with T_X:` block that re-executes top-to-bottom on every Streamlit rerun; results needed across reruns (e.g. Compounding Sim) go in `st.session_state`. Charts are Plotly.
- **A live input field's value must always win over any cached/global config value on the next run.** The recurring bug class in this codebase: a tab has two actions sharing a "top-N"-style live field, and one action correctly threads the field through while a sibling action silently reads a stale value from `self._settings` / `DYNAMIC_UNIVERSE["SCORE_TOP_N"]` instead (this happened with the Long-Term tab's "Run LT Backtest" ignoring the live "Top-N IN" field while "Run Long-Term Screen" in the same tab honored it). When adding or touching a `_run_X`/`with T_X:` block, trace every live widget value all the way into the function call it feeds — don't assume a sibling action in the same tab does the same thing.
- Config/state that both UIs must agree on lives in a shared `src/` module, not duplicated per-UI: `src/app_settings.py` (`DEFAULTS`/`load_settings`/`save_settings`, backing `app_settings.json`) and `src/compounding_simulator.py` (`REGION_CONFIG`, `format_amount`, `format_amount_abbrev`). If you find yourself copy-pasting a config dict or formatting helper from one UI file to the other, extract it into `src/` instead — that duplication is exactly how the two UIs drifted apart before.

### Decision engine — single source of truth

`src/decision_engine.py`'s `DecisionEngine.run_day()` is the one pipeline used by **both** the live daily scan and the short-term backtest, specifically to prevent live/backtest logic drift. Its phases (see the module docstring):

```
Phase 0   Universe refresh (dynamic universe, if enabled)
Phase 0.5 Quality pre-filter (stock_selector scoring)
Phase 1   Read tuner state
Phase 2   Portfolio review (peak, breakeven, trailing stop, exits)
Phase 3   Universe scan (evaluate 5-gates per non-held ticker)
Phase 4   Sizing (R-based)
Phase 5   Replacement scan (same-market replacements for exits)
Phase 6   Fill remaining open slots
Phase 7   Update tuner
```

`src/config.py` is the single source of truth for strategy parameters (`ACCOUNT`, `RISK`, `MARKET_PARAMS`, `GATE_DEFAULTS`, `TUNER_MODES`/`TUNER_PARAMS`, `DYNAMIC_UNIVERSE`, `MOMENTUM_EXIT`, `RANKING`, `STRESS_WINDOWS`, `JOURNAL`). GUI Settings tabs patch this module in-process at runtime (`app.py`'s `_apply_settings_to_config()`) rather than editing the file — config.py itself should only be hand-edited for the underlying strategy watchlist/parameter defaults.

SIP has its own separate config block, `SIP_CONFIG` in `src/sip_strategy.py`, independent of the ATR-Dynamic/Long-Term config above (different budgets, gates, exit thresholds).

### Three strategy modes share infrastructure, not logic

- **Short-term (ATR-Dynamic)**: `decision_engine.py` + `backtest.py`, days-to-weeks holding period, 5-gate entry (`rules.py`), ATR-based sizing/trailing stop.
- **Long-term (fundamental + momentum)**: `run_longterm.py`/`backtest_longterm.py` + `fundamental.py` (9-metric Q-score), quarterly rebalancing, independent from the short-term DecisionEngine. `run_longterm.py`'s live position sizing (`_lt_update_portfolio`) deliberately mirrors `backtest_longterm.py`'s equal-weight formula exactly (`shares = floor((equity/slots) / (price×(1+slippage)×(1+commission)))`) — same lockstep-logic requirement as SIP below. Exit conditions for held positions are evaluated by `check_lt_exit()` against thresholds (`compute_exit_thresholds()`) captured *at entry time*, not recomputed against a moving target — don't evaluate a position's fundamentals against a freshly-recomputed threshold, or every position looks perpetually fine.
- **SIP (regime-reserve)**: `sip_strategy.py` (live) / `backtest_sip.py` (historical) implement *identical* regime-reserve mechanics on purpose — a per-region cash reserve (`regime_reserve_pct`, default 10%) is held back monthly and released in full when that region's benchmark index closes below its SMA_200. Keep these two files' logic in lockstep; that's what makes backtest results comparable to live behavior. `src/compare_sip_*.py` are experimental A/B comparisons, not part of the production path.

All three modes route through the same `data.py` (yfinance + parquet cache in `data/`) and `universe.py` (dynamic universe builder, cached under `universes/`, 7-day TTL) — these are two distinct cache layers, don't conflate them.

### Known historical footguns (already fixed, but explain otherwise-confusing code)

- `update_trailing_stop()`'s `trail_mult` parameter was silently ignored in the backtest until 2026-06-07 (hardcoded to 5.5× regardless of config) — if you see stale comments/results referencing this, that's why.
- `RISK["MAX_POSITION_SIZE_PCT"]` in `backtest.py` is the only effective position-size cap; `risk_pct` in `MARKET_PARAMS` does not affect backtest sizing despite looking like it should.
- IN's dynamic universe used to be capped at the Nifty 250 constituent list regardless of the configured Top-N, so raising "Top-N IN" above ~250 had no effect. `src/universe.py`'s `build_in_universe()` now sources Nifty 500 first (falls back to Nifty 250, then Nifty 100, only if that fetch fails) — if you touch universe sizing, verify against the *actual* index list size, not just that a parameter threads through code.

### `RUN17_STRATEGY.md`

This is the locked, validated production parameter set for the short-term IN strategy (10-year backtest, 22 tuning runs). Treat it as authoritative for those specific parameter values — don't change `trail_mult`/`stop_mult`/position-size defaults without re-running the full 10-year backtest referenced there.

### Root-level `tmp_bt_*.py` and `_bt_time.py`/`_fetch_test.py`/`_wfo_*.py` in `src/`

One-off exploratory/debug scripts from prior tuning sessions, not part of the maintained architecture — don't treat them as call sites to keep in sync when refactoring `src/`.
