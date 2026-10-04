# Semiconductor Dependency Map

An interactive map of who depends on whom in the chip industry: brands and hyperscalers → chip designers → foundries → critical inputs, with imec's shared R&D underneath. A company's row is its tier and its colour is its moat. **Portfolio** view adds target weights for the model portfolio (50% core ETF + 50% satellites).

Research and education only, not investment advice.

## Files

| File | What it holds |
|---|---|
| `nodes.json` | Every company (tier, role, ticker, exchange, currency, price, moat, badge), the moat colour legend, tier labels and callouts, the shared disclaimer, and the prices' as-of date |
| `portfolios.json` | Named portfolios: core ETF and its weight, satellite weights by node id, overlap note, excluded tickers with reasons, German tax notes. Add another entry under `portfolios` and a selector appears in Portfolio view |
| `index.html` | The page. Reads both JSON files; no build step and no libraries |

## Run it

Browsers block a page opened from disk from reading its JSON files, so serve the folder:

```bash
python -m http.server 8000 -d semimap
# open http://localhost:8000  (add #portfolio to open in Portfolio view)
```

## Refresh prices

Prices are point-in-time, not live. To pull the latest closes from Yahoo Finance and update the as-of date:

```bash
python src/semimap_prices.py --dry-run   # show changes
python src/semimap_prices.py             # write semimap/nodes.json
```

Tickers that fail to refresh keep their old price, and the page says how many.

## Backtest

`src/run_backtest_semimap.py` backtests the map's stocks in EUR (every price is converted with daily FX first):

```bash
python src/run_backtest_semimap.py                         # all three, from 2016
python src/run_backtest_semimap.py --mode portfolio --rebalance 21
python src/run_backtest_semimap.py --mode short            # ATR-Dynamic strategy on the map's stocks
python src/run_backtest_semimap.py --mode long --slots 5   # momentum rotation on the map's stocks
```

- **portfolio:** the weights in `portfolios.json` (50% core ETF + satellites), rebalanced every 63 trading days by default, compared with holding only the core ETF. Shows year-by-year returns and what each holding contributed. VVSM.DE only trades from late 2020, so earlier dates use the returns of SMH (VanEck's US ETF on the same index) converted to EUR. Companies that list later, such as CoreWeave, join at the first rebalance after their first price.
- **short / long:** the app's existing short-term and long-term backtest engines with the map's stocks as the universe, compared with the core ETF.

The same backtest is on the **Semis Backtest** tab in both the desktop and browser apps, with an equity-curve chart.

Reports go to `reports/semimap-backtest-<date>.txt` and the equity curves to a matching `.csv`. `--synthetic` swaps in random-walk prices so the pipeline can be tested offline; its numbers mean nothing.

`tests/test_semimap.py` checks that ids are unique, every weight points to a real company, and each portfolio adds up to 100%.
