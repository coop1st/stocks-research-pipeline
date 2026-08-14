"""
Fetch SEC's free bulk quarterly Form 3/4/5 (insider transactions) datasets,
filter to open-market purchases (TRANS_CODE == 'P', acquired not disposed)
by CIKs in our universe, and store in the `insider_transactions` table.

Source: https://www.sec.gov/data-research/sec-markets-data/insider-transactions-data-sets
One ZIP per quarter (~5-15MB), containing the *entire* market's insider
filings in structured TSV -- this is far lighter than fetching individual
Form 4 filings one at a time (which would mean tens of thousands of
requests across our universe).

Only open-market purchases count here (TRANS_CODE 'P' + TRANS_ACQUIRED_DISP_CD
'A'). Grants, option exercises, gifts, and tax withholding (A/M/G/F/... codes)
aren't a "this person spent their own money because they think the stock is
going up" signal the way an open-market buy is, so they're excluded.
"""
import io
import zipfile
from datetime import date

import pandas as pd
import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from config import SEC_USER_AGENT
from db import get_connection

HEADERS = {"User-Agent": SEC_USER_AGENT}
BASE_URL = "https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/{quarter}_form345.zip"

SUB_COLS = ["ACCESSION_NUMBER", "FILING_DATE", "ISSUERCIK"]
TRANS_COLS = [
    "ACCESSION_NUMBER", "NONDERIV_TRANS_SK", "TRANS_DATE", "TRANS_CODE",
    "TRANS_SHARES", "TRANS_PRICEPERSHARE", "TRANS_ACQUIRED_DISP_CD",
]
OWNER_COLS = ["ACCESSION_NUMBER", "RPTOWNERNAME", "RPTOWNER_RELATIONSHIP"]


def _quarters_since(start_year):
    today = date.today()
    current_quarter = (today.month - 1) // 3 + 1
    quarters = []
    for year in range(start_year, today.year + 1):
        max_q = 4 if year < today.year else current_quarter
        for q in range(1, max_q + 1):
            quarters.append(f"{year}q{q}")
    return quarters


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=2, max=30))
def _download_quarter_zip(quarter):
    url = BASE_URL.format(quarter=quarter)
    r = requests.get(url, headers=HEADERS, timeout=120)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return zipfile.ZipFile(io.BytesIO(r.content))


def _parse_quarter(z, cik_to_symbol):
    sub = pd.read_csv(z.open("SUBMISSION.tsv"), sep="\t", usecols=SUB_COLS, dtype=str)
    sub["cik_int"] = pd.to_numeric(sub["ISSUERCIK"], errors="coerce")
    sub = sub[sub["cik_int"].isin(cik_to_symbol)]
    if sub.empty:
        return pd.DataFrame()

    nd = pd.read_csv(z.open("NONDERIV_TRANS.tsv"), sep="\t", usecols=TRANS_COLS, dtype=str)
    nd = nd[(nd["TRANS_CODE"] == "P") & (nd["TRANS_ACQUIRED_DISP_CD"] == "A")]
    if nd.empty:
        return pd.DataFrame()

    ro = pd.read_csv(z.open("REPORTINGOWNER.tsv"), sep="\t", usecols=OWNER_COLS, dtype=str)
    ro = ro.drop_duplicates(subset="ACCESSION_NUMBER")  # good enough for a "did an insider buy" signal

    merged = nd.merge(sub, on="ACCESSION_NUMBER", how="inner").merge(ro, on="ACCESSION_NUMBER", how="left")
    if merged.empty:
        return merged

    merged["symbol"] = merged["cik_int"].map(cik_to_symbol)
    merged["shares"] = pd.to_numeric(merged["TRANS_SHARES"], errors="coerce")
    merged["price_per_share"] = pd.to_numeric(merged["TRANS_PRICEPERSHARE"], errors="coerce")
    merged["value"] = merged["shares"] * merged["price_per_share"]

    for src, dst in (("FILING_DATE", "filed_date"), ("TRANS_DATE", "trans_date")):
        merged[dst] = pd.to_datetime(merged[src], format="%d-%b-%Y", errors="coerce").dt.strftime("%Y-%m-%d")

    merged = merged.rename(columns={
        "ACCESSION_NUMBER": "accession_number",
        "NONDERIV_TRANS_SK": "trans_sk",
        "RPTOWNERNAME": "owner_name",
        "RPTOWNER_RELATIONSHIP": "relationship",
    })
    return merged[[
        "symbol", "accession_number", "trans_sk", "owner_name", "relationship",
        "trans_date", "filed_date", "shares", "price_per_share", "value",
    ]].dropna(subset=["symbol", "filed_date"])


def _store(df):
    with get_connection() as conn:
        conn.executemany(
            """
            INSERT INTO insider_transactions
                (symbol, accession_number, trans_sk, owner_name, relationship,
                 trans_date, filed_date, shares, price_per_share, value)
            VALUES (:symbol, :accession_number, :trans_sk, :owner_name, :relationship,
                    :trans_date, :filed_date, :shares, :price_per_share, :value)
            ON CONFLICT(accession_number, trans_sk) DO UPDATE SET
                symbol=excluded.symbol, owner_name=excluded.owner_name,
                relationship=excluded.relationship, trans_date=excluded.trans_date,
                filed_date=excluded.filed_date, shares=excluded.shares,
                price_per_share=excluded.price_per_share, value=excluded.value
            """,
            df.to_dict("records"),
        )


def fetch_insider_transactions(cik_to_symbol, start_year=2020, verbose=True):
    """cik_to_symbol: dict of {int(cik): symbol} for the universe to match against."""
    total_rows = 0
    for q in _quarters_since(start_year):
        z = _download_quarter_zip(q)
        if z is None:
            if verbose:
                print(f"  {q}: not available yet, skipping")
            continue
        df = _parse_quarter(z, cik_to_symbol)
        if not df.empty:
            _store(df)
            total_rows += len(df)
        if verbose:
            print(f"  {q}: {len(df)} matching purchase transactions")
    return total_rows


if __name__ == "__main__":
    from db import get_universe, init_db

    init_db()
    universe = get_universe()
    cik_to_symbol = {int(t["cik"]): t["symbol"] for t in universe if t["cik"]}
    print(f"Matching against {len(cik_to_symbol)} CIKs")
    n = fetch_insider_transactions(cik_to_symbol)
    print(f"Total stored: {n}")
