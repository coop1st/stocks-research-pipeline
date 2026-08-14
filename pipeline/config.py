"""Shared configuration for the stock data pipeline."""
import os
from pathlib import Path

# Some local network setups (e.g. antivirus/firewall software that does TLS
# inspection) generate certificates that Python's bundled OpenSSL validator
# rejects on strict X.509 technicalities, even though the OS's own validator
# (and browsers) accept them fine. Routing through the OS-native validator
# instead avoids that mismatch without weakening certificate validation --
# this is strictly "trust what Windows already trusts," not "skip
# verification." Safe to call unconditionally; a no-op if nothing intercepts
# traffic on a given machine.
import truststore

truststore.inject_into_ssl()

PIPELINE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PIPELINE_DIR.parent
DB_PATH = PROJECT_DIR / "data" / "db" / "stocks.db"

# How much daily price history to backfill on first fetch.
HISTORY_YEARS = 5

# Fundamentals need a longer lookback than price history: the earliest price
# anchor date needs the *most recently filed* annual report as of that date,
# and annual reports lag their fiscal year end by up to ~90 days (e.g. a
# 2022-01-03 snapshot for a calendar-fiscal-year company needs FY2020 data,
# since the FY2021 10-K isn't filed until Feb/Mar 2022) -- so fetch fundamentals
# further back than HISTORY_YEARS or the earliest backtest year is starved of data.
FUNDAMENTALS_HISTORY_YEARS = HISTORY_YEARS + 2

# yfinance batch download: tickers per request and pause between requests.
# Yahoo has no published hard limit, but batching + pacing avoids IP throttling.
PRICE_BATCH_SIZE = 40
PRICE_BATCH_PAUSE_SECONDS = 2.0

# SEC EDGAR requires a descriptive User-Agent with contact info (fair access
# policy: https://www.sec.gov/os/webmaster-faq#developers). Pulled from an
# env var rather than hardcoded so a real email address never ends up in the
# (public) repo -- set it once with:
#   [Environment]::SetEnvironmentVariable("SEC_CONTACT_EMAIL", "you@example.com", "User")
_SEC_CONTACT_EMAIL = os.environ.get("SEC_CONTACT_EMAIL", "set-SEC_CONTACT_EMAIL-env-var@example.com")
SEC_USER_AGENT = f"Personal stock research pipeline {_SEC_CONTACT_EMAIL}"
SEC_REQUEST_PAUSE_SECONDS = 0.15  # SEC allows up to ~10 req/sec; stay well under

# Universe sources (both free, no API key required)
SEC_TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
NASDAQ_LISTED_URL = "https://api.nasdaq.com/api/screener/stocks?tableonly=true&limit=25&exchange=nasdaq"
NASDAQ_TRADER_LISTED_URLS = {
    "nasdaq": "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt",
    "other": "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt",
}

# Fundamentals: XBRL concepts pulled from SEC companyfacts, mapped to a friendly
# name. Listed as candidate tags per metric because companies vary in which
# US-GAAP tag they report under (e.g. most switched from "Revenues" to
# "RevenueFromContractWithCustomerExcludingAssessedTax" after adopting ASC 606
# around 2018) -- all candidates are pulled in and merged under one metric name.
FUNDAMENTAL_CONCEPTS = {
    "eps_diluted": ("EarningsPerShareDiluted",),
    "revenue": (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
    ),
    "net_income": ("NetIncomeLoss",),
    "stockholders_equity": ("StockholdersEquity",),
    "shares_outstanding": (
        "CommonStockSharesOutstanding",
        "EntityCommonStockSharesOutstanding",
    ),
    "total_assets": ("Assets",),
    "total_liabilities": ("Liabilities",),
    "operating_income": ("OperatingIncomeLoss",),
    "cash_and_equivalents": ("CashAndCashEquivalentsAtCarryingValue",),
    # Added for the Piotroski F-Score quality indicator.
    "operating_cash_flow": (
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ),
    "current_assets": ("AssetsCurrent",),
    "current_liabilities": ("LiabilitiesCurrent",),
    "long_term_debt": ("LongTermDebtNoncurrent", "LongTermDebt"),
    "gross_profit": ("GrossProfit",),
}

# Which of the above are duration facts (cover a start->end period, e.g.
# annual/quarterly earnings) vs instant facts (a single balance-sheet date).
# Used to sanity-check that a "duration" value we're treating as annual
# actually spans ~12 months, since XBRL filings mix quarterly and annual
# durations under the same concept.
DURATION_METRICS = {
    "eps_diluted", "revenue", "net_income", "operating_income",
    "operating_cash_flow", "gross_profit",
}

# SEC SIC code (Standard Industrial Classification) ranges bucketed into a
# manageable set of ~25 categories for the industry-sentiment overlay --
# raw SIC has ~1,000 codes, far too granular to hand-score sentiment on
# weekly. Ranges are inclusive (low, high, category). Checked in order;
# first match wins. Anything unmatched falls into "Other".
SIC_CATEGORY_RANGES = [
    (100, 999, "Agriculture"),
    (1000, 1099, "Mining & Minerals"),
    (1200, 1299, "Coal Mining"),
    (1300, 1399, "Oil & Gas"),
    (1400, 1499, "Mining & Minerals"),
    (1500, 1799, "Construction & Materials"),
    (2000, 2199, "Food & Beverage"),
    (2200, 2399, "Apparel & Textiles"),
    (2400, 2599, "Wood & Furniture"),
    (2600, 2699, "Paper"),
    (2700, 2799, "Publishing & Media"),
    (2830, 2836, "Pharmaceuticals & Biotech"),
    (2800, 2899, "Chemicals"),
    (2900, 2999, "Oil & Gas"),
    (3000, 3199, "Rubber, Plastics & Leather"),
    (3200, 3299, "Construction & Materials"),
    (3300, 3399, "Metals"),
    (3400, 3499, "Industrial Machinery"),
    (3570, 3579, "Computer Hardware"),
    (3672, 3679, "Semiconductors & Chips"),
    (3500, 3699, "Industrial Machinery"),
    (3700, 3719, "Automobiles"),
    (3720, 3729, "Aerospace & Defense"),
    (3760, 3769, "Aerospace & Defense"),
    (3700, 3799, "Transportation Equipment"),
    (3800, 3899, "Medical Devices & Instruments"),
    (3900, 3999, "Misc Manufacturing"),
    (4000, 4099, "Railroads"),
    (4500, 4599, "Airlines"),
    (4000, 4799, "Transportation & Logistics"),
    (4800, 4899, "Telecom & Media"),
    (4900, 4999, "Utilities"),
    (5000, 5199, "Wholesale Trade"),
    (5200, 5999, "Retail"),
    (6000, 6199, "Banks & Financial Services"),
    (6200, 6299, "Investment & Brokerage"),
    (6300, 6499, "Insurance"),
    (6500, 6599, "Real Estate & REITs"),
    (6700, 6799, "Investment & Holding Companies"),
    (7370, 7379, "Technology & Software"),
    (7000, 7099, "Hospitality & Leisure"),
    (7800, 7999, "Hospitality & Leisure"),
    (8000, 8099, "Healthcare Services"),
    (8700, 8799, "Business & Research Services"),
    (7000, 8999, "Services"),
    (9000, 9999, "Public Administration"),
]


def sic_to_category(sic_code):
    if not sic_code:
        return "Other"
    try:
        code = int(sic_code)
    except (TypeError, ValueError):
        return "Other"
    for low, high, category in SIC_CATEGORY_RANGES:
        if low <= code <= high:
            return category
    return "Other"
