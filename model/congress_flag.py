"""
Congress trading flags: two booleans, congress_buy_flag and
congress_sell_flag -- 1 if a member of Congress disclosed an open-market
buy (or sell) of the ticker within the trailing DECAY_DAYS window of
as_of, 0/absent otherwise. Same convention as insider_flag.py.

IMPORTANT -- this is NOT a validated indicator. Unlike everything else in
this project, there's no reliable free structured data source for
congressional trading (see data/congress_trades/README.md): the two open
free trackers that used to exist are dead or years stale, and the
official government disclosures are unstructured PDFs, not a data feed.
This is instead a small, hand-compiled list (42 trades, 2023-2026) built
by searching news coverage year by year -- it is sparse, skews heavily
toward a few high-profile/frequently-covered members (Pelosi, Gottheimer,
Whitehouse) and large round-dollar trades, and gets thinner the further
back you go (1 trade found for all of 2023 vs ~16-19/year for 2025-2026).
It is not statistically meaningful and was not backtested. Present in the
data for visibility, not intended to feed the confluence scoring model.

Point-in-time note: `disclosed_date` is only known for a handful of rows
(where the source article stated it explicitly); the rest fall back to
`trans_date`, so this doesn't have the same disclosure-lag rigor as
insider_flag.py's SEC-sourced data.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

import pandas as pd
from db import get_connection

DECAY_DAYS = 180  # ~6 months, matching insider_flag.py


def load_congress_transactions():
    with get_connection() as conn:
        df = pd.read_sql_query(
            "SELECT politician_name, chamber, symbol, action, amount_range, "
            "trans_date, date_precision, disclosed_date, notes FROM congress_transactions",
            conn,
        )
    # Fall back to trans_date where disclosed_date isn't known (see module docstring).
    # SQLite round-trips missing CSV values as '' rather than NULL, so blank out
    # empty strings before fillna -- fillna alone doesn't touch '' as if it were NaN.
    disclosed = df["disclosed_date"].replace("", None)
    df["effective_date"] = disclosed.fillna(df["trans_date"])
    return df


def _rate_action(as_of, action, decay_days, trans_df):
    window_start = (pd.Timestamp(as_of) - pd.Timedelta(days=decay_days)).strftime("%Y-%m-%d")
    sub = trans_df[trans_df["action"] == action]
    in_window = sub[(sub["effective_date"] > window_start) & (sub["effective_date"] <= as_of)]

    flag_col = f"congress_{action}_flag"
    if in_window.empty:
        return pd.DataFrame(columns=["symbol", flag_col, "politician_names", "trade_count"])

    out = in_window.groupby("symbol").agg(
        politician_names=("politician_name", lambda s: ", ".join(sorted(set(s)))),
        trade_count=("politician_name", "count"),
    ).reset_index()
    out[flag_col] = 1
    return out


def rate_congress_buying(as_of, decay_days=DECAY_DAYS, trans_df=None):
    trans_df = trans_df if trans_df is not None else load_congress_transactions()
    return _rate_action(as_of, "buy", decay_days, trans_df)


def rate_congress_selling(as_of, decay_days=DECAY_DAYS, trans_df=None):
    trans_df = trans_df if trans_df is not None else load_congress_transactions()
    return _rate_action(as_of, "sell", decay_days, trans_df)


if __name__ == "__main__":
    from datetime import date

    today = date.today().isoformat()
    trans_df = load_congress_transactions()
    buys = rate_congress_buying(today, trans_df=trans_df)
    sells = rate_congress_selling(today, trans_df=trans_df)
    print(f"as_of: {today}")
    print(f"\ncongress_buy_flag=1 for {len(buys)} tickers:")
    print(buys.to_string(index=False))
    print(f"\ncongress_sell_flag=1 for {len(sells)} tickers:")
    print(sells.to_string(index=False))
