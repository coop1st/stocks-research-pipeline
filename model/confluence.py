"""
Confluence model: combines the individual indicator ratings into one
recommendation per ticker.

Weighted by each indicator's *validated* predictive strength (the |IC|
from backtest.py / validate_indicators.py) rather than a naive majority
vote, so a strong, well-evidenced signal (valuation) counts for more than
a weak or unvalidated one (RSI, congress trading). Weights below are
copied from those validation runs -- rerun the backtests periodically
(see the quarterly revalidation cadence) and update these if the picture
changes materially.

| Indicator  | Validated |IC| | In confluence score? |
|------------|----------------|-----------------------|
| Valuation  | 0.26           | Yes                    |
| 52w range  | 0.20           | Yes                    |
| Momentum   | 0.15           | Yes                    |
| Quality    | 0.15           | Yes                    |
| Trend      | 0.09           | Yes                    |
| Insider buy| ~0.035 (mean-return spread, inconsistent sign) | Small fixed nudge, not a full weighted vote |
| RSI        | ~0.00 (sign flips every year)  | No -- shown as context only |
| Congress   | Never validated (42-trade hand-compiled sample) | No -- shown as context only |

A ticker needs at least MIN_CORE_INDICATORS of the 5 weighted indicators
present to get a recommendation at all (avoids a confident-looking score
built from just one or two data points).

Output: recommendation_score, 1-5 (same convention as every individual
indicator -- 1 = most bullish, 5 = most bearish), plus a human-readable
recommendation label (STRONG BUY..STRONG SELL) and bullish/bearish
indicator counts for context.
"""
import pandas as pd

CORE_WEIGHTS = {
    "valuation_rating": 0.26,
    "range52w_rating": 0.20,
    "momentum_rating": 0.15,
    "quality_rating": 0.15,
    "trend_rating": 0.09,
}
MIN_CORE_INDICATORS = 3
INSIDER_BUY_NUDGE = -0.3  # subtracted from the weighted rating (lower = more bullish) when flagged

_LABEL_THRESHOLDS = [  # (max weighted rating for this label, label) -- checked in order
    (1.75, "STRONG BUY"),
    (2.5, "BUY"),
    (3.5, "HOLD"),
    (4.25, "SELL"),
    (float("inf"), "STRONG SELL"),
]


def _label_for(score):
    if pd.isna(score):
        return None
    for threshold, label in _LABEL_THRESHOLDS:
        if score <= threshold:
            return label
    return "STRONG SELL"


def compute_confluence(ratings_df):
    """ratings_df: the merged per-ticker DataFrame from compute_all_ratings.py
    (or anything with the same column names). Adds recommendation_score,
    recommendation, bullish_count, bearish_count, core_indicators_available."""
    df = ratings_df.copy()
    core_cols = list(CORE_WEIGHTS.keys())

    weights = pd.Series(CORE_WEIGHTS)
    # Coerce explicitly: a core column that's entirely missing for every
    # ticker (e.g. moving_averages not yet recomputed for today) comes out
    # of the merge as object dtype full of None rather than float64 NaN,
    # which turns the arithmetic below into elementwise Python division and
    # raises a real ZeroDivisionError for any 0/0 row instead of producing
    # NaN the way vectorized numeric division would.
    values = df[core_cols].apply(pd.to_numeric, errors="coerce")
    available = values.notna()
    df["core_indicators_available"] = available.sum(axis=1)

    weighted_sum = values.fillna(0).mul(weights, axis=1).sum(axis=1)
    weight_total = available.mul(weights, axis=1).sum(axis=1)
    raw_score = (weighted_sum / weight_total).where(weight_total > 0)

    # df.get(..., 0) falls back to a literal int when the column is absent
    # entirely (as opposed to present-but-empty), and int has no .fillna --
    # go through reindex instead so a caller without an insider stage (e.g.
    # an ad-hoc snapshot) gets a proper all-NaN Series rather than a crash.
    insider_nudge = df.reindex(columns=["insider_buying_flag"]).iloc[:, 0].fillna(0) * INSIDER_BUY_NUDGE
    score = (raw_score + insider_nudge).clip(lower=1, upper=5)
    df["recommendation_score"] = score.where(df["core_indicators_available"] >= MIN_CORE_INDICATORS)

    df["bullish_count"] = (values <= 2).sum(axis=1)
    df["bearish_count"] = (values >= 4).sum(axis=1)

    df["recommendation"] = df["recommendation_score"].apply(_label_for)

    return df


if __name__ == "__main__":
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
    from db import get_connection

    with get_connection() as conn:
        latest = conn.execute("SELECT MAX(as_of_date) FROM ratings").fetchone()[0]
        df = pd.read_sql_query("SELECT * FROM ratings WHERE as_of_date = ?", conn, params=(latest,))

    result = compute_confluence(df)
    print(f"as_of {latest}, {result['recommendation'].notna().sum()} tickers with a recommendation")
    print(result["recommendation"].value_counts())
    cols = ["symbol", "recommendation", "recommendation_score", "bullish_count", "bearish_count",
            "valuation_rating", "trend_rating", "momentum_rating", "quality_rating", "range52w_rating"]
    print("\nStrongest buys:")
    print(result.sort_values("recommendation_score").head(15)[cols].to_string(index=False))
    print("\nStrongest sells:")
    print(result.sort_values("recommendation_score", ascending=False).head(15)[cols].to_string(index=False))
