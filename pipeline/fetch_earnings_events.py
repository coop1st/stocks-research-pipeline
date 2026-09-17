"""
Fetch earnings announcement dates, analyst EPS estimates, actual reported
EPS, and surprise % from yfinance's free earnings calendar, for the
post-earnings-announcement-drift (PEAD) day-trade model feature (see
model/daytrade_features.py and model/README.md's "Day trading model"
section).

No API key needed (same free/unofficial Yahoo data everything else in
this pipeline already uses), but it's a per-symbol call with no bulk
equivalent to yf.download() -- roughly 0.5-1s per symbol, so a full
backfill across the ~4,700-symbol liquid universe takes about an hour.
Piggybacks on the existing monthly cadence (see scheduled_run.py) since
earnings are quarterly.
"""
import time
from datetime import time as dtime

import yfinance as yf
from tenacity import retry, stop_after_attempt, wait_exponential

from db import get_connection, log_fetch, upsert_earnings_events

MARKET_OPEN = dtime(9, 30)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=2, max=20))
def _get_earnings_dates(symbol, limit=40):
    return yf.Ticker(symbol).get_earnings_dates(limit=limit)


def _first_trading_day_on_or_after(conn, symbol, calendar_date, strict_after=False):
    op = ">" if strict_after else ">="
    row = conn.execute(
        f"SELECT MIN(date) FROM prices WHERE symbol = ? AND date {op} ?",
        (symbol, calendar_date),
    ).fetchone()
    return row[0] if row and row[0] else None


def _effective_date(conn, symbol, earnings_ts):
    """BMO (before market open): the market already reacted by that same
    day's close, so effective_date is that trading day itself. AMC (after
    close) or any ambiguous/missing time: conservatively treat as AMC --
    the market can't react until the next session, so effective_date is
    the next trading day. Falls back to the calendar date (unadjusted) if
    no matching price row exists yet (e.g. very recent event not yet in
    `prices`) rather than dropping the row."""
    calendar_date = earnings_ts.date().isoformat()
    is_bmo = earnings_ts.time() < MARKET_OPEN
    trading_day = _first_trading_day_on_or_after(conn, symbol, calendar_date, strict_after=not is_bmo)
    return trading_day or calendar_date


def _rows_from_earnings_dates(conn, symbol, ed):
    rows = []
    for earnings_ts, row in ed.iterrows():
        rows.append({
            "earnings_date": earnings_ts.isoformat(),
            "effective_date": _effective_date(conn, symbol, earnings_ts),
            "eps_estimate": float(row["EPS Estimate"]) if row["EPS Estimate"] == row["EPS Estimate"] else None,
            "reported_eps": float(row["Reported EPS"]) if row["Reported EPS"] == row["Reported EPS"] else None,
            "surprise_pct": float(row["Surprise(%)"]) if row["Surprise(%)"] == row["Surprise(%)"] else None,
        })
    return rows


def fetch_earnings_events_for(symbols, verbose=True):
    ok_count, fail_count, empty_count = 0, 0, 0
    with get_connection() as conn:
        for symbol in symbols:
            try:
                ed = _get_earnings_dates(symbol)
                if ed is None or ed.empty:
                    log_fetch(symbol, "earnings", "empty_result")
                    empty_count += 1
                    continue
                rows = _rows_from_earnings_dates(conn, symbol, ed)
                upsert_earnings_events(symbol, rows)
                log_fetch(symbol, "earnings", "ok")
                ok_count += 1
                if verbose:
                    print(f"  {symbol}: {len(rows)} earnings events")
            except Exception as e:
                log_fetch(symbol, "earnings", "error", str(e))
                fail_count += 1
                if verbose:
                    print(f"  {symbol}: failed - {e}")

    return {"ok": ok_count, "failed": fail_count, "empty": empty_count}


if __name__ == "__main__":
    import config  # noqa: F401 -- applies truststore/curl_cffi cert fixes
    from db import init_db

    init_db()
    start = time.time()
    result = fetch_earnings_events_for(["AAPL", "DDOG", "AACG"], verbose=True)
    print(f"result: {result}, elapsed: {time.time() - start:.1f}s")
