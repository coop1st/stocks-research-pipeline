"""
Fetch each ticker's industry classification (SIC code) from SEC's free
submissions endpoint and bucket it into a broad category (see
SIC_CATEGORY_RANGES in config.py) for the industry-sentiment overlay.

One request per CIK, same rate-limit pacing as fetch_fundamentals.py.
Changes rarely (a company's SIC code is stable for years), so this only
needs to run monthly, not weekly.
"""
import time

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from config import SEC_USER_AGENT, sic_to_category
from db import log_fetch, upsert_industry_classification

HEADERS = {"User-Agent": SEC_USER_AGENT}
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
REQUEST_PAUSE_SECONDS = 0.12


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=2, max=20))
def _get_submission(cik):
    r = requests.get(SUBMISSIONS_URL.format(cik=cik), headers=HEADERS, timeout=20)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def fetch_industry_classification(symbol_cik_pairs, verbose=True):
    ok_count, fail_count, skip_count = 0, 0, 0
    batch = []

    for symbol, cik in symbol_cik_pairs:
        if not cik:
            skip_count += 1
            continue
        try:
            data = _get_submission(cik)
            if data is None:
                log_fetch(symbol, "industry", "no_edgar_record")
                skip_count += 1
                continue
            sic = data.get("sic") or None
            sic_desc = data.get("sicDescription") or None
            category = sic_to_category(sic)
            batch.append({
                "symbol": symbol, "industry_sic": sic,
                "industry_sic_description": sic_desc, "industry_category": category,
            })
            log_fetch(symbol, "industry", "ok")
            ok_count += 1
            if verbose and ok_count % 500 == 0:
                print(f"  {ok_count} done...")
        except Exception as e:
            log_fetch(symbol, "industry", "error", str(e))
            fail_count += 1
        time.sleep(REQUEST_PAUSE_SECONDS)

    if batch:
        upsert_industry_classification(batch)

    return {"ok": ok_count, "failed": fail_count, "skipped": skip_count}


if __name__ == "__main__":
    from db import get_universe, init_db

    init_db()
    universe = get_universe()
    pairs = [(t["symbol"], t["cik"]) for t in universe if t["cik"]]
    print(f"Fetching industry classification for {len(pairs)} tickers...")
    result = fetch_industry_classification(pairs)
    print(result)
