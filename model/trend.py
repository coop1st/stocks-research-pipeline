"""
Trend indicator: price position relative to its own moving averages.

Unlike the valuation/momentum scores, this is NOT cross-sectional -- a
stock is either above or below its own 200-day average regardless of what
peers are doing, so there's no "ranked against the universe" step here.
Rating convention matches the rest of the project: 1 = strong uptrend
(bullish), 5 = strong downtrend (bearish).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

import pandas as pd
from db import get_connection

# 4-point bull/bear checklist -> trend_points 0-4 -> trend_rating 5..1
_POINTS_TO_RATING = {4: 1, 3: 2, 2: 3, 1: 4, 0: 5}


def _nearest_trading_date(as_of):
    with get_connection() as conn:
        row = conn.execute("SELECT MAX(date) FROM prices WHERE date <= ?", (as_of,)).fetchone()
        return row[0] if row else None


def load_trend_inputs(as_of):
    trading_date = _nearest_trading_date(as_of)
    if not trading_date:
        return pd.DataFrame()
    with get_connection() as conn:
        prices = pd.read_sql_query(
            "SELECT symbol, close AS price FROM prices WHERE date = ?", conn, params=(trading_date,)
        )
        mas = pd.read_sql_query(
            "SELECT symbol, sma_20, sma_50, sma_100, sma_200 FROM moving_averages WHERE date = ?",
            conn, params=(trading_date,),
        )
    df = prices.merge(mas, on="symbol", how="left")
    df["as_of_trading_date"] = trading_date
    return df


def score_trend(df):
    """Adds trend_points (0-4) and trend_rating (1-5). Requires sma_20,
    sma_50, sma_200 all present -- newer tickers without 200 days of
    history yet get NaN (not enough data to call a trend)."""
    df = df.copy()
    have_data = df[["price", "sma_20", "sma_50", "sma_200"]].notna().all(axis=1)

    checks = [
        df["price"] > df["sma_200"],   # price above long-term average
        df["sma_50"] > df["sma_200"],  # golden-cross state (not death-cross)
        df["price"] > df["sma_50"],    # price above medium-term average
        df["sma_20"] > df["sma_50"],   # short-term average turning up
    ]
    points = sum(c.fillna(False).astype(int) for c in checks)
    df["trend_points"] = points.where(have_data)
    df["trend_rating"] = df["trend_points"].map(_POINTS_TO_RATING)
    return df


def detect_recent_cross(as_of, lookback_days=15):
    """golden_cross / death_cross: did sma_50 vs sma_200 flip sign within
    the trailing `lookback_days` trading days? A simple earliest-vs-latest
    sign comparison -- good enough to flag "this just happened", not meant
    to catch every flip-flop inside the window."""
    with get_connection() as conn:
        date_rows = conn.execute(
            "SELECT DISTINCT date FROM moving_averages WHERE date <= ? ORDER BY date DESC LIMIT ?",
            (as_of, lookback_days),
        ).fetchall()
        dates = [r[0] for r in date_rows]
        if len(dates) < 2:
            return pd.DataFrame(columns=["symbol", "golden_cross", "death_cross"])
        placeholders = ",".join("?" * len(dates))
        df = pd.read_sql_query(
            f"SELECT symbol, date, sma_50, sma_200 FROM moving_averages WHERE date IN ({placeholders})",
            conn, params=dates,
        )

    df = df.dropna(subset=["sma_50", "sma_200"]).sort_values(["symbol", "date"])

    def flag(g):
        sign = g["sma_50"] > g["sma_200"]
        return pd.Series({
            "golden_cross": bool((not sign.iloc[0]) and sign.iloc[-1]),
            "death_cross": bool(sign.iloc[0] and (not sign.iloc[-1])),
        })

    if df.empty:
        return pd.DataFrame(columns=["symbol", "golden_cross", "death_cross"])
    return df.groupby("symbol").apply(flag, include_groups=False).reset_index()


def rate_trend(as_of, lookback_days=15):
    df = score_trend(load_trend_inputs(as_of))
    crosses = detect_recent_cross(as_of, lookback_days=lookback_days)
    if not crosses.empty:
        df = df.merge(crosses, on="symbol", how="left")
        df["golden_cross"] = df["golden_cross"].fillna(False)
        df["death_cross"] = df["death_cross"].fillna(False)
    return df


if __name__ == "__main__":
    from datetime import date

    today = date.today().isoformat()
    rated = rate_trend(today)
    print(f"as_of trading date: {rated['as_of_trading_date'].iloc[0]}")
    print(f"n with a trend rating: {rated['trend_rating'].notna().sum()} / {len(rated)}")
    print(rated["trend_rating"].value_counts(dropna=False).sort_index())
    print(f"recent golden crosses: {rated['golden_cross'].sum()}, death crosses: {rated['death_cross'].sum()}")
    print(rated[rated.symbol.isin(["AAPL", "MSFT", "GOOGL", "META", "NVDA"])])
