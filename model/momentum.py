"""
Momentum indicator: trailing total return, ranked cross-sectionally against
the rest of the universe (same relative convention as the valuation score).

Rating: 1 = strongest momentum (top quintile, bullish), 5 = weakest
(bottom quintile, bearish). Uses adjusted close (splits + dividends), same
reasoning as the valuation model's forward_return calc.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

import pandas as pd

from snapshot import MIN_PRICE, load_prices, nearest_trading_date

LOOKBACK_MONTHS = (6, 12)


def compute_returns(as_of, price_df=None, lookback_months=LOOKBACK_MONTHS):
    price_df = price_df if price_df is not None else load_prices()
    all_dates = sorted(price_df["date"].unique())
    as_of_td = nearest_trading_date(all_dates, as_of)

    cur_rows = price_df[price_df.date == as_of_td].set_index("symbol")
    out = cur_rows[["close", "adj_close"]].rename(columns={"close": "price", "adj_close": "adj_price"})
    out = out[out["price"] >= MIN_PRICE]

    for m in lookback_months:
        past_date = (pd.Timestamp(as_of) - pd.DateOffset(months=m)).strftime("%Y-%m-%d")
        past_td = nearest_trading_date(all_dates, past_date)
        if past_td is None:
            out[f"return_{m}m"] = None
            continue
        past_adj = price_df[price_df.date == past_td].set_index("symbol")["adj_close"].rename(f"adj_{m}m_ago")
        out = out.join(past_adj)
        out[f"return_{m}m"] = out["adj_price"] / out[f"adj_{m}m_ago"] - 1

    out.index.name = "symbol"
    out["as_of_trading_date"] = as_of_td
    return out.reset_index()


def score_momentum(df, cols=tuple(f"return_{m}m" for m in LOOKBACK_MONTHS), n_buckets=5):
    """Cross-sectional percentile composite of the given return columns,
    bucketed so the HIGHEST momentum gets rating 1.

    Uses fixed-width bins on the composite (already a 0-1 percentile
    average) rather than qcut: qcut derives bin *edges* from the data,
    which errors out ("bin labels must be one fewer than bin edges") when
    enough ties collapse the number of distinct edges below n_buckets --
    fixed-width bins on an already-uniform-ish [0,1] score don't have that
    failure mode.
    """
    df = df.copy()
    pctile_cols = []
    for c in cols:
        pc = f"{c}_pctile"
        df[pc] = df[c].rank(pct=True)
        pctile_cols.append(pc)

    df["momentum_composite"] = df[pctile_cols].mean(axis=1, skipna=True)
    valid = df["momentum_composite"].notna()
    labels = list(range(n_buckets, 0, -1))
    edges = [i / n_buckets for i in range(n_buckets + 1)]
    edges[0], edges[-1] = -0.001, 1.001  # inclusive of exact 0.0 and 1.0
    df.loc[valid, "momentum_rating"] = pd.cut(
        df.loc[valid, "momentum_composite"], bins=edges, labels=labels
    ).astype(float)
    return df


def rate_momentum(as_of, price_df=None):
    return score_momentum(compute_returns(as_of, price_df=price_df))


if __name__ == "__main__":
    from datetime import date

    today = date.today().isoformat()
    rated = rate_momentum(today)
    print(f"as_of trading date: {rated['as_of_trading_date'].iloc[0]}")
    print(f"n with a momentum rating: {rated['momentum_rating'].notna().sum()} / {len(rated)}")
    print(rated["momentum_rating"].value_counts(dropna=False).sort_index())
    cols = ["symbol", "price", "return_6m", "return_12m", "momentum_rating"]
    print(rated[rated.symbol.isin(["AAPL", "MSFT", "GOOGL", "META", "NVDA"])][cols])
