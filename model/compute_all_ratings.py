"""
Compute today's rating from every indicator and persist one row per
ticker into the `ratings` table.

This is the piece that makes "recalculate everything on the new data"
complete: the weekly pipeline update refreshes prices/moving_averages/
price_indicators, but those are raw inputs -- the actual 1-5 scores
(valuation, trend, momentum, quality, RSI, 52-week range) and the
insider/congress flags were previously only ever computed on demand when
you ran each model/*.py script by hand. This script runs all of them
against the same as-of date and writes the result to one wide table, so
after a scheduled run there's a ready-to-query current snapshot rather
than needing to invoke 8 separate scripts.

Run standalone or via scheduled_run.py (wired in as the last weekly step).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

import pandas as pd
from db import get_connection

from confluence import compute_confluence
from congress_flag import load_congress_transactions, rate_congress_buying, rate_congress_selling
from insider_flag import rate_insider_buying
from momentum import rate_momentum
from quality import build_piotroski
from range52w import rate_range52w
from rate_universe import rate_today
from rsi import rate_rsi
from trend import rate_trend

RATINGS_SCHEMA = """
CREATE TABLE IF NOT EXISTS ratings (
    symbol TEXT NOT NULL,
    as_of_date TEXT NOT NULL,
    valuation_rating REAL,
    valuation_composite REAL,
    trend_rating REAL,
    golden_cross INTEGER,
    death_cross INTEGER,
    momentum_rating REAL,
    quality_rating REAL,
    rsi_rating REAL,
    rsi_14 REAL,
    range52w_rating REAL,
    range_position_52w REAL,
    insider_buying_flag INTEGER,
    insider_cluster_buy_count INTEGER,
    congress_buy_flag INTEGER,
    congress_sell_flag INTEGER,
    recommendation TEXT,
    recommendation_score REAL,
    bullish_count INTEGER,
    bearish_count INTEGER,
    core_indicators_available INTEGER,
    PRIMARY KEY (symbol, as_of_date)
);
"""


def _left_merge(base, other, cols, rename=None):
    other = other[["symbol", *cols]].copy()
    if rename:
        other = other.rename(columns=rename)
    return base.merge(other, on="symbol", how="left")


def compute_and_store():
    with get_connection() as conn:
        conn.executescript(RATINGS_SCHEMA)

    valuation, as_of_date, _weights, _avg_ic = rate_today()

    merged = valuation[["symbol", "rating", "composite"]].rename(
        columns={"rating": "valuation_rating", "composite": "valuation_composite"}
    )

    trend = rate_trend(as_of_date)
    if not trend.empty:
        merged = _left_merge(merged, trend, ["trend_rating", "golden_cross", "death_cross"])

    momentum = rate_momentum(as_of_date)
    if not momentum.empty:
        merged = _left_merge(merged, momentum, ["momentum_rating"])

    quality = build_piotroski(as_of_date)
    if not quality.empty:
        merged = _left_merge(merged, quality, ["quality_rating"])

    rsi = rate_rsi(as_of_date)
    if not rsi.empty:
        merged = _left_merge(merged, rsi, ["rsi_rating", "rsi_14"])

    range52w = rate_range52w(as_of_date)
    if not range52w.empty:
        merged = _left_merge(merged, range52w, ["range52w_rating", "range_position_52w"])

    insider = rate_insider_buying(as_of_date)
    if not insider.empty:
        merged = _left_merge(
            merged, insider, ["insider_buying_flag", "cluster_buy_count"],
            rename={"cluster_buy_count": "insider_cluster_buy_count"},
        )

    congress_trans = load_congress_transactions()
    congress_buy = rate_congress_buying(as_of_date, trans_df=congress_trans)
    if not congress_buy.empty:
        merged = _left_merge(merged, congress_buy, ["congress_buy_flag"])
    congress_sell = rate_congress_selling(as_of_date, trans_df=congress_trans)
    if not congress_sell.empty:
        merged = _left_merge(merged, congress_sell, ["congress_sell_flag"])

    for c in ("golden_cross", "death_cross"):
        if c in merged.columns:
            merged[c] = merged[c].fillna(False).astype(int)
    for c in ("insider_buying_flag", "congress_buy_flag", "congress_sell_flag"):
        if c in merged.columns:
            merged[c] = merged[c].fillna(0).astype(int)

    # Any columns from indicators with zero flagged/qualifying rows this
    # week (e.g. no insider buys at all) won't exist yet -- backfill so the
    # schema is always complete regardless of which indicators had hits.
    for col in (
        "trend_rating", "golden_cross", "death_cross", "momentum_rating", "quality_rating",
        "rsi_rating", "rsi_14", "range52w_rating", "range_position_52w",
        "insider_buying_flag", "insider_cluster_buy_count", "congress_buy_flag", "congress_sell_flag",
    ):
        if col not in merged.columns:
            merged[col] = 0 if col.endswith("_flag") or col in ("golden_cross", "death_cross") else None

    merged["as_of_date"] = as_of_date
    merged = compute_confluence(merged)

    cols = [
        "symbol", "as_of_date", "valuation_rating", "valuation_composite", "trend_rating",
        "golden_cross", "death_cross", "momentum_rating", "quality_rating", "rsi_rating", "rsi_14",
        "range52w_rating", "range_position_52w", "insider_buying_flag", "insider_cluster_buy_count",
        "congress_buy_flag", "congress_sell_flag",
        "recommendation", "recommendation_score", "bullish_count", "bearish_count", "core_indicators_available",
    ]
    merged = merged[cols]

    with get_connection() as conn:
        conn.execute("DELETE FROM ratings WHERE as_of_date = ?", (as_of_date,))
        conn.executemany(
            f"INSERT INTO ratings ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",
            merged.where(pd.notna(merged), None).values.tolist(),
        )

    return len(merged), as_of_date


if __name__ == "__main__":
    n, as_of_date = compute_and_store()
    print(f"Stored ratings for {n} tickers as of {as_of_date}")
