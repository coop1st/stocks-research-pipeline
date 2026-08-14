"""
52-week high/low indicator.

Exposes two standalone normalized metrics for use in a scorecard:
- pct_from_52w_high: (price / 52w_high) - 1, always <= 0 (0 = sitting at
  the high, more negative = further below it)
- pct_from_52w_low: (price / 52w_low) - 1, always >= 0 (0 = sitting at
  the low, more positive = further above it)

...plus a combined range_position_52w in [0, 1] (0 = at the 52-week low,
1 = at the 52-week high) that the 1-5 rating is built from.

Unlike RSI, this rewards STRENGTH continuing: the "52-week high effect"
(George & Hwang 2004) is a reasonably well-documented academic finding
that stocks near their 52-week high tend to keep outperforming, more than
plain momentum explains -- same direction as the momentum indicator,
opposite of RSI's mean-reversion framing. So "overbought RSI + near
52-week high" isn't a contradiction to resolve, it's "strong and possibly
due for a pause" vs. RSI alone reading as "reversing."

Not cross-sectional -- like trend and RSI, this is about the stock's own
trailing range, not relative to peers.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

import pandas as pd
from db import get_connection

_RANGE_COLS = ["high_52w", "low_52w", "pct_from_52w_high", "pct_from_52w_low", "range_position_52w"]
# (upper bound for this bucket, rating) checked in order, near-low -> bearish
_RANGE_TO_RATING = [(0.2, 5), (0.4, 4), (0.6, 3), (0.8, 2), (float("inf"), 1)]


def _nearest_trading_date(as_of):
    with get_connection() as conn:
        row = conn.execute("SELECT MAX(date) FROM prices WHERE date <= ?", (as_of,)).fetchone()
        return row[0] if row else None


def load_range52w(as_of):
    trading_date = _nearest_trading_date(as_of)
    if not trading_date:
        return pd.DataFrame()
    with get_connection() as conn:
        df = pd.read_sql_query(
            f"SELECT symbol, {', '.join(_RANGE_COLS)} FROM price_indicators WHERE date = ?",
            conn, params=(trading_date,),
        )
    for c in _RANGE_COLS:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["as_of_trading_date"] = trading_date
    return df


def _range_to_rating(pos):
    if pd.isna(pos):
        return float("nan")
    for threshold, rating in _RANGE_TO_RATING:
        if pos < threshold:
            return float(rating)
    return 1.0


def rate_range52w(as_of):
    df = load_range52w(as_of)
    if df.empty:
        return df
    df = df.copy()
    df["range52w_rating"] = df["range_position_52w"].apply(_range_to_rating)
    return df


if __name__ == "__main__":
    from datetime import date

    today = date.today().isoformat()
    rated = rate_range52w(today)
    print(f"as_of trading date: {rated['as_of_trading_date'].iloc[0]}")
    print(f"n with a range52w rating: {rated['range52w_rating'].notna().sum()} / {len(rated)}")
    print(rated["range52w_rating"].value_counts(dropna=False).sort_index())
    cols = ["symbol", "pct_from_52w_high", "pct_from_52w_low", "range_position_52w", "range52w_rating"]
    print(rated[rated.symbol.isin(["AAPL", "MSFT", "GOOGL", "META", "NVDA"])][cols])
