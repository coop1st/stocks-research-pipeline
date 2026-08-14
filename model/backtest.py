"""
Leave-one-year-out backtest of the composite valuation rating.

For each of the 4 full-year windows we have (2022, 2023, 2024, 2025):
  - "train" on the other 3 years: measure each yield metric's historical
    Spearman rank-IC (correlation between that metric's cross-sectional
    percentile and forward 1-year return) and weight metrics by their
    average IC (metrics with no historical predictive power get ~0 weight).
  - "test" on the held-out year: apply those fixed weights, bucket into
    quintiles 1-5, and check whether cheap (1) actually outperformed
    expensive (5) that year -- out of sample, using a formula never fit on
    that year's data.

This directly answers "does the rating scheme have predictive power" rather
than just asserting it does. See README.md in this folder for the important
caveats (small sample, survivorship bias, no transaction costs).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

import numpy as np
import pandas as pd
from scipy import stats

from rating import YIELD_COLS, add_percentile_ranks, rate_snapshot
from snapshot import build_snapshot, load_fundamentals, load_prices

ANCHORS = {
    2022: ("2022-01-03", "2023-01-03"),
    2023: ("2023-01-03", "2024-01-02"),
    2024: ("2024-01-02", "2025-01-02"),
    2025: ("2025-01-02", "2026-01-02"),
}


def build_all_snapshots():
    price_df = load_prices()
    fund_df = load_fundamentals()
    snaps = {}
    for year, (as_of, fwd) in ANCHORS.items():
        snap = build_snapshot(as_of, forward_to=fwd, price_df=price_df, fund_df=fund_df)
        snap = add_percentile_ranks(snap)
        snaps[year] = snap
        print(f"[{year}] as_of={as_of} n={len(snap)} "
              f"eps={snap.earnings_yield.notna().sum()} "
              f"book={snap.book_yield.notna().sum()} "
              f"sales={snap.sales_yield.notna().sum()}")
    return snaps


def yearly_ic(snap):
    """Spearman IC of each yield's percentile vs forward_return, this year only."""
    ic = {}
    for col in YIELD_COLS:
        x = snap[f"{col}_pctile"]
        y = snap["forward_return"]
        mask = x.notna() & y.notna()
        if mask.sum() < 30:
            ic[col] = np.nan
            continue
        rho, _ = stats.spearmanr(x[mask], y[mask])
        ic[col] = rho
    return ic


def fit_weights(train_snaps):
    """Average each metric's IC across the training years; negative/absent
    predictive power gets weight 0 rather than working against the score."""
    ics = pd.DataFrame([yearly_ic(s) for s in train_snaps])
    avg_ic = ics.mean(skipna=True)
    weights = avg_ic.clip(lower=0).to_dict()
    if sum(weights.values()) == 0:
        weights = {c: 1.0 for c in YIELD_COLS}
    return weights, avg_ic.to_dict()


def bucket_summary(rated_snap):
    g = rated_snap.dropna(subset=["rating"]).groupby("rating", observed=True)
    return g["forward_return"].agg(["mean", "median", "count"]).sort_index()


def run_backtest():
    snaps = build_all_snapshots()
    years = sorted(snaps)
    fold_results = []

    for test_year in years:
        train_years = [y for y in years if y != test_year]
        weights, train_ic = fit_weights([snaps[y] for y in train_years])

        rated = rate_snapshot(snaps[test_year].copy(), weights=weights)
        summary = bucket_summary(rated)

        x, y = rated["composite"], rated["forward_return"]
        mask = x.notna() & y.notna()
        oos_ic, _ = stats.spearmanr(x[mask], y[mask])

        fold_results.append({
            "test_year": test_year,
            "train_years": train_years,
            "weights": weights,
            "train_ic": train_ic,
            "bucket_summary": summary,
            "oos_ic": oos_ic,
        })

    return fold_results


def print_report(fold_results):
    print("\n" + "=" * 78)
    print("LEAVE-ONE-YEAR-OUT BACKTEST: composite valuation rating vs forward return")
    print("=" * 78)

    spreads, oos_ics, monotonic_count = [], [], 0
    for r in fold_results:
        print(f"\n--- Test year: {r['test_year']}  (trained on {r['train_years']}) ---")
        print(f"  trained weights (from avg IC, train years): "
              + ", ".join(f"{k}={v:.3f}" for k, v in r["weights"].items()))
        print(f"  train-year ICs per metric: "
              + ", ".join(f"{k}={v:.3f}" if pd.notna(v) else f"{k}=n/a" for k, v in r["train_ic"].items()))
        print(f"  out-of-sample IC (composite vs forward return, test year only): {r['oos_ic']:.3f}")
        print("  forward return by rating bucket (1=cheapest .. 5=most expensive):")
        print(r["bucket_summary"].to_string())

        s = r["bucket_summary"]
        if 1 in s.index and 5 in s.index:
            spread = s.loc[1, "mean"] - s.loc[5, "mean"]
            spreads.append(spread)
            print(f"  spread (bucket1 - bucket5 mean return): {spread:+.2%}")
        means = s["mean"].reindex([1, 2, 3, 4, 5])
        if means.notna().all() and means.is_monotonic_decreasing:
            monotonic_count += 1
        oos_ics.append(r["oos_ic"])

    print("\n" + "=" * 78)
    print("SUMMARY ACROSS ALL 4 FOLDS")
    print("=" * 78)
    print(f"  average bucket1-bucket5 spread: {np.mean(spreads):+.2%}")
    print(f"  average out-of-sample IC:       {np.mean(oos_ics):+.3f}")
    print(f"  folds with monotonic 1->5 return decline: {monotonic_count}/4")


if __name__ == "__main__":
    results = run_backtest()
    print_report(results)
