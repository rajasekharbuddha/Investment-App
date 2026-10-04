# Mastermind Pro — Investment Research & Analysis Platform

Systematic stock research, signal generation, and strategy backtesting across US, European, and Indian equity markets. Available as both a **Tkinter desktop app** and a **Streamlit browser app** — both share the same strategy engine.

> **Disclaimer:** This tool is for research and paper trading only. Output is not financial advice. Do not deploy live capital without independent validation and regulatory compliance review.

---

## Table of Contents

1. [Overview](#overview)
2. [Features](#features)
3. [Architecture](#architecture)
4. [Installation](#installation)
5. [Quick Start](#quick-start)
6. [Desktop App Tabs](#desktop-app-tabs)
7. [Browser App Tabs](#browser-app-tabs)
8. [CLI Tools](#cli-tools)
9. [Strategy Details](#strategy-details)
10. [Configuration](#configuration)
11. [File Structure](#file-structure)
12. [Markets Supported](#markets-supported)
13. [Glossary & Further Reading](#glossary--further-reading)

---

## Overview

Mastermind Pro combines two complementary investment frameworks:

| Mode | Timeframe | Approach |
|------|-----------|----------|
| **Short-Term (ATR-Dynamic)** | Days to weeks | 5-gate technical filter → ATR-sized positions → adaptive trailing stop |
| **Long-Term (Fundamental + Momentum)** | Months to years | Fundamental Q-score pre-screen → momentum rotation → exit-watch signals |
| **SIP (Systematic Investment Plan)** | Monthly, ongoing | Fixed monthly budget per region → quality + momentum picks → [regime-reserve](#glossary--further-reading) dip-buying |

Both modes are accessible from either the desktop GUI or the browser UI. All `src/` strategy modules are shared — any config change applies to both interfaces.

### Two interfaces, one engine

| Interface | Launch command | Best for |
|-----------|---------------|----------|
| **Desktop app** (`app.py`) | `python app.py` | Daily use, journal integration, offline |
| **Browser app** (`app_web.py`) | `streamlit run app_web.py` | Richer charts, Portfolio live P&L, shareable locally |

---

## Features

### Daily Signal Engine
- 5-gate entry filter: SMA trend, volume, RSI, MACD histogram, SMA distance
- Signals: **ENTER** (buy), **NEAR** (approaching entry), **WAIT**, **SKIP**
- ATR-based position sizing with per-regime risk percentages
- Momentum exit timer: ejects positions whose momentum turns negative before the trailing stop fires
- Adaptive tuner: automatically loosens or tightens gate thresholds based on signal density
- **Auto-saves portfolio** after every scan — `positions.json` reflects entries, exits, and held positions immediately

### Portfolio Monitor
- Loads open positions from `portfolio/positions.json` (auto-updated after each scan)
- Fetches live prices via Yahoo Finance — batch download with per-ticker fallback
- Per-position: live P&L, P&L %, R-multiple, stop cushion %, days held
- Stop Dist % rendered as a visual progress bar in browser app
- Alert banners: 🔴 STOP HIT / 🟡 Near stop (<5%) / 🟢 Safe
- **4 regional sub-tabs** (Overview / US / EU / IN) — each regional tab shows single-currency totals; Overview shows counts per market
- Long-term positions (added by LT Screener) flagged with `[LT]` badge and fundamental grade
- Available in both desktop app (Portfolio tab) and browser app

### Short-Term Backtest
- Tick-by-tick simulation using the same DecisionEngine as the live scan
- 8-slot equal-weight portfolio, 24% base position size (32% cap for momentum leaders)
- 5.5× ATR trailing stop
- Benchmark comparison (Nifty / S&P 500 / STOXX 50)
- Outputs: equity curve chart, trade log CSV download, year-by-year returns, drawdown analysis

### Long-Term Screener
- Fundamental scoring across 9 metrics: ROE, revenue growth, EPS growth, D/E ratio, operating margin, FCF yield, PEG ratio, P/B ratio, net margin
- Technical pre-gate: SMA_50 > SMA_200 + Close > SMA_200 + SMA_50 rising
- Tiered output: **BUY**, **NEAR**, **WATCH** with combined quality score (0–100)
- Per-stock **Exit Watch** block: SMA levels, gap %, and dynamic fundamental sell thresholds
- Per-stock **Graham line** (*The Intelligent Investor* defensive tests): current ratio ≥ 2, long-term debt ≤ net current assets, P/E × P/B ≤ 22.5, plus the Graham number and margin of safety. An optional **Graham filter** drops stocks that fail

### Long-Term Backtest
- Quarterly momentum rebalancing: rotate out of laggards, fill slots with top scorers
- Three independent sell triggers:
  1. **Daily SMA breakdown** — exit immediately when SMA_50 crosses below SMA_200
  2. **Momentum floor** (exit-watch proxy) — exit at rebalance if avg momentum score < threshold
  3. **Rotation** — dropped out of top-N ranking
- Year-by-year returns vs benchmark, alpha calculation

### SIP Monthly Plan (Regime-Reserve Strategy)
- Deploys a fixed monthly budget per region (default $2,000 US / €2,000 EU / ₹20,000 IN) into quality, uptrending stocks
- Candidate gates: SMA_50 > SMA_200 ([uptrend](#glossary--further-reading)) + Q-score ≥ 55, ranked by 40% Q-score / 60% [momentum](#glossary--further-reading)
- **C2 "regime reserve"**: 10% of each region's monthly budget is *held back* in cash instead of invested immediately. The reserve accumulates until the region's benchmark index ([S&P 500](#glossary--further-reading), [STOXX 50](#glossary--further-reading), or [Nifty 50](#glossary--further-reading)) closes below its 200-day [simple moving average](#glossary--further-reading) — a common downtrend signal — at which point the *entire* accumulated reserve is deployed in one go, concentrating dry powder at market weakness rather than spreading it evenly
- Exit rules: SMA breakdown (10 consecutive days below SMA_200), Q-score < 35, or position > 15% of portfolio (trimmed to 10%)
- Both live cycle (`sip_strategy.py`) and historical simulation (`backtest_sip.py`) implement the identical regime-reserve mechanics, so backtest results are directly comparable to live behaviour
- State persisted per-run in `portfolio/sip_holdings.json` — tracks holdings, per-region cash reserve, and full cycle history
- Available as the **SIP Plan** tab in both apps, plus `run_sip.py` / `run_backtest_sip.py` CLI tools

### Walk-Forward Optimisation
- Splits history into (train, test) folds
- Optimises gate parameters on training window, evaluates on out-of-sample test
- Reports per-fold CAGR, Sharpe, and best parameter set

### Monte Carlo Robustness
- N simulations over backtest trade log
- Random trade ordering (bootstrap), random trade skipping, cost multiplier shock
- Outputs percentile bands (5th / 25th / 50th / 75th / 95th) for final equity
- Percentile equity path chart in browser app

### Stress Tests
- **Historical windows**: 2008 financial crisis, 2020 COVID crash, 2022 rate shock
- **Synthetic shocks**: ATR doubled, volume × 0.30, overnight gap injection, correlation crisis

### Dynamic Universe
- Builds universe from live index constituents (Nifty 500 — falling back to Nifty 250, then Nifty 100, only if that fetch fails — S&P 500, DAX, FTSE 100, FTSE MIB)
- Quality-scores all constituents and keeps top-N per market
- Cached with 7-day TTL to avoid redundant downloads

### Reports & Journal
- All scan and backtest outputs saved to `reports/` as plain-text files
- Excel trading journal integration via openpyxl
- **Logs ENTER signals only** — WAIT/NEAR are too numerous with dynamic universe enabled (~200 tickers per market)
- Deduplicates: skips tickers already logged today

---

## Architecture

```
InvestmentApp/
├── app.py                    # Tkinter desktop GUI — 14 tabs
├── app_web.py                # Streamlit browser app — 15 tabs
├── CLAUDE.md                 # Guidance for Claude Code sessions working in this repo
├── .streamlit/
│   └── config.toml           # Streamlit config (skips email prompt, sets port 8501)
├── src/
│   ├── config.py             # All strategy parameters (single source of truth)
│   ├── app_settings.py       # Shared GUI settings (app_settings.json) — both UIs read/write the same file
│   ├── data.py               # yfinance fetch + parquet cache
│   ├── indicators.py         # SMA, ATR, RSI, MACD, Bollinger, volume indicators
│   ├── rules.py              # 5-gate entry evaluator
│   ├── ranking.py            # Momentum scoring [14, 30, 63] periods
│   ├── adaptive_tuner.py     # Gate auto-tightening / loosening
│   ├── decision_engine.py    # 7-phase buy/sell pipeline (live + backtest)
│   ├── backtest.py           # Short-term historical simulation
│   ├── report.py             # Daily scan report formatter
│   ├── fundamental.py        # Fundamental data fetch, cache, and scoring
│   ├── run_longterm.py       # Long-term screener pipeline + CLI — equal-weight sizing, Exit Watch (compute_exit_thresholds/check_lt_exit)
│   ├── backtest_longterm.py  # Long-term backtest engine (same equal-weight sizing formula as run_longterm.py)
│   ├── compounding_simulator.py  # Two-phase compounding simulator + shared REGION_CONFIG (used by both UIs)
│   ├── universe.py           # Dynamic universe builder
│   ├── stock_selector.py     # Quality composite score
│   ├── select_stocks.py      # Stock selection helpers
│   ├── replacement_list.py   # Bench candidate list builder
│   ├── replacement_engine.py # In-portfolio replacement logic
│   ├── stress_tests.py       # Historical + synthetic stress tests
│   ├── walk_forward.py       # Walk-forward optimisation
│   ├── monte_carlo.py        # Monte Carlo simulation
│   ├── post_trade.py         # Post-trade journal enrichment
│   ├── journal.py            # Excel journal writer (ENTER signals only)
│   ├── sip_strategy.py       # SIP live cycle — regime-reserve dip-buying (see Strategy Details)
│   ├── backtest_sip.py       # SIP historical simulation (same regime-reserve mechanics)
│   ├── compare_sip_variants.py       # A/B/C/D SIP variant comparison (experimental)
│   ├── compare_sip_exits.py          # SIP exit-rule comparison (experimental)
│   ├── compare_sip_dip_reserve.py    # Per-position dip-reserve variant (experimental)
│   ├── run_daily.py          # CLI: daily scan
│   ├── run_backtest.py       # CLI: short-term backtest
│   ├── run_backtest_longterm.py  # CLI: long-term backtest
│   ├── run_sip.py            # CLI: SIP monthly cycle
│   ├── run_backtest_sip.py   # CLI: SIP backtest
│   ├── run_montecarlo.py     # CLI: Monte Carlo
│   ├── run_walkforward.py    # CLI: walk-forward
│   ├── run_stresstests.py    # CLI: stress tests
│   └── run_replacement_list.py  # CLI: replacement candidates
├── tests/
│   ├── test_gates.py         # Gate evaluation unit tests
│   └── test_backtest.py      # Backtest unit tests
├── data/                     # Parquet price cache (auto-populated)
├── universes/                # Index constituent CSV files
│   ├── IN_nifty500.csv
│   ├── IN_nifty250.csv       # fallback
│   ├── IN_nifty100.csv       # fallback
│   ├── US_sp500.csv
│   ├── EU_dax.csv
│   ├── EU_ftse100.csv
│   └── EU_ftsemib.csv
├── reports/                  # Saved scan and backtest reports
├── portfolio/
│   ├── positions.json        # Legacy combined positions file (desktop app)
│   ├── st_{US,EU,IN}.json    # Short-term (ATR-Dynamic) open positions, per region
│   ├── lt_{US,EU,IN}.json    # Long-term (fundamental+momentum) open positions, per region
│   └── sip_holdings.json     # SIP holdings, per-region cash reserve, and cycle history
└── requirements.txt
```

---

## Installation

**Requirements:** Python 3.10+ (tested on 3.14) · Windows 10/11 · macOS 12+ · Ubuntu 20.04+

---

### Windows

1. **Install Python** from [python.org](https://python.org) — check **"Add Python to PATH"** during setup.
2. **Clone or download** the project.
3. Open **Command Prompt** or **PowerShell** in the project folder:

```powershell
cd InvestmentApp
python -m pip install -r requirements.txt
```

> **Multiple Python installs?** Use `python -m pip` (not bare `pip`) to guarantee packages land in the same interpreter that runs the app. If `python` opens the Windows Store, run `python3` instead or use the full path: `C:\Path\To\python.exe -m pip install -r requirements.txt`.

Tkinter is bundled with the official Windows Python installer — no extra step needed.

---

### macOS

1. **Install Python 3.10+** — recommended via [python.org](https://python.org) installer or Homebrew:

```bash
brew install python@3.12
```

2. **Clone or download** the project, then:

```bash
cd InvestmentApp
python3 -m pip install -r requirements.txt
```

Tkinter is included with the python.org macOS installer. If you installed via Homebrew:

```bash
brew install python-tk@3.12   # match your Python version
```

**Journal path:** The app auto-detects OneDrive on macOS at `~/Library/CloudStorage/OneDrive-Personal/`. If your OneDrive layout differs, set the path explicitly:

```bash
export JOURNAL_PATH="$HOME/path/to/Mastermind-Trading-Journal.xlsx"
```

Add that line to `~/.zshrc` (or `~/.bash_profile`) to make it permanent.

---

### Linux (Ubuntu / Debian)

1. **Install Python and Tkinter:**

```bash
sudo apt update
sudo apt install python3 python3-pip python3-tk
```

For Fedora / RHEL:
```bash
sudo dnf install python3 python3-pip python3-tkinter
```

2. **Clone or download** the project, then:

```bash
cd InvestmentApp
pip3 install -r requirements.txt
```

**Journal path:** Set via environment variable (no OneDrive auto-detect on Linux):

```bash
export JOURNAL_PATH="$HOME/path/to/Mastermind-Trading-Journal.xlsx"
```

---

### Python packages installed

```
yfinance>=0.2.40
pandas>=2.0
numpy>=1.26
openpyxl>=3.1
pyarrow>=14.0
requests>=2.31
streamlit>=1.35
```

---

## Quick Start

### Desktop app (Tkinter)

| OS | Command |
|----|---------|
| Windows | `python app.py` |
| macOS | `python3 app.py` |
| Linux | `python3 app.py` |

### Browser app (Streamlit)

| OS | Command |
|----|---------|
| Windows | `python -m streamlit run app_web.py` |
| macOS / Linux | `streamlit run app_web.py` |

Opens automatically at **http://localhost:8501**. If the browser doesn't open, navigate there manually.

### CLI — daily scan
```bash
python src/run_daily.py
python src/run_daily.py --markets IN
python src/run_daily.py --markets US,EU,IN --dynamic
```

### CLI — short-term backtest
```bash
python src/run_backtest.py
python src/run_backtest.py --market IN --start 2016-01-01
```

### CLI — long-term screener
```bash
python src/run_longterm.py
python src/run_longterm.py --markets IN --no-near
python src/run_longterm.py --equity 200000 --slots 15   # equal-weight sizing for new BUY signals
python src/run_longterm.py --graham                     # keep only Graham defensive passes
```

### CLI — long-term backtest
```bash
python src/run_backtest_longterm.py
python src/run_backtest_longterm.py --market IN --start 2015-01-01
python src/run_backtest_longterm.py --market IN --slots 10 --rebalance 63 --momentum-floor -5
python src/run_backtest_longterm.py --no-breakdown --momentum-floor -99
```

### CLI — SIP monthly cycle
```bash
python src/run_sip.py                       # US + EU + IN, per-region budgets, today
python src/run_sip.py --markets US          # US only
python src/run_sip.py --min-q 60            # stricter quality gate
python src/run_sip.py --dry-run             # preview without saving state
```

### CLI — SIP backtest
```bash
python src/run_backtest_sip.py
python src/run_backtest_sip.py --markets IN --start 2018-01-01
python src/run_backtest_sip.py --regime-reserve 0.20   # test a larger reserve
python src/run_backtest_sip.py --regime-reserve 0      # disable reserve (100% deployed monthly)
```

> **macOS / Linux:** Replace `python` with `python3` in all CLI commands if `python` is not aliased to Python 3 on your system.

---

## Desktop App Tabs

Launch with `python app.py`. Fourteen tabs across the top — full feature parity with the browser app.

### Daily Scan
Select markets (US / EU / IN / All), an as-of date (today or a past date for historical simulation), and optional quality filter. Runs the live signal scan. Output shows ENTER / NEAR / WAIT / SKIP decisions with ATR, gate details, stop levels, and position sizing. Portfolio is auto-saved and journal is updated after each run.

### Universe
Scores every ticker in the configured universe by momentum velocity, SMA_50 trend distance, and composite grade. Useful for seeing which stocks are building momentum before they trigger a full ENTER signal.

### Post-Trade
Enriches today's journal rows (WAIT / ENTER / NEAR) with position sizing details, market-behaviour context, and Tier 3 reflection notes. Run once after the daily scan.

### Backtest
Configure market, date range, and equity. Runs the full ATR-Dynamic short-term strategy simulation. Output includes equity curve, year-by-year returns vs benchmark, trade statistics, max drawdown, and Sharpe/Sortino ratios.

### Long-Term
Two sub-tools in one tab:

**Screener** — fundamental + technical quality screener. Produces a tiered report (BUY / NEAR / WATCH) with Q-scores, red-flag alerts, and an Exit Watch block per stock. Configure **Slots** alongside Markets/Min-Q/Top-N IN: new Tier-1 ENTER signals are automatically sized equal-weight (account equity ÷ slots) and added to the portfolio with real share counts, capped to however many slots are actually empty — no more manually filling in `shares`/`cost` after the fact. Each new position also stores the fundamental Exit Watch thresholds (ROE floor, D/E ceiling, revenue-growth/FCF sign, entry P/E) captured at that moment, so a later Portfolio refresh can evaluate them against *today's* numbers. Tick **Graham filter** to keep only stocks that pass Graham's defensive tests.

**Backtest** — quarterly momentum rebalancing backtest with configurable slots, rebalance interval, breakdown exit toggle, and momentum floor.

### Walk-Forward
Rolling optimisation. Configure market, years of history, train/test window size (trading days), and anchored vs rolling mode.

### Stress Tests
Run historical (2008/2020/2022) and/or synthetic (vol spike, liquidity collapse, gap risk, correlation crisis) scenarios.

### Monte Carlo
Bootstraps a trades CSV from a previous Backtest run. Configure simulations, trade-skip probability, and equity. Shows a percentile equity-path chart (p5 / p25 / median / p75 / p95) alongside the text summary.

### SIP Plan
Monthly Systematic Investment Plan — deploys a fixed per-region budget into quality/momentum picks, holding back a 10% regime reserve that releases in full when a region's benchmark index drops below its 200-day SMA. Two sub-tools:

**Monthly Cycle** — run this month's SIP deployment (or a dry-run preview), showing candidates, allocation, exit signals, and a regime-reserve status readout per region.

**SIP Backtest** — historical simulation of the same regime-reserve logic, with a configurable `regime_reserve_pct`. See [SIP: Regime-Reserve Dip-Buying Strategy](#sip-regime-reserve-dip-buying-strategy) for the mechanics.

### Compounding Sim
A standalone, pure compound-interest simulator — independent of the stock-picking engines in every other tab. Models a two-phase wealth strategy: **Phase 1 "Matching Velocity"** (contribute a fixed monthly amount matching the lump sum's initial organic growth) followed by **Phase 2 "Pure Compounding"** (contributions drop to zero). Configurable region (IN/US/EU), initial capital, target ROI, and phase durations. Outputs an Executive Summary, a milestone tracker, a Rule of 72 validation, a phase-colour-coded trajectory chart (embedded matplotlib) with milestone lines, and a full Yearly/Monthly ledger (CSV export). **Stress Test mode** runs a Monte Carlo simulation to show the success rate of reaching the goal and the effect of volatility drag on the median outcome, with a percentile-outcome histogram. Backed by `src/compounding_simulator.py`, shared with the browser app via `REGION_CONFIG`/`format_amount`/`format_amount_abbrev`.

### Portfolio
Live portfolio monitor — split into **4 regional sub-tabs** so P&L totals are always in a single currency:

| Sub-tab | Content |
|---------|---------|
| 🌍 Overview | All positions combined — position counts by region, pointer to regional tabs for financials |
| 🇺🇸 US | US positions only — totals in USD |
| 🇪🇺 EU | EU positions only — totals in EUR |
| 🇮🇳 IN | IN positions only — totals in INR |

A global alerts strip above the tabs shows stop hits and near-stop warnings across all regions.

| Column | Description |
|--------|-------------|
| Status | STOP HIT / NEAR STOP / Safe (short-term); for long-term positions, also EXIT SIGNAL / WATCH from the live Exit Watch check (see Long-Term above) |
| Live Px | Current price from Yahoo Finance |
| Stop | Current trailing stop level |
| Dist% | Distance to stop as a percentage |
| P&L / P&L% | Unrealised gain/loss vs cost |
| R× | R-multiples earned based on initial risk |
| Days | Calendar days since entry |

Click **Refresh Prices** to fetch live prices. Rows colour red for stop hit, yellow for near stop, green for safe. Details area below each table shows full per-position breakdown. Long-term positions added by the LT Screener are marked `[LT]` and show their fundamental score, grade, and an **Exit Watch** line with the specific technical/fundamental reasons behind the current verdict (e.g. "SMA_50 < SMA_200 for 12 day(s) — watching for confirmation", or "ROE 6.2% below exit threshold 10%").

### Reports
Lists all saved `.txt` report files in `reports/`. Click any file to view it.

### Bench List
Builds a ranked replacement candidate list — stocks that are almost ready to enter and could replace an exiting position. Configure market and top-N count.

### Settings
Adjust account size, position limits, risk parameters, momentum periods, and universe size without editing code. Changes persist to `app_settings.json`.

---

## Browser App Tabs

Launch with `streamlit run app_web.py` → open **http://localhost:8501**. Fifteen tabs — full feature parity with the desktop app.

Sidebar controls (equity, commission, slippage, strategy flags) apply to every tab.

### Daily Scan
Same pipeline as the desktop app. Real-time step-by-step progress. Summary metrics bar shows ENTER / NEAR counts and tuner mode. Portfolio is auto-saved after the run.

### Universe
Scores every ticker in the configured universe by momentum velocity, SMA_50 trend distance, and composite grade.

### Post-Trade
Enriches today's journal rows (WAIT / ENTER / NEAR) with position sizing details, market-behaviour context, and Tier 3 reflection notes.

### ST Backtest
ATR-Dynamic short-term backtest. Renders an interactive equity curve chart. Includes a **Download Trades CSV** button.

### LT Backtest
Long-term quarterly rebalancing backtest. Interactive equity curve chart. Configurable rebalance interval (monthly / quarterly / semi-annual / annual), momentum floor, and SMA breakdown exit toggle.

### LT Screener
Fundamental screener. Full tiered output (BUY / NEAR / WATCH) with Exit Watch blocks per stock. Configure **Equity** and **Slots** alongside Markets/Min-Q/Universe size: new Tier-1 ENTER signals are automatically sized equal-weight and added to the portfolio with real share counts (capped to available empty slots), and each stores the fundamental Exit Watch thresholds captured at that moment for later live evaluation. Tick **Graham filter** to keep only stocks that pass Graham's defensive tests.

### Walk-Forward
Rolling optimisation. Configure train/test window size and anchored vs rolling mode.

### Stress Tests
Run historical and/or synthetic scenarios.

### Monte Carlo
Bootstraps a trades CSV from a previous backtest. Shows percentile equity path chart (p5 / p25 / median / p75 / p95) and a metrics row.

### SIP Plan
Monthly Systematic Investment Plan — two sub-tabs, **📅 Monthly Cycle** and **📊 SIP Backtest**. Configure per-region monthly budget, min Q-score, and universe size; run a dry-run preview or a live cycle. Displays candidates, allocation, exit signals, and a **Regime Reserve** status box showing each region's uptrend/downtrend state and current held-back cash. The backtest sub-tab simulates the same regime-reserve logic over history with a configurable reserve percentage. See [SIP: Regime-Reserve Dip-Buying Strategy](#sip-regime-reserve-dip-buying-strategy) below.

### Compounding Sim
A standalone, pure compound-interest simulator — independent of the stock-picking engines in every other tab. Models a two-phase wealth strategy: **Phase 1 "Matching Velocity"** (contribute a fixed monthly amount matching the lump sum's initial organic growth) followed by **Phase 2 "Pure Compounding"** (contributions drop to zero). Configurable region (IN/US/EU), initial capital, target ROI, and phase durations. Outputs an Executive Summary table, a milestone tracker, a [Rule of 72](https://en.wikipedia.org/wiki/Rule_of_72) validation, a phase-colour-coded trajectory chart with milestone lines, and a full monthly/yearly ledger (CSV download). **Stress Test mode** runs a Monte Carlo simulation (random annual returns drawn from a Normal distribution around the target ROI) to show the success rate of reaching the goal and the effect of [volatility drag](https://en.wikipedia.org/wiki/Volatility_tax) on the median outcome. Backed by `src/compounding_simulator.py`.

### Portfolio
Live portfolio monitor with **4 regional sub-tabs** (Overview / US / EU / IN) — each regional tab shows single-currency totals so P&L figures are always meaningful. Alerts for stop hits, near-stop, and Exit Watch signals appear above the tabs. Progress-bar Stop Dist % column and per-position expanders. Long-term Screener entries are marked with an `[LT]` badge showing their fundamental grade and a live **Exit Watch** verdict (HOLD / WATCH / SELL) with the specific reasons, evaluated against the thresholds captured when the position was added. Prices cached per session; click **Refresh Prices** to update (also re-checks Exit Watch for long-term positions).

### Reports
Browse and download all `.txt` reports saved by the scan and backtest runs.

### Bench List
Builds a ranked replacement candidate list — stocks that are almost ready to enter and could replace an exiting position. Configure market, Top N, and quality sort.

### Settings
Adjust account size, position limits, risk parameters, momentum periods, and universe size without editing code. Persisted to `app_settings.json` — shared with the desktop app via `src/app_settings.py`, so changes made in either UI apply to both.

---

## CLI Tools

All CLI runners live in `src/` and support `--help` for full argument lists.

| Script | Purpose | Key Arguments |
|--------|---------|---------------|
| `run_daily.py` | Daily signal scan | `--markets`, `--dynamic`, `--quality-filter`, `--top-n`, `--skip-journal`, `--asof` |
| `run_backtest.py` | Short-term backtest | `--market`, `--start`, `--end`, `--equity`, `--no-dynamic` |
| `run_backtest_longterm.py` | Long-term backtest | `--market`, `--start`, `--end`, `--slots`, `--rebalance`, `--no-breakdown`, `--momentum-floor` |
| `run_longterm.py` | Long-term screener | `--markets`, `--no-near`, `--min-q`, `--top-n-in`, `--equity`, `--slots` |
| `run_sip.py` | SIP monthly cycle | `--markets`, `--min-q`, `--top-n`, `--dry-run`, `--refresh-cache` |
| `run_backtest_sip.py` | SIP backtest | `--markets`, `--start`, `--end`, `--max-picks`, `--top-n`, `--regime-reserve` |
| `run_walkforward.py` | Walk-forward optimisation | `--market`, `--years`, `--train`, `--test`, `--anchored` |
| `run_montecarlo.py` | Monte Carlo simulation | `--trades`, `--n-sims`, `--skip-prob` |
| `run_stresstests.py` | Stress tests | `--market`, `--historical-only`, `--synthetic-only` |
| `run_replacement_list.py` | Replacement candidates | `--market`, `--mode` |
| `post_trade.py` | Enrich journal rows post-session | *(no args — reads journal path from config)* |

---

## Strategy Details

### Short-Term ATR-Dynamic Strategy

**Entry gates (all must pass):**
1. SMA_50 > SMA_200 and SMA_50 rising (trend structure)
2. Close > SMA_200 by minimum distance (SMA distance gate)
3. Volume above rolling average × multiplier
4. RSI within configured band (42–80 for IN, 47–78 for US/EU)
5. MACD histogram positive (or above threshold)

**Sizing:** R-based — risk a fixed percentage of equity per trade. Position size = (equity × risk_pct) / (ATR × stop_multiplier). Maximum 24% per position (32% cap for momentum leaders).

**Trailing stop:** Configurable per market and regime via `trail_mult` in `MARKET_PARAMS`. Stop = (rolling peak) − (trail_mult × ATR). Default fallback is 5.5× ATR. Production values: IN HIGH 3.5×, US HIGH 3.5×, EU HIGH 3.5×.

**Exits:**
- Trailing stop hit
- Momentum exit timer: exit if momentum score turns negative after a 7-day grace period
- Rebalance rotation (backtest only)

**Circuit breaker:** Disabled (`DRAWDOWN_BANDS: []`) — full-size entries throughout drawdown recovery. Tested across 22 runs; enabling it consistently hurt returns.

**Adaptive tuner:** Monitors signal density. If density is too low, loosens gate parameters (SOFT → ULTRA_SOFT). If too high, tightens (BASE → STRICT). Transitions over 3 days with EMA smoothing.

---

### Backtest Results — 10-Year (Jan 2016 – Jun 2026)

All runs use 100k initial equity, 0.10% slippage, 0.10% commission, dynamic universe (250 IN / 200 US tickers), 8-slot portfolio at 24% base position size.

#### IN Market (benchmark: Nifty 50 +11.12% CAGR)

| Run | trail_mult HIGH | stop_mult HIGH | Universe | CAGR | Max DD | Sharpe | Trades | Win% | Alpha vs Nifty |
|-----|----------------|----------------|----------|------|--------|--------|--------|------|----------------|
| Run 17 baseline (hardcoded 5.5×) | 5.5× (hardcoded) | 3.5× | Static 20 | 14.05% | -25.97% | 0.862 | — | — | +2.93% |
| v3 — static universe | 5.5× (hardcoded¹) | 3.5× | Static 20 | 3.79% | -25.87% | 0.334 | 331 | 34.4% | — |
| v4 — dynamic universe | 5.5× (hardcoded¹) | 3.5× | Dynamic 250 | 11.11% | -25.83% | 0.723 | 453 | 33.1% | -0.01% |
| **v5 — optimised ✅** | **3.5×** | **4.0×** | Dynamic 250 | **22.78%** | -30.61% | **1.284** | 383 | 36.6% | **+11.66%** |
| v6 — tighter trail | 3.0× | 4.5× | Dynamic 250 | 15.16% | -39.77% | 0.896 | 444 | 41.7% | +4.04% |

¹ trail_mult parameter stored in positions but ignored — `update_trailing_stop()` had hardcoded 5.5× until the 2026-06-07 fix.

**Best IN config (v5):** trail_mult `LOW=5.5 / NORMAL=4.5 / HIGH=3.5`, stop_mult `LOW=2.5 / NORMAL=3.0 / HIGH=4.0`

**Why "wide initial + tight trailing" works for IN:** IN HIGH regime (ATR 2–4%). With stop_mult=4.0× at entry, the trade has 8–16% breathing room before hitting the initial stop — fewer false exits. Once profitable, trail_mult=3.5× locks in gains 7–14% from the peak (vs 11–22% at old 5.5×). For a typical 25% peak move: old code exits at +8.5%; new code exits at +14.5% — 70% more profit captured per winner.

---

#### US Market (benchmark: S&P 500 +13.29% CAGR)

| Run | trail_mult HIGH | stop_mult HIGH | Pos size | Universe | CAGR | Max DD | Sharpe | Trades | Win% | Alpha vs S&P |
|-----|----------------|----------------|----------|----------|------|--------|--------|--------|------|--------------|
| v4 — dynamic universe | 5.5× (hardcoded¹) | 3.0× | 24% | Dynamic 200 | 9.95% | -28.42% | 0.688 | 455 | 35.2% | -3.34% |
| **v5 — optimised ✅** | **3.5×** | **3.5×** | 24% | Dynamic 200 | **11.62%** | -31.50% | **0.808** | 265 | 40.8% | -1.67% |
| v6 — tighter trail, larger pos | 3.0× | 4.0× | 30% | Dynamic 200 | 8.07% | -36.19% | 0.590 | 263 | 46.0% | -5.22% |

¹ Same hardcoded trail_mult bug as IN — fixed 2026-06-07.

**Best US config (v5):** trail_mult `LOW=7.0 / NORMAL=5.0 / HIGH=3.5`, stop_mult `LOW=2.5 / NORMAL=2.5 / HIGH=3.5`

**Note on v6:** Increasing position size to 30% hurt CAGR despite a higher win rate — fewer concurrent positions reduce diversification and compound growth.

---

#### Key Findings

- **Dynamic universe is essential** — 250 IN tickers vs 20 static: +11.11% vs +3.79% CAGR (same trail_mult).
- **trail_mult HIGH=3.5× is the sweet spot for both IN and US** — tighter locks in more profit; looser gives back too much at the peak. Values below 3.0× exit too early on normal intraday pullbacks.
- **Wider initial stop (stop_mult 4.0× for IN) outperforms tighter initial (3.5×)** — gives positions room to develop, reducing noise-driven exits at entry.
- **Larger position sizes (30%) consistently hurt** — fewer active slots reduce diversification; compounding benefits from more concurrent smaller positions outweigh the per-trade sizing advantage.
- **risk_pct in MARKET_PARAMS does not affect backtest sizing** — position size is capped by `RISK["MAX_POSITION_SIZE_PCT"]` = 0.24 in `backtest.py`. Only `trail_mult`, `stop_mult`, and `RISK` config are effective levers.

---

### Long-Term Fundamental + Momentum Strategy

**Fundamental scoring (Q-score, 0–100):**

| Metric | Weight | Notes |
|--------|--------|-------|
| ROE | 20% | Core profitability |
| Revenue growth (3yr CAGR) | 15% | Top-line momentum |
| EPS growth | 12% | Earnings quality |
| D/E ratio | 15% | Balance-sheet safety |
| Operating margin | 10% | Business moat |
| FCF yield | 8% | Real cash generation |
| PEG ratio | 10% | Valuation vs growth |
| P/B ratio | 5% | Asset backing |
| Net margin | 5% | Net profitability |

D/E is stored as a ratio (0.45 = 0.45×). yfinance reports `debtToEquity` as a percentage, so `fetch_fundamentals()` divides it by 100.

**Graham defensive tests** (`graham_check()` in `src/fundamental.py`, from Benjamin Graham's *The Intelligent Investor*):

| Test | Rule |
|------|------|
| Financial strength | Current ratio ≥ 2 |
| Debt cover | Long-term debt ≤ net current assets (current assets − current liabilities) |
| Moderate price | P/E × P/B ≤ 22.5 |
| Graham number | √(22.5 × EPS × book value per share), shown with the margin of safety `(Graham number − price) ÷ Graham number` |

Financial-sector stocks skip the two balance-sheet tests, since banks and insurers have no meaningful current ratio. Missing data counts as a fail. The tests are always shown in the report. With the Graham filter on (`--graham` or the checkbox in either app), failing stocks are excluded before tiering, so they can't become Tier-1 entries. Graham's 10–20-year earnings and dividend record rules are not included because yfinance only provides about 4 years of statements. The long-term backtest doesn't apply the filter, because historical fundamentals aren't available.

**Tiered output:**
- **BUY** (Q ≥ 70, all gates pass)
- **NEAR** (Q ≥ 50, most gates pass)
- **WATCH** (Q ≥ 35)

**Exit Watch signals:**

*Technical:* Sell if SMA_50 crosses below SMA_200, confirmed over 2–3 weeks.

*Fundamental thresholds (dynamic — computed from each stock's current values):*
- ROE drops below max(10%, current_roe × 50%)
- Revenue growth negative for 2 consecutive years
- D/E ratio exceeds max(2.0×, current_de × 2)
- FCF yield turns negative
- P/E exceeds current_pe × 2 without growth acceleration

*Momentum floor (backtest proxy):* Exit at rebalance if avg momentum score < –5% (default). Set to –99 to disable.

**Position sizing:** new Tier-1 ENTER signals are sized equal-weight — `shares = floor((equity ÷ slots) / (price × (1 + slippage) × (1 + commission)))` — the same formula the Long-Term Backtest engine uses, capped to however many of the configured `slots` are actually empty. The Exit Watch thresholds above are computed and stored on the position *at entry* (not recomputed against a moving target), so a later Portfolio refresh evaluates today's fundamentals against the values captured when the position was added. The technical breakdown check requires SMA_50 < SMA_200 for 15 consecutive trading days (≈3 weeks) before returning a SELL verdict — until then it shows WATCH. See `check_lt_exit()` / `compute_exit_thresholds()` in `src/run_longterm.py`.

**Long-term backtest results (IN market, 2015–2026):** ~28% CAGR, significant alpha over Nifty.

---

### SIP: Regime-Reserve Dip-Buying Strategy

A [Systematic Investment Plan](#glossary--further-reading) (SIP) deploys a fixed budget every month regardless of price — the classic argument for this is [dollar-cost averaging](#glossary--further-reading): buying a fixed rupee/dollar/euro amount at regular intervals naturally buys more shares when prices are low and fewer when prices are high, smoothing out entry price over time versus trying to time the market.

This app implements SIP with an added twist, internally called **"C2"** — a **regime reserve**:

1. **Every month**, only 90% of each region's budget (`region_budget × (1 − regime_reserve_pct)`) is deployed into that month's top-ranked candidates. The remaining 10% (`regime_reserve_pct`) is parked as cash in a per-region `dip_reserve` bucket instead of being invested immediately.
2. **Before each month's deployment**, the region's benchmark index (S&P 500 for US, STOXX 50 for EU, Nifty 50 for IN) is checked against its own [200-day simple moving average](#glossary--further-reading) (SMA_200) — a widely used proxy for whether a market is in a broad uptrend or downtrend.
3. **If the index closes below its SMA_200** (a downtrend / correction signal), the *entire accumulated reserve* for that region — potentially several months' worth — is released and deployed alongside that month's normal budget. This concentrates extra buying power at a moment of broad market weakness rather than spreading it evenly, on the theory that buying more when the market is cheap (relative to its own trend) improves long-run returns versus pure calendar-based SIP.
4. **If the index is above its SMA_200** (uptrend intact), the reserve keeps accumulating and only the reduced 90% budget is deployed as normal.

**Candidate selection** (same gates for both the live cycle and backtest):
- Technical gate: SMA_50 > SMA_200 (uptrend only)
- Fundamental gate: Q-score ≥ 55 (see [Q-score](#glossary--further-reading) table above)
- Ranked by composite score: 40% Q-score + 60% [momentum](#glossary--further-reading) (1M / 3M / 6M / 12M periods)

**Exit rules** (checked before each monthly deployment):
- SMA_50 < SMA_200 for ≥ 10 consecutive trading days → structural breakdown exit
- Q-score drops below 35 → quality deterioration exit
- Position exceeds 15% of portfolio value → trimmed to 10%

**Allocation:** equal weight across top picks per region, minimum position size per region (`region_min_alloc`: $200 US / €200 EU / ₹2,000 IN), sector cap at 25% of total portfolio.

**Why this differs from plain SIP:** a pure calendar SIP invests 100% every month unconditionally. This variant sacrifices a small, steady amount of immediate market exposure (the held-back 10%) in exchange for a larger, concentrated purchase precisely when the region's own trend-following signal says prices are depressed relative to trend — a systematic (not discretionary) attempt at "buying the dip" without predicting the bottom. The `src/compare_sip_variants.py` script backtests this against three alternatives (no reserve, a smaller per-position dip-reserve, and a wider-diversification variant) so you can evaluate the trade-off yourself rather than take the design on faith.

---

## Configuration

All parameters are in `src/config.py`. Key sections:

```python
ACCOUNT = {
    "equity":            100_000.0,
    "commission":        0.001,     # 0.10% one-way
    "slippage":          0.001,
    "max_position_size": 0.24,      # 24% per position
}

RISK = {
    "MAX_OPEN_POSITIONS":          8,
    "MAX_POSITION_SIZE_PCT":       0.24,
    "MAX_TOTAL_CONCENTRATION_PCT": 0.32,  # 32% cap for momentum leaders
    "DRAWDOWN_BANDS":              [],    # circuit breaker disabled
}

DYNAMIC_UNIVERSE = {
    "ENABLED":    True,
    "MAX_AGE_DAYS": 7,
    "SCORE_TOP_N": {"US": 200, "EU": 200, "IN": 250},
}

MOMENTUM_EXIT = {
    "ENABLED":         True,
    "SCORE_THRESHOLD": 0.0,  # exit when momentum turns negative
    "GRACE_DAYS":      7,
}

RANKING = {
    "MOMENTUM_PERIODS": [14, 30, 63],  # 3W / 6W / 3M
}

JOURNAL = {
    "PRIMARY_PATH": r"C:\Users\monik\OneDrive\Raj\Investments\Mastermind-Trading-Journal.xlsx",
    "SHEET_SIGNALS": "4. Trade Log",
    "DATA_START_ROW": 6,
}
```

The desktop app Settings tab and the browser app sidebar expose the most commonly changed parameters and persist them to `app_settings.json`.

SIP-specific parameters live in `src/sip_strategy.py` (`SIP_CONFIG`), separate from the ATR-Dynamic / Long-Term config above:

```python
SIP_CONFIG = {
    "markets":            ["US", "EU", "IN"],
    "region_budget":      {"US": 2000.0, "EU": 2000.0, "IN": 20000.0},  # local currency, per month
    "region_min_alloc":   {"US": 200.0, "EU": 200.0, "IN": 2000.0},     # minimum position size
    "regime_reserve_pct": 0.10,   # 10% held back monthly, released on index < SMA_200
    "regime_bench":       {"US": "^GSPC", "EU": "^STOXX50E", "IN": "^NSEI"},
    "min_q_entry":        55.0,   # Q-score gate for new positions
    "min_q_exit":         35.0,   # Q-score exit trigger
    "sector_cap":         0.25,   # max 25% of portfolio in one sector
    "max_position_pct":   0.15,   # trim trigger
    "trim_to_pct":        0.10,   # trim target
    "sma_breakdown_days":  10,    # consecutive days below SMA_200 before exit
    "q_weight":           0.40,   # composite ranking: 40% Q-score
    "mom_weight":         0.60,   # composite ranking: 60% momentum
}
```

---

## File Structure

```
data/                   Parquet-cached price data (populated on first run)
universes/              Index constituent CSVs used by dynamic universe builder
reports/                Auto-saved scan and backtest output files
portfolio/
  positions.json        Active paper portfolio (auto-updated after every scan)
.streamlit/
  config.toml           Streamlit server config (port 8501, no usage stats prompt)
tuner_state.json        Adaptive tuner state (persists between runs)
state/
  last_decisions.json   Last decision output per ticker
fundamental_cache.json  7-day fundamental data cache
app_settings.json       Desktop GUI settings
```

Price data is cached as Parquet files in `data/` after the first download. Subsequent runs fetch only the delta. Cache files are named `TICKER.parquet`.

---

## Markets Supported

| Market | Label | Currency | Benchmark | Broker (configured) |
|--------|-------|----------|-----------|---------------------|
| India | `IN` | INR (Rs) | Nifty 50 (^NSEI) | HDFC Securities / Zerodha |
| United States | `US` | USD ($) | S&P 500 (^GSPC) | Scalable Capital Prime+ |
| Europe | `EU` | EUR | STOXX 50 (^STOXX50E) | Scalable Capital Prime+ |

Universe CSVs cover Nifty 500 (falling back to Nifty 250, then Nifty 100), S&P 500, DAX, FTSE 100, and FTSE MIB. Ticker symbols follow Yahoo Finance conventions (`.NS` for NSE, `.DE` / `.PA` / `.L` etc. for European exchanges).

---

## Glossary & Further Reading

Plain-English explanations of the technical, fundamental, and strategy terms used throughout the app and this README, with links to learn more. `Q-score`, `regime reserve`, and `R-multiple` are noted separately as app-specific or non-standard terms.

### Technical indicators (used in gates, entries, exits)

| Term | What it means here | Learn more |
|------|--------------------|------------|
| **SMA** (Simple Moving Average) | Average closing price over the last N days (e.g. SMA_50, SMA_200). The app uses SMA_50 vs SMA_200 crossovers to detect uptrends/downtrends. | [Moving average — Wikipedia](https://en.wikipedia.org/wiki/Moving_average) |
| **RSI** (Relative Strength Index) | Momentum oscillator (0–100) measuring how fast/far price has moved recently. Used as one of the 5 entry gates. | [Relative strength index — Wikipedia](https://en.wikipedia.org/wiki/Relative_strength_index) |
| **MACD** (Moving Average Convergence/Divergence) | Trend/momentum indicator from the difference between two EMAs; the "histogram" gate checks it's positive before entry. | [MACD — Wikipedia](https://en.wikipedia.org/wiki/MACD) |
| **ATR** (Average True Range) | Average daily price range over N days — a volatility measure. Used to size positions and set trailing stops (e.g. "3.5× ATR"). | [Average true range — Wikipedia](https://en.wikipedia.org/wiki/Average_true_range) |
| **Bollinger Bands** | Volatility bands plotted N standard deviations above/below a moving average; referenced by some indicator calculations in `indicators.py`. | [Bollinger Bands — Wikipedia](https://en.wikipedia.org/wiki/Bollinger_Bands) |
| **Trailing stop** | A stop-loss that moves up with the price peak but never moves down — locks in gains while giving a position room to run. | [Order types (trailing stop) — Wikipedia](https://en.wikipedia.org/wiki/Order_(exchange)) |
| **Circuit breaker / drawdown band** | A rule that reduces or halts new entries after the portfolio (or market) falls a certain amount — this app's is currently disabled (`DRAWDOWN_BANDS: []`). Named after the exchange-level version. | [Trading curb — Wikipedia](https://en.wikipedia.org/wiki/Trading_curb) |

### Performance & risk metrics (backtest, Monte Carlo, walk-forward)

| Term | What it means here | Learn more |
|------|--------------------|------------|
| **CAGR** (Compound Annual Growth Rate) | The single steady annual growth rate that would take starting equity to ending equity over the period — the headline return figure in every backtest report. | [Compound annual growth rate — Wikipedia](https://en.wikipedia.org/wiki/Compound_annual_growth_rate) |
| **Max Drawdown** | The largest peak-to-trough decline in equity during the backtest — the worst-case loss an investor would have experienced. | [Drawdown — Wikipedia](https://en.wikipedia.org/wiki/Drawdown_(economics)) |
| **Sharpe ratio** | Return earned per unit of total risk (volatility). Higher is better; a common way to compare strategies with different volatility. | [Sharpe ratio — Wikipedia](https://en.wikipedia.org/wiki/Sharpe_ratio) |
| **Sortino ratio** | Like Sharpe, but only penalises *downside* volatility, not upside swings — arguably more relevant for judging strategies that aim to avoid losses. | [Sortino ratio — Wikipedia](https://en.wikipedia.org/wiki/Sortino_ratio) |
| **Alpha** | Excess return of the strategy vs. its benchmark (Nifty 50 / S&P 500 / STOXX 50) over the same period — the value the strategy added (or subtracted) beyond just tracking the market. | [Alpha (finance) — Wikipedia](https://en.wikipedia.org/wiki/Alpha_(finance)) |
| **XIRR** | Extended Internal Rate of Return — the annualised return of a series of cash flows that happen on *irregular* dates (exactly the SIP's monthly-but-not-fixed-interval deployments). Excel/Google Sheets' `XIRR()` function implements the same underlying IRR math. | [Internal rate of return — Wikipedia](https://en.wikipedia.org/wiki/Internal_rate_of_return) |
| **R-multiple** | A trade's profit/loss expressed as a multiple of the amount originally risked (1R = the entry-to-stop-loss distance). Shown in the Portfolio tab as `R×`. Not a Wikipedia-indexed term — popularised by trader Van K. Tharp. | [R-Multiple explainer — TraderSync](https://tradersync.com/r-multiple/) |
| **Monte Carlo simulation** | Re-running a backtest's trade sequence thousands of times with randomised ordering/skipping to see the *range* of possible outcomes, not just the one historical path. | [Monte Carlo method — Wikipedia](https://en.wikipedia.org/wiki/Monte_Carlo_method) |
| **Bootstrap resampling** | The specific randomisation technique Monte Carlo uses here — drawing trades with replacement from the historical trade log to build alternate equity paths. | [Bootstrapping (statistics) — Wikipedia](https://en.wikipedia.org/wiki/Bootstrapping_(statistics)) |
| **Walk-forward optimisation** | Splitting history into a training window (to pick parameters) and a following test window (to check they still work out-of-sample) — repeated across rolling/anchored periods to catch over-fitting. | [Walk forward optimization — Wikipedia](https://en.wikipedia.org/wiki/Walk_forward_optimization) |
| **Backtesting** | Simulating a strategy's rules against historical price data to estimate how it would have performed — the basis of every "Backtest" tab in this app. | [Backtesting — Wikipedia](https://en.wikipedia.org/wiki/Backtesting) |

### Fundamental ratios (Long-Term Screener & SIP Q-score)

| Term | What it means here | Learn more |
|------|--------------------|------------|
| **ROE** (Return on Equity) | Net income ÷ shareholder equity — how efficiently a company turns invested capital into profit. Largest single weight (20%) in the Q-score. | [Return on equity — Wikipedia](https://en.wikipedia.org/wiki/Return_on_equity) |
| **D/E ratio** (Debt-to-Equity) | Total debt ÷ shareholder equity — balance-sheet leverage/safety. Lower is generally safer. | [Debt-to-equity ratio — Wikipedia](https://en.wikipedia.org/wiki/Debt-to-equity_ratio) |
| **Operating margin** | Operating income ÷ revenue — how much profit survives after core operating costs, before interest/tax. Proxy for competitive "moat". | [Operating margin — Wikipedia](https://en.wikipedia.org/wiki/Operating_margin) |
| **FCF yield** (Free Cash Flow Yield) | Free cash flow ÷ market value — real cash generation relative to price, harder to manipulate than reported earnings. | [Free cash flow — Wikipedia](https://en.wikipedia.org/wiki/Free_cash_flow) |
| **PEG ratio** | P/E ratio ÷ earnings growth rate — a valuation check that accounts for how fast a company is growing, not just its raw P/E. | [PEG ratio — Wikipedia](https://en.wikipedia.org/wiki/PEG_ratio) |
| **P/B ratio** (Price-to-Book) | Market price ÷ book value per share — how much investors pay relative to net assets. | [P/B ratio — Wikipedia](https://en.wikipedia.org/wiki/P/B_ratio) |
| **Net margin** | Net profit ÷ revenue — bottom-line profitability after all expenses, interest, and tax. | [Profit margin — Wikipedia](https://en.wikipedia.org/wiki/Profit_margin) |
| **Q-score** | This app's own 0–100 composite of the nine metrics above (see [Strategy Details](#strategy-details)) — not a term you'll find externally; it's specific to `fundamental.py`. | *(app-specific — no external reference)* |

### Strategy concepts

| Term | What it means here | Learn more |
|------|--------------------|------------|
| **Momentum (investing)** | The tendency for assets that have performed well recently to keep performing well short-term. Drives the ranking/rotation logic in both the Long-Term and SIP strategies. | [Momentum investing — Wikipedia](https://en.wikipedia.org/wiki/Momentum_investing) |
| **SIP** (Systematic Investment Plan) | Investing a fixed amount at regular intervals (here: monthly, per region) regardless of price, rather than timing the market with a lump sum. | [Systematic investment plan — Wikipedia](https://en.wikipedia.org/wiki/Systematic_investment_plan) |
| **Dollar-cost averaging** | The underlying rationale for SIP investing — fixed periodic purchases buy more shares when prices are low and fewer when high, smoothing average entry cost over time. | [Dollar cost averaging — Wikipedia](https://en.wikipedia.org/wiki/Dollar_cost_averaging) |
| **Regime reserve ("C2")** | This app's variant on plain SIP: hold back part of the monthly budget and release it in bulk when a region's index falls into a downtrend (below its SMA_200). See [SIP: Regime-Reserve Dip-Buying Strategy](#sip-regime-reserve-dip-buying-strategy) for the full mechanics. Not an industry-standard term — coined internally for this project. | *(app-specific — no external reference)* |

---

## Running Tests

```bash
pytest tests/
```

Tests cover gate evaluation logic (`test_gates.py`) and core backtest accounting (`test_backtest.py`).
