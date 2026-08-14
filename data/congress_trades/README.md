# Congress trades (hand-compiled)

`congress_trades_2023_2026.csv` -- 42 individual congressional stock
trades, compiled by searching news coverage year by year and stopping
once the yield dropped near zero.

## Why this isn't an automated fetcher like everything else

Every other data source in this project (SEC EDGAR, Nasdaq Trader, Yahoo
Finance) is a free, structured, programmatically-fetchable feed. Congress
trading doesn't have one:

- The two open community trackers that used to fill this gap (House Stock
  Watcher, Senate Stock Watcher) are dead or years stale as of this build.
- The official source (house.gov / senate.gov Periodic Transaction
  Reports) is unstructured, scanned PDFs -- not a data feed.
- The remaining structured options (Quiver Quantitative, Apify scrapers)
  are paid.

So this was built manually instead: search news coverage of congressional
trades for a given year, extract whatever individual (member, ticker,
action, date) trades are reported, and count. Yield by year:

| Year | Individual trades found |
|---|---|
| 2026 (partial, through Aug) | 16 |
| 2025 | 19 |
| 2024 | 6 |
| 2023 | 1 |

The sharp drop-off going backward reflects that real-time per-trade news
coverage (mainly Benzinga's dedicated "government trades" vertical) is a
recent phenomenon -- 2023 and earlier only turn up retrospective
aggregate stats (total volume, top performers by return), not per-trade
detail. Search stopped at 2023 by design, not because trades stopped
existing that far back.

## What this data is (and isn't) good for

- **Is**: a small, real, dated set of notable congressional trades, useful
  to see in the data / spot-check specific tickers.
- **Isn't**: comprehensive. It skews heavily toward a handful of
  frequently-covered members (Pelosi, Gottheimer, Whitehouse, Khanna) and
  large or round-number trades that make for a headline. The true
  disclosure volume is much higher -- e.g. one tracker reported 147
  disclosed trades in a single week in mid-2026, and Rep. Ro Khanna alone
  has over 24,000 filings on record. This dataset captures maybe a few
  dozen trades out of many thousands.
- **Not validated**: unlike every other indicator in `model/`, this was
  never backtested against forward returns -- the sample is too sparse
  and too biased toward high-profile names to say anything statistically
  meaningful. It's in the data for visibility, per explicit request, not
  as an input to the confluence scoring model.

## Columns

`politician_name, chamber, symbol, action (buy/sell), amount_range,
trans_date, date_precision (exact/month/year -- some source articles only
gave a month or "sometime in 2025"), disclosed_date (only known for a few
rows), notes, source_url`

## Updating

Add rows to the CSV, then rerun `python pipeline/load_congress_trades.py`
(full replace, not incremental) to reload the `congress_transactions`
table.
