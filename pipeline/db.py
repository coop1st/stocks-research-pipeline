"""SQLite schema and connection helper for the stock data pipeline."""
import sqlite3
from contextlib import contextmanager

from config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS tickers (
    symbol TEXT PRIMARY KEY,
    name TEXT,
    exchange TEXT,
    security_type TEXT,
    cik TEXT,
    is_active INTEGER DEFAULT 1,
    updated_at TEXT,
    industry_sic TEXT,
    industry_sic_description TEXT,
    industry_category TEXT
);

CREATE TABLE IF NOT EXISTS prices (
    symbol TEXT NOT NULL,
    date TEXT NOT NULL,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    adj_close REAL,
    volume INTEGER,
    PRIMARY KEY (symbol, date)
);
CREATE INDEX IF NOT EXISTS idx_prices_date ON prices(date);

CREATE TABLE IF NOT EXISTS moving_averages (
    symbol TEXT NOT NULL,
    date TEXT NOT NULL,
    sma_20 REAL,
    sma_50 REAL,
    sma_100 REAL,
    sma_200 REAL,
    PRIMARY KEY (symbol, date)
);

CREATE TABLE IF NOT EXISTS price_indicators (
    symbol TEXT NOT NULL,
    date TEXT NOT NULL,
    rsi_14 REAL,
    high_52w REAL,
    low_52w REAL,
    pct_from_52w_high REAL,
    pct_from_52w_low REAL,
    range_position_52w REAL,
    volatility_3d REAL,
    volatility_7d REAL,
    dollar_volume_avg_20d REAL,
    PRIMARY KEY (symbol, date)
);

CREATE TABLE IF NOT EXISTS congress_transactions (
    politician_name TEXT NOT NULL,
    chamber TEXT,
    symbol TEXT NOT NULL,
    action TEXT NOT NULL,  -- 'buy' or 'sell'
    amount_range TEXT,
    trans_date TEXT NOT NULL,
    date_precision TEXT,   -- 'exact', 'month', or 'year' -- see data/congress_trades/README.md
    disclosed_date TEXT,
    notes TEXT,
    source_url TEXT,
    PRIMARY KEY (politician_name, symbol, action, trans_date, source_url)
);
CREATE INDEX IF NOT EXISTS idx_congress_symbol ON congress_transactions(symbol);

CREATE TABLE IF NOT EXISTS insider_transactions (
    symbol TEXT NOT NULL,
    accession_number TEXT NOT NULL,
    trans_sk TEXT NOT NULL,
    owner_name TEXT,
    relationship TEXT,
    trans_date TEXT,
    filed_date TEXT,
    shares REAL,
    price_per_share REAL,
    value REAL,
    PRIMARY KEY (accession_number, trans_sk)
);
CREATE INDEX IF NOT EXISTS idx_insider_symbol ON insider_transactions(symbol);
CREATE INDEX IF NOT EXISTS idx_insider_filed_date ON insider_transactions(filed_date);

CREATE TABLE IF NOT EXISTS fundamentals (
    symbol TEXT NOT NULL,
    metric TEXT NOT NULL,
    fiscal_end TEXT NOT NULL,
    form TEXT,
    value REAL,
    filed_date TEXT,
    PRIMARY KEY (symbol, metric, fiscal_end, form)
);
CREATE INDEX IF NOT EXISTS idx_fundamentals_symbol ON fundamentals(symbol);

CREATE TABLE IF NOT EXISTS earnings_events (
    symbol TEXT NOT NULL,
    earnings_date TEXT NOT NULL,     -- full timestamp incl. time-of-day, as returned by yfinance
    effective_date TEXT NOT NULL,    -- first trading-day close the market could have reacted by (BMO/AMC-aware, see fetch_earnings_events.py)
    eps_estimate REAL,
    reported_eps REAL,
    surprise_pct REAL,
    PRIMARY KEY (symbol, earnings_date)
);
CREATE INDEX IF NOT EXISTS idx_earnings_events_symbol ON earnings_events(symbol);

CREATE TABLE IF NOT EXISTS benchmark_prices (
    symbol TEXT NOT NULL,
    date TEXT NOT NULL,
    close REAL,
    PRIMARY KEY (symbol, date)
);

CREATE TABLE IF NOT EXISTS fetch_log (
    symbol TEXT NOT NULL,
    kind TEXT NOT NULL,  -- 'prices', 'fundamentals', or 'earnings'
    last_success TEXT,
    last_status TEXT,
    last_error TEXT,
    PRIMARY KEY (symbol, kind)
);
"""


@contextmanager
def get_connection():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# (column, definition) pairs added after the initial schema -- CREATE TABLE
# IF NOT EXISTS doesn't alter an already-existing table, so new columns need
# an explicit migration for databases created before they were added.
_MIGRATIONS = [
    ("tickers", "industry_sic", "TEXT"),
    ("tickers", "industry_sic_description", "TEXT"),
    ("tickers", "industry_category", "TEXT"),
    ("price_indicators", "volatility_3d", "REAL"),
    ("price_indicators", "volatility_7d", "REAL"),
    ("price_indicators", "dollar_volume_avg_20d", "REAL"),
]


def _run_migrations(conn):
    for table, column, coltype in _MIGRATIONS:
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")


def init_db():
    with get_connection() as conn:
        conn.executescript(SCHEMA)
        _run_migrations(conn)


def upsert_tickers(rows):
    """rows: iterable of dicts with symbol, name, exchange, security_type, cik"""
    with get_connection() as conn:
        conn.executemany(
            """
            INSERT INTO tickers (symbol, name, exchange, security_type, cik, is_active, updated_at)
            VALUES (:symbol, :name, :exchange, :security_type, :cik, 1, datetime('now'))
            ON CONFLICT(symbol) DO UPDATE SET
                name=excluded.name,
                exchange=excluded.exchange,
                security_type=excluded.security_type,
                cik=excluded.cik,
                is_active=1,
                updated_at=datetime('now')
            """,
            rows,
        )


def upsert_industry_classification(rows):
    """rows: iterable of dicts with symbol, industry_sic, industry_sic_description, industry_category"""
    with get_connection() as conn:
        conn.executemany(
            """
            UPDATE tickers SET
                industry_sic=:industry_sic,
                industry_sic_description=:industry_sic_description,
                industry_category=:industry_category
            WHERE symbol=:symbol
            """,
            rows,
        )


def upsert_prices(symbol, rows):
    """rows: iterable of dicts with date, open, high, low, close, adj_close, volume"""
    with get_connection() as conn:
        conn.executemany(
            """
            INSERT INTO prices (symbol, date, open, high, low, close, adj_close, volume)
            VALUES (:symbol, :date, :open, :high, :low, :close, :adj_close, :volume)
            ON CONFLICT(symbol, date) DO UPDATE SET
                open=excluded.open, high=excluded.high, low=excluded.low,
                close=excluded.close, adj_close=excluded.adj_close, volume=excluded.volume
            """,
            [{**r, "symbol": symbol} for r in rows],
        )


def upsert_benchmark_prices(symbol, rows):
    """rows: iterable of dicts with date, close. Deliberately a separate
    table from `prices` -- snapshot.py and other live-ratings scripts read
    `FROM prices` with no symbol allowlist, so benchmark ETFs (SPY/MDY/IJR)
    stored there would leak into the live weekly recommendation output."""
    with get_connection() as conn:
        conn.executemany(
            """
            INSERT INTO benchmark_prices (symbol, date, close)
            VALUES (:symbol, :date, :close)
            ON CONFLICT(symbol, date) DO UPDATE SET close=excluded.close
            """,
            [{**r, "symbol": symbol} for r in rows],
        )


def get_last_benchmark_price_date(symbol):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT MAX(date) FROM benchmark_prices WHERE symbol = ?", (symbol,)
        ).fetchone()
        return row[0] if row and row[0] else None


def upsert_moving_averages(symbol, rows):
    """rows: iterable of dicts with date, sma_20, sma_50, sma_100, sma_200"""
    with get_connection() as conn:
        conn.executemany(
            """
            INSERT INTO moving_averages (symbol, date, sma_20, sma_50, sma_100, sma_200)
            VALUES (:symbol, :date, :sma_20, :sma_50, :sma_100, :sma_200)
            ON CONFLICT(symbol, date) DO UPDATE SET
                sma_20=excluded.sma_20, sma_50=excluded.sma_50,
                sma_100=excluded.sma_100, sma_200=excluded.sma_200
            """,
            [{**r, "symbol": symbol} for r in rows],
        )


def upsert_fundamentals(symbol, rows):
    """rows: iterable of dicts with metric, fiscal_end, form, value, filed_date"""
    with get_connection() as conn:
        conn.executemany(
            """
            INSERT INTO fundamentals (symbol, metric, fiscal_end, form, value, filed_date)
            VALUES (:symbol, :metric, :fiscal_end, :form, :value, :filed_date)
            ON CONFLICT(symbol, metric, fiscal_end, form) DO UPDATE SET
                value=excluded.value, filed_date=excluded.filed_date
            """,
            [{**r, "symbol": symbol} for r in rows],
        )


def upsert_earnings_events(symbol, rows):
    """rows: iterable of dicts with earnings_date, effective_date,
    eps_estimate, reported_eps, surprise_pct"""
    with get_connection() as conn:
        conn.executemany(
            """
            INSERT INTO earnings_events
                (symbol, earnings_date, effective_date, eps_estimate, reported_eps, surprise_pct)
            VALUES (:symbol, :earnings_date, :effective_date, :eps_estimate, :reported_eps, :surprise_pct)
            ON CONFLICT(symbol, earnings_date) DO UPDATE SET
                effective_date=excluded.effective_date,
                eps_estimate=excluded.eps_estimate,
                reported_eps=excluded.reported_eps,
                surprise_pct=excluded.surprise_pct
            """,
            [{**r, "symbol": symbol} for r in rows],
        )


def log_fetch(symbol, kind, status, error=None):
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO fetch_log (symbol, kind, last_success, last_status, last_error)
            VALUES (?, ?, datetime('now'), ?, ?)
            ON CONFLICT(symbol, kind) DO UPDATE SET
                last_success=CASE WHEN ?='ok' THEN datetime('now') ELSE fetch_log.last_success END,
                last_status=?,
                last_error=?
            """,
            (symbol, kind, status, error, status, status, error),
        )


def get_last_price_date(symbol):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT MAX(date) FROM prices WHERE symbol = ?", (symbol,)
        ).fetchone()
        return row[0] if row and row[0] else None


def get_universe(active_only=True):
    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        q = "SELECT * FROM tickers"
        if active_only:
            q += " WHERE is_active = 1"
        return [dict(r) for r in conn.execute(q).fetchall()]


def get_classified_symbols():
    """Symbols that already have a stored SIC classification.

    Used by the monthly industry stage to fetch only what's missing -- SIC
    codes are stable for years, so refetching the whole universe every month
    is ~5,500 wasted SEC requests.
    """
    with get_connection() as conn:
        return {
            r[0]
            for r in conn.execute(
                "SELECT symbol FROM tickers "
                "WHERE industry_sic IS NOT NULL AND industry_sic <> ''"
            )
        }


def get_last_fetch_success(symbol, kind):
    """UTC timestamp of the last successful fetch_log entry, or None.

    Also used with a sentinel symbol to record whole-universe sweeps (see
    INDUSTRY_REFRESH_MARKER in run_pipeline.py).
    """
    with get_connection() as conn:
        row = conn.execute(
            "SELECT last_success FROM fetch_log WHERE symbol = ? AND kind = ?",
            (symbol, kind),
        ).fetchone()
        return row[0] if row and row[0] else None
