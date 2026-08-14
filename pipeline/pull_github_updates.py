"""
Pull the cloud-fetched weekly price data from GitHub and merge it into the
local historical database.

This is the local half of the split described in pipeline/README.md: the
GitHub Actions weekly-price-fetch workflow runs with no local machine
needed and commits a small dated CSV; this script (run whenever the local
machine is next on) pulls that down and upserts it into the real database
using the same upsert_prices logic the local incremental fetch uses --
re-processing an already-merged file is a harmless no-op, so there's no
need to track "have I seen this file before" state.
"""
import subprocess
from pathlib import Path

import pandas as pd

from config import PROJECT_DIR
from db import upsert_prices

PRICES_SYNC_DIR = PROJECT_DIR / "data" / "github_sync" / "prices"


def git_pull():
    result = subprocess.run(
        ["git", "pull"], cwd=PROJECT_DIR, capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git pull failed: {result.stderr}")
    return result.stdout.strip()


def merge_synced_prices():
    if not PRICES_SYNC_DIR.exists():
        return {"files": 0, "rows": 0}

    files = sorted(PRICES_SYNC_DIR.glob("*.csv"))
    total_rows = 0
    for f in files:
        df = pd.read_csv(f)
        if df.empty:
            continue
        for symbol, group in df.groupby("symbol"):
            rows = group[["date", "open", "high", "low", "close", "adj_close", "volume"]].to_dict("records")
            upsert_prices(symbol, rows)
            total_rows += len(rows)

    return {"files": len(files), "rows": total_rows}


def pull_and_merge():
    print("Pulling latest from GitHub...")
    pull_output = git_pull()
    print(pull_output)

    print("Merging synced price files into local database...")
    result = merge_synced_prices()
    print(f"Merged {result['rows']} rows from {result['files']} file(s)")
    return result


if __name__ == "__main__":
    from db import init_db

    init_db()
    pull_and_merge()
