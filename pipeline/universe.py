"""
Build the tradable stock universe from two free, keyless sources:

- Nasdaq Trader listed-symbol files: authoritative daily list of every symbol
  listed on Nasdaq/NYSE/NYSE American/etc, with an ETF flag and test-issue flag.
  This is our free proxy for "the whole US market" (true Russell 3000
  constituents are proprietary FTSE Russell data and not freely available).
- SEC company_tickers.json: maps ticker -> CIK, needed later to pull
  fundamentals from EDGAR.

Result is the common-stock universe (ETFs, warrants, units, rights, and test
issues excluded) with a CIK attached where SEC has one on file.
"""
import requests

from config import NASDAQ_TRADER_LISTED_URLS, SEC_TICKER_MAP_URL, SEC_USER_AGENT

HEADERS = {"User-Agent": SEC_USER_AGENT}

# Security-name substrings that indicate a non-common-stock instrument.
# Deliberately does NOT match "- class" or "depositary" alone: multi-class
# common stock (GOOGL/GOOG, META, FOX/FOXA, NWS/NWSA, ...) legitimately uses
# "- Class A/B/C Common Stock" in its name, and foreign issuers often only
# trade as "... Depositary/Depository Shares" representing ordinary common
# stock (ADRs). Preferred depositary shares still get excluded via
# " preferred" since their names spell that out (e.g. "... representing a
# 1/20th Interest in a Share of ... Preferred Stock").
_EXCLUDE_NAME_MARKERS = (
    " warrant", " warrants", " unit", " units", " rights", " right",
    " preferred", " notes", " debenture",
)


def _fetch_sec_ticker_cik_map():
    r = requests.get(SEC_TICKER_MAP_URL, headers=HEADERS, timeout=30)
    r.raise_for_status()
    data = r.json()
    return {row["ticker"].upper(): str(row["cik_str"]).zfill(10) for row in data.values()}


def _parse_pipe_file(text):
    lines = text.strip().split("\n")
    header = lines[0].split("|")
    rows = []
    for line in lines[1:]:
        if not line or line.startswith("File Creation Time"):
            continue
        fields = line.split("|")
        if len(fields) != len(header):
            continue
        rows.append(dict(zip(header, fields)))
    return rows


def _fetch_nasdaq_listed():
    r = requests.get(NASDAQ_TRADER_LISTED_URLS["nasdaq"], headers=HEADERS, timeout=30)
    r.raise_for_status()
    out = []
    for row in _parse_pipe_file(r.text):
        out.append({
            "symbol": row["Symbol"].strip().upper(),
            "name": row["Security Name"].strip(),
            "exchange": "NASDAQ",
            "is_etf": row.get("ETF") == "Y",
            "is_test": row.get("Test Issue") == "Y",
        })
    return out


_OTHER_EXCHANGE_CODES = {
    "A": "NYSE American",
    "N": "NYSE",
    "P": "NYSE Arca",
    "Z": "Cboe BZX",
    "V": "IEX",
}


def _fetch_other_listed():
    r = requests.get(NASDAQ_TRADER_LISTED_URLS["other"], headers=HEADERS, timeout=30)
    r.raise_for_status()
    out = []
    for row in _parse_pipe_file(r.text):
        symbol = row.get("ACT Symbol", "").strip().upper()
        if not symbol:
            continue
        out.append({
            "symbol": symbol,
            "name": row["Security Name"].strip(),
            "exchange": _OTHER_EXCHANGE_CODES.get(row.get("Exchange", ""), row.get("Exchange", "")),
            "is_etf": row.get("ETF") == "Y",
            "is_test": row.get("Test Issue") == "Y",
        })
    return out


def _looks_like_common_stock(name):
    lowered = name.lower()
    return not any(marker in lowered for marker in _EXCLUDE_NAME_MARKERS)


def build_universe():
    """Returns a list of dicts: symbol, name, exchange, security_type, cik."""
    listed = _fetch_nasdaq_listed() + _fetch_other_listed()
    cik_map = _fetch_sec_ticker_cik_map()

    seen = {}
    for row in listed:
        if row["is_etf"] or row["is_test"]:
            continue
        if not _looks_like_common_stock(row["name"]):
            continue
        symbol = row["symbol"]
        # Nasdaq Trader symbols can carry class suffixes (e.g. BRK.A shown as BRK/A);
        # skip anything with characters yfinance/SEC won't recognize cleanly.
        if any(ch in symbol for ch in ("$", "+", "=")):
            continue
        if symbol in seen:
            continue
        # SEC's ticker map uses hyphens for share classes (BRK-B) where
        # Nasdaq Trader uses periods (BRK.B) -- same mismatch as Yahoo Finance.
        seen[symbol] = {
            "symbol": symbol,
            "name": row["name"],
            "exchange": row["exchange"],
            "security_type": "common_stock",
            "cik": cik_map.get(symbol) or cik_map.get(symbol.replace(".", "-")),
        }

    return sorted(seen.values(), key=lambda r: r["symbol"])


if __name__ == "__main__":
    rows = build_universe()
    with_cik = sum(1 for r in rows if r["cik"])
    print(f"Universe size: {len(rows)} (CIK matched: {with_cik})")
    for r in rows[:10]:
        print(r)
