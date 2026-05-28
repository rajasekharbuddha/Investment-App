# Mastermind Pro — Investment Research & Analysis Platform

A desktop application for systematic stock research, signal generation, and strategy backtesting across US, European, and Indian equity markets. Built for paper trading and research — not a live order-routing system.

> **Disclaimer:** This tool is for research and paper trading only. Output is not financial advice. Do not deploy live capital without independent validation and regulatory compliance review.

---

## Table of Contents

1. [Overview](#overview)
2. [Features](#features)
3. [Architecture](#architecture)
4. [Installation](#installation)
5. [Quick Start](#quick-start)
6. [GUI Tabs](#gui-tabs)
7. [CLI Tools](#cli-tools)
8. [Strategy Details](#strategy-details)
9. [Configuration](#configuration)
10. [File Structure](#file-structure)
11. [Markets Supported](#markets-supported)

---

## Overview

Mastermind Pro combines two complementary investment frameworks in a single Tkinter desktop application:

| Mode | Timeframe | Approach |
|------|-----------|----------|
| **Short-Term (ATR-Dynamic)** | Days to weeks | 5-gate technical filter → ATR-sized positions → adaptive trailing stop |
| **Long-Term (Fundamental + Momentum)** | Months to years | Fundamental Q-score pre-screen → momentum rotation → exit-watch signals |

Both modes share the same data layer, indicator engine, and reporting infrastructure. Each has a CLI runner for headless/scheduled use and a GUI tab for interactive exploration.

---

## Features

### Daily Signal Engine
- 5-gate entry filter: SMA trend, volume, RSI, MACD histogram, SMA distance
- Signals: **ENTER** (buy), **NEAR** (approaching entry), **WAIT**, **SKIP**
- ATR-based position sizing with per-regime risk percentages
- Momentum exit timer: ejects positions whose momentum turns negative before the trailing stop fires
- Adaptive tuner: automatically loosens or tightens gate thresholds based on signal density

### Short-Term Backtest
- Tick-by-tick simulation using the same DecisionEngine as the live scan
- 8-slot equal-weight portfolio, 24% base position size (32% cap for momentum leaders)
- 5.5× ATR trailing stop
- Benchmark comparison (Nifty / S&P 500 / STOXX 50)
- Sector and market concentration limits
- Outputs: equity curve, trade log CSV, year-by-year returns, drawdown analysis

### Long-Term Screener
- Fundamental scoring across 9 metrics: ROE, revenue growth, EPS growth, D/E ratio, operating margin, FCF yield, PEG ratio, P/B ratio, net margin
- Technical pre-gate: SMA_50 > SMA_200 + Close > SMA_200 + SMA_50 rising
- Tiered output: **BUY**, **NEAR**, **WATCH** with combined quality score (0–100)
- Per-stock **Exit Watch** block: shows SMA levels, gap %, and dynamic fundamental sell thresholds

### Long-Term Backtest
- Quarterly momentum rebalancing: rotate out of laggards, fill slots with top scorers
- Three independent sell triggers:
  1. **Daily SMA breakdown** — exit immediately when SMA_50 crosses below SMA_200
  2. **Momentum floor** (exit-watch proxy) — exit at rebalance if avg momentum score < threshold
  3. **Rotation** — dropped out of top-N ranking
- Year-by-year returns vs benchmark with proportional bar chart
- Tracks breakdown exits, weakness exits, and rotation sells separately

### Walk-Forward Optimisation
- Splits history into (train, test) folds
- Optimises gate parameters on training window, evaluates on out-of-sample test
- Reports per-fold CAGR, Sharpe, and best parameter set

### Monte Carlo Robustness
- N simulations over backtest trade log
- Random trade ordering (bootstrap), random trade skipping, cost multiplier shock
- Outputs percentile bands (5th / 25th / 50th / 75th / 95th) for final equity

### Stress Tests
- **Historical windows**: 2008 financial crisis, 2020 COVID crash, 2022 rate shock
- **Synthetic shocks**: ATR doubled (volatility spike), volume × 0.30 (liquidity collapse), overnight gap injection, correlation crisis (sector cap = 1)

### Dynamic Universe
- Builds universe from live index constituents (Nifty 250, S&P 500, DAX, FTSE 100, FTSE MIB)
- Quality-scores all constituents and keeps top-N per market
- Cached with 7-day TTL to avoid redundant downloads

### Reports & Journal
- All scan and backtest outputs saved to `reports/` as plain-text files
- Reports tab in GUI lists and displays all saved reports
- Excel trading journal integration via openpyxl (writes signals to configured sheet)

---

## Architecture

```
InvestmentApp/
├── app.py                    # Tkinter GUI — 7 tabs
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
│   ├── run_longterm.py       # Long-term screener pipeline
│   ├── backtest_longterm.py  # Long-term backtest engine
│   ├── universe.py           # Dynamic universe builder
│   ├── stock_selector.py     # Quality composite score
│   ├── select_stocks.py      # Stock selection helpers
│   ├── replacement_list.py   # Bench candidate list builder
│   ├── replacement_engine.py # In-portfolio replacement logic
│   ├── stress_tests.py       # Historical + synthetic stress tests
│   ├── walk_forward.py       # Walk-forward optimisation
│   ├── monte_carlo.py        # Monte Carlo simulation
│   ├── post_trade.py         # Post-trade analytics
│   ├── journal.py            # Excel journal writer
│   ├── run_daily.py          # CLI: daily scan
│   ├── run_backtest.py       # CLI: short-term backtest
│   ├── run_backtest_longterm.py  # CLI: long-term backtest
│   ├── run_longterm.py       # CLI: long-term screener (also the module)
│   ├── run_montecarlo.py     # CLI: Monte Carlo
│   ├── run_walkforward.py    # CLI: walk-forward
│   ├── run_stresstests.py    # CLI: stress tests
│   ├── run_replacement_list.py  # CLI: replacement candidates
│   └── post_trade.py         # CLI: enrich journal rows with sizing + reflection fields
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
├── requirements.txt
└── app_settings.json         # GUI settings persistence
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
```

Tkinter is included with the standard Python distribution. On Linux it may need:
```bash
sudo apt-get install python3-tk
```

---

## Quick Start

### Launch the GUI
```bash
python app.py
```

### Run a daily scan (CLI)
```bash
python src/run_daily.py
python src/run_daily.py --markets IN
python src/run_daily.py --markets US,EU,IN --dynamic
```

### Run the short-term backtest (CLI)
```bash
python src/run_backtest.py
python src/run_backtest.py --market IN --start 2015-01-01
python src/run_backtest.py --market US --slots 8 --no-momentum-exit
```

### Run the long-term screener (CLI)
```bash
python src/run_longterm.py
python src/run_longterm.py --markets IN --near
```

### Run the long-term backtest (CLI)
```bash
python src/run_backtest_longterm.py
python src/run_backtest_longterm.py --market IN --start 2015-01-01
python src/run_backtest_longterm.py --market IN --slots 10 --rebalance 63 --momentum-floor -5
python src/run_backtest_longterm.py --no-breakdown --momentum-floor -99   # disable both exits
```

---

## GUI Tabs

### Daily Scan
Select markets (US / EU / IN / All), enable dynamic universe or quality filter, and run the live signal scan. Output shows ENTER / NEAR / WAIT / SKIP decisions with ATR, stop levels, and position sizing. Results are saved to `reports/daily-DATE.txt`.

### Backtest
Configure market, date range, equity, slots, and commission. Runs the full ATR-Dynamic short-term strategy simulation. Output includes: equity curve (ASCII chart), year-by-year returns vs benchmark, trade statistics, max drawdown, Sharpe/Sortino ratios, and a trades CSV.

### Walk-Forward
Set training and test window lengths. Optimises gate parameters (SMA distance, volume multiplier, RSI bounds, MACD threshold) on rolling in-sample windows and reports out-of-sample performance per fold.

### Stress Tests
Run the portfolio through the three historical crisis windows (2008, 2020, 2022) and four synthetic shock scenarios. Each scenario reports final equity, max drawdown, and trade count vs the baseline backtest.

### Monte Carlo
Set number of simulations and shock parameters (skip probability, cost multiplier). Bootstraps the backtest trade log N times and shows the percentile distribution of final equity, CAGR, and max drawdown.

### Long-Term Investment
Two independent sub-tools in one tab:

**Screener bar** — runs the fundamental + technical quality screener. Produces a tiered report (BUY / NEAR / WATCH) with Q-scores, red-flag alerts, and an Exit Watch block per stock showing:
- SMA_200 level and distance from current price (sell if SMA_50 crosses below SMA_200)
- Dynamic fundamental sell thresholds: ROE floor, consecutive negative revenue years, D/E ceiling, FCF yield, P/E cap

**Backtest bar** — runs the quarterly momentum rebalancing backtest with configurable slots, rebalance interval, breakdown exit toggle, and momentum floor (exit-watch proxy). Reports CAGR, drawdown, alpha vs Nifty/S&P/STOXX, year-by-year bar chart, weakness exits, and top tickers by holding frequency.

### Reports
Lists all saved `.txt` report files in `reports/`. Click any file to view it in the terminal panel. Reports are generated automatically after each scan or backtest run.

---

## CLI Tools

All CLI runners live in `src/` and support `--help` for full argument lists.

| Script | Purpose | Key Arguments |
|--------|---------|---------------|
| `run_daily.py` | Daily signal scan | `--markets`, `--dynamic`, `--quality-filter`, `--top-n`, `--skip-journal` |
| `run_backtest.py` | Short-term backtest | `--market`, `--start`, `--end`, `--equity`, `--slots`, `--no-momentum-exit` |
| `run_backtest_longterm.py` | Long-term backtest | `--market`, `--start`, `--end`, `--slots`, `--rebalance`, `--no-breakdown`, `--momentum-floor` |
| `run_longterm.py` | Long-term screener | `--markets`, `--near`, `--top-n` |
| `run_walkforward.py` | Walk-forward optimisation | `--market`, `--start`, `--end`, `--train-days`, `--test-days` |
| `run_montecarlo.py` | Monte Carlo simulation | `--n`, `--skip-prob`, `--cost-mult` |
| `run_stresstests.py` | Stress tests | `--market` |
| `run_replacement_list.py` | Replacement candidates | `--market`, `--mode` |
| `post_trade.py` | Enrich journal rows post-session | _(no args — reads journal path from config)_ |

---

## Strategy Details

### Short-Term ATR-Dynamic Strategy

**Entry gates (all must pass):**
1. SMA_50 > SMA_200 and SMA_50 rising (trend structure)
2. Close > SMA_200 by minimum distance (SMA distance gate)
3. Volume above rolling average × multiplier
4. RSI within configured band (42–80 for IN, 47–78 for US/EU)
5. MACD histogram positive (or above threshold)

**Sizing:** R-based — risk a fixed percentage of equity per trade. ATR determines the stop distance, position size = (equity × risk_pct) / (ATR × stop_multiplier). Maximum 24% per position (32% cap for momentum leaders).

**Trailing stop:** 5.5× ATR from the rolling high — wide enough to hold through volatility, tight enough to protect gains.

**Exits:**
- Trailing stop hit
- Momentum exit timer: exit if momentum score turns negative after a 7-day grace period
- Rebalance rotation (backtest only): replaced by a higher-ranked candidate

**Circuit breaker:** Disabled (`DRAWDOWN_BANDS: []`) — full-size entries throughout drawdown recovery. Tested and found to hurt returns by keeping capital on the sideline during recoveries.

**Adaptive tuner:** Monitors signal density (BUY/NEAR rate). If density is too low, it loosens gate parameters (SOFT → ULTRA_SOFT). If too high, it tightens (BASE → STRICT). Transitions over 3 days with EMA smoothing.

**Backtest results (IN market, Run 17 locked config):**

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
- **BUY** (Q ≥ 70, all gates pass): strong fundamental + technical alignment
- **NEAR** (Q ≥ 50, most gates pass): approaching entry conditions
- **WATCH** (Q ≥ 35): on radar; not yet ready

**Backtest rebalance logic (quarterly by default):**
- Score all tickers by momentum across [14, 30, 63] day periods
- Structural gate must pass: SMA_50 > SMA_200, Close > SMA_200, SMA_50 rising
- Rotate out of bottom-ranked holders; buy top-scored new entrants into empty slots

**Exit Watch signals (shown in screener, modelled in backtest):**

*Technical:* Sell if SMA_50 crosses below SMA_200, confirmed over 2–3 weeks.

*Fundamental thresholds (dynamic — computed from each stock's current values):*
- ROE drops below max(10%, current_roe × 50%)
- Revenue growth negative for 2 consecutive years
- D/E ratio exceeds max(2.0×, current_de × 2)
- FCF yield turns negative
- P/E exceeds current_pe × 2 without growth acceleration

*Momentum floor (backtest proxy):* Exit at rebalance if avg momentum score < –5% (default). This proxies fundamental deterioration since historical fundamentals are unavailable via yfinance. Set to –99 to disable.

**Long-term backtest results (IN market, 2015–2026):** ~28% CAGR, significant alpha over Nifty

---

## Configuration

All parameters are in `src/config.py`. Key sections:

```python
# Starting capital and commission
ACCOUNT = {
    "equity":            100_000.0,
    "commission":        0.001,     # 0.10% one-way
    "slippage":          0.001,
    "max_position_size": 0.24,      # 24% per position
}

# Portfolio risk limits
RISK = {
    "MAX_OPEN_POSITIONS":       8,
    "MAX_POSITION_SIZE_PCT":    0.24,
    "MAX_TOTAL_CONCENTRATION_PCT": 0.32,  # 32% cap for momentum leaders
    ...
}

# Dynamic universe
DYNAMIC_UNIVERSE = {
    "ENABLED":    True,
    "MAX_AGE_DAYS": 7,
    "SCORE_TOP_N": {"US": 200, "EU": 200, "IN": 250},
}

# Momentum exit
MOMENTUM_EXIT = {
    "ENABLED":         True,
    "SCORE_THRESHOLD": 0.0,    # exit when momentum turns negative
    "GRACE_DAYS":      7,
}

# Ranking periods
RANKING = {
    "MOMENTUM_PERIODS": [14, 30, 63],  # 3W / 6W / 3M
}
```

The GUI Settings tab exposes the most commonly changed parameters (account size, markets, commission, dynamic universe toggle) and persists them to `app_settings.json`.

---

## File Structure

```
data/                   Parquet-cached price data (populated on first run)
universes/              Index constituent CSVs used by dynamic universe builder
reports/                Auto-saved scan and backtest output files
portfolio/
  positions.json        Active paper portfolio positions
tuner_state.json        Adaptive tuner state (persists between runs)
state/
  last_decisions.json   Last decision output per ticker
fundamental_cache.json  7-day fundamental data cache
app_settings.json       GUI settings
```

Price data is cached as Parquet files in `data/` after the first download. Subsequent runs use the cache and only fetch the delta. Cache files are named `TICKER.parquet`.

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
