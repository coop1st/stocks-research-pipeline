"""
Sanity-check backtest for the trend and momentum indicators.

Unlike the valuation model, these aren't fit/trained on anything -- trend
is a fixed rule (price vs its own moving averages), momentum is a
cross-sectional rank of trailing return. This script isn't validating a
trained model, it's checking the ratings point the direction they're
supposed to (rating 1 should beat rating 5) using the same 4 non-overlapping
annual windows (2022-2025) as the valuation backtest, and computing forward
1-year total return the same way (adjusted close, no lookahead).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

import numpy as np
import pandas as pd
from scipy import stats

from backtest import ANCHORS
from insider_flag import load_insider_transactions, rate_insider_buying
from momentum import compute_returns as compute_momentum_returns
from momentum import score_momentum
from quality import build_piotroski
from range52w import rate_range52w
from rsi import rate_rsi
from snapshot import MIN_PRICE, load_fundamentals, load_prices, nearest_trading_date
from trend import detect_recent_cross, load_trend_inputs, score_trend


def _forward_return(as_of, forward_to, price_df, all_dates):
    as_of_td = nearest_trading_date(all_dates, as_of)
    fwd_td = nearest_trading_date(all_dates, forward_to)
    asof_rows = price_df[price_df.date == as_of_td].set_index("symbol")
    # Same $1 floor as the valuation/momentum models: sub-$1 tickers produce
    # blown-up (sometimes literally infinite) percentage returns off a
    # near-zero base that swamp everything else in a mean.
    cur = asof_rows.loc[asof_rows["close"] >= MIN_PRICE, "adj_close"].rename("adj_price")
    fwd = price_df[price_df.date == fwd_td].set_index("symbol")["adj_close"].rename("adj_fwd_price")
    out = cur.to_frame().join(fwd)
    out["forward_return"] = out["adj_fwd_price"] / out["adj_price"] - 1
    return out[["forward_return"]]


def _trend_at(as_of, price_df, all_dates):
    trading_date = nearest_trading_date(all_dates, as_of)
    with_prices = price_df[price_df.date == trading_date][["symbol", "close"]].rename(columns={"close": "price"})
    from db import get_connection
    with get_connection() as conn:
        mas = pd.read_sql_query(
            "SELECT symbol, sma_20, sma_50, sma_100, sma_200 FROM moving_averages WHERE date = ?",
            conn, params=(trading_date,),
        )
    for c in ("sma_20", "sma_50", "sma_100", "sma_200"):
        mas[c] = pd.to_numeric(mas[c], errors="coerce")
    df = with_prices.merge(mas, on="symbol", how="left")
    return score_trend(df).set_index("symbol")[["trend_rating"]]


def _momentum_at(as_of, price_df):
    df = compute_momentum_returns(as_of, price_df=price_df)
    rated = score_momentum(df)
    return rated.set_index("symbol")[["momentum_rating"]]


def _quality_at(as_of, fund_df):
    rated = build_piotroski(as_of, fund_df=fund_df)
    return rated.set_index("symbol")[["quality_rating", "answerable_criteria"]]


def _rsi_at(as_of):
    rated = rate_rsi(as_of)
    return rated.set_index("symbol")[["rsi_rating"]]


def _range52w_at(as_of):
    rated = rate_range52w(as_of)
    return rated.set_index("symbol")[["range52w_rating"]]


def _one_month_forward(as_of):
    return (pd.Timestamp(as_of) + pd.DateOffset(months=1)).strftime("%Y-%m-%d")


def bucket_summary(df, rating_col):
    g = df.dropna(subset=[rating_col]).groupby(rating_col, observed=True)
    return g["forward_return"].agg(["mean", "median", "count"]).sort_index()


def run_validation():
    price_df = load_prices()
    fund_df = load_fundamentals()
    all_dates = sorted(price_df["date"].unique())

    print("\n" + "=" * 78)
    print("TREND indicator: rating vs forward 1-year return (rule-based, not trained)")
    print("=" * 78)
    trend_ics = []
    for year, (as_of, fwd) in ANCHORS.items():
        trend = _trend_at(as_of, price_df, all_dates)
        fret = _forward_return(as_of, fwd, price_df, all_dates)
        merged = trend.join(fret, how="inner")
        x, y = merged["trend_rating"], merged["forward_return"]
        mask = x.notna() & y.notna()
        if mask.sum() < 30:
            print(f"\n--- {year} (as_of {as_of}) --- skipped: only {mask.sum()} tickers had "
                  f"200 days of price history yet (our price data starts 2021-08-12)")
            continue
        ic, _ = stats.spearmanr(x[mask], y[mask])
        trend_ics.append(ic)
        print(f"\n--- {year} (as_of {as_of}) --- IC={ic:+.3f}  (n={mask.sum()})")
        print(bucket_summary(merged, "trend_rating").to_string())
    print(f"\naverage trend IC across years: {np.mean(trend_ics):+.3f} "
          f"(negative expected sign is -, since rating1=bullish should correlate NEGATIVELY "
          f"with rating number vs positive return -- i.e. IC should be negative here)")

    print("\n" + "=" * 78)
    print("MOMENTUM indicator: rating vs forward 1-year return")
    print("=" * 78)
    mom_ics = []
    for year, (as_of, fwd) in ANCHORS.items():
        mom = _momentum_at(as_of, price_df)
        fret = _forward_return(as_of, fwd, price_df, all_dates)
        merged = mom.join(fret, how="inner")
        x, y = merged["momentum_rating"], merged["forward_return"]
        mask = x.notna() & y.notna()
        if mask.sum() < 30:
            print(f"\n--- {year} (as_of {as_of}) --- skipped: only {mask.sum()} tickers had "
                  f"12 months of price history yet (our price data starts 2021-08-12)")
            continue
        ic, _ = stats.spearmanr(x[mask], y[mask])
        mom_ics.append(ic)
        print(f"\n--- {year} (as_of {as_of}) --- IC={ic:+.3f}  (n={mask.sum()})")
        print(bucket_summary(merged, "momentum_rating").to_string())
    print(f"\naverage momentum IC across years: {np.mean(mom_ics):+.3f}")

    print("\n" + "=" * 78)
    print("QUALITY indicator (Piotroski F-Score): rating vs forward 1-year return")
    print("=" * 78)
    qual_ics = []
    for year, (as_of, fwd) in ANCHORS.items():
        qual = _quality_at(as_of, fund_df)
        fret = _forward_return(as_of, fwd, price_df, all_dates)
        merged = qual.join(fret, how="inner")
        x, y = merged["quality_rating"], merged["forward_return"]
        mask = x.notna() & y.notna()
        if mask.sum() < 30:
            print(f"\n--- {year} (as_of {as_of}) --- skipped: only {mask.sum()} tickers had "
                  f"2 years of annual filings on file yet")
            continue
        ic, _ = stats.spearmanr(x[mask], y[mask])
        qual_ics.append(ic)
        print(f"\n--- {year} (as_of {as_of}) --- IC={ic:+.3f}  (n={mask.sum()})")
        print(bucket_summary(merged, "quality_rating").to_string())
    print(f"\naverage quality IC across years: {np.mean(qual_ics):+.3f}")

    print("\n" + "=" * 78)
    print("RSI indicator: rating vs forward ~1-MONTH return (RSI is a short-horizon")
    print("mean-reversion signal, so testing it on a 1-year window like the others")
    print("would be the wrong test -- its expected effect resolves in days/weeks)")
    print("=" * 78)
    rsi_ics = []
    for year, (as_of, _fwd_1y) in ANCHORS.items():
        fwd_1m = _one_month_forward(as_of)
        rsi = _rsi_at(as_of)
        fret = _forward_return(as_of, fwd_1m, price_df, all_dates)
        merged = rsi.join(fret, how="inner")
        x, y = merged["rsi_rating"], merged["forward_return"]
        mask = x.notna() & y.notna()
        if mask.sum() < 30:
            print(f"\n--- {year} (as_of {as_of}) --- skipped: only {mask.sum()} tickers")
            continue
        ic, _ = stats.spearmanr(x[mask], y[mask])
        rsi_ics.append(ic)
        print(f"\n--- {year} (as_of {as_of}, forward to {fwd_1m}) --- IC={ic:+.3f}  (n={mask.sum()})")
        print(bucket_summary(merged, "rsi_rating").to_string())
    print(f"\naverage RSI IC across years (1-month forward): {np.mean(rsi_ics):+.3f}")

    print("\n" + "=" * 78)
    print("52-WEEK RANGE indicator: rating vs forward 1-year return")
    print("=" * 78)
    range_ics = []
    for year, (as_of, fwd) in ANCHORS.items():
        rng = _range52w_at(as_of)
        fret = _forward_return(as_of, fwd, price_df, all_dates)
        merged = rng.join(fret, how="inner")
        x, y = merged["range52w_rating"], merged["forward_return"]
        mask = x.notna() & y.notna()
        if mask.sum() < 30:
            print(f"\n--- {year} (as_of {as_of}) --- skipped: only {mask.sum()} tickers had "
                  f"enough history for a 52-week range yet")
            continue
        ic, _ = stats.spearmanr(x[mask], y[mask])
        range_ics.append(ic)
        print(f"\n--- {year} (as_of {as_of}) --- IC={ic:+.3f}  (n={mask.sum()})")
        print(bucket_summary(merged, "range52w_rating").to_string())
    print(f"\naverage 52-week range IC across years: {np.mean(range_ics):+.3f}")

    print("\n" + "=" * 78)
    print("INSIDER BUYING flag: flagged (1) vs unflagged (0) forward 1-year return")
    print("=" * 78)
    insider_df = load_insider_transactions()
    spreads = []
    for year, (as_of, fwd) in ANCHORS.items():
        flagged = rate_insider_buying(as_of, trans_df=insider_df)
        fret = _forward_return(as_of, fwd, price_df, all_dates)
        merged = fret.join(flagged.set_index("symbol")[["insider_buying_flag", "cluster_buy_count"]], how="left")
        merged["insider_buying_flag"] = merged["insider_buying_flag"].fillna(0)
        g = merged.dropna(subset=["forward_return"]).groupby("insider_buying_flag")["forward_return"]
        summary = g.agg(["mean", "median", "count"])
        print(f"\n--- {year} (as_of {as_of}) ---")
        print(summary.to_string())
        if 0 in summary.index and 1 in summary.index:
            spread = summary.loc[1, "mean"] - summary.loc[0, "mean"]
            spreads.append(spread)
            print(f"  spread (flagged - unflagged mean return): {spread:+.2%}")
        cluster = merged[merged["insider_buying_flag"] == 1]
        multi = cluster[cluster["cluster_buy_count"] >= 2]
        if len(multi) >= 10:
            print(f"  cluster buys (2+ distinct insiders, n={len(multi)}): "
                  f"mean forward return {multi['forward_return'].mean():+.2%} "
                  f"vs single-buyer (n={len(cluster) - len(multi)}): "
                  f"{cluster[cluster['cluster_buy_count'] < 2]['forward_return'].mean():+.2%}")
    print(f"\naverage flagged-vs-unflagged spread across years: {np.mean(spreads):+.2%}")


if __name__ == "__main__":
    run_validation()
