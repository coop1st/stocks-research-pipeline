"""
Quick "is my data current" check -- reads the last scheduled run's status
plus live freshness checks. Doesn't re-run or fetch anything, safe to run
anytime.

Usage:
    python check_status.py
"""
import json
from datetime import date

from config import PROJECT_DIR
from db import get_connection

STATUS_PATH = PROJECT_DIR / "data" / "logs" / "last_run_status.json"


def main():
    with get_connection() as conn:
        latest_price = conn.execute("SELECT MAX(date) FROM prices").fetchone()[0]
        latest_filed = conn.execute("SELECT MAX(filed_date) FROM fundamentals").fetchone()[0]
        latest_ratings = conn.execute("SELECT MAX(as_of_date) FROM ratings").fetchone()[0] \
            if conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='ratings'"
            ).fetchone() else None
        n_prices = conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0]
        n_tickers = conn.execute("SELECT COUNT(*) FROM tickers").fetchone()[0]

    print(f"Tickers in universe: {n_tickers}")
    print(f"Prices: latest date {latest_price} ({n_prices:,} total rows)"
          + (f"  [{(date.today() - date.fromisoformat(latest_price)).days} days old]" if latest_price else ""))
    print(f"Fundamentals: latest filing {latest_filed}")
    print(f"Ratings snapshot: as of {latest_ratings}"
          + (f"  [{(date.today() - date.fromisoformat(latest_ratings)).days} days old]" if latest_ratings else " (never computed)"))

    if STATUS_PATH.exists():
        status = json.loads(STATUS_PATH.read_text())
        print(f"\nLast scheduled run: {status.get('run_type')} at {status.get('started_at')}")
        print(f"Overall status: {status.get('overall_status', 'unknown').upper()}")
        for r in status.get("stages", []):
            marker = "OK" if r["status"] == "success" else "FAIL"
            print(f"  [{marker}] {r['stage']} ({r.get('elapsed_s', '?')}s)")
            if r["status"] == "failed":
                print(f"        {r.get('error')}")
        if status.get("warnings"):
            print("Warnings:")
            for w in status["warnings"]:
                print(f"  - {w}")
        if status.get("log_file"):
            print(f"Full log: {status['log_file']}")
    else:
        print("\nNo scheduled run status found yet -- has the scheduled task run at least once?")
        print("Run manually with: python scheduled_run.py weekly")


if __name__ == "__main__":
    main()
