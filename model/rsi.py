"""
RSI (14-day) indicator: short-term overbought/oversold oscillator.

Unlike trend/momentum (which reward strength continuing), RSI is a
MEAN-REVERSION signal -- it deliberately points the OPPOSITE direction
from recent price strength: an overbought (high RSI) stock gets a
bearish rating on the theory it's due for a pullback, an oversold (low
RSI) stock gets a bullish rating on the theory it's due for a bounce.
This is exactly the kind of independent, sometimes-contradicting signal
confluence is meant to catch -- e.g. strong trend + strong momentum +
overbought RSI reads as "running hard, may be due for a pause," not a
contradiction to resolve.

Also worth knowing: RSI's mean-reversion effect is a SHORT-horizon
phenomenon (days to weeks), unlike the other indicators here which were
validated against 1-year forward returns. See validate_indicators.py and
README.md for a test against a ~1-month forward window instead.

Not cross-sectional -- the 30/70 thresholds are universal conventions,
not relative to the rest of the universe on a given date.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

import pandas as pd
from db import get_connection

# (upper bound for this bucket, rating) checked in order, oversold -> bullish
_RSI_TO_RATING = [(30, 1), (45, 2), (55, 3), (70, 4), (float("inf"), 5)]


def _nearest_trading_date(as_of):
    with get_connection() as conn:
        row = conn.execute("SELECT MAX(date) FROM prices WHERE date <= ?", (as_of,)).fetchone()
        return row[0] if row else None


def load_rsi(as_of):
    trading_date = _nearest_trading_date(as_of)
    if not trading_date:
        return pd.DataFrame()
    with get_connection() as conn:
        df = pd.read_sql_query(
            "SELECT symbol, rsi_14 FROM price_indicators WHERE date = ?", conn, params=(trading_date,)
        )
    df["rsi_14"] = pd.to_numeric(df["rsi_14"], errors="coerce")
    df["as_of_trading_date"] = trading_date
    return df


def _rsi_to_rating(rsi):
    if pd.isna(rsi):
        return float("nan")
    for threshold, rating in _RSI_TO_RATING:
        if rsi < threshold:
            return float(rating)
    return 5.0


def rate_rsi(as_of):
    df = load_rsi(as_of)
    if df.empty:
        return df
    df = df.copy()
    df["rsi_rating"] = df["rsi_14"].apply(_rsi_to_rating)
    return df


if __name__ == "__main__":
    from datetime import date

    today = date.today().isoformat()
    rated = rate_rsi(today)
    print(f"as_of trading date: {rated['as_of_trading_date'].iloc[0]}")
    print(f"n with an RSI rating: {rated['rsi_rating'].notna().sum()} / {len(rated)}")
    print(rated["rsi_rating"].value_counts(dropna=False).sort_index())
    print(rated[rated.symbol.isin(["AAPL", "MSFT", "GOOGL", "META", "NVDA"])])
