"""
Reusable spot-check: what's the actual win rate of the confluence STRONG
BUY / STRONG SELL calls, at three forward horizons -- 1 week, 60 days,
and 1 year?

This answers a different question from backtest.py/validate_indicators.py:
those check each *individual* indicator's rank correlation with 1-year
forward return (the horizon the confluence weights were validated at).
This checks the *combined* confluence label's practical hit rate, and
adds two shorter horizons nobody had checked before (1-week, 60-day) --
useful since STRONG BUY/STRONG SELL are what actually gets shortlisted
for the weekly email, and someone reading that email cares about "did
this call work out" over weeks/months, not just a year out.

Samples N_YEARS random years from the available ANCHORS (see
backtest.py), excluding EXCLUDE_YEARS, and reports win rate + mean/median
return per label per horizon, both per-year and combined. Rerun anytime
(e.g. alongside a quarterly revalidation) with different random years for
a fresh read -- results are noisy at n=2 years, as the first run already
showed (STRONG SELL swung from an 18% win rate in a strong-recovery year
(2023) to 55%+ in a weaker year (2025)), so don't over-read any single run.

Reuses existing building blocks: backtest.py's leave-one-year-out
valuation weighting/rating, validate_indicators.py's per-indicator
as-of-date helpers, and confluence.py's combination logic -- assembled
into a full multi-indicator snapshot at each anchor date, which none of
the existing scripts do (each only tests one indicator in isolation).
"""
import random
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

import numpy as np
import pandas as pd

from backtest import ANCHORS, build_all_snapshots, fit_weights
from confluence import compute_confluence
from rating import rate_snapshot
from snapshot import load_fundamentals, load_prices
from validate_indicators import _forward_return, _momentum_at, _quality_at, _range52w_at, _trend_at

EXCLUDE_YEARS = {2020, 2021, 2022}
N_YEARS = 2


def build_confluence_snapshot(year, snaps, price_df, fund_df, all_dates):
    as_of, _fwd_1y = ANCHORS[year]

    train_years = [y for y in snaps if y != year]
    weights, _train_ic = fit_weights([snaps[y] for y in train_years])
    valuation = rate_snapshot(snaps[year].copy(), weights=weights)
    valuation = valuation.set_index("symbol")[["rating", "composite"]].rename(
        columns={"rating": "valuation_rating", "composite": "valuation_composite"}
    )

    trend = _trend_at(as_of, price_df, all_dates)
    momentum = _momentum_at(as_of, price_df)
    quality = _quality_at(as_of, fund_df)
    range52w = _range52w_at(as_of)

    merged = valuation.join([trend, momentum, quality, range52w], how="outer").reset_index()
    merged["insider_buying_flag"] = np.nan  # skips the insider stage; see confluence.py's reindex fallback
    return compute_confluence(merged), as_of


def win_rate_report(rated, as_of, forward_to, price_df, all_dates, horizon_label):
    fret = _forward_return(as_of, forward_to, price_df, all_dates)
    merged = rated.set_index("symbol").join(fret, how="inner").dropna(subset=["forward_return"])

    rows = []
    for label, win_if in [("STRONG BUY", lambda r: r > 0), ("STRONG SELL", lambda r: r < 0)]:
        subset = merged[merged["recommendation"] == label]
        n = len(subset)
        if n == 0:
            rows.append({"horizon": horizon_label, "label": label, "n": 0, "win_rate": np.nan,
                         "mean_return": np.nan, "median_return": np.nan})
            continue
        wins = subset["forward_return"].apply(win_if).sum()
        rows.append({
            "horizon": horizon_label, "label": label, "n": n,
            "win_rate": wins / n,
            "mean_return": subset["forward_return"].mean(),
            "median_return": subset["forward_return"].median(),
        })
    return pd.DataFrame(rows)


def horizons_for(year, as_of):
    """(label, forward_to date) pairs -- 1-year reuses ANCHORS' own forward
    date (trading-day-aligned, consistent with the rest of the codebase)
    rather than a generic +365-day offset."""
    fwd_1y = ANCHORS[year][1]
    return [
        ("1-week", (pd.Timestamp(as_of) + timedelta(days=7)).strftime("%Y-%m-%d")),
        ("60-day", (pd.Timestamp(as_of) + timedelta(days=60)).strftime("%Y-%m-%d")),
        ("1-year", fwd_1y),
    ]


def main():
    eligible_years = [y for y in ANCHORS if y not in EXCLUDE_YEARS]
    test_years = sorted(random.sample(eligible_years, N_YEARS))
    print(f"Eligible years (excluding {sorted(EXCLUDE_YEARS)}): {eligible_years}")
    print(f"Randomly selected test years: {test_years}\n")

    print("Building valuation snapshots for all anchor years (needed for leave-one-year-out weighting)...")
    snaps = build_all_snapshots()
    price_df = load_prices()
    fund_df = load_fundamentals()
    all_dates = sorted(price_df["date"].unique())

    all_reports = []
    for year in test_years:
        print(f"\n{'=' * 78}\nYear {year}\n{'=' * 78}")
        rated, as_of = build_confluence_snapshot(year, snaps, price_df, fund_df, all_dates)
        n_buy = (rated["recommendation"] == "STRONG BUY").sum()
        n_sell = (rated["recommendation"] == "STRONG SELL").sum()
        print(f"as_of={as_of}  STRONG BUY={n_buy}  STRONG SELL={n_sell}")

        for horizon_label, forward_to in horizons_for(year, as_of):
            report = win_rate_report(rated, as_of, forward_to, price_df, all_dates, horizon_label)
            report.insert(0, "year", year)
            all_reports.append(report)
            print(f"\n--- {horizon_label} forward (to {forward_to}) ---")
            print(report.drop(columns=["year"]).to_string(index=False))

    combined = pd.concat(all_reports, ignore_index=True)
    print(f"\n{'=' * 78}\nCOMBINED ACROSS TEST YEARS {test_years}\n{'=' * 78}")
    for horizon_label in ["1-week", "60-day", "1-year"]:
        for label in ["STRONG BUY", "STRONG SELL"]:
            subset = combined[(combined["horizon"] == horizon_label) & (combined["label"] == label)]
            total_n = subset["n"].sum()
            if total_n == 0:
                continue
            weighted_win_rate = (subset["win_rate"] * subset["n"]).sum() / total_n
            weighted_mean_return = (subset["mean_return"] * subset["n"]).sum() / total_n
            print(f"{horizon_label:8s} {label:12s} n={total_n:5d}  "
                  f"win_rate={weighted_win_rate:.1%}  mean_return={weighted_mean_return:+.2%}")


if __name__ == "__main__":
    main()
