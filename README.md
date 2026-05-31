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

---

## Overview

Mastermind Pro combines two complementary investment frameworks:

| Mode | Timeframe | Approach |
|------|-----------|----------|
| **Short-Term (ATR-Dynamic)** | Days to weeks | 5-gate technical filter → ATR-sized positions → adaptive trailing stop |
| **Long-Term (Fundamental + Momentum)** | Months to years | Fundamental Q-score pre-screen → momentum rotation → exit-watch signals |

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

### Portfolio Monitor *(browser app)*
- Loads open positions from `portfolio/positions.json`
- Fetches live prices via Yahoo Finance (cached per session, refresh on demand)
- Per-position: live P&L, P&L %, R-multiple, stop cushion %, days held
- Stop Dist % rendered as a visual progress bar — shorter = closer to stop
- Alert banners: 🔴 STOP HIT / 🟡 Near stop (<5%) / 🟢 Safe
- Detail expanders: ATR at entry, trail multiplier, peak price, regime

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

### Long-Term Backtest
- Quarterly momentum rebalancing: rotate out of laggards, fill slots with top scorers
- Three independent sell triggers:
  1. **Daily SMA breakdown** — exit immediately when SMA_50 crosses below SMA_200
  2. **Momentum floor** (exit-watch proxy) — exit at rebalance if avg momentum score < threshold
  3. **Rotation** — dropped out of top-N ranking
- Year-by-year returns vs benchmark, alpha calculation

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
- Builds universe from live index constituents (Nifty 250, S&P 500, DAX, FTSE 100, FTSE MIB)
- Quality-scores all constituents and keeps top-N per market
- Cached with 7-day TTL to avoid redundant downloads

### Reports & Journal
- All scan and backtest outputs saved to `reports/` as plain-text files
- Excel trading journal integration via openpyxl (writes signals to configured sheet)

---

## Architecture

```
InvestmentApp/
├── app.py                    # Tkinter desktop GUI — 7 tabs
├── app_web.py                # Streamlit browser app — 9 tabs
├── .streamlit/
│   └── config.toml           # Streamlit config (skips email prompt, sets port 8501)
├── src/
│   ├── config.py             # All strategy parameters (single source of truth)
│   ├── data.py               # yfinance fetch + parquet cache
│   ├── indicators.py         # SMA, ATR, RSI, MACD, Bollinger, volume indicators
│   ├── rules.py              # 5-gate entry evaluator
│   ├── ranking.py            # Momentum scoring [14, 30, 63] periods
│   ├── adaptive_tuner.py     # Gate auto-tightening / loosening
│   ├── decision_engine.py    # 7-phase buy/sell pipeline (live + backtest)
│   ├── backtest.py           # Short-term historical simulation
│   ├── report.py             # Daily scan report formatter
│   ├── fundamental.py        # Fundamental data fetch, cache, and scoring
│   ├── run_longterm.py       # Long-term screener pipeline + CLI
│   ├── backtest_longterm.py  # Long-term backtest engine
│   ├── universe.py           # Dynamic universe builder
│   ├── stock_selector.py     # Quality composite score
│   ├── select_stocks.py      # Stock selection helpers
│   ├── replacement_list.py   # Bench candidate list builder
│   ├── replacement_engine.py # In-portfolio replacement logic
│   ├── stress_tests.py       # Historical + synthetic stress tests
│   ├── walk_forward.py       # Walk-forward optimisation
│   ├── monte_carlo.py        # Monte Carlo simulation
│   ├── post_trade.py         # Post-trade journal enrichment
│   ├── journal.py            # Excel journal writer
│   ├── run_daily.py          # CLI: daily scan
│   ├── run_backtest.py       # CLI: short-term backtest
│   ├── run_backtest_longterm.py  # CLI: long-term backtest
│   ├── run_montecarlo.py     # CLI: Monte Carlo
│   ├── run_walkforward.py    # CLI: walk-forward
│   ├── run_stresstests.py    # CLI: stress tests
│   └── run_replacement_list.py  # CLI: replacement candidates
├── tests/
│   ├── test_gates.py         # Gate evaluation unit tests
│   └── test_backtest.py      # Backtest unit tests
├── data/                     # Parquet price cache (auto-populated)
├── universes/                # Index constituent CSV files
│   ├── IN_nifty100.csv
│   ├── IN_nifty250.csv
│   ├── US_sp500.csv
│   ├── EU_dax.csv
│   ├── EU_ftse100.csv
│   └── EU_ftsemib.csv
├── reports/                  # Saved scan and backtest reports
├── portfolio/
│   └── positions.json        # Current open positions
└── requirements.txt
```

---

## Installation

**Requirements:** Python 3.10+ (tested on 3.14), Windows / macOS / Linux

```bash
# Clone or download the project, then:
cd InvestmentApp
pip install -r requirements.txt
```

`requirements.txt`:
```
yfinance>=0.2.40
pandas>=2.0
numpy>=1.26
openpyxl>=3.1
pyarrow>=14.0
requests>=2.31
streamlit>=1.35
```

Tkinter is included with the standard Python distribution. On Linux it may need:
```bash
sudo apt-get install python3-tk
```

---

## Quick Start

### Desktop app (Tkinter)
```bash
python app.py
```

### Browser app (Streamlit)
```bash
streamlit run app_web.py
# Opens automatically at http://localhost:8501
```

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
```

### CLI — long-term backtest
```bash
python src/run_backtest_longterm.py
python src/run_backtest_longterm.py --market IN --start 2015-01-01
python src/run_backtest_longterm.py --market IN --slots 10 --rebalance 63 --momentum-floor -5
python src/run_backtest_longterm.py --no-breakdown --momentum-floor -99
```

---

## Desktop App Tabs

Launch with `python app.py`.

### Daily Scan
Select markets (US / EU / IN / All), enable dynamic universe or quality filter, and run the live signal scan. Output shows ENTER / NEAR / WAIT / SKIP decisions with ATR, stop levels, and position sizing. Results are saved to `reports/daily-DATE.txt`.

### Backtest
Configure market, date range, equity, and commission. Runs the full ATR-Dynamic short-term strategy simulation. Output includes equity curve (ASCII chart), year-by-year returns vs benchmark, trade statistics, max drawdown, and Sharpe/Sortino ratios.

### Walk-Forward
Set training and test window lengths. Optimises gate parameters (SMA distance, volume multiplier, RSI bounds, MACD threshold) on rolling in-sample windows and reports out-of-sample performance per fold.

### Stress Tests
Run the portfolio through the three historical crisis windows (2008, 2020, 2022) and four synthetic shock scenarios.

### Monte Carlo
Set number of simulations and shock parameters. Bootstraps the backtest trade log N times and shows the percentile distribution of final equity, CAGR, and max drawdown.

### Long-Term Investment
Two sub-tools in one tab:

**Screener** — fundamental + technical quality screener. Produces a tiered report (BUY / NEAR / WATCH) with Q-scores, red-flag alerts, and an Exit Watch block per stock.

**Backtest** — quarterly momentum rebalancing backtest with configurable slots, rebalance interval, breakdown exit toggle, and momentum floor.

### Reports
Lists all saved `.txt` report files in `reports/`. Click any file to view it in the terminal panel.

---

## Browser App Tabs

Launch with `streamlit run app_web.py` → open **http://localhost:8501**.

Sidebar controls (equity, commission, slippage, strategy flags) apply to every tab.

### 📊 Daily Scan
Same pipeline as the desktop app. Real-time step-by-step progress via `st.status()`. Summary metrics bar shows ENTER / NEAR counts and tuner mode after the run.

### 📈 ST Backtest
ATR-Dynamic short-term backtest. Renders an **interactive equity curve chart** after the run. Includes a **Download Trades CSV** button.

### 🏦 LT Backtest
Long-term quarterly rebalancing backtest. Interactive equity curve chart. Configurable rebalance interval (monthly / quarterly / semi-annual / annual), momentum floor, and SMA breakdown exit toggle.

### 🔭 LT Screener
Fundamental screener. Full tiered output (BUY / NEAR / WATCH) with Exit Watch blocks per stock.

### 🔄 Walk-Forward
Rolling optimisation. Configure train/test window size and anchored vs rolling mode.

### 💪 Stress Tests
Run historical and/or synthetic scenarios. Select All / Historical only / Synthetic only.

### 🎲 Monte Carlo
Bootstraps a trades CSV from a previous backtest. Shows **percentile equity path chart** (p5 / p25 / median / p75 / p95) and a metrics row. Pick any saved trades file from the dropdown.

### 💼 Portfolio
Live portfolio monitor — the most useful tab for daily review:

| Column | Description |
|--------|-------------|
| Status | 🔴 STOP HIT / 🟡 Near stop / 🟢 Safe |
| Live Px | Current price from Yahoo Finance |
| Stop | Current trailing stop level |
| Stop Dist % | Distance to stop as a progress bar |
| P&L / P&L % | Unrealised gain/loss vs cost basis |
| R-Mult | R-multiples earned (based on initial stop risk) |
| Days | Calendar days since entry |

Alert banners appear at the top for any stop breach or near-stop position. Prices are fetched in batch and cached in the session — click **🔄 Refresh Prices** to update. Expand any row for ATR, trail multiplier, peak price, and regime detail.

### 📁 Reports
Browse and download all `.txt` reports saved by the scan and backtest runs.

---

## CLI Tools

All CLI runners live in `src/` and support `--help` for full argument lists.

| Script | Purpose | Key Arguments |
|--------|---------|---------------|
| `run_daily.py` | Daily signal scan | `--markets`, `--dynamic`, `--quality-filter`, `--top-n`, `--skip-journal` |
| `run_backtest.py` | Short-term backtest | `--market`, `--start`, `--end`, `--equity`, `--no-dynamic` |
| `run_backtest_longterm.py` | Long-term backtest | `--market`, `--start`, `--end`, `--slots`, `--rebalance`, `--no-breakdown`, `--momentum-floor` |
| `run_longterm.py` | Long-term screener | `--markets`, `--no-near`, `--min-q`, `--top-n-in` |
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

**Trailing stop:** 5.5× ATR from the rolling high.

**Exits:**
- Trailing stop hit
- Momentum exit timer: exit if momentum score turns negative after a 7-day grace period
- Rebalance rotation (backtest only)

**Circuit breaker:** Disabled (`DRAWDOWN_BANDS: []`) — full-size entries throughout drawdown recovery. Tested across 22 runs; enabling it consistently hurt returns.

**Adaptive tuner:** Monitors signal density. If density is too low, loosens gate parameters (SOFT → ULTRA_SOFT). If too high, tightens (BASE → STRICT). Transitions over 3 days with EMA smoothing.

**Backtest results — Run 17 locked config (IN market):**

| Window | CAGR | Total Return | Max DD | Sharpe | Alpha vs Nifty |
|--------|------|-------------|--------|--------|----------------|
| 10-year (Jan 2016 – May 2026) | 14.05% | +292% (₹1L → ₹3.92L) | -25.97% | 0.862 | +2.73% |
| 3-year (Jan 2023 – May 2026) | 20.77% | +89.68% | -16.68% | 1.245 | +12.62% |

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

**Long-term backtest results (IN market, 2015–2026):** ~28% CAGR, significant alpha over Nifty.

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
```

The desktop app Settings tab and the browser app sidebar expose the most commonly changed parameters and persist them to `app_settings.json`.

---

## File Structure

```
data/                   Parquet-cached price data (populated on first run)
universes/              Index constituent CSVs used by dynamic universe builder
reports/                Auto-saved scan and backtest output files
portfolio/
  positions.json        Active paper portfolio positions
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

Universe CSVs cover Nifty 100, Nifty 250, S&P 500, DAX, FTSE 100, and FTSE MIB. Ticker symbols follow Yahoo Finance conventions (`.NS` for NSE, `.DE` / `.PA` / `.L` etc. for European exchanges).

---

## Running Tests

```bash
pytest tests/
```

Tests cover gate evaluation logic (`test_gates.py`) and core backtest accounting (`test_backtest.py`).
