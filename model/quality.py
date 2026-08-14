"""
Quality indicator: Piotroski F-Score.

A 9-point checklist across profitability, leverage/liquidity, and
operating efficiency, each comparing the most recent annual (10-K) figures
against the prior year's. Not cross-sectional -- like trend, this is about
a single company's own trajectory (improving vs deteriorating), independent
of what peers are doing. This is the standard academic answer to "how do
you avoid a value trap" -- a stock that's statistically cheap AND
deteriorating on this checklist is a much weaker case than one that's
cheap AND improving.

Rating convention matches the rest of the project: 1 = strong improvement
(bullish), 5 = strong deterioration (bearish). Uses the *normalized* score
(true_count / answerable_count * 9) since coverage of the underlying XBRL
tags isn't uniform -- a ticker missing e.g. long_term_debt still gets
scored off the other 8 criteria rather than being penalized for a data gap.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

import pandas as pd

from snapshot import load_fundamentals

METRICS = (
    "net_income", "total_assets", "operating_cash_flow", "current_assets",
    "current_liabilities", "long_term_debt", "shares_outstanding",
    "gross_profit", "revenue",
)

MIN_ANSWERABLE = 5  # need at least this many of the 9 criteria computable
_SCORE_TO_RATING = [  # (min normalized score, rating)
    (7.5, 1), (6.0, 2), (4.0, 3), (2.0, 4), (-1.0, 5),
]


def _latest_two_annual(fund_df, as_of, metrics):
    """For each symbol+metric: the two most recent 10-K values whose filing
    was already public (filed_date <= as_of) -- '_t' (most recent) and
    '_t1' (the year before), for year-over-year comparison."""
    sub = fund_df[
        fund_df.metric.isin(metrics)
        & (fund_df.form == "10-K")
        & (fund_df.filed_date != "")
        & (fund_df.filed_date <= as_of)
    ].copy()
    sub["rank"] = sub.groupby(["symbol", "metric"])["fiscal_end"].rank(method="first", ascending=False)
    sub = sub[sub["rank"] <= 2]
    sub["col"] = sub["metric"] + sub["rank"].map({1: "_t", 2: "_t1"})
    return sub.pivot_table(index="symbol", columns="col", values="value", aggfunc="first")


def _safe_gt(a, b):
    return (a > b).where(a.notna() & b.notna())


def build_piotroski(as_of, fund_df=None):
    fund_df = fund_df if fund_df is not None else load_fundamentals()
    df = _latest_two_annual(fund_df, as_of, METRICS)
    for m in METRICS:
        for suf in ("_t", "_t1"):
            if f"{m}{suf}" not in df.columns:
                df[f"{m}{suf}"] = pd.NA
                df[f"{m}{suf}"] = df[f"{m}{suf}"].astype("float64")

    roa_t = df["net_income_t"] / df["total_assets_t"]
    roa_t1 = df["net_income_t1"] / df["total_assets_t1"]
    leverage_t = df["long_term_debt_t"] / df["total_assets_t"]
    leverage_t1 = df["long_term_debt_t1"] / df["total_assets_t1"]
    current_ratio_t = df["current_assets_t"] / df["current_liabilities_t"]
    current_ratio_t1 = df["current_assets_t1"] / df["current_liabilities_t1"]
    gross_margin_t = df["gross_profit_t"] / df["revenue_t"]
    gross_margin_t1 = df["gross_profit_t1"] / df["revenue_t1"]
    turnover_t = df["revenue_t"] / df["total_assets_t"]
    turnover_t1 = df["revenue_t1"] / df["total_assets_t1"]

    criteria = {
        "positive_roa": (roa_t > 0).where(roa_t.notna()),
        "positive_cfo": (df["operating_cash_flow_t"] > 0).where(df["operating_cash_flow_t"].notna()),
        "improving_roa": _safe_gt(roa_t, roa_t1),
        "earnings_quality": _safe_gt(df["operating_cash_flow_t"], df["net_income_t"]),
        "decreasing_leverage": _safe_gt(leverage_t1, leverage_t),  # lower is better -> t1 > t
        "improving_current_ratio": _safe_gt(current_ratio_t, current_ratio_t1),
        "no_dilution": (df["shares_outstanding_t"] <= df["shares_outstanding_t1"]).where(
            df["shares_outstanding_t"].notna() & df["shares_outstanding_t1"].notna()
        ),
        "improving_gross_margin": _safe_gt(gross_margin_t, gross_margin_t1),
        "improving_turnover": _safe_gt(turnover_t, turnover_t1),
    }
    crit_df = pd.DataFrame(criteria)

    answerable = crit_df.notna().sum(axis=1).astype("float64")
    raw_score = crit_df.sum(axis=1, skipna=True).astype("float64")
    safe_answerable = answerable.where(answerable > 0, other=pd.NA).astype("float64")
    normalized = (raw_score / safe_answerable * 9).where(answerable >= MIN_ANSWERABLE)

    out = df.copy()
    out["answerable_criteria"] = answerable
    out["raw_f_score"] = raw_score.where(answerable >= MIN_ANSWERABLE)
    out["f_score"] = normalized
    out["quality_rating"] = out["f_score"].apply(_score_to_rating)
    out.index.name = "symbol"
    return out.reset_index()


def _score_to_rating(score):
    if pd.isna(score):
        return float("nan")
    for threshold, rating in _SCORE_TO_RATING:
        if score >= threshold:
            return float(rating)
    return 5.0


if __name__ == "__main__":
    from datetime import date

    today = date.today().isoformat()
    rated = build_piotroski(today)
    print(f"n with a quality rating: {rated['quality_rating'].notna().sum()} / {len(rated)}")
    print(rated["quality_rating"].value_counts(dropna=False).sort_index())
    cols = ["symbol", "raw_f_score", "answerable_criteria", "f_score", "quality_rating"]
    print(rated[rated.symbol.isin(["AAPL", "MSFT", "GOOGL", "META", "NVDA"])][cols])
