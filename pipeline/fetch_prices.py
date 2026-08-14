"""
Fetch daily OHLCV history from Yahoo Finance (via yfinance) and upsert into
the local SQLite `prices` table.

Incremental: on reruns, each ticker only pulls data since its last stored
date (falls back to full HISTORY_YEARS backfill for new tickers).

Note: yfinance is an unofficial wrapper around Yahoo's endpoints, not a
documented/guaranteed API. It can break or get rate-limited without notice --
this module batches requests and retries with backoff to be a reasonable
citizen, but treat failures as expected on a full-universe run and just
rerun the pipeline later to pick up what failed (fetch_log tracks status).
"""
import time
from datetime import date, timedelta

import pandas as pd
import yfinance as yf
from tenacity import retry, stop_after_attempt, wait_exponential

from config import HISTORY_YEARS, PRICE_BATCH_PAUSE_SECONDS, PRICE_BATCH_SIZE
from db import get_last_price_date, log_fetch, upsert_prices


def _chunk(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=2, max=30))
def _download(tickers, start):
    return yf.download(
        tickers,
        start=start,
        group_by="ticker",
        auto_adjust=False,
        threads=True,
        progress=False,
    )


def _rows_from_frame(df):
    rows = []
    for idx, r in df.iterrows():
        if pd.isna(r.get("Close")):
            continue
        rows.append({
            "date": idx.strftime("%Y-%m-%d"),
            "open": float(r["Open"]) if pd.notna(r["Open"]) else None,
            "high": float(r["High"]) if pd.notna(r["High"]) else None,
            "low": float(r["Low"]) if pd.notna(r["Low"]) else None,
            "close": float(r["Close"]) if pd.notna(r["Close"]) else None,
            "adj_close": float(r["Adj Close"]) if pd.notna(r["Adj Close"]) else None,
            "volume": int(r["Volume"]) if pd.notna(r["Volume"]) else None,
        })
    return rows


def _start_date_for(symbol):
    last = get_last_price_date(symbol)
    if last:
        return (date.fromisoformat(last) + timedelta(days=1)).isoformat()
    return (date.today() - timedelta(days=365 * HISTORY_YEARS)).isoformat()


def _yf_symbol(symbol):
    """Yahoo Finance uses a hyphen for share classes (BRK-B) where
    Nasdaq/SEC use a period (BRK.B). Translate only for the outbound query;
    everything is stored under the original canonical symbol."""
    return symbol.replace(".", "-")


def fetch_prices_for(symbols, verbose=True):
    """Fetch and store price history for a list of ticker symbols.
    Groups tickers by required start date so a batch download covers each
    ticker's actual gap (new tickers get full history, existing ones just
    the missing tail)."""
    by_start = {}
    is_fresh = {}
    for sym in symbols:
        last = get_last_price_date(sym)
        is_fresh[sym] = last is None
        start = (date.fromisoformat(last) + timedelta(days=1)).isoformat() if last \
            else (date.today() - timedelta(days=365 * HISTORY_YEARS)).isoformat()
        by_start.setdefault(start, []).append(sym)

    today_str = date.today().isoformat()
    ok_count, fail_count = 0, 0

    for start, syms in by_start.items():
        if start > today_str:
            continue  # already up to date
        for batch in _chunk(syms, PRICE_BATCH_SIZE):
            yf_to_sym = {_yf_symbol(sym): sym for sym in batch}
            yf_symbols = list(yf_to_sym.keys())
            try:
                data = _download(yf_symbols, start)
            except Exception as e:
                for sym in batch:
                    log_fetch(sym, "prices", "error", str(e))
                fail_count += len(batch)
                if verbose:
                    print(f"  batch failed ({len(batch)} tickers) from {start}: {e}")
                time.sleep(PRICE_BATCH_PAUSE_SECONDS)
                continue

            for yf_sym, sym in yf_to_sym.items():
                try:
                    if len(batch) == 1:
                        sym_df = data
                    else:
                        if yf_sym not in data.columns.get_level_values(0):
                            raise ValueError("no data returned")
                        sym_df = data[yf_sym]
                    rows = _rows_from_frame(sym_df)
                    if rows:
                        upsert_prices(sym, rows)
                        log_fetch(sym, "prices", "ok")
                        ok_count += 1
                    elif is_fresh[sym]:
                        # A brand-new ticker with zero rows back from a 5-year
                        # request is suspicious (wrong symbol, delisted, etc.)
                        # -- flag instead of silently counting as ok.
                        log_fetch(sym, "prices", "empty_result", "no rows returned for fresh backfill")
                        fail_count += 1
                    else:
                        log_fetch(sym, "prices", "ok")  # incremental: no new trading days is normal
                        ok_count += 1
                except Exception as e:
                    log_fetch(sym, "prices", "error", str(e))
                    fail_count += 1

            if verbose:
                print(f"  fetched batch of {len(batch)} from {start} (ok={ok_count} fail={fail_count})")
            time.sleep(PRICE_BATCH_PAUSE_SECONDS)

    return {"ok": ok_count, "failed": fail_count}


if __name__ == "__main__":
    import sys

    test_symbols = sys.argv[1:] or ["AAPL", "MSFT", "NVDA"]
    from db import init_db

    init_db()
    result = fetch_prices_for(test_symbols)
    print(result)
