"""
Load the hand-compiled congress trades CSV into the `congress_transactions`
table.

Unlike every other fetcher in this pipeline, this is NOT an automated,
re-runnable data feed -- there's no reliable free structured source for
congressional trading data (see data/congress_trades/README.md for why).
This is a one-time, manually-researched compilation from news articles
(mostly Benzinga's real-time government-trades coverage, plus retrospective
"top trades of the year" pieces for completed years), built by searching
year by year until the number of findable individual trades dropped close
to zero (2023). Rerun this after manually adding more rows to the CSV.
"""
import csv

from config import PROJECT_DIR
from db import get_connection, init_db

CSV_PATH = PROJECT_DIR / "data" / "congress_trades" / "congress_trades_2023_2026.csv"


def load():
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    with get_connection() as conn:
        conn.execute("DELETE FROM congress_transactions")
        conn.executemany(
            """
            INSERT INTO congress_transactions
                (politician_name, chamber, symbol, action, amount_range,
                 trans_date, date_precision, disclosed_date, notes, source_url)
            VALUES (:politician_name, :chamber, :symbol, :action, :amount_range,
                    :trans_date, :date_precision, :disclosed_date, :notes, :source_url)
            """,
            rows,
        )
    return len(rows)


if __name__ == "__main__":
    init_db()
    n = load()
    print(f"Loaded {n} congress trades from {CSV_PATH}")
