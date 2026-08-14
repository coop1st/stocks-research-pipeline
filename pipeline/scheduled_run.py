"""
Unattended entry point for the Windows Scheduled Tasks that keep this
pipeline updated (see data/logs/README.md for the schedule and how to
change it).

Unlike run_pipeline.py (built for interactive use, where an exception
crashing the process is fine -- you're watching it), this is built to run
completely unattended with nobody watching:

- Every stage is isolated in a try/except -- one stage failing (e.g. a
  Yahoo rate-limit) doesn't prevent the others from running.
- Everything is logged to a timestamped file under data/logs/, not just
  stdout, since a scheduled task's console output normally goes nowhere.
- A machine-readable data/logs/last_run_status.json is written every run,
  and a post-run health check flags data staleness or an elevated fetch
  failure rate -- check_status.py reads this to give a plain-English
  answer to "is my data still current" without re-running anything.
- The weekly run's last step recomputes and persists every indicator's
  current rating (model/compute_all_ratings.py) into the `ratings` table,
  so "recalculate everything on the new data" covers the scored outputs
  too, not just the raw prices/moving-averages/price-indicators inputs
  they're built from.
- The weekly run starts by pulling the cloud-fetched price data GitHub
  Actions gathered since the last time this machine was on (no need to
  re-fetch what's already sitting there), and ends by publishing the
  fresh ratings/recommendations snapshot back to GitHub -- that published
  file is what the separately-scheduled Claude routine reads to draft the
  weekly recommendation email.

Usage (what the scheduled tasks actually call):
    python scheduled_run.py weekly
    python scheduled_run.py monthly
"""
import json
import sys
import time
import traceback
from datetime import date, datetime
from pathlib import Path

from config import PROJECT_DIR
from db import get_connection, get_universe, init_db
from run_pipeline import (
    run_fundamentals_stage,
    run_insider_transactions_stage,
    run_moving_averages_stage,
    run_price_indicators_stage,
    run_prices_stage,
    run_universe_stage,
)

from publish_to_github import publish as publish_ratings
from pull_github_updates import pull_and_merge

sys.path.insert(0, str(PROJECT_DIR / "model"))
from compute_all_ratings import compute_and_store as compute_all_ratings  # noqa: E402

LOG_DIR = PROJECT_DIR / "data" / "logs"
STATUS_PATH = LOG_DIR / "last_run_status.json"
STALE_PRICE_DAYS = 10  # weekly runs should keep this well under; margin for a missed week
STALE_FUNDAMENTALS_DAYS = 45  # monthly runs should keep this well under


class _Tee:
    """Mirrors writes to multiple streams -- lets stdout go to both the
    console (when run interactively) and the log file (always)."""

    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for s in self.streams:
            s.write(data)
            s.flush()

    def flush(self):
        for s in self.streams:
            s.flush()


def run_stage_safely(name, fn, *args):
    print(f"\n{'=' * 70}\n[{name}] starting at {datetime.now().isoformat()}\n{'=' * 70}")
    t0 = time.time()
    try:
        result = fn(*args)
        elapsed = time.time() - t0
        print(f"[{name}] SUCCESS in {elapsed:.1f}s: {result}")
        return {"stage": name, "status": "success", "elapsed_s": round(elapsed, 1), "result": str(result)[:500]}
    except Exception as e:
        elapsed = time.time() - t0
        print(f"[{name}] FAILED after {elapsed:.1f}s: {e}")
        traceback.print_exc()
        return {"stage": name, "status": "failed", "elapsed_s": round(elapsed, 1), "error": str(e)}


def health_check():
    """Post-run sanity checks. Returns a list of warning strings (empty if clean)."""
    warnings = []
    with get_connection() as conn:
        latest_price_date = conn.execute("SELECT MAX(date) FROM prices").fetchone()[0]
        if latest_price_date:
            days_stale = (date.today() - date.fromisoformat(latest_price_date)).days
            if days_stale > STALE_PRICE_DAYS:
                warnings.append(f"prices are {days_stale} days stale (latest: {latest_price_date})")
        else:
            warnings.append("prices table is empty")

        latest_filed = conn.execute("SELECT MAX(filed_date) FROM fundamentals").fetchone()[0]
        if latest_filed:
            days_stale = (date.today() - date.fromisoformat(latest_filed)).days
            if days_stale > STALE_FUNDAMENTALS_DAYS:
                warnings.append(f"fundamentals haven't seen a new filing in {days_stale} days (latest: {latest_filed})")

        recent_price_failures = conn.execute(
            "SELECT COUNT(*) FROM fetch_log WHERE kind='prices' AND last_status NOT IN ('ok', 'empty_result') "
            "AND last_success > datetime('now', '-8 days')"
        ).fetchone()[0]
        total_tickers = conn.execute("SELECT COUNT(*) FROM tickers").fetchone()[0]
        if total_tickers and recent_price_failures > total_tickers * 0.1:
            warnings.append(
                f"{recent_price_failures} of {total_tickers} tickers failed to fetch prices in the last week "
                "(elevated -- check for a yfinance breakage, not just normal individual-symbol noise)"
            )

    return warnings


def run_weekly():
    results = [run_stage_safely("pull_github_updates", pull_and_merge)]
    results.append(run_stage_safely("universe", run_universe_stage))
    tickers = get_universe()
    if tickers:
        results.append(run_stage_safely("prices", run_prices_stage, [t["symbol"] for t in tickers]))
    else:
        results.append({"stage": "prices", "status": "failed", "elapsed_s": 0, "error": "no tickers in universe"})
    results.append(run_stage_safely("moving_averages", run_moving_averages_stage))
    results.append(run_stage_safely("price_indicators", run_price_indicators_stage))
    results.append(run_stage_safely("ratings", compute_all_ratings))
    results.append(run_stage_safely("publish_ratings", publish_ratings))
    return results


def run_monthly():
    tickers = get_universe()
    if not tickers:
        return [{"stage": "fundamentals", "status": "failed", "elapsed_s": 0, "error": "no tickers in universe"}]
    pairs = [(t["symbol"], t["cik"]) for t in tickers if t["cik"]]
    return [
        run_stage_safely("fundamentals", run_fundamentals_stage, pairs),
        run_stage_safely("insider_transactions", run_insider_transactions_stage, tickers),
    ]


def main():
    run_type = sys.argv[1] if len(sys.argv) > 1 else "weekly"
    if run_type not in ("weekly", "monthly"):
        print(f"Unknown run type {run_type!r}, expected 'weekly' or 'monthly'")
        sys.exit(1)

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"{run_type}_{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.log"
    original_stdout = sys.stdout

    with open(log_path, "w", encoding="utf-8") as logfile:
        sys.stdout = _Tee(original_stdout, logfile)
        exit_code = 0
        try:
            init_db()
            start = time.time()
            results = run_weekly() if run_type == "weekly" else run_monthly()
            warnings = health_check()
            elapsed = time.time() - start

            failed = [r for r in results if r["status"] == "failed"]
            overall = "failed" if failed else ("warning" if warnings else "success")

            status = {
                "run_type": run_type,
                "started_at": datetime.now().isoformat(),
                "elapsed_s": round(elapsed, 1),
                "stages": results,
                "warnings": warnings,
                "overall_status": overall,
                "log_file": str(log_path),
            }
            STATUS_PATH.write_text(json.dumps(status, indent=2))

            print(f"\n{'=' * 70}\nOVERALL: {overall.upper()} in {elapsed:.1f}s")
            if warnings:
                print("Warnings:")
                for w in warnings:
                    print(f"  - {w}")
            if failed:
                print("Failed stages:")
                for r in failed:
                    print(f"  - {r['stage']}: {r['error']}")
                exit_code = 1
            print("=" * 70)
        except Exception:
            print("UNHANDLED ERROR in scheduled_run.py itself:")
            traceback.print_exc()
            STATUS_PATH.write_text(json.dumps({
                "run_type": run_type,
                "started_at": datetime.now().isoformat(),
                "overall_status": "failed",
                "error": "unhandled exception in scheduled_run.py -- see log file",
                "log_file": str(log_path),
            }, indent=2))
            exit_code = 1
        finally:
            sys.stdout = original_stdout

    print(f"[{run_type}] done, log written to {log_path}")
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
