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

`tests/test_semimap.py` checks that ids are unique, every weight points to a real company, and each portfolio adds up to 100%.
