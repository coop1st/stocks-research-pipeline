"""
Orchestrates the full data pipeline: universe -> prices -> fundamentals.

Usage:
    python run_pipeline.py --stage universe
    python run_pipeline.py --stage prices --limit 50
    python run_pipeline.py --stage fundamentals --limit 50
    python run_pipeline.py --stage all --limit 50      # smoke test
    python run_pipeline.py --stage all                 # full universe run

--limit restricts to the first N tickers (alphabetically) of the stored
universe -- useful for testing before committing to a full run, since a
full run over ~5000 tickers takes a while and hits two rate-limited free
APIs (Yahoo via yfinance, SEC EDGAR).
"""
import argparse
import time

from compute_moving_averages import compute_and_store as compute_moving_averages
from compute_price_indicators import compute_and_store as compute_price_indicators
from db import get_universe, init_db, upsert_tickers
from fetch_fundamentals import fetch_fundamentals_for
from fetch_insider_transactions import fetch_insider_transactions
from fetch_prices import fetch_prices_for
from universe import build_universe


def run_universe_stage():
    print("[universe] building ticker list from Nasdaq Trader + SEC...")
    rows = build_universe()
    upsert_tickers(rows)
    with_cik = sum(1 for r in rows if r["cik"])
    print(f"[universe] stored {len(rows)} tickers ({with_cik} with CIK matched)")
    return rows


def run_prices_stage(symbols):
    print(f"[prices] fetching OHLCV for {len(symbols)} tickers...")
    result = fetch_prices_for(symbols)
    print(f"[prices] done: {result}")
    return result


def run_moving_averages_stage():
    # Pure local computation over the whole prices table -- always full,
    # --limit doesn't apply here (a symbol's SMA needs its own full history,
    # not a subset, and recomputing everything only takes ~30s).
    print("[moving_averages] computing SMAs from stored prices...")
    n = compute_moving_averages()
    print(f"[moving_averages] done: {n} rows")
    return n


def run_price_indicators_stage():
    # Same story as moving averages: pure local computation, always full.
    print("[price_indicators] computing RSI-14 and 52-week high/low from stored prices...")
    n = compute_price_indicators()
    print(f"[price_indicators] done: {n} rows")
    return n


def run_fundamentals_stage(symbol_cik_pairs):
    print(f"[fundamentals] fetching SEC fundamentals for {len(symbol_cik_pairs)} tickers...")
    result = fetch_fundamentals_for(symbol_cik_pairs, verbose=False)
    print(f"[fundamentals] done: {result}")
    return result


def run_insider_transactions_stage(tickers):
    # Full historical refetch every time (~2-3 min for the whole universe,
    # cheap enough not to bother with incremental logic) -- SEC publishes
    # this as one bulk file per quarter, not a per-ticker feed.
    cik_to_symbol = {int(t["cik"]): t["symbol"] for t in tickers if t["cik"]}
    print(f"[insider_transactions] fetching SEC bulk Form 3/4/5 data for {len(cik_to_symbol)} tickers...")
    n = fetch_insider_transactions(cik_to_symbol, verbose=False)
    print(f"[insider_transactions] done: {n} purchase transactions stored")
    return n



ALL_STAGES = (
    "universe", "prices", "moving_averages", "price_indicators",
    "fundamentals", "insider_transactions",
)

# Stages worth running on a weekly cadence: universe (cheap, catches new
# listings), prices (incremental -- only the missing days get fetched, so
# a weekly run is fast even though the initial backfill wasn't), and the
# two pure-local recomputations that depend on fresh prices. Deliberately
# excludes fundamentals (companies file quarterly, not weekly -- a full
# refetch costs ~50-60 min for ~0 new information most weeks) and
# insider_transactions (SEC publishes its bulk file per quarter with a
# lag, so a weekly refetch would mostly redownload data that hasn't
# changed). Run those two monthly instead; congress_trades is manual
# research, updated ad hoc via load_congress_trades.py.
WEEKLY_STAGES = ("universe", "prices", "moving_averages", "price_indicators")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--stage",
        choices=[*ALL_STAGES, "weekly", "all"],
        default="all",
    )
    parser.add_argument("--limit", type=int, default=None, help="restrict to first N tickers (testing)")
    args = parser.parse_args()

    init_db()
    start = time.time()
    if args.stage == "all":
        active_stages = set(ALL_STAGES)
    elif args.stage == "weekly":
        active_stages = set(WEEKLY_STAGES)
    else:
        active_stages = {args.stage}

    if "universe" in active_stages:
        run_universe_stage()

    # These two are pure local computation over the whole prices table --
    # no ticker list needed, unlike the fetch stages below.
    if "moving_averages" in active_stages:
        run_moving_averages_stage()

    if "price_indicators" in active_stages:
        run_price_indicators_stage()

    needs_tickers = active_stages & {"prices", "fundamentals", "insider_transactions"}
    if needs_tickers:
        tickers = get_universe()
        if not tickers:
            print("No tickers stored yet -- run --stage universe first.")
            return
        if args.limit:
            tickers = tickers[: args.limit]

        if "prices" in active_stages:
            run_prices_stage([t["symbol"] for t in tickers])

        if "fundamentals" in active_stages:
            pairs = [(t["symbol"], t["cik"]) for t in tickers if t["cik"]]
            run_fundamentals_stage(pairs)

        if "insider_transactions" in active_stages:
            run_insider_transactions_stage(tickers)

    elapsed = time.time() - start
    print(f"\nDone in {elapsed:.1f}s")


if __name__ == "__main__":
    main()
