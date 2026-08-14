# Data pipeline

Pulls free daily price history and fundamentals for the US common-stock
universe into a local SQLite database at `../data/db/stocks.db`.

## Sources (all free, no API key)

- **Universe**: Nasdaq Trader listed-symbol files (`nasdaqlisted.txt` /
  `otherlisted.txt`) filtered to common stock, ETFs/warrants/units/test
  issues excluded. This is a proxy for "the whole US market" -- true
  Russell 3000 constituents are proprietary FTSE Russell data and aren't
  freely available.
- **Prices**: Yahoo Finance via `yfinance`. Unofficial API -- can break or
  get rate-limited without notice. Batched (40 tickers/request) with retry.
- **Fundamentals**: SEC EDGAR's XBRL `companyfacts` API. Official, free,
  no key, but requires a descriptive `User-Agent` (set in `config.py`) and
  a light rate limit (~10 req/sec max).
- **Insider transactions**: SEC's free bulk quarterly Form 3/4/5 datasets
  (structured TSV, one ZIP per quarter covering the whole market's insider
  filings). Only open-market purchases are kept.

## Usage

```bash
pip install -r requirements.txt

# 1. Build/refresh the ticker universe (fast, ~5-10s)
python run_pipeline.py --stage universe

# 2. Smoke-test on a handful of tickers before committing to a full run
python run_pipeline.py --stage all --limit 20

# 3. Full backfill (~5,900 tickers) -- prices take minutes, fundamentals
#    take tens of minutes since it's one request per ticker to EDGAR.
#    Safe to rerun: prices are incremental, fundamentals just re-upsert.
python run_pipeline.py --stage all
```

### Keeping it updated: recommended cadence

Not every stage needs to run on the same schedule -- the underlying data
changes at very different rates, and running everything every time wastes
most of the run re-fetching things that haven't changed.

| Cadence | Stages | Why |
|---|---|---|
| **Weekly** | `python run_pipeline.py --stage weekly` (bundles universe + prices + moving_averages + price_indicators) | Prices are the only thing that actually changes daily; the universe check is cheap insurance against missing new listings; moving averages/RSI/52-week range are pure local recomputation from prices, so they need to rerun whenever prices do. |
| **Monthly** | `--stage fundamentals`, `--stage insider_transactions` | Companies file quarterly, not weekly -- a full fundamentals refetch costs ~50-60 min for close to zero new information most weeks. SEC's insider-transactions bulk file is also published per quarter with a lag (the "current" quarter often isn't available yet), so a weekly refetch would mostly redownload data that hasn't changed. |
| **Ad hoc** | `python load_congress_trades.py` (after manually adding rows to the CSV) | Not an automated fetch -- see `data/congress_trades/README.md`. |

`--stage prices` (and therefore `weekly`) only pulls each ticker's missing
tail (or full `HISTORY_YEARS` history for a brand-new ticker) rather than
redownloading everything -- see `get_last_price_date` in `db.py`. Every
stage is safe to rerun (upserts, not appends), so there's no harm in
running `--stage all` occasionally to catch up everything at once if the
weekly/monthly cadence has lapsed.

## Schema (`data/db/stocks.db`)

- `tickers` -- symbol, name, exchange, security_type, cik
- `prices` -- symbol, date, open, high, low, close, adj_close, volume
- `moving_averages` -- symbol, date, sma_20, sma_50, sma_100, sma_200 (pure
  local computation from `prices`, no external calls; recomputed in full
  each run via `compute_moving_averages.py`, ~30s for the whole universe)
- `price_indicators` -- symbol, date, rsi_14, high_52w, low_52w,
  pct_from_52w_high, pct_from_52w_low, range_position_52w (also pure local
  computation, `compute_price_indicators.py`, ~30s for the whole universe)
- `fundamentals` -- symbol, metric, fiscal_end, form, value, filed_date
  (metrics: eps_diluted, revenue, net_income, stockholders_equity,
  shares_outstanding, total_assets, total_liabilities, operating_income,
  cash_and_equivalents, operating_cash_flow, current_assets,
  current_liabilities, long_term_debt, gross_profit -- see
  `FUNDAMENTAL_CONCEPTS` in `config.py`)
- `insider_transactions` -- symbol, accession_number, trans_sk, owner_name,
  relationship, trans_date, filed_date, shares, price_per_share, value
  (open-market purchases only; `fetch_insider_transactions.py`)
- `congress_transactions` -- politician_name, chamber, symbol, action,
  amount_range, trans_date, date_precision, disclosed_date, notes,
  source_url (hand-compiled from news coverage, not an automated fetch --
  see `data/congress_trades/README.md`; loaded via `load_congress_trades.py`)
- `fetch_log` -- per-symbol/kind status of the last fetch attempt, for
  diagnosing partial-failure reruns

## Network note

`config.py` calls `truststore.inject_into_ssl()` on import, which routes
Python's HTTPS certificate validation through the OS's native validator
(Windows' CryptoAPI) instead of Python's bundled OpenSSL one. This was
added because some local security software (e.g. antivirus/firewall
products that inspect HTTPS traffic) generates certificates that pass
Windows' own validation but get rejected by OpenSSL's stricter checks --
without this, every HTTPS request from any script in this project would
fail with a certificate error on an affected machine. It's a no-op (and
harmless) on a machine without that kind of software installed.

## Known caveats

- Universe is a free proxy for the broad market, not exact Russell 3000
  membership, and reflects *currently listed* companies -- it will not
  include delisted/failed companies, so any backtest built on it has
  survivorship bias.
- `yfinance` failures are expected at this scale; `fetch_log` records which
  symbols failed so a rerun can be scoped to just those if needed.
- Valuation metrics (P/E, P/B, etc.) aren't precomputed here -- join
  `prices.close` with the relevant `fundamentals` row (e.g. divide price by
  trailing 4-quarter summed `eps_diluted`) in the next layer (screener/model).
