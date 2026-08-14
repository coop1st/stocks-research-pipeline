"""
Compute simple moving averages (20/50/100/200-day) from stored daily closes
and write them to the `moving_averages` table.

This is a pure local computation (no external calls), so it just recomputes
from whatever's in `prices` -- cheap enough to always run against the full
table rather than trying to do it incrementally. Each SMA is a trailing
window ending on that date (no lookahead), so sma_20 first appears on a
symbol's 20th trading day, sma_200 on its 200th, etc.
"""
import sqlite3
import time

import pandas as pd

from config import DB_PATH

WINDOWS = (20, 50, 100, 200)


def compute_and_store(chunk_size=20000):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")

    print("[moving_averages] loading prices...")
    df = pd.read_sql_query("SELECT symbol, date, close FROM prices ORDER BY symbol, date", conn)
    print(f"[moving_averages] {len(df)} price rows loaded, computing SMAs...")

    df["date"] = pd.to_datetime(df["date"])
    sma_cols = []
    for w in WINDOWS:
        col = f"sma_{w}"
        df[col] = df.groupby("symbol")["close"].transform(
            lambda s: s.rolling(window=w, min_periods=w).mean()
        )
        sma_cols.append(col)

    df = df.dropna(how="all", subset=sma_cols)
    df["date"] = df["date"].dt.strftime("%Y-%m-%d")
    print(f"[moving_averages] {len(df)} rows have at least one SMA populated, writing...")

    rows = df[["symbol", "date", *sma_cols]].where(pd.notna(df[["symbol", "date", *sma_cols]]), None).values.tolist()
    # Explicit transaction: DELETE + all INSERTs either all land or none do,
    # so a crash/kill mid-write leaves the previous (stale but complete)
    # table intact instead of an empty or half-populated one.
    try:
        conn.execute("DELETE FROM moving_averages")
        cur = conn.cursor()
        for i in range(0, len(rows), chunk_size):
            cur.executemany(
                "INSERT INTO moving_averages (symbol, date, sma_20, sma_50, sma_100, sma_200) VALUES (?, ?, ?, ?, ?, ?)",
                rows[i : i + chunk_size],
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    print(f"[moving_averages] done, {len(rows)} rows stored")
    return len(rows)


if __name__ == "__main__":
    from db import init_db

    init_db()
    start = time.time()
    compute_and_store()
    print(f"Elapsed: {time.time() - start:.1f}s")
