"""
Daily stock-price ledger update -- runs as a step of the cloud day-trade
routine (see cloud/daytrade_shortlist.py, which this follows), separate
from the RSI/valuation/quality shortlist logic itself.

Purpose: keep one running record, across the whole life of the day-trade
email, of every stock that has ever been suggested and the price it was
suggested at on each day -- so a future project (options trading, built
separately) can look back months/years later and compute how a
suggested stock actually moved from its suggestion price.

Ledger file: data/github_sync/daytrade_ledger/stock_price_ledger.csv
(committed to the repo -- the cloud sandbox has no other persistent
storage between runs, and the ledger needs to survive and accumulate
across every run).

Columns:
  symbol             -- ticker, one row per ticker ever suggested
  company_name       -- most recently seen company name for this ticker
  current_price      -- the price from the most recent date column that
                         has a value for this ticker (i.e. the last time
                         it was actually suggested, not necessarily today)
  count_last_30_days -- how many of the last 30 calendar days' date
                         columns have a price for this ticker (recomputed
                         fresh every run, since it's a rolling window --
                         while the ledger has fewer than 30 days of
                         history, this is just a running total, which
                         falls out naturally from the same rolling-window
                         formula)
  <date columns>      -- one column per calendar date starting
                         2026-08-21 (the first day the email included a
                         price), in order, one for every day since then
                         whether or not that day produced an email (a
                         no-run day, e.g. a weekend or a missed weekday,
                         just leaves that column blank for everyone) --
                         populated with the price for any ticker
                         suggested that day, blank otherwise.

Self-contained (no imports from the rest of this repo), matching
cloud/daytrade_shortlist.py's convention -- must run correctly in an
ephemeral cloud sandbox that only has this repo cloned.

Run from the repo root, after cloud/daytrade_shortlist.py has produced
today's shortlist: `python cloud/update_stock_ledger.py`
"""
import csv
import os
import sys
from datetime import date, timedelta

SHORTLIST_PATH = "data/cache/daytrade_shortlist_today.csv"
LEDGER_PATH = "data/github_sync/daytrade_ledger/stock_price_ledger.csv"
LEDGER_START_DATE = date(2026, 8, 21)
ROLLING_WINDOW_DAYS = 30
FIXED_COLS = ["symbol", "company_name", "current_price", "count_last_30_days"]


def _date_range(start, end):
    cols = []
    d = start
    while d <= end:
        cols.append(d.isoformat())
        d += timedelta(days=1)
    return cols


def load_ledger():
    if not os.path.exists(LEDGER_PATH):
        return {}
    with open(LEDGER_PATH, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = {}
        for row in reader:
            rows[row["symbol"]] = row
        return rows


def load_shortlist():
    if not os.path.exists(SHORTLIST_PATH):
        print(f"LEDGER_ERROR shortlist file not found: {SHORTLIST_PATH}")
        sys.exit(1)
    with open(SHORTLIST_PATH, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader)


def main():
    today = date.today()
    today_str = today.isoformat()

    shortlist_rows = load_shortlist()
    ledger = load_ledger()

    date_cols = _date_range(LEDGER_START_DATE, today)

    new_tickers = 0
    for row in shortlist_rows:
        symbol = row["symbol"]
        company_name = row["company_name"]
        last_close = row.get("last_close", "")
        if symbol not in ledger:
            ledger[symbol] = {c: "" for c in FIXED_COLS + date_cols}
            ledger[symbol]["symbol"] = symbol
            new_tickers += 1
        ledger[symbol]["company_name"] = company_name
        ledger[symbol][today_str] = last_close
        ledger[symbol]["current_price"] = last_close

    window_start = today - timedelta(days=ROLLING_WINDOW_DAYS - 1)
    window_cols = [c for c in date_cols if date.fromisoformat(c) >= window_start]

    for symbol, row in ledger.items():
        # Backfill any date columns this ticker's existing row predates
        # (new date columns since the ledger was last written) with "".
        for c in date_cols:
            row.setdefault(c, "")
        row["count_last_30_days"] = sum(1 for c in window_cols if row.get(c))

    os.makedirs(os.path.dirname(LEDGER_PATH), exist_ok=True)
    header = FIXED_COLS + date_cols
    with open(LEDGER_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=header)
        writer.writeheader()
        for symbol in sorted(ledger.keys()):
            writer.writerow({k: ledger[symbol].get(k, "") for k in header})

    print(f"LEDGER_UPDATED rows={len(ledger)} date={today_str} new_tickers={new_tickers} "
          f"suggested_today={len(shortlist_rows)}")


if __name__ == "__main__":
    main()
