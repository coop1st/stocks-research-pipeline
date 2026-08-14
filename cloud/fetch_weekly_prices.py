"""
Stateless weekly price fetch for GitHub Actions.

Unlike pipeline/fetch_prices.py (which diffs against the local database
to fetch only what's missing), this has no database to diff against --
the Actions runner starts fresh every time. So instead it just pulls a
fixed trailing window (comfortably more than the ~7 days between weekly
runs) for every ticker, unconditionally, and writes the result to a small
dated CSV that gets committed to the repo. The local pipeline
(pipeline/pull_github_updates.py) picks it up later, whenever the local
machine is next on, and merges it into the real historical database with
the usual upsert logic (so re-fetching a few overlapping days here causes
no harm -- it's just a no-op update on the local side).

Known limitation: Yahoo Finance (via yfinance, an unofficial API) can be
more prone to blocking/rate-limiting requests from shared cloud/CI IP
ranges than from a home connection. If a run fails or comes back mostly
empty, that's expected occasionally -- the local pipeline can always
independently backfill anything missed, since it does its own proper
incremental fetch against Yahoo whenever it runs.
"""
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

import pandas as pd
import yfinance as yf

from universe import build_universe

TRAILING_DAYS = 10
BATCH_SIZE = 40
REPO_ROOT = Path(__file__).resolve().parent.parent
PRICES_OUT_DIR = REPO_ROOT / "data" / "github_sync" / "prices"
UNIVERSE_OUT_PATH = REPO_ROOT / "data" / "github_sync" / "universe.csv"


def _chunk(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def _yf_symbol(symbol):
    return symbol.replace(".", "-")


def fetch_and_save():
    print("Building universe...")
    universe = build_universe()
    symbols = [r["symbol"] for r in universe]
    print(f"{len(symbols)} tickers")

    start = (date.today() - timedelta(days=TRAILING_DAYS)).isoformat()
    rows = []
    ok_batches, failed_batches = 0, 0

    for batch in _chunk(symbols, BATCH_SIZE):
        yf_to_sym = {_yf_symbol(s): s for s in batch}
        try:
            data = yf.download(
                list(yf_to_sym.keys()), start=start, group_by="ticker",
                auto_adjust=False, threads=True, progress=False,
            )
        except Exception as e:
            print(f"  batch failed ({len(batch)} tickers): {e}")
            failed_batches += 1
            continue

        for yf_sym, orig_sym in yf_to_sym.items():
            try:
                sym_df = data if len(batch) == 1 else data[yf_sym]
            except Exception:
                continue
            for idx, r in sym_df.iterrows():
                close = r.get("Close")
                if pd.isna(close):
                    continue
                rows.append({
                    "symbol": orig_sym,
                    "date": idx.strftime("%Y-%m-%d"),
                    "open": r["Open"], "high": r["High"], "low": r["Low"],
                    "close": close, "adj_close": r["Adj Close"], "volume": r["Volume"],
                })
        ok_batches += 1

    df = pd.DataFrame(rows)
    PRICES_OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = PRICES_OUT_DIR / f"{date.today().isoformat()}.csv"
    df.to_csv(out_path, index=False)
    print(f"batches: {ok_batches} ok, {failed_batches} failed")
    print(f"Wrote {len(df)} price rows to {out_path}")

    UNIVERSE_OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(universe).to_csv(UNIVERSE_OUT_PATH, index=False)
    print(f"Wrote universe ({len(universe)} tickers) to {UNIVERSE_OUT_PATH}")

    return out_path, len(df)


if __name__ == "__main__":
    fetch_and_save()
