"""
Insider buying flag: boolean 1/0, not a 1-5 rating like the other
indicators. 1 = at least one insider open-market purchase's *filing*
became public within the trailing DECAY_DAYS window of as_of; 0/absent
otherwise.

Point-in-time correct: gated on filed_date (when the purchase became
public knowledge), not trans_date (when the purchase actually happened).
Form 4 requires disclosure within ~2 business days, so the gap is usually
small, but filed_date is what avoids lookahead bias in a backtest.

Only returns rows for FLAGGED tickers (a sparse event) -- left-join
against the full universe and treat missing as 0 downstream.

Also carries cluster_buy_count (distinct insiders who bought within the
window -- multiple insiders buying together is a stronger signal than one
lone purchase) and days_since_last_purchase, so a later refinement (e.g.
decaying the signal's weight instead of a flat on/off, or requiring 2+
buyers) doesn't need new data, just a different derivation from what's
already stored.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

import pandas as pd
from db import get_connection

DECAY_DAYS = 180  # ~6 months


def load_insider_transactions():
    with get_connection() as conn:
        return pd.read_sql_query(
            "SELECT symbol, owner_name, trans_date, filed_date, value FROM insider_transactions", conn
        )


def rate_insider_buying(as_of, decay_days=DECAY_DAYS, trans_df=None):
    trans_df = trans_df if trans_df is not None else load_insider_transactions()
    window_start = (pd.Timestamp(as_of) - pd.Timedelta(days=decay_days)).strftime("%Y-%m-%d")

    in_window = trans_df[(trans_df["filed_date"] > window_start) & (trans_df["filed_date"] <= as_of)]
    if in_window.empty:
        return pd.DataFrame(columns=[
            "symbol", "insider_buying_flag", "cluster_buy_count",
            "total_value", "most_recent_filed_date", "days_since_last_purchase",
        ])

    out = in_window.groupby("symbol").agg(
        cluster_buy_count=("owner_name", "nunique"),
        total_value=("value", "sum"),
        most_recent_filed_date=("filed_date", "max"),
    ).reset_index()
    out["insider_buying_flag"] = 1
    out["days_since_last_purchase"] = (
        pd.Timestamp(as_of) - pd.to_datetime(out["most_recent_filed_date"])
    ).dt.days

    return out


if __name__ == "__main__":
    from datetime import date

    today = date.today().isoformat()
    rated = rate_insider_buying(today)
    print(f"as_of: {today}, tickers flagged: {len(rated)}")
    print(rated.sort_values("total_value", ascending=False).head(15).to_string(index=False))
