# Day-trade shortlist: fully cloud daily automation

Status: approved design, not yet implemented.

## Problem

The day-trade shortlist automation built in the previous session (see
`HANDOVER.md`) runs entirely as a local Windows Scheduled Task
(`StocksPipeline-DailyDayTrade`, Mon-Fri 2am): fetch prices for the
liquid universe, recompute indicators, build the RSI-alone model
shortlist, then shell out to `claude -p` for web search and Gmail
drafting. This requires the local PC to be on, awake, and undisturbed
for up to an hour every weekday. The first live end-to-end test of this
task failed silently overnight (2026-08-17/18) when a brief
sleep/resume event interrupted the long-running `claude -p` subprocess
partway through — the run died with no checkpoint written and no
Gmail draft produced, and had to be manually rerun the next morning.

Kevin wants the daily task to run without the local PC being on or
awake at a specific time, while keeping the full capability of the
existing task (same shortlist methodology, same email content and
structure).

## Key insight: training vs. scoring have very different data needs

Investigation into `model/daytrade_live_shortlist.py` and
`pipeline/compute_price_indicators.py` found that "today's RSI-14 and
price" is genuinely small data (RSI-14's Wilder smoothing settles
within roughly 90-120 trading days of history per symbol — not years).
The reason the existing daily task depends on the local 2GB SQLite
database is not that indicator, but that `build_shortlist()` **retrains
the gboost model from scratch on the full historical panel every single
run** (1.5M+ rows going back years, used to build the forward-return
training labels and the Q1/tertile RSI cutoffs), and also needs
market-cap bucketing (shares outstanding, from monthly SEC
fundamentals), industry category (monthly SEC classification), and the
valuation/quality filter (from the weekly confluence model, itself
built on SEC fundamentals).

All of those non-price inputs are already lower-frequency than daily,
and the weekly job already publishes a ratings snapshot to GitHub
(`pipeline/publish_to_github.py`). This means training (needs full
history) and daily scoring (needs only today's fresh RSI-14 plus
already-published lower-frequency data) can be decoupled onto different
cadences and different machines.

## Sandbox capability findings (empirical, this session)

A disposable Claude Code cloud routine (via `RemoteTrigger`, same
mechanism `/schedule` uses, same infra the chocolate-digest routine
runs on) was created several times purely to test capability. No
production code was touched by these tests. Findings:

- Python 3.11.15, `pip install pandas numpy scikit-learn yfinance`: all
  work fine (~26s).
- Cloning the public `coop1st/stocks-research-pipeline` repo into the
  sandbox: works fine, confirmed `data/db/stocks.db` is genuinely
  absent (gitignored, never committed) — the sandbox has no access to
  the full local database, confirming the training/scoring split is
  necessary, not optional.
- Yahoo Finance was initially blocked by the account's network egress
  proxy (`EGRESS_BLOCKED` / proxy `403`, both for raw Python requests
  and for the `WebFetch` tool — same underlying proxy either way).
  Fixed by Kevin adding `*.finance.yahoo.com`, `fc.yahoo.com`, and
  `*.fc.yahoo.com` to the account's egress allowlist (the same
  allowlist mechanism already used for the chocolate-digest routine's
  news sites). `fc.yahoo.com` (yfinance's cookie/crumb bootstrap host)
  was the one that needed a second round to find — it isn't a
  subdomain of `finance.yahoo.com`.
- Even with the network path open, `yfinance`'s default HTTP backend
  (`curl_cffi`, which does browser TLS fingerprint impersonation) still
  failed against this sandbox's TLS-terminating egress proxy
  (`SSLError: Recv failure: Connection reset by peer`) — a different
  mechanism than the local machine's own Norton-TLS-inspection fix in
  `pipeline/config.py`, but the same *family* of problem (something
  in the middle re-terminates TLS and a fingerprinting HTTP client
  doesn't tolerate it). **Fix**: set the environment variable
  `YF_DISABLE_CURL_CFFI=1`, which makes `yfinance` fall back to plain
  `requests`. Confirmed working.
- With that env var set, replicating this repo's actual
  `pipeline/fetch_prices.py` batching pattern (`yf.download()`, batch
  size 40, 2s pause between batches) against 80 real symbols pulled
  from the repo's own published ratings CSV: **80/80 succeeded**, real
  non-empty OHLCV data confirmed for a spot-check of 5 symbols.
  Batch timing ~3.7s per batch of 40 → extrapolated **~5 minutes for
  2,000 symbols, ~7 minutes for 3,000 symbols**.

Conclusion: the full daily task — price fetch, RSI-14, shortlist
scoring, per-industry and per-stock news search, Gmail draft — is
deliverable entirely in the cloud, fast, with the local PC's Windows
Scheduled Task for this task no longer needed at all.

## Architecture

```
Weekly (local, existing Saturday 11am Irish schedule, unchanged cadence)
  ... existing stages unchanged ...
  → publish_ratings (extended: add company_name, shares_outstanding)
  → NEW: train_daytrade_model (retrain RSI-alone model + cutoffs,
          publish a small lookup-table artifact to GitHub)
  → email_draft (existing weekly confluence email, unchanged)

Daily (Mon-Fri, fully cloud — Claude Code scheduled routine, no local PC)
  → read latest ratings CSV + daytrade model artifact from GitHub
  → fetch ~100 days OHLCV for the full published ticker universe
    from Yahoo Finance (YF_DISABLE_CURL_CFFI=1)
  → compute rsi_14 + dollar_volume_avg_20d fresh, from that window
  → apply liquidity filter, cap-bucket (price × shares_outstanding),
    apply published model lookup table + Q1 gate + valuation/quality
    filter — same methodology as today's build_shortlist()
  → web search: per-industry sentiment, per-stock 14-day news
  → draft Gmail email (same structure as today)
```

The local Windows Scheduled Task `StocksPipeline-DailyDayTrade` and
`scheduled_run.py`'s `daily` run type are retired — the daily cadence
moves entirely to the cloud routine. The weekly, monthly, and quarterly
local tasks are unaffected.

## Components and changes

### 1. `pipeline/publish_to_github.py` (extend)

`export_latest_ratings()`'s SQL query gains two joined columns the
cloud task needs and doesn't currently have anywhere on GitHub:
`company_name` (from `tickers.name`) and `shares_outstanding` (latest
non-null value from `fundamentals` where `metric='shares_outstanding'`,
same lookup `daytrade_live_shortlist.py` already does locally). No
change to the publish mechanism itself, just the exported columns.

### 2. `model/train_daytrade_model.py` (new)

Runs the same training the current `build_shortlist()` does inline,
but as its own weekly stage, decoupled from live scoring:

- Build the full historical training panel
  (`daytrade_features.build_feature_panel`), same as today.
- Per bucket (large/mid/small): fit the gboost classifier on `rsi_14`
  alone, same hyperparameters as today.
- Instead of exporting the fitted sklearn model object, export a
  **lookup table**: predicted probability at a fine grid of `rsi_14`
  values (e.g. every 0.5 points from 0-100), per bucket. This is a
  single-feature model, so the lookup table is a complete, exact
  representation of it, and the cloud task can use it with plain
  interpolation — no `scikit-learn` version-compatibility risk between
  the local training environment and the cloud sandbox, no pickle
  deserialization trust concerns.
- Also export the Q1 cutoff and the two tertile cutoffs per bucket
  (used for the RSI confidence wording), computed from the training
  panel exactly as today.
- Write to `data/github_sync/daytrade_model/{date}.json`, publish via
  the same `commit_and_push()` pattern already in
  `publish_to_github.py`.

### 3. `scheduled_run.py` (edit)

- `run_weekly()`: add the new `train_daytrade_model` stage after
  `publish_ratings`, same `run_stage_safely()` isolation as every
  other stage.
- Remove `run_daily()`, the `daytrade_daily_checkpoint.json` logic, and
  the `daily` run type from `main()`'s dispatch. `daily_liquid_fetch.py`
  and `pipeline/draft_daytrade_email.py` become unused by the local
  pipeline (see "Not done here" below for what happens to them).

### 4. Windows Scheduled Task (retire)

`Unregister-ScheduledTask -TaskName "StocksPipeline-DailyDayTrade"` —
implementation-time step, not code.

### 5. Cloud routine (new, via `/schedule` / `RemoteTrigger`)

- Recurring cron, Monday-Friday, source repo
  `coop1st/stocks-research-pipeline`, Gmail MCP connector attached
  (same connector already used by the chocolate-digest routine).
- `allowed_tools`: `Bash`, `Read`, `Glob`, `WebSearch`, and the Gmail
  connector's `create_draft` tool.
- Self-contained prompt (the cloud agent starts with zero conversation
  context every run) instructing it to:
  1. `pip install pandas numpy yfinance` (no `scikit-learn` needed —
     the lookup-table export avoids that dependency cloud-side).
  2. Find and read the latest `data/github_sync/ratings/*.csv` and
     the latest `data/github_sync/daytrade_model/*.json` from the
     cloned repo.
  3. Fetch ~100 days of OHLCV for the full ticker universe (the
     ratings CSV's `symbol` column) from Yahoo Finance, batched the
     same way `pipeline/fetch_prices.py` does (batch size 40, 2s
     pause), with `YF_DISABLE_CURL_CFFI=1` set.
  4. Compute `rsi_14` (Wilder's smoothing, same formula as
     `compute_price_indicators.py`) and `dollar_volume_avg_20d` from
     the freshly fetched window.
  5. Apply the liquidity filter, compute `market_cap` (last close ×
     `shares_outstanding` from the ratings CSV) and `cap_bucket`, look
     up each stock's predicted probability from the published lookup
     table (interpolating on `rsi_14`), compute today's top-decile
     cutoff from that population, apply the Q1 gate and the
     valuation/quality filter from the ratings CSV — reproducing
     `daytrade_live_shortlist.py`'s `build_shortlist()` logic exactly,
     just against freshly-fetched short-window data instead of a local
     database query.
  6. Web search per-industry sentiment and per-stock 14-day news, same
     as the current `pipeline/draft_daytrade_email.py` prompt.
  7. Draft (not send) the Gmail email, same subject/structure
     convention as today: `Day-trade shortlist -- {date}`, cap bucket
     → industry category → stock, `display_line` verbatim, up to 3
     news items per stock.
  8. If the ratings CSV or model artifact can't be found, or is
     stale beyond a threshold (the weekly job missed its run), draft
     an alert email instead of staying silent, per Kevin's direction.

## Error handling

- Weekly local stages keep the existing `run_stage_safely()`
  try/except isolation — a training-stage failure doesn't block the
  rest of the weekly run, same as every other stage today.
- Cloud routine: no local checkpoint file to coordinate with anymore
  (there's no daily local run to skip/coordinate against). The
  staleness check in step 8 above is the equivalent safety net.
- The cloud routine is stateless between runs (no `persist_session`,
  no push-back to the repo needed) — every run reads fresh from GitHub
  and Yahoo Finance and produces one Gmail draft, nothing more.

## Not done here (explicitly out of scope for this spec)

- **Timing**: the earliest safe time for the *weekly* local job to run
  is unchanged (Saturdays 11am Irish, as today). The *new cloud daily
  routine's* schedule still needs the empirical "how soon after market
  close is Yahoo's daily bar reliably complete" test that was proposed
  earlier this session but not yet run (today, 2026-08-18, is a live
  trading day — the plan was to sample data availability this evening
  after the 4pm ET close). Until that test runs, default the cloud
  routine's cron to a conservative placeholder (e.g. 9:30pm ET / 2:30am
  UTC, mirroring the old local task's margin) and tighten it once the
  test data is in. This is implementation-plan work, not a design
  question.
- **Retiring `daily_liquid_fetch.py` and `pipeline/draft_daytrade_email.py`**:
  once the cloud routine is live and verified, these two files have no
  caller left in the local pipeline. Whether to delete them, or keep
  them as a documented manual/local fallback, is Kevin's call at
  implementation time — not decided here.
- **What happens to `model/daytrade_live_shortlist.py`**: its scoring
  logic is being reproduced (not imported) inside the cloud routine's
  prompt, since the cloud sandbox can't import this repo's local
  DB-backed Python modules directly against fresh short-window data
  without more refactoring than this spec scopes. A future cleanup
  could factor the scoring logic into a DB-independent function this
  file and the cloud routine's script both call, but that's a
  nice-to-have, not required for this to work.
- **Testing/rollout plan**: covered by the implementation plan, not
  this design doc — expect one full manual round-trip (trigger the
  weekly training/publish stage, then manually run the cloud routine)
  before trusting either schedule unattended, same verification
  posture as the original local-only version.
