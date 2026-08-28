"""
Weekly rating-shortlist ledger -- persists the high-conviction STRONG
BUY/STRONG SELL shortlist that draft_weekly_email.py computes, so a
future project (options trading, built separately) can read a directional
bias per ticker the same way it already reads the day-trade shortlist
ledger (cloud/update_stock_ledger.py) for its watchlist.

Runs as a weekly-job stage, right before email_draft, since it reuses
draft_weekly_email.build_shortlist()'s already-computed, already-filtered
DataFrame (STRONG BUY/STRONG SELL by recommendation_score AND all three
of valuation/momentum/quality independently at their own extreme
threshold -- see draft_weekly_email.py's docstring). This is the
pre-industry-sentiment-exclusion list: the exclusion rule applied inside
draft_weekly_email's `claude -p` step is a soft, non-reproducible
judgment call meant for the human-facing email narrative, not something
a downstream mechanical trade-selection system should inherit.

Ledger file: data/github_sync/weekly_ratings_ledger/stock_rating_ledger.csv

Columns:
  symbol          -- ticker, one row per ticker ever shortlisted
  company_name    -- most recently seen company name for this ticker
  <date columns>  -- one column per week this stage has run (the
                     ratings snapshot's as_of_date), populated with
                     "RECOMMENDATION/last_close" (e.g. "STRONG BUY/152.30")
                     for any ticker shortlisted that week, blank
                     otherwise. Unlike the daily day-trade ledger, gaps
                     between date columns aren't backfilled to every
                     calendar day -- weekly cadence gaps are normal and
                     not tracked here.
"""
import csv
import subprocess

from config import PROJECT_DIR
from draft_weekly_email import build_shortlist

LEDGER_PATH = PROJECT_DIR / "data" / "github_sync" / "weekly_ratings_ledger" / "stock_rating_ledger.csv"
FIXED_COLS = ["symbol", "company_name"]


def load_ledger():
    if not LEDGER_PATH.exists():
        return {}, []
    with open(LEDGER_PATH, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        date_cols = [c for c in reader.fieldnames if c not in FIXED_COLS]
        rows = {row["symbol"]: row for row in reader}
        return rows, date_cols


def update_ledger():
    ratings_path, shortlist = build_shortlist()
    if shortlist.empty:
        return None, 0, "no_shortlist"

    # The week's date isn't a shortlist column -- recover it from the ratings
    # filename (build_shortlist reads _latest_file(RATINGS_DIR), named
    # "{as_of_date}.csv").
    week_col = ratings_path.stem

    ledger, date_cols = load_ledger()
    if week_col not in date_cols:
        date_cols.append(week_col)

    new_tickers = 0
    for _, row in shortlist.iterrows():
        symbol = row["symbol"]
        if symbol not in ledger:
            ledger[symbol] = {c: "" for c in FIXED_COLS + date_cols}
            ledger[symbol]["symbol"] = symbol
            new_tickers += 1
        ledger[symbol]["company_name"] = row["company_name"]
        price = f"{row['last_close']:.2f}" if row["last_close"] == row["last_close"] else ""  # NaN check
        ledger[symbol][week_col] = f"{row['recommendation']}/{price}" if price else row["recommendation"]

    for symbol, row in ledger.items():
        for c in date_cols:
            row.setdefault(c, "")

    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    header = FIXED_COLS + date_cols
    with open(LEDGER_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=header)
        writer.writeheader()
        for symbol in sorted(ledger.keys()):
            writer.writerow({k: ledger[symbol].get(k, "") for k in header})

    return week_col, len(shortlist), "written"


def commit_and_push(paths, message):
    paths = [str(p) for p in paths]
    subprocess.run(["git", "add", *paths], cwd=PROJECT_DIR, check=True, timeout=60)
    diff = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=PROJECT_DIR, timeout=30)
    if diff.returncode == 0:
        return "no_changes"
    subprocess.run(["git", "commit", "-m", message], cwd=PROJECT_DIR, check=True, timeout=60)
    subprocess.run(["git", "push"], cwd=PROJECT_DIR, check=True, timeout=120)
    return "pushed"


def run_publish_rating_shortlist_stage():
    week_col, n_rows, status = update_ledger()
    if status == "no_shortlist":
        print("No STRONG BUY/STRONG SELL shortlist this week -- nothing to publish")
        return {"status": "no_shortlist", "rows": 0}

    print(f"Updated rating shortlist ledger: week={week_col} tickers_this_week={n_rows}")
    push_status = commit_and_push(
        [LEDGER_PATH], f"Weekly rating shortlist: {week_col} ({n_rows} tickers)",
    )
    print(f"Publish status: {push_status}")
    return {"status": push_status, "week": week_col, "rows": n_rows}


if __name__ == "__main__":
    print(run_publish_rating_shortlist_stage())
