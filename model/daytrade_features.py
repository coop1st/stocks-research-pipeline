"""
Builds the feature/label panel for the day-trade forecasting model.

Fourth attempt (see model/README.md's "Day trading model" section for
the first three, all null/near-null results): two changes from attempt 3,
per the owner's direction:

1. A simpler label -- instead of attempt 3's compound "3-of-5-up-days AND
   high touches 3%" rule, this predicts whether the best CLOSE reached
   anywhere in the next 5 trading days is >=2% above the origin day's
   close (see label_2pct_5d). Deliberately a closing-price touch, not an
   intraday high -- "best close reached," not "best wick."
2. Every validated indicator this project has, not just technicals/PEAD
   -- valuation, momentum, quality, and insider buying, recomputed at
   DAILY granularity across full history in daytrade_confluence_features.py
   (the `ratings` table these normally live in only holds the last few
   weekly snapshots, unusable for backtesting -- same reason
   recommendation_score/trend_rating/momentum_rating/quality_rating below
   are still excluded in their `ratings`-table form). Congress buy/sell
   flags are skipped -- already documented in model/README.md as too
   sparse (42 hand-compiled trades) to mean anything.

Everything from attempt 3 (volatility_3d/7d, rsi_14, range_position_52w,
pct_from_52w_high, gap_pct_t, intraday_range_t, volume_vs_avg20d,
trend_points, price_vs_sma20/50/100/200, return_20d, earnings_surprise_pct,
days_since_earnings) is kept -- none of it individually predicted
direction in attempt 3, but it's still available for the model to combine
with the new confluence features.

Diagnostic discipline carried over from attempt 1's mistake (trusting a
regression IC that turned out to measure volatility, not direction): two
continuous targets are computed alongside the binary label --
max_gain_close_5 (matches the label's own construction, a near-
tautological sanity check) and forward_return_5 (genuine hold-to-day-5
direction, close[t+5] vs close[t] -- an independent cross-check that this
label isn't quietly re-measuring volatility the way attempt 1's did).

Winsorization: some tickers show extreme price cliffs that are real
reverse/forward splits yfinance hasn't retroactively adjusted for in
adj_close (observed directly, e.g. SXTC: ~$0.05 -> ~$3.59 between
2026-08-07 and 2026-08-10 while volume collapsed ~1000x -- a classic
reverse-split signature, not a data error, but not real "volatility"
either). Left unclipped, a handful of these dominate every cross-
sectional ranking. Features derived from returns/ratios are clipped at
generous but finite bounds (see WINSOR_BOUNDS) rather than dropped, so
the affected symbol-days still contribute their other information.
"""
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
from config import DB_PATH  # noqa: E402
from daytrade_confluence_features import (  # noqa: E402
    add_cap_rotation_features,
    add_insider_features,
    add_momentum_features,
    add_quality_features,
    add_valuation_features,
)

GAIN_THRESHOLD = 0.02  # best close in the next 5 trading days must be >= 2% above the origin close

FEATURE_COLS = [
    "volatility_3d", "volatility_7d", "rsi_14", "range_position_52w",
    "pct_from_52w_high", "gap_pct_t", "intraday_range_t", "volume_vs_avg20d",
    "volume_zscore_90d",
    "trend_points", "price_vs_sma20", "price_vs_sma50", "price_vs_sma100", "price_vs_sma200",
    "return_20d", "earnings_surprise_pct", "days_since_earnings",
    "valuation_composite_pctile", "momentum_composite_pctile", "quality_f_score",
    "insider_buying_flag", "days_since_insider_purchase", "cap_rotation_momentum",
]

# (column, lower, upper) -- generous bounds, just enough to blunt split-
# artifact/data outliers without pretending the underlying noise doesn't exist.
WINSOR_BOUNDS = {
    "volatility_3d": (0, 2.0),
    "volatility_7d": (0, 2.0),
    "gap_pct_t": (-0.9, 5.0),
    "intraday_range_t": (0, 5.0),
    "volume_vs_avg20d": (0, 50.0),
    "volume_zscore_90d": (-5.0, 15.0),
    "price_vs_sma20": (-0.9, 5.0),
    "price_vs_sma50": (-0.9, 5.0),
    "price_vs_sma100": (-0.9, 5.0),
    "price_vs_sma200": (-0.9, 5.0),
    "return_20d": (-0.9, 5.0),
    "earnings_surprise_pct": (-100.0, 300.0),  # e.g. AACG showed +700% off a near-zero estimate base
    "days_since_earnings": (0, 500),
    "days_since_insider_purchase": (0, 1800),  # matches the "no history" sentinel in daytrade_confluence_features.py
    "cap_rotation_momentum": (-0.3, 0.3),  # quarterly rate-of-change of a 1yr-smoothed ratio -- large moves are rare
}

NO_EARNINGS_SURPRISE_FILL = 0.0
NO_EARNINGS_DAYS_FILL = 500


def _winsorize(df):
    for col, (lo, hi) in WINSOR_BOUNDS.items():
        df[col] = df[col].clip(lower=lo, upper=hi)
    return df


def _add_pead_features(prices, conn):
    """Adds earnings_surprise_pct and days_since_earnings to `prices`
    (already sorted by symbol, date), point-in-time correct via
    effective_date and counted in actual trading days (not calendar
    days) via a per-symbol running trading-day index."""
    prices["trading_day_idx"] = prices.groupby("symbol").cumcount()

    earnings = pd.read_sql_query(
        "SELECT symbol, effective_date AS date, surprise_pct FROM earnings_events "
        "WHERE surprise_pct IS NOT NULL",
        conn,
    )
    earnings["date"] = pd.to_datetime(earnings["date"])
    earnings = earnings.merge(
        prices[["symbol", "date", "trading_day_idx"]], on=["symbol", "date"], how="inner"
    )
    earnings = earnings.rename(columns={"trading_day_idx": "event_trading_day_idx"})
    earnings = earnings.sort_values(["symbol", "event_trading_day_idx"]).drop_duplicates(
        subset=["symbol", "event_trading_day_idx"], keep="last"
    )

    prices_sorted = prices.sort_values("trading_day_idx").reset_index(drop=True)
    earnings_sorted = earnings.sort_values("event_trading_day_idx").reset_index(drop=True)
    merged = pd.merge_asof(
        prices_sorted,
        earnings_sorted[["symbol", "event_trading_day_idx", "surprise_pct"]],
        left_on="trading_day_idx", right_on="event_trading_day_idx",
        by="symbol", direction="backward",
    )
    merged["earnings_surprise_pct"] = merged["surprise_pct"].fillna(NO_EARNINGS_SURPRISE_FILL)
    merged["days_since_earnings"] = (
        merged["trading_day_idx"] - merged["event_trading_day_idx"]
    ).fillna(NO_EARNINGS_DAYS_FILL)
    return merged.drop(columns=["surprise_pct", "event_trading_day_idx", "trading_day_idx"])


def build_feature_panel(min_dollar_volume=5_000_000, start_date=None, end_date=None,
                         horizon_days=5, gain_threshold=GAIN_THRESHOLD, label_on_high=False):
    """Returns one row per (symbol, feature_date) with FEATURE_COLS, a
    binary label column, two continuous diagnostic targets, and the raw
    next-horizon_days-trading-day OHLC needed to simulate a multi-day
    take-profit/stop-loss trade rule -- restricted to rows meeting the
    liquidity floor on feature_date.

    horizon_days/gain_threshold default to exactly the original 5-day/2%
    attempt-4 behavior (and its exact column names -- max_gain_close_5,
    forward_return_5, label_2pct_5d) so existing callers are unaffected;
    passing other values names the columns after the actual horizon/
    threshold used (e.g. horizon_days=10, gain_threshold=0.015 ->
    max_gain_close_10, forward_return_10, label_1.5pct_10d).

    label_on_high=True switches the label (and its matching max-gain
    diagnostic) from "best CLOSE reached" to "best intraday HIGH reached"
    -- a wick touch, not a closing-price touch, the opposite of this
    project's original deliberate choice (see this file's module
    docstring) -- and suffixes the column names with _high so they're
    never confused with the close-based versions. forward_return stays
    close-based regardless (it's a genuine hold-to-close diagnostic, not
    a touch criterion)."""
    max_gain_col = f"max_gain_high_{horizon_days}" if label_on_high else f"max_gain_close_{horizon_days}"
    forward_return_col = f"forward_return_{horizon_days}"
    label_col = f"label_{gain_threshold * 100:g}pct_{horizon_days}d" + ("_high" if label_on_high else "")
    conn = sqlite3.connect(DB_PATH)

    prices = pd.read_sql_query(
        "SELECT symbol, date, open, high, low, close, adj_close, volume FROM prices ORDER BY symbol, date", conn
    )
    indicators = pd.read_sql_query(
        "SELECT symbol, date, rsi_14, range_position_52w, pct_from_52w_high, "
        "volatility_3d, volatility_7d, dollar_volume_avg_20d FROM price_indicators",
        conn,
    )
    moving_averages = pd.read_sql_query(
        "SELECT symbol, date, sma_20, sma_50, sma_100, sma_200 FROM moving_averages", conn
    )
    fund_df = pd.read_sql_query(
        "SELECT symbol, metric, fiscal_end, form, value, filed_date FROM fundamentals", conn
    )
    insider_df = pd.read_sql_query(
        "SELECT symbol, owner_name, trans_date, filed_date, value FROM insider_transactions", conn
    )
    benchmark_df = pd.read_sql_query(
        "SELECT symbol, date, close FROM benchmark_prices", conn
    )

    prices["date"] = pd.to_datetime(prices["date"])
    indicators["date"] = pd.to_datetime(indicators["date"])
    benchmark_df["date"] = pd.to_datetime(benchmark_df["date"])
    moving_averages["date"] = pd.to_datetime(moving_averages["date"])

    prices = prices.sort_values(["symbol", "date"]).reset_index(drop=True)
    g = prices.groupby("symbol")

    prices["prior_close"] = g["close"].shift(1)
    prices["gap_pct_t"] = (prices["open"] - prices["prior_close"]) / prices["prior_close"]
    prices["intraday_range_t"] = (prices["high"] - prices["low"]) / prices["open"]
    prices["volume_avg20d"] = g["volume"].transform(
        lambda s: s.rolling(window=20, min_periods=20).mean()
    )
    prices["volume_vs_avg20d"] = prices["volume"] / prices["volume_avg20d"]
    prices["volume_avg90d"] = g["volume"].transform(
        lambda s: s.rolling(window=90, min_periods=90).mean()
    )
    prices["volume_std90d"] = g["volume"].transform(
        lambda s: s.rolling(window=90, min_periods=90).std()
    )
    prices["volume_zscore_90d"] = (prices["volume"] - prices["volume_avg90d"]) / prices["volume_std90d"]
    prices["return_20d"] = g["close"].transform(lambda s: s.pct_change(20))

    prices = prices.merge(moving_averages, on=["symbol", "date"], how="left")
    prices["price_vs_sma20"] = prices["close"] / prices["sma_20"] - 1
    prices["price_vs_sma50"] = prices["close"] / prices["sma_50"] - 1
    prices["price_vs_sma100"] = prices["close"] / prices["sma_100"] - 1
    prices["price_vs_sma200"] = prices["close"] / prices["sma_200"] - 1

    # trend.py's exact 4-point bull/bear checklist, computed here directly
    # (rather than imported) since trend.py only scores a single as-of date,
    # not a full historical panel. NaN (not 0) whenever the required SMAs
    # aren't all present yet -- not enough history to call a trend at all,
    # distinct from "trend looks bearish."
    have_trend_data = prices[["close", "sma_20", "sma_50", "sma_200"]].notna().all(axis=1)
    trend_checks = [
        prices["close"] > prices["sma_200"],
        prices["sma_50"] > prices["sma_200"],
        prices["close"] > prices["sma_50"],
        prices["sma_20"] > prices["sma_50"],
    ]
    trend_points = sum(c.fillna(False).astype(int) for c in trend_checks)
    prices["trend_points"] = trend_points.where(have_trend_data)

    prices = _add_pead_features(prices, conn)
    conn.close()

    # Re-derive the groupby after the merge above (merges don't preserve
    # group order/structure).
    prices = prices.sort_values(["symbol", "date"]).reset_index(drop=True)

    # Momentum needs each symbol's full contiguous trading-day history --
    # its 126/252-day windows are row-position pct_change, not date-based,
    # so filtering rows out first would silently make the lookback span
    # more real trading days than intended for any symbol with liquidity
    # gaps. Unlike valuation/quality/insider below, it can't be deferred
    # until after the liquidity filter.
    prices = add_momentum_features(prices)
    prices = prices.sort_values(["symbol", "date"]).reset_index(drop=True)
    g = prices.groupby("symbol")

    # Next horizon_days trading days' OHLC -- needed both for the label and
    # for the multi-day trade simulation in daytrade_backtest.py.
    for i in range(1, horizon_days + 1):
        prices[f"d{i}_open"] = g["open"].shift(-i)
        prices[f"d{i}_high"] = g["high"].shift(-i)
        prices[f"d{i}_low"] = g["low"].shift(-i)
        prices[f"d{i}_close"] = g["close"].shift(-i)

    close_cols = [f"d{i}_close" for i in range(1, horizon_days + 1)]
    has_full_window = prices[["close", *close_cols]].notna().all(axis=1)

    max_gain_cols = [f"d{i}_high" for i in range(1, horizon_days + 1)] if label_on_high else close_cols
    max_price_horizon = prices[max_gain_cols].max(axis=1)
    prices[max_gain_col] = (max_price_horizon / prices["close"] - 1).where(has_full_window)
    prices[forward_return_col] = (prices[f"d{horizon_days}_close"] / prices["close"] - 1).where(has_full_window)
    prices[label_col] = (prices[max_gain_col] >= gain_threshold).where(has_full_window)

    df = prices.merge(indicators, on=["symbol", "date"], how="inner")
    df = df.rename(columns={"date": "feature_date"})

    df = df[df["dollar_volume_avg_20d"] >= min_dollar_volume]
    if start_date:
        df = df[df["feature_date"] >= pd.Timestamp(start_date)]
    if end_date:
        df = df[df["feature_date"] <= pd.Timestamp(end_date)]

    # Deferred until after the liquidity filter above cuts ~6.37M rows to
    # ~2M -- these three all look up point-in-time values by actual date
    # (merge_asof), so dropping illiquid rows first doesn't change the
    # result for the rows that remain. add_valuation_features is the
    # single biggest memory cost (4 separate merge_asof calls), so this
    # is most of the fix. Side effect: valuation_composite_pctile's
    # cross-sectional percentile is now computed within the liquid
    # universe rather than across all ~5,900 symbols -- intentional, see
    # HANDOVER.md.
    df = df.rename(columns={"feature_date": "date"})
    df = add_valuation_features(df, fund_df)
    df = add_quality_features(df, fund_df)
    df = add_insider_features(df, insider_df)
    df = add_cap_rotation_features(df, fund_df, benchmark_df)
    df = df.rename(columns={"date": "feature_date"})

    df = df.dropna(subset=[*FEATURE_COLS, label_col, max_gain_col, forward_return_col])
    df[label_col] = df[label_col].astype(int)
    df = _winsorize(df)

    trade_cols = ["d1_open"] + [f"d{i}_high" for i in range(1, horizon_days + 1)] + \
                 [f"d{i}_low" for i in range(1, horizon_days + 1)] + \
                 [f"d{i}_close" for i in range(1, horizon_days + 1)]
    # cap_bucket rides along as metadata (not a FEATURE_COLS model input --
    # it's the categorical string cap_rotation_momentum was already derived
    # from) so a per-bucket model/backtest can group rows without
    # recomputing market cap from scratch.
    return df[["symbol", "feature_date", "cap_bucket", *FEATURE_COLS, max_gain_col, forward_return_col,
               label_col, *trade_cols]].reset_index(drop=True)


if __name__ == "__main__":
    from scipy import stats

    panel = build_feature_panel()
    print(f"panel: {len(panel)} rows, {panel['symbol'].nunique()} symbols, "
          f"{panel['feature_date'].min().date()} to {panel['feature_date'].max().date()}")
    print(f"  label_2pct_5d: base rate {panel['label_2pct_5d'].mean():.2%}")
    print(f"  earnings coverage: {(panel['days_since_earnings'] < NO_EARNINGS_DAYS_FILL).mean():.2%}")
    print(f"  valuation coverage: {panel['valuation_composite_pctile'].notna().mean():.2%} (pre-dropna would show gaps)")
    print(f"  quality coverage: {panel['quality_f_score'].notna().mean():.2%}")
    print(f"  insider flag rate: {panel['insider_buying_flag'].mean():.2%}")

    ic_tautological, _ = stats.spearmanr(panel["label_2pct_5d"], panel["max_gain_close_5"])
    ic_independent, _ = stats.spearmanr(panel["label_2pct_5d"], panel["forward_return_5"])
    print(f"  sanity check: label vs max_gain_close_5 (should be very high) IC={ic_tautological:+.3f}")
    print(f"  sanity check: label vs forward_return_5 (independent check) IC={ic_independent:+.3f}")
