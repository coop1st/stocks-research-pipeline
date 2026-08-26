"""
Fully cloud-side day-trade shortlist scorer -- no local database, no
scikit-learn. Reproduces model/daytrade_live_shortlist.py's
build_shortlist() logic, but against a freshly-fetched short window of
Yahoo Finance data instead of the local 2GB price history, and using
the lookup-table artifact model/train_daytrade_model.py publishes
instead of an in-process trained model.

Deliberately self-contained (no imports from the rest of this repo):
it must run correctly in an ephemeral cloud sandbox that only has this
repo cloned, not pipeline/config.py's Windows-specific setup or the
local database.

Run from the repo root: `python cloud/daytrade_shortlist.py`
"""
import glob
import json
import os
import re
import sys
import time

os.environ["YF_DISABLE_CURL_CFFI"] = "1"  # this sandbox's TLS-terminating
# egress proxy rejects curl_cffi's browser-TLS-impersonation handshake;
# plain `requests` works fine through it. Set unconditionally (harmless
# and still correct when this script is run locally instead).

import numpy as np
import pandas as pd
import yfinance as yf

RSI_PERIOD = 14
DOLLAR_VOLUME_WINDOW = 20
MIN_DOLLAR_VOLUME = 5_000_000
CAP_BUCKET_THRESHOLDS = ((10e9, "large"), (2e9, "mid"), (300e6, "small"))
VALUATION_KEEP = {1, 2, 3}
QUALITY_KEEP = {1, 2}
TOP_DECILE = 0.10
BATCH_SIZE = 40
BATCH_PAUSE_SECONDS = 2
FETCH_PERIOD = "6mo"  # comfortably more than the ~90-120 trading days
# RSI-14's Wilder smoothing needs to settle, plus room for the 20-day
# dollar-volume window.

RATING_DESCRIPTORS = {
    "valuation_rating": {1: "extremely cheap", 2: "cheap", 3: "fairly priced", 4: "expensive", 5: "extremely expensive"},
    "quality_rating": {1: "strong improvement", 2: "improving", 3: "stable", 4: "deteriorating", 5: "strong deterioration"},
}
_SHARE_CLASS_SUFFIX = re.compile(r"\s*-?\s*(Class\s+[A-Za-z]\s+)?Common Stock\s*$", re.IGNORECASE)

OUT_PATH = "data/cache/daytrade_shortlist_today.csv"


def _rsi(adj_close):
    delta = adj_close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / RSI_PERIOD, adjust=False, min_periods=RSI_PERIOD).mean()
    avg_loss = loss.ewm(alpha=1 / RSI_PERIOD, adjust=False, min_periods=RSI_PERIOD).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _market_cap_bucket(market_cap):
    conditions = [market_cap >= floor for floor, _ in CAP_BUCKET_THRESHOLDS]
    choices = [name for _, name in CAP_BUCKET_THRESHOLDS]
    return np.select(conditions, choices, default=None)


def _clean_company_name(name):
    if pd.isna(name):
        return name
    return _SHARE_CLASS_SUFFIX.sub("", str(name)).strip()


def _describe_rating(col, value):
    if pd.isna(value):
        return "not available"
    return RATING_DESCRIPTORS[col].get(round(value), "not available")


def _rsi_confidence_word(rsi, tertile_lo, tertile_mid):
    if rsi <= tertile_lo:
        return "high"
    elif rsi <= tertile_mid:
        return "medium"
    return "low"


def _yf_symbol(symbol):
    return symbol.replace(".", "-")


def fetch_price_history(symbols):
    frames = {}
    yf_to_sym = {_yf_symbol(s): s for s in symbols}
    yf_symbols = list(yf_to_sym.keys())
    n_batches = (len(yf_symbols) - 1) // BATCH_SIZE + 1
    for i in range(0, len(yf_symbols), BATCH_SIZE):
        batch = yf_symbols[i:i + BATCH_SIZE]
        try:
            data = yf.download(batch, period=FETCH_PERIOD, group_by="ticker", auto_adjust=False, threads=True, progress=False)
        except Exception as e:
            print(f"batch {i // BATCH_SIZE + 1}/{n_batches} failed: {e}")
            time.sleep(BATCH_PAUSE_SECONDS)
            continue
        for yf_sym in batch:
            sym = yf_to_sym[yf_sym]
            try:
                sym_df = data if len(batch) == 1 else data[yf_sym]
                sym_df = sym_df.dropna(subset=["Close"])
                if not sym_df.empty:
                    frames[sym] = sym_df
            except Exception:
                continue
        print(f"fetched batch {i // BATCH_SIZE + 1}/{n_batches} ({len(frames)} ok so far)")
        time.sleep(BATCH_PAUSE_SECONDS)
    return frames


def compute_indicators(frames):
    rows = []
    for sym, df in frames.items():
        adj_close = df["Adj Close"]
        rsi = _rsi(adj_close)
        dollar_vol = (df["Close"] * df["Volume"]).rolling(DOLLAR_VOLUME_WINDOW, min_periods=DOLLAR_VOLUME_WINDOW).mean()
        if rsi.dropna().empty:
            continue
        rows.append({
            "symbol": sym,
            "last_close": float(df["Close"].iloc[-1]),
            "rsi_14": float(rsi.iloc[-1]) if pd.notna(rsi.iloc[-1]) else None,
            "dollar_volume_avg_20d": float(dollar_vol.iloc[-1]) if pd.notna(dollar_vol.iloc[-1]) else None,
        })
    return pd.DataFrame(rows)


def build_shortlist(today_df, ratings_df, model):
    today_df = today_df.merge(
        ratings_df[["symbol", "company_name", "shares_outstanding", "valuation_rating", "quality_rating", "industry_category"]],
        on="symbol", how="left",
    )
    today_df["market_cap"] = today_df["last_close"] * today_df["shares_outstanding"]
    today_df["cap_bucket"] = _market_cap_bucket(today_df["market_cap"])

    liquid = today_df[today_df["dollar_volume_avg_20d"] >= MIN_DOLLAR_VOLUME].copy()
    liquid = liquid.dropna(subset=["rsi_14", "cap_bucket"])

    picks = []
    for bucket in ["large", "mid", "small"]:
        b = model["buckets"][bucket]
        rsi_grid = np.array(b["rsi_grid"])
        proba_grid = np.array(b["proba_grid"])
        tertile_lo, tertile_mid, q1_cutoff = b["tertile_lo"], b["tertile_mid"], b["q1_cutoff"]

        bucket_today = liquid[liquid["cap_bucket"] == bucket].copy()
        if bucket_today.empty:
            continue
        bucket_today["proba"] = np.interp(bucket_today["rsi_14"], rsi_grid, proba_grid)

        proba_cutoff = np.quantile(bucket_today["proba"], 1 - TOP_DECILE)
        in_top_decile = bucket_today["proba"] >= proba_cutoff
        in_q1 = bucket_today["rsi_14"] < q1_cutoff
        bucket_picks = bucket_today[in_top_decile & in_q1].copy()
        bucket_picks["rsi_confidence"] = bucket_picks["rsi_14"].apply(
            lambda r: _rsi_confidence_word(r, tertile_lo, tertile_mid)
        )
        picks.append(bucket_picks)

    shortlist = pd.concat(picks, ignore_index=True) if picks else pd.DataFrame()
    if shortlist.empty:
        return shortlist

    valuation_ok = shortlist["valuation_rating"].isin(VALUATION_KEEP) | shortlist["valuation_rating"].isna()
    quality_ok = shortlist["quality_rating"].isin(QUALITY_KEEP) | shortlist["quality_rating"].isna()
    shortlist = shortlist[valuation_ok & quality_ok].copy()

    shortlist["company_name"] = shortlist["company_name"].apply(_clean_company_name)
    shortlist["industry_category"] = shortlist["industry_category"].fillna("Other")
    shortlist["valuation_desc"] = shortlist["valuation_rating"].apply(lambda v: _describe_rating("valuation_rating", v))
    shortlist["quality_desc"] = shortlist["quality_rating"].apply(lambda v: _describe_rating("quality_rating", v))

    shortlist["display_line"] = shortlist.apply(
        lambda r: (
            f"{r['symbol']} ({r['company_name']}) -- last close ${r['last_close']:.2f} -- "
            f"RSI {r['rsi_14']:.1f} ({r['rsi_confidence']} confidence buy signal) -- "
            f"valuation: {r['valuation_desc']}, quality: {r['quality_desc']}"
        ),
        axis=1,
    )
    return shortlist.sort_values(["cap_bucket", "industry_category", "proba"], ascending=[True, True, False])


def main():
    ratings_files = sorted(glob.glob("data/github_sync/ratings/*.csv"))
    model_files = sorted(glob.glob("data/github_sync/daytrade_model/*.json"))
    if not ratings_files or not model_files:
        print("SHORTLIST_ERROR missing ratings or model artifact in data/github_sync/")
        sys.exit(1)

    ratings_path, model_path = ratings_files[-1], model_files[-1]
    ratings_df = pd.read_csv(ratings_path)
    with open(model_path) as f:
        model = json.load(f)

    symbols = ratings_df["symbol"].dropna().unique().tolist()
    print(f"Fetching {len(symbols)} symbols from ratings snapshot {ratings_path}...")
    frames = fetch_price_history(symbols)
    print(f"Got usable data for {len(frames)}/{len(symbols)} symbols")

    today_df = compute_indicators(frames)
    shortlist = build_shortlist(today_df, ratings_df, model)

    os.makedirs("data/cache", exist_ok=True)
    out_cols = ["symbol", "company_name", "cap_bucket", "industry_category", "last_close", "display_line"]
    if shortlist.empty:
        pd.DataFrame(columns=out_cols).to_csv(OUT_PATH, index=False)
    else:
        shortlist[out_cols].to_csv(OUT_PATH, index=False)

    print(f"SHORTLIST_READY rows={len(shortlist)} ratings_as_of={ratings_path} model_as_of={model_path}")


if __name__ == "__main__":
    main()
