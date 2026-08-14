"""
Point-in-time valuation snapshots.

Joins fundamentals *known as of* a given date (gated on filed_date, not
fiscal period end -- a fiscal year ending Dec 2024 usually isn't filed until
Feb/Mar 2025, so using fiscal_end as the cutoff would leak future
information into the snapshot) with the price on that date.
"""
import bisect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

import pandas as pd
from db import get_connection

# Annual figures only (10-K), to avoid stitching quarters together across
# possibly-restated, non-calendar fiscal years -- simpler and more robust
# than a true TTM roll-up given data quality across ~5,000 tickers.
ANNUAL_FLOW_METRICS = ("eps_diluted", "revenue")
# Balance-sheet snapshots: take whatever is most recently on file (10-K or 10-Q).
BALANCE_METRICS = ("stockholders_equity", "shares_outstanding")

MIN_PRICE = 1.0  # exclude sub-$1 tickers; ratios get noisy at penny-stock scale


def load_prices():
    with get_connection() as conn:
        df = pd.read_sql_query("SELECT symbol, date, close, adj_close FROM prices", conn)
    # Yahoo occasionally returns a corrupted adjusted close (seen: exactly
    # 0.0, and even negative) for a handful of tickers -- almost certainly a
    # bad split/dividend adjustment factor upstream. Left alone this turns
    # into an infinite or nonsensical "return" for anything that divides by
    # it, so treat non-positive adj_close as missing rather than real.
    df.loc[df["adj_close"] <= 0, "adj_close"] = None
    df.loc[df["close"] <= 0, "close"] = None
    return df


def load_fundamentals():
    with get_connection() as conn:
        return pd.read_sql_query(
            "SELECT symbol, metric, fiscal_end, form, value, filed_date FROM fundamentals", conn
        )


def nearest_trading_date(all_dates_sorted, target):
    """Latest trading date <= target."""
    i = bisect.bisect_right(all_dates_sorted, target) - 1
    return all_dates_sorted[i] if i >= 0 else None


def _latest_as_of(fund_df, as_of, metrics, forms=None):
    """For each symbol+metric: the value from the most recent fiscal period
    whose filing was already public (filed_date <= as_of)."""
    sub = fund_df[
        fund_df.metric.isin(metrics) & (fund_df.filed_date != "") & (fund_df.filed_date <= as_of)
    ]
    if forms:
        sub = sub[sub.form.isin(forms)]
    sub = sub.sort_values(["symbol", "metric", "fiscal_end"])
    latest = sub.groupby(["symbol", "metric"], as_index=False).last()
    return latest.pivot(index="symbol", columns="metric", values="value")


def build_snapshot(as_of, forward_to=None, price_df=None, fund_df=None):
    """Returns a DataFrame indexed by symbol with price, raw fundamentals,
    the three valuation yields, and (if forward_to given) forward_return."""
    price_df = price_df if price_df is not None else load_prices()
    fund_df = fund_df if fund_df is not None else load_fundamentals()

    all_dates = sorted(price_df["date"].unique())
    as_of_trading = nearest_trading_date(all_dates, as_of)
    # Raw close for "price": valuation ratios need the actual price paid per
    # share matched against EPS/book value as reported for that share count
    # at the time -- adjusted close is retroactively rescaled for splits and
    # dividends since (relative to today), which would mismatch historical
    # per-share fundamentals.
    asof_rows = price_df[price_df.date == as_of_trading].set_index("symbol")
    prices_asof = asof_rows["close"].rename("price")
    adj_prices_asof = asof_rows["adj_close"].rename("adj_price")

    annual = _latest_as_of(fund_df, as_of, ANNUAL_FLOW_METRICS, forms=("10-K",))
    balance = _latest_as_of(fund_df, as_of, BALANCE_METRICS)

    snap = prices_asof.to_frame().join(adj_prices_asof).join(annual, how="left").join(balance, how="left")
    snap = snap[snap["price"] >= MIN_PRICE].copy()

    snap["book_value_ps"] = snap.get("stockholders_equity") / snap.get("shares_outstanding")
    snap["sales_ps"] = snap.get("revenue") / snap.get("shares_outstanding")

    snap["earnings_yield"] = snap.get("eps_diluted") / snap["price"]
    if "eps_diluted" in snap:
        snap.loc[snap["eps_diluted"] <= 0, "earnings_yield"] = None

    snap["book_yield"] = snap["book_value_ps"] / snap["price"]
    snap.loc[snap["book_value_ps"] <= 0, "book_yield"] = None

    snap["sales_yield"] = snap["sales_ps"] / snap["price"]
    snap.loc[snap["sales_ps"] <= 0, "sales_yield"] = None

    if forward_to:
        fwd_trading = nearest_trading_date(all_dates, forward_to)
        # Adjusted close for the return calc: total return (splits +
        # dividends) is what "did cheap beat expensive" should measure, and
        # ratio-ing raw close would show a spurious swing on any ticker that
        # split or paid a large dividend between the two dates.
        fwd_adj_price = price_df[price_df.date == fwd_trading].set_index("symbol")["adj_close"].rename("forward_adj_price")
        snap = snap.join(fwd_adj_price, how="left")
        snap["forward_return"] = snap["forward_adj_price"] / snap["adj_price"] - 1

    snap.index.name = "symbol"
    return snap.reset_index()


if __name__ == "__main__":
    snap = build_snapshot("2024-01-02", forward_to="2025-01-02")
    print(f"snapshot rows: {len(snap)}")
    for col in ("earnings_yield", "book_yield", "sales_yield", "forward_return"):
        print(f"{col}: {snap[col].notna().sum()} non-null")
    print(snap[snap.symbol.isin(["AAPL", "MSFT", "GOOGL", "META"])])
