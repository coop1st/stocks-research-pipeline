"""
Fetch fundamental line items (EPS, revenue, net income, equity, shares out,
etc.) from SEC EDGAR's free XBRL companyfacts API and store them in the
`fundamentals` table.

No API key needed, but SEC requires a descriptive User-Agent (see config.py)
and asks that you stay under ~10 requests/second.
"""
import time
from datetime import date, timedelta

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from config import (
    DURATION_METRICS,
    FUNDAMENTAL_CONCEPTS,
    FUNDAMENTALS_HISTORY_YEARS,
    SEC_REQUEST_PAUSE_SECONDS,
    SEC_USER_AGENT,
)
from db import log_fetch, upsert_fundamentals

HEADERS = {"User-Agent": SEC_USER_AGENT}
COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=2, max=20))
def _get_companyfacts(cik):
    r = requests.get(COMPANYFACTS_URL.format(cik=cik), headers=HEADERS, timeout=20)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def _is_annual_duration(entry):
    """True if a duration fact's start->end span is ~12 months. XBRL filings
    mix quarterly and annual durations under the same concept/form (e.g. a
    10-K can carry a Q4-only figure alongside the full-year one), so this
    guards against treating a quarterly value as if it were annual."""
    start, end = entry.get("start"), entry.get("end")
    if not start or not end:
        return False
    days = (date.fromisoformat(end) - date.fromisoformat(start)).days
    return 330 <= days <= 400


def _extract_rows(facts, cutoff_date):
    gaap = facts.get("facts", {}).get("us-gaap", {})
    candidates = {}  # (metric, fiscal_end, form) -> row, keeping earliest filed_date

    for friendly_name, concept_candidates in FUNDAMENTAL_CONCEPTS.items():
        is_duration = friendly_name in DURATION_METRICS
        for concept in concept_candidates:
            concept_data = gaap.get(concept)
            if not concept_data:
                continue
            for unit_name, entries in concept_data.get("units", {}).items():
                for e in entries:
                    end = e.get("end")
                    if not end or end < cutoff_date:
                        continue
                    if e.get("val") is None:
                        continue
                    if is_duration and not _is_annual_duration(e):
                        continue
                    filed = e.get("filed", "")
                    if not filed:
                        continue
                    key = (friendly_name, end, e.get("form", ""))
                    # A restated/comparative figure re-reported in a later
                    # filing shouldn't push back when this fact first became
                    # public -- keep whichever filing disclosed it earliest.
                    if key not in candidates or filed < candidates[key]["filed_date"]:
                        candidates[key] = {
                            "metric": friendly_name,
                            "fiscal_end": end,
                            "form": e.get("form", ""),
                            "value": float(e["val"]),
                            "filed_date": filed,
                        }

    return list(candidates.values())


def fetch_fundamentals_for(symbol_cik_pairs, verbose=True):
    """symbol_cik_pairs: iterable of (symbol, cik) tuples. cik should already
    be zero-padded to 10 digits (as stored by universe.build_universe)."""
    cutoff = (date.today() - timedelta(days=365 * FUNDAMENTALS_HISTORY_YEARS)).isoformat()
    ok_count, fail_count, skip_count = 0, 0, 0

    for symbol, cik in symbol_cik_pairs:
        if not cik:
            skip_count += 1
            continue
        try:
            facts = _get_companyfacts(cik)
            if facts is None:
                log_fetch(symbol, "fundamentals", "no_edgar_record")
                skip_count += 1
                continue
            rows = _extract_rows(facts, cutoff)
            if rows:
                upsert_fundamentals(symbol, rows)
            log_fetch(symbol, "fundamentals", "ok")
            ok_count += 1
            if verbose:
                print(f"  {symbol}: {len(rows)} fundamental data points")
        except Exception as e:
            log_fetch(symbol, "fundamentals", "error", str(e))
            fail_count += 1
            if verbose:
                print(f"  {symbol}: failed - {e}")
        time.sleep(SEC_REQUEST_PAUSE_SECONDS)

    return {"ok": ok_count, "failed": fail_count, "skipped": skip_count}


if __name__ == "__main__":
    from db import init_db

    init_db()
    test_pairs = [
        ("AAPL", "0000320193"),
        ("MSFT", "0000789019"),
        ("NVDA", "0001045810"),
    ]
    result = fetch_fundamentals_for(test_pairs)
    print(result)
