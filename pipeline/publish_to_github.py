"""
Export the latest ratings/recommendations snapshot and push it to GitHub.

This is the local weekly job's final step: after prices are merged and
every indicator (including the confluence recommendation) is recomputed
against the full local history, the small resulting snapshot -- not the
2GB+ database it was computed from -- gets published, so it's the record
a later step (the Gmail-drafting Claude routine) reads from.
"""
import subprocess
from pathlib import Path

import pandas as pd

from config import PROJECT_DIR
from db import get_connection

RATINGS_SYNC_DIR = PROJECT_DIR / "data" / "github_sync" / "ratings"


def export_latest_ratings():
    with get_connection() as conn:
        latest = conn.execute("SELECT MAX(as_of_date) FROM ratings").fetchone()[0]
        if not latest:
            return None, 0
        df = pd.read_sql_query("SELECT * FROM ratings WHERE as_of_date = ?", conn, params=(latest,))

    RATINGS_SYNC_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RATINGS_SYNC_DIR / f"{latest}.csv"
    df.to_csv(out_path, index=False)
    return out_path, len(df)


def commit_and_push(paths, message):
    paths = [str(p) for p in paths]
    subprocess.run(["git", "add", *paths], cwd=PROJECT_DIR, check=True, timeout=60)

    diff = subprocess.run(
        ["git", "diff", "--cached", "--quiet"], cwd=PROJECT_DIR, timeout=30,
    )
    if diff.returncode == 0:
        return "no_changes"

    subprocess.run(["git", "commit", "-m", message], cwd=PROJECT_DIR, check=True, timeout=60)
    subprocess.run(["git", "push"], cwd=PROJECT_DIR, check=True, timeout=120)
    return "pushed"


def publish():
    out_path, n_rows = export_latest_ratings()
    if out_path is None:
        print("No ratings to publish yet")
        return {"status": "no_ratings", "rows": 0}

    print(f"Exported {n_rows} rows to {out_path}")
    status = commit_and_push([out_path], f"Weekly ratings: {out_path.stem} ({n_rows} tickers)")
    print(f"Publish status: {status}")
    return {"status": status, "rows": n_rows, "path": str(out_path)}


if __name__ == "__main__":
    publish()
