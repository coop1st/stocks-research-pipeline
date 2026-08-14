"""
Composite valuation score and 1-5 cheapness rating from a snapshot's yield
columns (earnings_yield, book_yield, sales_yield).

Rating scale matches what was asked for: 1 = extremely cheap, 2 = cheap,
3 = fairly priced, 4 = expensive, 5 = extremely expensive. It's a relative
(cross-sectional) rating -- "cheap" means cheap relative to the rest of the
universe on that date, not cheap in some absolute sense.
"""
import pandas as pd

YIELD_COLS = ("earnings_yield", "book_yield", "sales_yield")


def add_percentile_ranks(snap):
    """Cross-sectional percentile rank of each yield, computed within this
    snapshot only -- valuation is relative-to-peers-on-this-date, so ranks
    must never be pooled across dates/years."""
    snap = snap.copy()
    for col in YIELD_COLS:
        snap[f"{col}_pctile"] = snap[col].rank(pct=True)
    return snap


def composite_score(snap, weights=None):
    """weights: dict metric->weight, need not sum to 1 -- renormalized
    per-row over whichever metrics are actually available for that ticker,
    so a ticker missing e.g. book_yield isn't penalized for the omission."""
    weights = weights or {c: 1.0 for c in YIELD_COLS}
    snap = add_percentile_ranks(snap)
    pctile_cols = [f"{c}_pctile" for c in YIELD_COLS]
    w = pd.Series({f"{c}_pctile": weights.get(c, 0.0) for c in YIELD_COLS})

    weighted_sum = snap[pctile_cols].fillna(0).mul(w, axis=1).sum(axis=1)
    weight_total = snap[pctile_cols].notna().mul(w, axis=1).sum(axis=1)
    snap["composite"] = (weighted_sum / weight_total).where(weight_total > 0)
    return snap


def add_rating(snap, n_buckets=5):
    """1 = cheapest quintile of composite score ... 5 = most expensive."""
    snap = snap.copy()
    valid = snap["composite"].notna()
    labels = list(range(n_buckets, 0, -1))  # ascending composite -> descending rating
    snap.loc[valid, "rating"] = pd.qcut(
        snap.loc[valid, "composite"], n_buckets, labels=labels, duplicates="drop"
    ).astype(float)
    return snap


def rate_snapshot(snap, weights=None, n_buckets=5):
    return add_rating(composite_score(snap, weights), n_buckets)
