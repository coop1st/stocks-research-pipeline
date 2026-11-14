"""
Compute RSI-14, 52-week high/low range metrics, short-horizon volatility,
and a liquidity proxy from stored daily prices, written to the
`price_indicators` table.

Pure local computation (no external calls), same idea as
compute_moving_averages.py -- cheap enough to always recompute in full
from `prices` rather than doing it incrementally.

Uses adjusted close, not raw close, for RSI/52-week-range/volatility: all
three are about genuine price-level moves over the lookback window, and a
stock split partway through would otherwise show up as a fake price
cliff. dollar_volume_avg_20d uses raw close instead (it's about actual
traded value on that date, not a normalized price series, and 20 days is
short enough that split risk is negligible -- same reasoning
moving_averages.py uses raw close for its shorter windows).

volatility_3d/7d are daily-frequency (not annualized) trailing stdev of
daily pct returns -- feeds a next-day forecasting model, not a
portfolio-vol comparison, so annualizing would just be an extra
conversion nobody downstream needs.
"""
import sqlite3
import time

import pandas as pd

from config import DB_PATH

RSI_PERIOD = 14
LOOKBACK_52W = 252  # ~1 trading year
MIN_PERIODS_52W = 63  # ~1 quarter -- below this a "52-week" high/low is too thin to mean much
VOLATILITY_WINDOWS = (3, 7)  # trading days
DOLLAR_VOLUME_WINDOW = 20  # trading days


def _rsi(adj_close):
    delta = adj_close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    # Wilder's smoothing == EMA with alpha = 1/period
    avg_gain = gain.ewm(alpha=1 / RSI_PERIOD, adjust=False, min_periods=RSI_PERIOD).mean()
    avg_loss = loss.ewm(alpha=1 / RSI_PERIOD, adjust=False, min_periods=RSI_PERIOD).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def compute_and_store(chunk_size=20000):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")

    print("[price_indicators] loading prices...")
    df = pd.read_sql_query(
        "SELECT symbol, date, close, adj_close, volume FROM prices WHERE adj_close > 0 ORDER BY symbol, date", conn
    )
    print(f"[price_indicators] {len(df)} price rows loaded, computing indicators...")

    df["date"] = pd.to_datetime(df["date"])
    g_adj = df.groupby("symbol")["adj_close"]

    df["rsi_14"] = g_adj.transform(_rsi)
    df["high_52w"] = g_adj.transform(lambda s: s.rolling(window=LOOKBACK_52W, min_periods=MIN_PERIODS_52W).max())
    df["low_52w"] = g_adj.transform(lambda s: s.rolling(window=LOOKBACK_52W, min_periods=MIN_PERIODS_52W).min())

    df["pct_from_52w_high"] = df["adj_close"] / df["high_52w"] - 1  # <= 0
    df["pct_from_52w_low"] = df["adj_close"] / df["low_52w"] - 1    # >= 0
    range_size = df["high_52w"] - df["low_52w"]
    df["range_position_52w"] = ((df["adj_close"] - df["low_52w"]) / range_size).where(range_size > 0)

    for window in VOLATILITY_WINDOWS:
        df[f"volatility_{window}d"] = g_adj.transform(
            lambda s, w=window: s.pct_change().rolling(window=w, min_periods=w).std()
        )

    df["_dollar_volume"] = df["close"] * df["volume"]
    df["dollar_volume_avg_20d"] = df.groupby("symbol")["_dollar_volume"].transform(
        lambda s: s.rolling(window=DOLLAR_VOLUME_WINDOW, min_periods=DOLLAR_VOLUME_WINDOW).mean()
    )
    df = df.drop(columns=["_dollar_volume"])

    out_cols = [
        "rsi_14", "high_52w", "low_52w", "pct_from_52w_high", "pct_from_52w_low", "range_position_52w",
        "volatility_3d", "volatility_7d", "dollar_volume_avg_20d",
    ]
    df = df.dropna(how="all", subset=out_cols)
    df["date"] = df["date"].dt.strftime("%Y-%m-%d")
    print(f"[price_indicators] {len(df)} rows have at least one indicator populated, writing...")

    cols = ["symbol", "date", *out_cols]
    rows = df[cols].where(pd.notna(df[cols]), None).values.tolist()
    placeholders = ",".join("?" * len(cols))
    # Explicit transaction, same reasoning as compute_moving_averages.py: a
    # crash mid-write rolls back to the previous complete table rather than
    # leaving it empty or half-populated.
    try:
        conn.execute("DELETE FROM price_indicators")
        cur = conn.cursor()
        for i in range(0, len(rows), chunk_size):
            cur.executemany(
                f"INSERT INTO price_indicators ({','.join(cols)}) VALUES ({placeholders})",
                rows[i : i + chunk_size],
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    print(f"[price_indicators] done, {len(rows)} rows stored")
    return len(rows)


if __name__ == "__main__":
    from db import init_db

    init_db()
    start = time.time()
    compute_and_store()
    print(f"Elapsed: {time.time() - start:.1f}s")
