# Mastermind Pro — Cookbook

Practical step-by-step recipes for every workflow. Read the README for reference; come here to get things done.

---

## Table of Contents

1. [Daily Morning Routine](#1-daily-morning-routine)
2. [Reading the Scan Output](#2-reading-the-scan-output)
3. [Acting on an ENTER Signal](#3-acting-on-an-enter-signal)
4. [Managing Open Positions](#4-managing-open-positions)
5. [Checking Portfolio Health](#5-checking-portfolio-health)
6. [Running a Backtest](#6-running-a-backtest)
7. [Long-Term Screener](#7-long-term-screener)
8. [Setting Up the Journal](#8-setting-up-the-journal)
9. [Changing Markets or Universe Size](#9-changing-markets-or-universe-size)
10. [Troubleshooting](#10-troubleshooting)

---

## 1. Daily Morning Routine

**Goal:** Know what to buy, what to hold, and what is near a stop — in under 5 minutes.

### Step 1 — Open the app

```bash
python app.py          # desktop
# or
streamlit run app_web.py   # browser → http://localhost:8501
```

### Step 2 — Run the Daily Scan

Desktop: **Daily Scan** tab → choose markets → click **Run Daily Scan**

Browser: **Daily Scan** tab → click **Run Scan**

The scan runs 5 steps automatically:
1. Fetches price data from Yahoo Finance (uses disk cache when fresh)
2. Calculates SMA, ATR, RSI, MACD for all tickers
3. Runs the decision engine against open positions + universe
4. Prints the signal report
5. Saves portfolio → updates journal (ENTER signals)

> When the scan finishes, `portfolio/positions.json` is already updated. You do not need to do anything manually.

### Step 3 — Check Portfolio tab

Desktop: click **Portfolio** tab → **Refresh Prices**

Browser: click **Portfolio** tab → **Refresh Prices**

This shows live prices for every open position and flags any stop hits or near-stop positions. Do this before checking new signals.

The Portfolio tab has **4 sub-tabs** — start with the Overview for a full picture, then drill into the regional tab that matches your broker account (US / EU / IN) for single-currency P&L totals.

### Step 4 — Act on signals

- **ENTER** → place the order (see [Recipe 3](#3-acting-on-an-enter-signal))
- **NEAR** → add to watchlist, monitor tomorrow
- **WAIT** → ignore today, the gate that failed is shown in the reason
- **STOP HIT** in Portfolio → close the position, update positions.json

---

## 2. Reading the Scan Output

A typical output block looks like:

```
[IN] INDIA  (INR)
  --- NEAR (3) ---
  * WIPRO.NS     Rs 204    gates=4/5  Near-miss: structural not complete
  * HCLTECH.NS   Rs 1,184  gates=4/5  Near-miss: structural not complete

  --- WAIT (1) ---
  * SUNPHARMA.NS Rs 1,799  G2 Momentum fail: macd_gt_signal

  --- SKIP (15) ---
  * TCS.NS       Rs 2,259  Structural fail: sma50_gt_sma200, close_gt_sma200
```

### Signal meanings

| Signal | Gates passed | Meaning | Action |
|--------|-------------|---------|--------|
| **ENTER** | 5/5 | All gates pass, sizing ready | Place order |
| **NEAR** | 4/5 | One gate failing | Watch for tomorrow |
| **WAIT** | 3–4/5 | Key gates failing, reason shown | Review why |
| **SKIP** | <3/5 | Stock in downtrend or broken structure | Ignore |

### Gate names decoded

| Gate code | What failed |
|-----------|------------|
| `sma50_gt_sma200` | SMA_50 is below SMA_200 — stock in downtrend |
| `close_gt_sma200` | Price below long-term average |
| `close_gt_sma50` | Price below medium-term average |
| `sma50_rising` | SMA_50 trending down |
| `sma_dist_ok` | Price too far extended above SMA_50 (chasing) |
| `macd_gt_signal` | MACD histogram below signal — momentum weak |
| `rsi_in_band` | RSI outside 42–80 (overbought or oversold) |
| `volume_ok` | Volume too thin — liquidity gate |

### Sizing block (ENTER only)

```
  CRWD  $394   gates=5/5  momentum entry
    Shares: 6   Cost: $2,364   Stop: $367.40   Stop risk: 2.4%   Trail: 5.5×ATR
```

- **Shares** — how many to buy
- **Cost** — total position cost
- **Stop** — place your initial stop-loss here
- **Stop risk %** — ATR as % of price (higher = more volatile)
- **Trail** — trailing stop will be 5.5× ATR below the rolling high

---

## 3. Acting on an ENTER Signal

### What the engine already calculated for you

When you see an ENTER signal, the scan has already:
- Confirmed all 5 gates pass
- Sized the position to risk the configured % of equity
- Set an initial stop at 5.5× ATR below entry

### Checklist before placing the order

- [ ] Earnings date is more than 5 days away (check earnings calendar)
- [ ] No major macro event today (Fed meeting, RBI policy, etc.)
- [ ] Bid-ask spread is tight (< 0.5% for liquid stocks)
- [ ] You have a free slot (max 8 positions)
- [ ] Sector is not already at its cap

### Record the trade in positions.json

After you execute the order, open `portfolio/positions.json` and add an entry (or let the next scan do it automatically — the engine already queued the position):

```json
{
  "ticker": "CRWD",
  "market": "US",
  "sector": "Technology",
  "entry_price": 394.00,
  "entry_date": "2026-05-31",
  "shares": 6,
  "stop_loss": 367.40,
  "stop_loss_initial": 367.40,
  "trail_mult": 5.5,
  "peak_price": 394.00,
  "atr_at_entry": 12.30,
  "risk_pct": 0.04,
  "regime": "Normal",
  "is_high_vol": false,
  "cost": 2364.00
}
```

> **Tip:** The daily scan auto-writes this entry to positions.json when the engine decides to ENTER. If you ran the scan before market open and manually executed at a different price, update `entry_price`, `stop_loss`, and `cost` to match your actual fill.

### Update your trailing stop daily

The engine recalculates the trailing stop every day during the scan. The updated stop is shown in the Portfolio Review section at the end of the scan report. Check this each morning and move your broker stop up to match.

---

## 4. Managing Open Positions

### How positions.json works

`portfolio/positions.json` is the source of truth for the engine. It is read at the start of each scan and written back at the end with updated stops, exits, and new entries.

**Key fields:**

| Field | What it means |
|-------|--------------|
| `stop_loss` | Current trailing stop (updated after each scan) |
| `stop_loss_initial` | Stop at entry — used to calculate R-multiple |
| `peak_price` | Highest close since entry — stop trails from here |
| `trail_mult` | ATR multiplier for the trailing stop (default 5.5) |
| `atr_at_entry` | ATR when you entered — fixed reference |
| `regime` | Volatility regime at entry (Normal / High Vol) |
| `is_high_vol` | High Vol entries use tighter risk (2% instead of 4%) |

### Exiting a position

When you close a position at your broker:

1. Remove its entry from `portfolio/positions.json` (delete the `{...}` block)
2. Save the file
3. The next scan will not see the position and will free that slot

> Do not edit `stop_loss` manually. Let the engine trail it — manual edits will be overwritten by the next scan.

### Adding a position the engine did not generate

Occasionally you may add a discretionary position. Add it to positions.json using the format above. The engine will include it in the Portfolio Review and trail its stop automatically (using the configured `trail_mult` and `atr_at_entry` you provide).

### Changing stop multiplier for one position

Edit `trail_mult` in positions.json for that position. The engine will use the new multiplier from the next scan onwards. Useful for High Vol positions where you want to widen the stop.

---

## 5. Checking Portfolio Health

### Desktop app — Portfolio tab

1. Click **Portfolio** tab
2. Click **Refresh Prices** (fetches live Yahoo Finance prices)
3. Check the **global alerts strip** at the top — red = stop hit somewhere, green = all safe
4. Use the **4 sub-tabs** to review positions:

| Sub-tab | Best used for |
|---------|--------------|
| 🌍 Overview | Quick count of positions by region; see alerts at a glance |
| 🇺🇸 US | USD P&L total — matches your US brokerage account |
| 🇪🇺 EU | EUR P&L total — matches your EU brokerage account |
| 🇮🇳 IN | INR P&L total — matches your Zerodha / HDFC account |

5. Read the colour-coded rows within each tab:
   - **Red** → stop already breached — close the position today
   - **Yellow** → stop cushion < 5% — watch closely, tighten manually if needed
   - **Green** → safe

Details area below each table shows full per-position breakdown: entry, live price, stop, P&L, R-multiple, ATR at entry, regime.

Long-term positions added by the LT Screener show an `[LT]` marker and their fundamental grade. These use an 8× ATR trailing stop and exit on SMA_200 cross rather than the short-term 5.5× stop.

### What R-multiple tells you

R = (current price − entry price) / (entry price − initial stop)

| R value | Meaning |
|---------|---------|
| R < 0 | In loss |
| R = 0 to 1 | Profitable but not yet 1R |
| R = 1 | Covered your initial risk — consider moving stop to break even |
| R = 2+ | Strong winner — let the trail run |
| R = 3+ | Exceptional — protect with tighter trail if you choose |

### When to manually tighten a stop

The trailing stop is systematic — trust it unless:
- An earnings announcement is within 5 days (tighten before, widen after)
- A macro event could gap the stock overnight
- R > 3 and you want to lock in partial gains

To tighten: edit `trail_mult` in positions.json from 5.5 to e.g. 4.0, or edit `stop_loss` directly (note: the engine will re-derive from peak_price × ATR on the next scan, so set `peak_price` lower too if you want the manual stop to stick).

---

## 6. Running a Backtest

### Short-term ATR-Dynamic backtest

**Desktop:** Backtest tab → set market + date range → Run Backtest

**Browser:** ST Backtest tab → set parameters → Run

**CLI:**
```bash
python src/run_backtest.py --market IN --start 2016-01-01 --end 2026-05-31
python src/run_backtest.py --market US --start 2021-01-01
python src/run_backtest.py --market ALL --start 2020-01-01
```

**What to look for in results:**
- **CAGR** — annualised return. Target: > 12% for IN, > 10% for US/EU
- **Max Drawdown** — worst peak-to-trough. Should stay under 30%
- **Sharpe ratio** — risk-adjusted return. > 0.8 is good, > 1.0 is excellent
- **Alpha vs benchmark** — excess return over Nifty / S&P 500
- **Win rate** — expect 45–55%; this is a trend system so winners are big, losers are small

### Long-term momentum backtest

**Desktop:** Long-Term tab → Backtest section → Run LT Backtest

**Browser:** LT Backtest tab

**CLI:**
```bash
python src/run_backtest_longterm.py --market IN --start 2015-01-01 --slots 10 --rebalance 63
```

**Key parameters:**

| Parameter | Default | What it does |
|-----------|---------|-------------|
| Slots | 10 | Max positions held at once |
| Rebalance | 63 days | How often to rotate laggards (63 = quarterly) |
| Breakdown exit | ON | Sell immediately on SMA_50 < SMA_200 cross |
| Momentum floor | -5% | Exit at rebalance if avg momentum < -5% |

**Tuning tips:**
- Fewer slots (5–8) → higher concentration, more volatile returns
- More frequent rebalance (21 = monthly) → more turnover, higher costs
- Momentum floor of -10% is more lenient; -3% is stricter

### Walk-forward validation

Run this to confirm the strategy is not curve-fitted:

```bash
python src/run_walkforward.py --market IN --years 10 --train 3 --test 1
```

Each fold trains on 3 years and tests on 1. If out-of-sample Sharpe is consistently > 0.5, the strategy generalises.

---

## 7. Long-Term Screener

Identifies fundamentally strong stocks for multi-month to multi-year holds.

### Run the screener

**Desktop:** Long-Term tab → Screener section → Run Long-Term Screen

**Browser:** LT Screener tab

**CLI:**
```bash
python src/run_longterm.py --markets IN --min-q 55 --top-n-in 250
python src/run_longterm.py --markets US,EU,IN --no-near
```

### Reading the output

```
TIER 1 — BUY
  INFY.NS     Q=78  ROE=32% Rev-growth=14% D/E=0.1  Margin=23%
  EXIT WATCH:
    Technical: Sell if SMA_50 < SMA_200 (currently 4.2% above)
    Fundamental: Sell if ROE < 16% or revenue growth turns negative
```

- **Q-score 0–100** — composite fundamental quality. > 70 is strong.
- **EXIT WATCH** — pre-computed sell thresholds for the stock. Review weekly.
- **NEAR** tier — technically not quite set up; wait for SMA confirmation
- **WATCH** tier — fundamentally decent but not ideal; monitor only

### How to use Exit Watch

The Exit Watch block is computed fresh each time you run the screener. Review it monthly:
- If the technical condition triggers → exit regardless of fundamentals
- If a fundamental threshold triggers → research whether it is temporary or structural before exiting

---

## 8. Setting Up the Journal

The journal writes ENTER signals to an Excel file after each daily scan.

### Configure the path

Edit `src/config.py`:

```python
JOURNAL = {
    "PRIMARY_PATH":  r"C:\path\to\your\Mastermind-Trading-Journal.xlsx",
    "FALLBACK_NAME": "Mastermind-Trading-Journal.xlsx",  # saved to reports/ if primary missing
    "SHEET_SIGNALS": "4. Trade Log",                     # exact sheet name in your workbook
    "DATA_START_ROW": 6,                                 # first data row (below headers)
}
```

### Column layout (Excel sheet)

The journal writes to these columns automatically:

| Col | Content | Auto / Manual |
|-----|---------|--------------|
| A | Trade number (formula) | Auto |
| B | Entry date | Auto |
| C | Entry time | Auto |
| E | Ticker | Auto |
| F | Market | Auto |
| G | Signal (ENTER) | Auto |
| H | Setup name | Auto |
| I | Currency | Auto |
| J | Entry price | Auto |
| K | Stop loss | Auto |
| L | Target (2R) | Auto |
| M | Shares | Auto |
| N | Exit price | **Manual** — fill after closing |
| O | P&L (formula) | Auto |
| P | R-multiple (formula) | Auto |
| Q | Sector | Auto |
| R | Regime | Auto |
| S | ATR% | Auto |
| T | Gates passed | Auto |
| AC | Reflection notes | Auto |

Fill in column N (Exit price) after you close each position. Columns O and P calculate automatically.

### What gets logged

Only **ENTER** signals are logged (one row per ticker per day). If you run the scan twice in a day, duplicate tickers are skipped automatically.

### Troubleshooting the journal

| Error message | Fix |
|--------------|-----|
| `openpyxl not installed` | `python -m pip install openpyxl` (use the same Python that runs the app) |
| `Journal not found` | Check `PRIMARY_PATH` in config.py — file must exist |
| `Journal is open in Excel` | Close the Excel file, then re-run the scan |
| `Sheet '4. Trade Log' not found` | Rename your sheet to exactly match `SHEET_SIGNALS` in config |
| `Trade log is full (300 rows)` | Insert more rows in Excel below the last data row |

---

## 9. Changing Markets or Universe Size

### Switch to India-only scanning

Desktop: Settings tab → no change needed, just select **IN** in the Daily Scan dropdown each morning.

CLI:
```bash
python src/run_daily.py --markets IN
```

### Increase universe size

Edit `src/config.py`:
```python
DYNAMIC_UNIVERSE = {
    "SCORE_TOP_N": {"US": 300, "EU": 250, "IN": 350},  # increase as needed
}
```

Or in the desktop app Settings tab, change **Top-N IN (universe fetch)**.

Larger universes generate more signals but take longer to scan (each extra 50 tickers adds ~20–30 seconds due to yfinance rate limits).

### Add a custom ticker to the watchlist

Edit `src/config.py`:
```python
WATCHLIST = {
    "IN": [
        "RELIANCE.NS", "TCS.NS",
        "YOURTICKER.NS",   # add here
        ...
    ],
}
```

Also add a sector mapping:
```python
SECTOR_MAP = {
    "YOURTICKER.NS": "Technology",  # or whichever sector
}
```

### Disable dynamic universe (use fixed watchlist only)

Desktop: Settings tab → uncheck **Enable dynamic universe**

`src/config.py`:
```python
DYNAMIC_UNIVERSE = {"ENABLED": False}
```

With dynamic universe off, only tickers in `WATCHLIST` are scanned — much faster but smaller opportunity set.

### Change risk per trade

Desktop: Settings tab → **Entry Size Cap** (fraction, e.g. 0.20 = 20%)

`src/config.py`:
```python
ACCOUNT = {"max_position_size": 0.20}   # 20% per position
RISK    = {"MAX_POSITION_SIZE_PCT": 0.20}
```

---

## 10. Troubleshooting

### Scan takes very long

- Dynamic universe is downloading 600+ tickers. Normal for first run; subsequent runs use cache.
- Reduce universe size in Settings tab or config.py.
- Data cache in `data/` is per-ticker Parquet files — once populated, incremental fetches are fast.

### "No ENTER signals today" for weeks

This is normal during bear markets — the SMA trend gate blocks entries when most stocks are below SMA_50/SMA_200. Check the adaptive tuner mode in the report header:
- `SOFT` / `ULTRA_SOFT` → tuner is already loosening gates; market is genuinely weak
- `STRICT` → too many signals before; will loosen naturally if density drops

Do not manually override the tuner unless you understand the implications.

### Data fetch errors for specific tickers

Yahoo Finance occasionally renames or delists tickers. Errors like `No data for XYZ` or `KeyError: Close`:
- Check the ticker on finance.yahoo.com — it may have changed suffix (e.g. `.BO` instead of `.NS`)
- Add the correct symbol to `WATCHLIST` in config.py and remove the old one
- Delete the stale cache file: `data/XYZ.parquet`

### Journal not updating

Run through the checklist in [Recipe 8](#8-setting-up-the-journal). The most common cause on Windows is a multiple-Python-install issue — openpyxl is installed in one Python but the app runs on another. Fix:
```bash
# Find which python runs the app
python --version       # check path

# Install into the right one explicitly
C:\path\to\correct\python.exe -m pip install openpyxl
```

### Portfolio tab shows all dashes (no prices)

Click **Refresh Prices** — prices are not fetched on load, only on demand. If refresh shows errors, yfinance may be rate-limited; wait 60 seconds and try again. The batch download falls back to per-ticker if any fail.

Note: after clicking Refresh, check the **regional sub-tab** for your market (US / EU / IN) — the Overview sub-tab shows all positions but intentionally omits the combined cost/P&L total because currencies differ.

### positions.json is out of sync

If you manually closed positions at your broker but forgot to edit positions.json, run the scan — the engine compares the file against actual market data and will flag held positions that no longer pass gates. Then manually remove the closed positions from the file.

### Streamlit app not loading at http://localhost:8501

Check that the server is running:
```bash
streamlit run app_web.py
```

If it started but is stuck, check `.streamlit/config.toml` exists:
```toml
[browser]
gatherUsageStats = false

[server]
headless = true
port = 8501
```

If the port is blocked, change `port = 8502` in config.toml and browse to `http://localhost:8502`.

### "Busy — a task is already running"

The desktop app runs one task at a time. Wait for the current scan to finish (watch the status bar at the bottom). If the app appears stuck, close and reopen — the scan thread is daemon-mode and will not block restart.
