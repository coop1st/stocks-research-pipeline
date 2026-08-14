"""
Score the current universe: builds today's valuation snapshot, rates every
ticker 1 (extremely cheap) - 5 (extremely expensive) relative to its peers
right now, using metric weights fit from the full backtest history.

Usage:
    python rate_universe.py                  # print cheapest/most expensive 20
    python rate_universe.py --symbol AAPL     # look up one ticker
    python rate_universe.py --out ratings.csv # write full ranked table
"""
import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

from backtest import ANCHORS, build_all_snapshots, fit_weights
from rating import rate_snapshot
from snapshot import build_snapshot, load_fundamentals, load_prices


def fit_live_weights():
    """Use every historical year as training data (no held-out fold, since
    this is meant for live use, not evaluation) to get the final weights."""
    snaps = build_all_snapshots()
    weights, avg_ic = fit_weights(list(snaps.values()))
    return weights, avg_ic


def rate_today():
    price_df = load_prices()
    fund_df = load_fundamentals()
    latest_date = price_df["date"].max()
    weights, avg_ic = fit_live_weights()
    snap = build_snapshot(latest_date, price_df=price_df, fund_df=fund_df)
    rated = rate_snapshot(snap, weights=weights)
    return rated, latest_date, weights, avg_ic


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", help="look up a single ticker")
    parser.add_argument("--out", help="write full ranked table to this CSV path")
    parser.add_argument("--top", type=int, default=20, help="how many to show in each direction")
    args = parser.parse_args()

    rated, as_of, weights, avg_ic = rate_today()
    print(f"As of {as_of}. Metric weights (fit on full 2022-2025 history): "
          + ", ".join(f"{k}={v:.3f}" for k, v in weights.items()))
    print("Historical avg IC per metric: "
          + ", ".join(f"{k}={v:.3f}" if v == v else f"{k}=n/a" for k, v in avg_ic.items()))

    cols = ["symbol", "price", "rating", "composite", "earnings_yield", "book_yield", "sales_yield"]
    ranked = rated.dropna(subset=["rating"]).sort_values("composite", ascending=False)

    if args.symbol:
        row = rated[rated.symbol == args.symbol.upper()]
        if row.empty:
            print(f"{args.symbol} not found in today's snapshot (no price and/or fundamentals).")
        else:
            print(row[cols].to_string(index=False))
        return

    if args.out:
        ranked[cols].to_csv(args.out, index=False)
        print(f"Wrote {len(ranked)} rated tickers to {args.out}")
        return

    print(f"\nCheapest {args.top} (rating 1, highest composite):")
    print(ranked[cols].head(args.top).to_string(index=False))
    print(f"\nMost expensive {args.top} (rating 5, lowest composite):")
    print(ranked[cols].tail(args.top).to_string(index=False))


if __name__ == "__main__":
    main()
