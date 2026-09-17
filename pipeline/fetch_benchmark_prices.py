"""
Fetch daily close prices for the market-cap-bucket benchmark ETFs
(SPY/MDY/IJR) used by the day-trade model's cap_rotation_momentum feature
(see model/daytrade_confluence_features.py's add_cap_rotation_features).

Deliberately writes to its own `benchmark_prices` table, not `prices` --
snapshot.py and other live-ratings scripts read `FROM prices` with no
symbol allowlist, so these three ETFs would otherwise leak into the live
weekly recommendation output as if they were regular stocks.

Incremental like fetch_prices.py: each symbol only pulls its missing tail
on reruns. First backfill uses FUNDAMENTALS_HISTORY_YEARS (7yr, not the
5yr HISTORY_YEARS stocks use) so the feature's 1-year rolling smoothing
has a year of warm-up buffer before the earliest backtest date.
"""
from datetime import date, timedelta

import yfinance as yf
from tenacity import retry, stop_after_attempt, wait_exponential

from config import FUNDAMENTALS_HISTORY_YEARS
from db import get_last_benchmark_price_date, log_fetch, upsert_benchmark_prices

BENCHMARK_SYMBOLS = ["SPY", "MDY", "IJR"]


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=2, max=30))
def _download(symbol, start):
    return yf.download(symbol, start=start, auto_adjust=True, progress=False)


def _start_date_for(symbol):
    last = get_last_benchmark_price_date(symbol)
    if last:
        return (date.fromisoformat(last) + timedelta(days=1)).isoformat()
    return (date.today() - timedelta(days=365 * FUNDAMENTALS_HISTORY_YEARS)).isoformat()


def fetch_benchmark_prices(symbols=None, verbose=True):
    symbols = symbols or BENCHMARK_SYMBOLS
    today_str = date.today().isoformat()
    ok_count, fail_count = 0, 0

    for symbol in symbols:
        start = _start_date_for(symbol)
        if start > today_str:
            continue  # already up to date
        try:
            df = _download(symbol, start)
        except Exception as e:
            log_fetch(symbol, "benchmark_prices", "error", str(e))
            fail_count += 1
            if verbose:
                print(f"  {symbol} failed from {start}: {e}")
            continue

        if df.empty:
            log_fetch(symbol, "benchmark_prices", "ok")  # incremental: no new trading days is normal
            ok_count += 1
            continue

        # yf.download returns MultiIndex columns (Price, Ticker) even for a
        # single symbol -- squeeze down to a plain Series before iterating.
        close_col = df["Close"]
        if hasattr(close_col, "columns"):
            close_col = close_col.iloc[:, 0]
        rows = [
            {"date": idx.strftime("%Y-%m-%d"), "close": float(v)}
            for idx, v in close_col.items()
            if v == v  # skip NaN
        ]
        upsert_benchmark_prices(symbol, rows)
        log_fetch(symbol, "benchmark_prices", "ok")
        ok_count += 1
        if verbose:
            print(f"  {symbol}: {len(rows)} rows from {start}")

    return {"ok": ok_count, "failed": fail_count}


if __name__ == "__main__":
    from db import init_db

    init_db()
    result = fetch_benchmark_prices()
    print(result)
