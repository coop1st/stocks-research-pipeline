"""
Recomputes this project's already-validated confluence indicators
(valuation, momentum, quality, insider buying) at DAILY granularity
across full history, for the day-trade model (see daytrade_features.py).

Why this exists: the `ratings` table these indicators normally live in
only holds the last few weekly snapshots (overwritten in place, not
appended to), so it's unusable for backtesting -- NaN for nearly all of
the ~5 years of price history the day-trade panel spans. Each function
here reuses the exact point-in-time logic the live indicator scripts
(snapshot.py, momentum.py, quality.py, insider_flag.py) already use,
just vectorized across every trading date at once via merge_asof rather
than called once per as-of date.

Congress buy/sell flags are deliberately excluded -- already documented
in model/README.md as too sparse (42 hand-compiled trades) to be a
meaningful signal, same reasoning it's excluded from the live confluence
score.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

import numpy as np
import pandas as pd

from quality import build_piotroski
from snapshot import MIN_PRICE

VALUATION_ANNUAL_METRICS = ("eps_diluted", "revenue")   # 10-K only, same as snapshot.py
VALUATION_BALANCE_METRICS = ("stockholders_equity", "shares_outstanding")  # any form, same as snapshot.py
MOMENTUM_WINDOWS_TRADING_DAYS = {126: "return_126d", 252: "return_252d"}  # ~6mo/12mo, matches return_20d's convention
INSIDER_DECAY_DAYS = 180  # matches insider_flag.py's DECAY_DAYS

# Market-cap bucket thresholds -- validated against the local universe's
# actual liquid-symbol distribution before picking these (see the
# conversation that built this: Large/Mid/Small each land in a healthy
# 575-750 liquid-symbol range; below $300M the liquid population is too
# thin, ~140 symbols, to support its own signal, so those rows get no
# bucket and are excluded from this feature -- not folded into Small).
CAP_BUCKET_THRESHOLDS = (
    (10e9, "large"),
    (2e9, "mid"),
    (300e6, "small"),
)  # (floor, bucket) checked high to low; below the last floor -> excluded
CAP_ROTATION_WINDOW_TRADING_DAYS = 63  # ~1 quarter, the rate-of-change lookback for each ratio's 1yr-smoothed level
CAP_ROTATION_SMOOTH_TRADING_DAYS = 252  # ~1yr, matches the exploratory RSP/SPY, MDY/SPY, IJR/SPY analysis


def _asof_metric(fund_df, symbol_dates, metric, colname, forms=None):
    """For each (symbol, date) in symbol_dates, the value from the most
    recently FILED row for this metric with filed_date <= date -- a
    step-function forward-fill via merge_asof, not a per-date filter loop.
    Practical simplification of snapshot.py's exact "max fiscal_end among
    filed_date<=as_of" rule: orders by filed_date instead, which matches
    it except in the rare case of an out-of-order restatement filing --
    an acceptable trade-off for a vectorized full-history computation."""
    sub = fund_df[fund_df["metric"] == metric]
    if forms:
        sub = sub[sub["form"].isin(forms)]
    sub = sub[(sub["filed_date"].notna()) & (sub["filed_date"] != "")]
    sub = sub.sort_values(["symbol", "filed_date"])
    sub = sub.rename(columns={"filed_date": "date", "value": colname})
    sub["date"] = pd.to_datetime(sub["date"])
    sub = sub[["symbol", "date", colname]].drop_duplicates(subset=["symbol", "date"], keep="last")
    sub = sub.sort_values("date")

    left = symbol_dates.sort_values("date")
    merged = pd.merge_asof(left, sub, on="date", by="symbol", direction="backward")
    return merged[colname]


def add_valuation_features(df, fund_df):
    """Adds valuation_composite_pctile: equal-weighted mean of
    earnings/book/sales-yield cross-sectional percentile ranks, computed
    per date across the whole panel -- same yields/gating as
    snapshot.py's build_snapshot(), just vectorized. Uses raw `close`
    (not adjusted), same reasoning as snapshot.py: matches per-share
    fundamentals as actually reported, not a retroactively split-
    adjusted price."""
    symbol_dates = df[["symbol", "date"]].drop_duplicates()

    eps = _asof_metric(fund_df, symbol_dates, "eps_diluted", "eps_diluted", forms=("10-K",))
    revenue = _asof_metric(fund_df, symbol_dates, "revenue", "revenue", forms=("10-K",))
    equity = _asof_metric(fund_df, symbol_dates, "stockholders_equity", "stockholders_equity")
    shares = _asof_metric(fund_df, symbol_dates, "shares_outstanding", "shares_outstanding")

    v = symbol_dates.sort_values("date").reset_index(drop=True)
    v["eps_diluted"], v["revenue"], v["stockholders_equity"], v["shares_outstanding"] = eps, revenue, equity, shares
    v = v.merge(df[["symbol", "date", "close"]], on=["symbol", "date"], how="left")

    v["book_value_ps"] = v["stockholders_equity"] / v["shares_outstanding"]
    v["sales_ps"] = v["revenue"] / v["shares_outstanding"]

    v["earnings_yield"] = v["eps_diluted"] / v["close"]
    v.loc[(v["eps_diluted"] <= 0) | (v["close"] < MIN_PRICE), "earnings_yield"] = None
    v["book_yield"] = v["book_value_ps"] / v["close"]
    v.loc[(v["book_value_ps"] <= 0) | (v["close"] < MIN_PRICE), "book_yield"] = None
    v["sales_yield"] = v["sales_ps"] / v["close"]
    v.loc[(v["sales_ps"] <= 0) | (v["close"] < MIN_PRICE), "sales_yield"] = None

    for col in ("earnings_yield", "book_yield", "sales_yield"):
        v[f"{col}_pctile"] = v.groupby("date")[col].rank(pct=True)

    pctile_cols = ["earnings_yield_pctile", "book_yield_pctile", "sales_yield_pctile"]
    v["valuation_composite_pctile"] = v[pctile_cols].mean(axis=1, skipna=True)

    return df.merge(v[["symbol", "date", "valuation_composite_pctile"]], on=["symbol", "date"], how="left")


def add_momentum_features(df):
    """Adds momentum_composite_pctile: equal-weighted mean of trailing
    ~6mo/~12mo adjusted-return cross-sectional percentile ranks per date
    -- same shape as momentum.py's score_momentum(), computed across the
    whole panel via trading-day-offset pct_change (matching return_20d's
    existing convention) instead of one as-of date's calendar-month
    lookback."""
    d = df[["symbol", "date", "adj_close"]].sort_values(["symbol", "date"]).copy()
    g = d.groupby("symbol")["adj_close"]
    pctile_cols = []
    for window, colname in MOMENTUM_WINDOWS_TRADING_DAYS.items():
        d[colname] = g.pct_change(window)
        pc = f"{colname}_pctile"
        d[pc] = d.groupby("date")[colname].rank(pct=True)
        pctile_cols.append(pc)
    d["momentum_composite_pctile"] = d[pctile_cols].mean(axis=1, skipna=True)
    return df.merge(d[["symbol", "date", "momentum_composite_pctile"]], on=["symbol", "date"], how="left")


def add_quality_features(df, fund_df):
    """Adds quality_f_score: Piotroski F-score, computed at monthly
    anchor dates (reusing quality.build_piotroski() unmodified -- no
    reimplementation) and forward-filled to every trading day via
    merge_asof. Correct, not an approximation: the F-score only changes
    when a new 10-K is filed (~annually per company) and doesn't depend
    on price at all, so a monthly resolution loses nothing a true daily
    recomputation would have captured."""
    dates = df["date"]
    anchors = pd.date_range(dates.min(), dates.max(), freq="MS")
    scores = []
    for anchor in anchors:
        as_of = anchor.strftime("%Y-%m-%d")
        piotroski = build_piotroski(as_of, fund_df=fund_df)
        piotroski = piotroski[["symbol", "f_score"]].copy()
        piotroski["date"] = anchor
        scores.append(piotroski)
    q = pd.concat(scores, ignore_index=True).sort_values("date")

    symbol_dates = df[["symbol", "date"]].drop_duplicates().sort_values("date")
    merged = pd.merge_asof(symbol_dates, q, on="date", by="symbol", direction="backward")
    merged = merged.rename(columns={"f_score": "quality_f_score"})
    return df.merge(merged[["symbol", "date", "quality_f_score"]], on=["symbol", "date"], how="left")


def add_insider_features(df, insider_df):
    """Adds insider_buying_flag (0/1) and days_since_insider_purchase
    (calendar days, matching insider_flag.py's own pd.Timedelta(days=...)
    window -- not trading days). Exact, not approximate: insider_flag.py's
    binary flag is fully determined by the single most recent qualifying
    filing's age, since if that one is stale (> DECAY_DAYS), every
    earlier filing necessarily is too."""
    sub = insider_df[(insider_df["filed_date"].notna()) & (insider_df["filed_date"] != "")].copy()
    sub["event_date"] = pd.to_datetime(sub["filed_date"])
    events = sub.sort_values(["symbol", "event_date"]).drop_duplicates(subset=["symbol", "event_date"], keep="last")
    events = events[["symbol", "event_date"]].sort_values("event_date")

    symbol_dates = df[["symbol", "date"]].drop_duplicates().sort_values("date")
    merged = pd.merge_asof(
        symbol_dates, events, left_on="date", right_on="event_date", by="symbol", direction="backward"
    )
    merged["days_since_insider_purchase"] = (merged["date"] - merged["event_date"]).dt.days
    merged["insider_buying_flag"] = (merged["days_since_insider_purchase"] <= INSIDER_DECAY_DAYS).astype(int)
    no_history = merged["event_date"].isna()
    merged.loc[no_history, "insider_buying_flag"] = 0
    merged.loc[no_history, "days_since_insider_purchase"] = INSIDER_DECAY_DAYS * 10  # sentinel: no prior purchase on record

    out = merged[["symbol", "date", "insider_buying_flag", "days_since_insider_purchase"]]
    return df.merge(out, on=["symbol", "date"], how="left")


def _benchmark_rotation_series(benchmark_df):
    """From benchmark_prices (symbol, date, close for SPY/MDY/IJR), returns
    a date-indexed frame of mdy_mom/ijr_mom/large_mom: each ratio's 1-year-
    smoothed level's ~1-quarter rate of change, signed so positive always
    means "capital rotating toward this bucket." large_mom is the inverse
    of the mid/small signal (no natural "Large/SPY" ratio exists -- SPY
    effectively is large-cap already), averaging mdy_mom and ijr_mom since
    they're highly correlated (0.81 in the exploratory analysis) rather
    than picking one arbitrarily."""
    wide = benchmark_df.pivot(index="date", columns="symbol", values="close").sort_index()
    mdy_smooth = (wide["MDY"] / wide["SPY"]).rolling(
        CAP_ROTATION_SMOOTH_TRADING_DAYS, min_periods=CAP_ROTATION_SMOOTH_TRADING_DAYS
    ).mean()
    ijr_smooth = (wide["IJR"] / wide["SPY"]).rolling(
        CAP_ROTATION_SMOOTH_TRADING_DAYS, min_periods=CAP_ROTATION_SMOOTH_TRADING_DAYS
    ).mean()

    out = pd.DataFrame(index=wide.index)
    out["mdy_mom"] = mdy_smooth.pct_change(CAP_ROTATION_WINDOW_TRADING_DAYS)
    out["ijr_mom"] = ijr_smooth.pct_change(CAP_ROTATION_WINDOW_TRADING_DAYS)
    out["large_mom"] = -out[["mdy_mom", "ijr_mom"]].mean(axis=1, skipna=True)
    return out.reset_index()


def _market_cap_bucket(market_cap):
    """First-match-wins against CAP_BUCKET_THRESHOLDS (checked high to low,
    same pattern as config.py's sic_to_category) -- None below the lowest
    floor, deliberately excluded rather than folded into the smallest
    named bucket."""
    conditions = [market_cap >= floor for floor, _ in CAP_BUCKET_THRESHOLDS]
    choices = [name for _, name in CAP_BUCKET_THRESHOLDS]
    return np.select(conditions, choices, default=None)


def add_cap_rotation_features(df, fund_df, benchmark_df):
    """Adds cap_bucket (large/mid/small/None) and cap_rotation_momentum: a
    signed feature reflecting whether capital is currently rotating toward
    or away from a stock's own market-cap bucket, derived from the MDY/SPY
    and IJR/SPY ETF ratios rather than hand-asserted -- same "build it,
    then let the per-feature IC check judge it" discipline as every other
    indicator here, not assumed predictive by construction."""
    symbol_dates = df[["symbol", "date"]].drop_duplicates()
    shares = _asof_metric(fund_df, symbol_dates, "shares_outstanding", "shares_outstanding")

    mc = symbol_dates.sort_values("date").reset_index(drop=True)
    mc["shares_outstanding"] = shares
    mc = mc.merge(df[["symbol", "date", "close"]], on=["symbol", "date"], how="left")
    mc["market_cap"] = mc["close"] * mc["shares_outstanding"]
    mc["cap_bucket"] = _market_cap_bucket(mc["market_cap"])

    rotation = _benchmark_rotation_series(benchmark_df)
    mc = mc.merge(rotation, on="date", how="left")

    mc["cap_rotation_momentum"] = np.select(
        [mc["cap_bucket"] == "mid", mc["cap_bucket"] == "small", mc["cap_bucket"] == "large"],
        [mc["mdy_mom"], mc["ijr_mom"], mc["large_mom"]],
        default=np.nan,
    )

    out = mc[["symbol", "date", "cap_bucket", "cap_rotation_momentum"]]
    return df.merge(out, on=["symbol", "date"], how="left")
