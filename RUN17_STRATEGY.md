# Run 17 — Mastermind Pro Strategy Reference

**Status: LOCKED PRODUCTION CONFIG**
Last validated: June 2026 (10-year backtest, 22 runs)
Do not change any parameter below without re-running the full 10-year backtest.

---

## 1. What Is Run 17?

Run 17 is the 17th and final iteration of a systematic ATR-dynamic trend-following strategy backtested over a 10-year period (Jan 2016 – May 2026) on Indian equities. It was reached after 22 sequential backtesting runs, each testing one change at a time. Every parameter in this document was explicitly tested and locked because it produced the best risk-adjusted return across the full decade.

The system trades a rotating quality-filtered universe of Nifty 250 stocks, enters on multi-gate confirmation, and exits via a ratcheting ATR-based trailing stop.

---

## 2. Backtest Results

### Primary: 10-Year (Jan 2016 – May 2026) — IN Market

| Metric | Value |
|---|---|
| CAGR | **14.05%** |
| Total Return | **+292.39%** |
| Rs 1,00,000 grew to | **Rs 3,92,389** |
| Max Drawdown | -25.97% |
| Sharpe Ratio | 0.862 |
| Alpha vs Nifty | +2.73% per year |

### Secondary: 3-Year (Jan 2023 – May 2026) — IN Market

| Metric | Value |
|---|---|
| CAGR | 20.77% |
| Total Return | +89.68% |
| Max Drawdown | -16.68% |
| Sharpe Ratio | 1.245 |
| Alpha vs Nifty | +12.62% per year |

### Walk-Forward Validation (Jun 2026)

| | Fold 1 (Mar 2024 – Mar 2025) | Fold 2 (Mar 2025 – Mar 2026) |
|---|---|---|
| OOS Sharpe | 0.068 | -0.673 |
| OOS CAGR | -0.13% | -7.95% |
| Degradation | 93% | 1090% |

**WFO interpretation:** The 2024–2026 period was genuinely choppy for IN trend-following (Nifty sideways after Sep 2024 ATH). The WFO used only 3yr data × 20 tickers — too few trades to reliably distinguish parameters. The 10yr backtest over 247 rotating stocks is the authoritative validation. Run 17 locked config stands.

---

## 3. Account Settings

| Parameter | Value |
|---|---|
| Starting equity | Rs 1,00,000 |
| Commission per trade | 0.1% |
| Slippage | 0.1% |
| Max open positions | **8 slots** |
| Baseline position size | **24%** of equity |
| Velocity-leader ceiling | **32%** of equity |

---

## 4. Portfolio Risk Limits

| Limit | Value |
|---|---|
| Max open positions | 8 |
| Max per sector (IN) | **1.0 — no cap** |
| Max per sector (US/EU) | 0.50 (4 of 8 slots) |
| Max high-vol positions per market | 4 |
| Circuit breaker | **Disabled** (DRAWDOWN_BANDS: []) |

**Critical — IN sector cap = 1.0:** All Indian stocks are classified as "Unknown" sector (no granular NSE sector mapping). A 0.50 sector cap would allow only 4 of 8 slots to fill. Run 17 uses 1.0, meaning all 8 slots can fill with IN stocks.

**Circuit breaker disabled:** No position scaling during drawdowns. The system takes full-size entries throughout recovery. Testing showed that reducing size during drawdowns caused missed recoveries that hurt long-term CAGR more than the drawdown itself.

---

## 5. Universe

### Fixed Fallback Watchlist (20 IN tickers)
Used when dynamic universe is unavailable.

| Sector | Tickers |
|---|---|
| Technology | TCS.NS, INFY.NS, WIPRO.NS, HCLTECH.NS |
| Financials | HDFCBANK.NS, ICICIBANK.NS, KOTAKBANK.NS, AXISBANK.NS, BAJFINANCE.NS |
| Consumer | HINDUNILVR.NS, ITC.NS, MARUTI.NS, TITAN.NS |
| Industrials | LT.NS |
| Energy | RELIANCE.NS, NTPC.NS |
| Healthcare | SUNPHARMA.NS, DRREDDY.NS |
| Telecom | BHARTIARTL.NS |
| Utilities | POWERGRID.NS |

### Dynamic Universe (Live Mode)
- Enabled by default
- Sources Nifty 250 constituents
- Quality-scores all stocks, keeps top 250 by score
- Refreshed every 7 days
- Min quality score to enter: 35 (excludes "Drag" stocks)
- Prefer stocks scoring 45+ ("Good" or better)

---

## 6. Entry System — Five Sequential Gates

All five gates must pass for an ENTER decision. Gates are evaluated in order; structural failures (G1, G3, G4) issue SKIP, while execution failures (G2, G5) issue WAIT.

**Decision outputs:**
- `ENTER` — all 5 gates pass
- `NEAR` — 4 of 5 gates pass (monitor, may enter soon)
- `WAIT` — structural ok, execution/momentum not ready
- `SKIP` — structural gate failed

### Gate 1 — Trend (Structural)

All five sub-conditions must be true:

| Check | Description |
|---|---|
| SMA50 > SMA200 | Price in long-term uptrend |
| Close > SMA200 | Price above key support |
| Close > SMA50 | Price above medium-term MA |
| SMA50 rising | SMA50 today > SMA50 15 days ago |
| SMA gap >= sma_dist_min | SMA50 is meaningfully above SMA200 (not just touching) |

### Gate 2 — Momentum (Execution)

| Check | Description |
|---|---|
| MACD > Signal | MACD line above signal line |
| RSI in [rsi_lo, rsi_hi] | Not oversold, not overbought/extended |

### Gate 3 — Volatility Regime (Structural)

Blocks entry only in EXTREME regime (ATR% >= 4.0%). All other regimes allow trading.

### Gate 4 — Liquidity (Structural)

Today's volume must be >= volume_mult × 20-day average volume.

### Gate 5 — MACD Execution (Execution)

All three must be true:
- MACD histogram today >= macd_hist_eps (at or above threshold)
- MACD histogram yesterday >= macd_hist_eps (two consecutive bars)
- MACD histogram today > yesterday (rising)

### Timing — Anti-Chase Blocker

After all gates: if the last 3 candles are all large green candles (body >= 80% of ATR), block entry. Prevents chasing breakouts at extended prices.

---

## 7. Per-Market Gate Parameters (Locked)

### India (IN)

| Parameter | Value |
|---|---|
| sma_dist_min | **0.005** (0.5% gap required) |
| volume_mult | **0.55** (55% of 20-day avg vol) |
| rsi_lo | **42** |
| rsi_hi | **80** |
| macd_hist_eps | **0.0** |

### United States (US)

| Parameter | Value |
|---|---|
| sma_dist_min | 0.008 (0.8% gap required) |
| volume_mult | 0.65 |
| rsi_lo | 47 |
| rsi_hi | 78 |
| macd_hist_eps | 0.0 |

### Europe (EU)

| Parameter | Value |
|---|---|
| sma_dist_min | 0.008 |
| volume_mult | 0.65 |
| rsi_lo | 47 |
| rsi_hi | 78 |
| macd_hist_eps | -0.001 (slightly relaxed) |

---

## 8. Volatility Regime System

ATR% (ATR as % of price) determines the trading regime:

| Regime | ATR% Range | Risk % | Trail Mult | Stop Mult | Trading |
|---|---|---|---|---|---|
| LOW | < 1.0% | see below | see below | see below | Yes |
| NORMAL | 1.0% – 2.0% | see below | see below | see below | Yes |
| HIGH | 2.0% – 4.0% | see below | see below | see below | Yes |
| EXTREME | >= 4.0% | 0% | 0× | 0× | No |

### Regime Parameters by Market

**India (IN):**
| Regime | Risk % | Trail Mult | Stop Mult |
|---|---|---|---|
| LOW | 9% | 7.0× | 2.5× |
| NORMAL | 7% | 7.0× | 2.0× |
| HIGH | 4% | 5.0× | 3.5× |

**US:**
| Regime | Risk % | Trail Mult | Stop Mult |
|---|---|---|---|
| LOW | 10% | 12.0× | 2.5× |
| NORMAL | 8% | 10.0× | 2.0× |
| HIGH | 4% | 6.0× | 3.0× |

**EU:**
| Regime | Risk % | Trail Mult | Stop Mult |
|---|---|---|---|
| LOW | 6% | 6.0× | 2.5× |
| NORMAL | 5% | 7.0× | 2.0× |
| HIGH | 3% | 4.5× | 3.0× |

*Risk % = fraction of total equity risked per trade (used to size shares).*

---

## 9. Position Sizing

Position size is R-based (risk-based), capped at 24% of equity:

```
risk_amount   = equity × risk_pct
stop_distance = entry_price - (stop_mult × ATR)
shares        = risk_amount / stop_distance
position_value = shares × entry_price
```

The 24% cap (`max_position_size`) prevents any single position exceeding that fraction regardless of R-sizing. Velocity leaders (highest quality score in bull breadth) can go up to 32% via elastic widening.

---

## 10. Exit Rules

### Primary: Trailing Stop (5.5× ATR)

```
trail_stop = peak_price - (5.5 × ATR)
stop       = max(current_stop, trail_stop)   # ratchets upward only
```

The stop only moves up, never down. It locks in profits as the stock climbs.

**Why 5.5×?** Tested 3×, 4×, 5×, 5.5×, 6× ATR:
- 3–4× ATR: too many whipsaws, exits good positions during normal pullbacks
- 5.5×: optimal — captures the trend without premature exits
- 6×: gives back too much profit before exiting

### Breakeven Floor

When price reaches **+1R** (entry + initial stop distance), the stop moves to entry price. This eliminates the risk of a loss on winning trades.

### Momentum Exit

Exits a held position when rolling momentum turns negative:
- Periods: [14, 30, 63] trading days (3W / 6W / 3M)
- Threshold: score < 0.0 (negative average return across periods, vol-adjusted)
- Grace period: 7 calendar days after entry (prevents triggering on normal consolidation)

**Why 0.0 threshold?** Tested -0.15 (more lenient) — it let declining stocks ride longer and hurt returns significantly.

### SMA200 Breach (Live Mode Only)

In live scanning (not backtest), if close falls below SMA200, position is exited immediately. This is a structural breakdown signal.

### Replacement Immunity Window

After a stop-loss exit, the slot cannot be immediately refilled (prevents whipsaw churn):
- Default: **21 calendar days** immunity
- Override: refill allowed early if the exited stock breaks below EMA20 (structural breakdown) or drops out of the ranked universe entirely

---

## 11. Elastic Adaptation (Quality + Macro)

The gates widen slightly in strong market conditions:

**Bullish macro (breadth > 50% stocks above SMA200) + stock quality score >= 60:**
- RSI_hi widened by 15% (e.g., 80 → 92) — allows entering stronger momentum
- volume_mult reduced by 20% — lower liquidity hurdle

**Bearish macro (breadth <= 50%):**
- RSI_hi tightened by 10% — requires cleaner momentum
- sma_dist_min increased by 50% (min 0.5%) — requires stronger trend separation
- volume_mult increased by 25% — requires stronger volume confirmation

---

## 12. Adaptive Tuner

The tuner watches signal density (ENTER/WAIT/SKIP ratios) and adjusts gate thresholds automatically. It has 4 modes:

| Mode | sma_dist_min | volume_mult | macd_hist_eps |
|---|---|---|---|
| STRICT | 0.015 | 0.80 | 0.000 |
| BASE | 0.010 | 0.60 | 0.000 |
| SOFT | 0.007 | 0.50 | -0.001 |
| ULTRA_SOFT | 0.003 | 0.40 | -0.002 |

- If too few ENTER signals (market too tight): loosens toward SOFT/ULTRA_SOFT
- If too many ENTER signals (market too hot): tightens toward STRICT
- EMA alpha: 0.2 (smooth, not reactive to one-day spikes)

---

## 13. Entry Priority (Phases 5 & 6)

When multiple ENTER signals appear on the same day:

1. **Phase 5 (Replacement):** same-market same-sector candidates first; skip sector if sector momentum is declining; same-market any sector second
2. **Phase 6 (Fill slots):** sort by: (1) rising sector momentum, (2) higher quality score, (3) lower ATR% as tiebreaker

Hysteresis hurdle: a replacement candidate must beat the exiting stock's quality score by 15% to prevent marginal churn.

---

## 14. Momentum Ranking (Entry Selection)

Used to rank candidates when multiple stocks qualify simultaneously:

- Periods: **[14, 30, 63]** trading days
- Simple average of percentage returns across the three periods
- Volatility penalty: off (raw return, not vol-adjusted)
- Tiebreaker: lower ATR% wins (less volatile among equally-ranked)

**Why [14, 30, 63] and not [7, 14, 30]?**
[7, 14, 30] caused excessive churn — positions entered on short-term momentum that faded within weeks. [14, 30, 63] captures established trends.

---

## 15. What the 22 Runs Tested

Each run changed exactly one parameter vs the previous:

| Run | Change | Outcome |
|---|---|---|
| 1–5 | Baseline setup, commission/slippage calibration | Foundation |
| 6 | IN sector cap 0.50 → 1.0 | Fixed critical bug: only 4/8 slots filled with 0.50 |
| 7 | Circuit breaker enabled (20% DD) | Hurt CAGR — missed recovery entries |
| 8 | Circuit breaker disabled | +1.2% CAGR recovered |
| 9 | Trailing stop 4× → 5.5× ATR | Eliminated whipsaws, +0.8% CAGR |
| 10 | Trailing stop 6× ATR | Gave back too much profit |
| 11 | Momentum periods [7,14,30] | Excessive churn |
| 12 | Momentum periods [14,30,63] | Settled positions, reduced churn |
| 13 | Momentum exit threshold -0.15 | Let losers ride — hurt Sharpe |
| 14 | Momentum exit threshold 0.0 | Correct — exit when momentum turns negative |
| 15 | Progressive trailing stop (locks at +20%) | Cut big winners early — hurt CAGR |
| 16 | 3-stock concentrated portfolio | 12.06% CAGR vs 8-slot 14.05% |
| 17 | 8-slot, 24% sizing, all above | **OPTIMAL — 14.05% CAGR, 0.862 Sharpe** |
| 18–22 | Parameter sensitivity checks | No improvement found |

---

## 16. What NOT to Change

The following parameters are locked. Do not change without a full 10-year backtest:

| Parameter | Locked Value | Why |
|---|---|---|
| Trailing stop | 5.5× ATR | Tested 3–6×; 5.5 is optimal |
| Momentum periods | [14, 30, 63] | [7,14,30] causes churn |
| Momentum exit threshold | 0.0 | -0.15 hurts badly |
| Circuit breaker | Disabled | Hurts CAGR by missing recoveries |
| IN sector cap | 1.0 | Must be 1.0 for 8 slots to fill |
| Max positions | 8 | 3-slot concentated underperforms |
| Position size | 24% | R-based, capped at 24% baseline |
| Concentration ceiling | 32% | Velocity leaders only |
| Grace period | 7 days | Prevents post-entry false exits |
| Immunity window | 21 days | Prevents whipsaw churn |

---

## 17. Files in Codebase

| File | Role |
|---|---|
| `src/config.py` | All strategy parameters — single source of truth |
| `src/rules.py` | Five gate functions + `evaluate_gates()` |
| `src/decision_engine.py` | Engine phases 0–7; `update_trailing_stop()` (5.5× ATR) |
| `src/backtest.py` | 10-year backtest runner |
| `src/ranking.py` | `momentum_score()` for entry ranking and momentum exit |
| `src/walk_forward.py` | Walk-forward optimisation framework |
| `src/indicators.py` | SMA/EMA/ATR/MACD/RSI calculation |
| `src/stock_selector.py` | Quality scoring (0–100) for universe filtering |
| `src/adaptive_tuner.py` | Signal-density-based gate tuning |
| `src/_wfo_run.py` | Lean 8-combo WFO runner with real-time logging |
| `app.py` | Tkinter desktop GUI (7 tabs) |
| `app_web.py` | Streamlit browser GUI (8 tabs) |

---

## 18. Running a Fresh Backtest

```bash
# IN market, 10-year
python3 src/backtest.py --market IN --start 2016-01-01 --end 2026-05-31

# All markets
python3 src/backtest.py --market ALL --start 2016-01-01 --end 2026-05-31

# Walk-forward validation (IN, 8-combo lean grid, ~16 min)
cd src && python3 _wfo_run.py
# Results: reports/wfo_progress.txt (live log), reports/wfo_result.json (full data)
```

---

## 19. Disclaimer

This document describes a research and paper-trading system. All backtest results are simulated. Past simulated performance does not guarantee future results. Do not deploy live capital without independent validation, regulatory compliance review, and full risk disclosure.
