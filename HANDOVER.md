# Handover — pick this up from here

Written 2026-08-14 to hand this project off from one Claude Code session to
another (cmd prompt, local). Everything referenced here is committed and
pushed to `main` at https://github.com/coop1st/stocks-research-pipeline
(public repo). Read this file first, then the READMEs it points to.

## What this project is

A free-data stock research pipeline: fetches prices (Yahoo/yfinance) and
fundamentals/insider trading (SEC EDGAR) for ~5,900 US common stocks,
computes 8 independent 1-5 rated indicators, combines them into a
weighted "confluence" recommendation score, and produces a weekly
recommendation email (industry-sentiment research, per-stock headline
check, and Gmail draft all run locally, as part of the same weekly job).

## Architecture (why things are split the way they are)

- **The full historical database (~2GB) stays local**, at
  `data/db/stocks.db`, gitignored. It's entirely regenerable from free
  public sources, so it doesn't need backing up the way irreplaceable
  data would — only the *code* that rebuilds it needs to be safe, which
  is why it's on GitHub.
- **A public GitHub repo hosts the code + small derived snapshots.**
  Never the raw DB (too big, changes too much for git to handle well).
- **Price fetching runs in the cloud** (GitHub Actions,
  `.github/workflows/weekly-price-fetch.yml`), so it doesn't need the
  local PC on. It can't use the DB-diffing incremental logic the local
  fetcher uses (no local DB to diff against), so it just grabs a fixed
  10-day trailing window unconditionally and commits it as a small CSV
  under `data/github_sync/prices/`.
- **Everything that needs the full history (moving averages, RSI,
  52-week range, all 8 indicator ratings, the confluence score) runs
  locally**, because that computation structurally requires the full
  local database — this can't move to the cloud without moving the whole
  2GB database there too, which we decided against.
- **The local weekly job publishes its output back to GitHub**
  (`data/github_sync/ratings/*.csv`) so downstream steps (see below) can
  read the latest recommendations without needing local DB access.

## What's built and working

1. **Data pipeline** (`pipeline/`) — prices, fundamentals, insider
   transactions (SEC bulk Form 3/4/5), industry/SIC classification, all
   free/keyless. See `pipeline/README.md` for the full source list and
   caveats (survivorship bias, yfinance being unofficial, etc.).
2. **8 indicators** (`model/`) — valuation, trend, momentum, quality
   (Piotroski), RSI, 52-week range, insider buying flag, congress
   buy/sell flags (hand-compiled, unvalidated, context-only). All share a
   1-5 convention: 1 = bullish, 5 = bearish. Each was backtested; see
   `model/README.md` for methodology and honest results per indicator
   (valuation is strongest at |IC| 0.26, RSI came back ~0/unvalidated,
   congress trading was never validated at all — read the caveats before
   trusting any of it).
3. **Confluence model** (`model/confluence.py`) — combines the 5
   *validated* core indicators (valuation, 52w-range, momentum, quality,
   trend) into `recommendation_score` (1-5) and a label (STRONG BUY ...
   STRONG SELL), weighted by each indicator's validated |IC|, not a
   naive vote. Insider buying gets a small nudge (±0.3), not a full
   weighted vote. RSI and congress trading are shown as context only —
   deliberately excluded from the score. Needs 3+ of the 5 core
   indicators present to produce a score at all. Worked examples of the
   math are in the conversation that built this, not yet written down in
   a file — worth adding to `model/README.md` if you want it documented
   there too.
4. **Industry classification** (`pipeline/fetch_industry_classification.py`)
   — SEC SIC codes bucketed into ~25 categories (see
   `SIC_CATEGORY_RANGES` in `pipeline/config.py`). All 5,903 tickers
   classified. Feeds the industry-sentiment overlay (see "not done" below).
5. **`ratings` table** (`model/compute_all_ratings.py`) — one row per
   ticker per week: every indicator's rating, the confluence score,
   industry category. This is what gets exported and published.
6. **Local weekly automation**: Windows Scheduled Task
   `StocksPipeline-WeeklyUpdate`, runs Saturdays 11am (Irish time), calls
   `python pipeline/scheduled_run.py weekly`, which: pulls the GitHub
   Actions price snapshot and merges it in
   (`pull_github_updates.py`) → local incremental price fetch as a
   fallback → recomputes moving averages/RSI/52w-range → recomputes all
   ratings + confluence → publishes the snapshot back to GitHub
   (`publish_to_github.py`) → drafts the weekly recommendation email
   (`draft_weekly_email.py`, see below). Fully logged (`data/logs/`), has
   a post-run health check, and every stage is isolated so one failure
   doesn't kill the rest. Check anytime with `python pipeline/check_status.py`.
7. **Cloud weekly price fetch**: GitHub Actions workflow, runs Saturdays
   03:00 UTC, fetches prices with no local PC needed, commits to the
   repo. Tested end-to-end successfully (a manual run completed in ~4
   minutes with 47,205 rows). **Originally ran at 13:00 UTC, which was
   actually *after* the local job when that job ran at 8am — fixed** by
   moving this to 03:00 UTC. The local job has since also moved to 11am
   Irish time, so the buffer is now 7-8 hours ahead of the local job's
   earliest possible UTC time (10:00 UTC in IST) year-round.
8. **Monthly local automation**: Windows Scheduled Task
   `StocksPipeline-MonthlyUpdate`, every 4 weeks on Saturday at 11:30am
   Irish time (30 min after the weekly job, so on the Saturdays where
   they coincide they don't both write to the local SQLite database at
   once). Calls `python pipeline/scheduled_run.py monthly`, which runs
   `fundamentals` → `insider_transactions` → `industry` (SEC fundamentals
   refresh, insider Form 3/4/5 refetch, SIC industry reclassification —
   all slow-changing enough that weekly cadence would be overkill).
   Anchored to the same reference week as the weekly task
   (2026-08-14) so the two stay in phase; first run 2026-08-15.
9. **Weekly research + Gmail draft**: `pipeline/draft_weekly_email.py`,
   the last stage of the local weekly job (`email_draft`, runs after
   `publish_ratings`). See "What's NOT done yet" below for detail and its
   not-yet-verified-end-to-end status.
10. **Quarterly model revalidation** (task #34): Windows Scheduled Task
    `StocksPipeline-QuarterlyValidation`, every 12 weeks on Saturday at
    1:00pm Irish time (staggered after both the weekly 11:00am and
    monthly 11:30am jobs on the Saturdays all three coincide — same
    reference week, so that's every 12th weekly Saturday, first
    2026-08-15). Calls `python pipeline/scheduled_run.py quarterly`
    (`pipeline/quarterly_validation.py`), which reruns
    `model/backtest.py` and `model/validate_indicators.py` against the
    by-then-larger local dataset, captures their full output to
    `data/logs/validation/`, then a local `claude -p` stage (same
    subscription-reuse pattern as the weekly email, no API billing)
    compares the results against the documented baseline in
    `model/README.md` and drafts a Gmail summary — still healthy, or
    something's drifted and worth a look. Not yet verified end-to-end
    (only compile/import-checked); first real run should be checked in
    `data/logs/` and the Gmail drafts folder.

## What's NOT done yet

- **The weekly research + Gmail draft step is now built**, in a
  follow-up session. The original open question was local-vs-cloud (see
  below for that history); the answer landed on **fully local**:
  - `pipeline/draft_weekly_email.py` is a new final stage of
    `scheduled_run.py`'s weekly run, isolated in the same try/except
    pattern as every other stage. It shells out to `claude -p` (scoped
    with
    `--allowedTools Read,Glob,WebSearch,mcp__claude_ai_Gmail__create_draft`
    — not a permissions bypass) with a single self-contained prompt that
    does the whole research step in one local agent turn: read the
    latest ratings CSV (path resolved by `_latest_file()`), filter to
    STRONG BUY/STRONG SELL, find the distinct industry categories
    actually represented in that shortlist, web-search sentiment (1-5)
    for just those categories, apply the exclusion rule (drop STRONG BUY
    if its industry scores 5, drop STRONG SELL if its industry scores
    1), web-search recent headlines for the survivors, then draft (not
    send) a Gmail email to kcoopercscs@gmail.com via `create_draft`. No
    changes to the Windows Scheduled Task were needed — this rides along
    inside the existing `scheduled_run.py weekly` call.
  - **A cloud-side split was tried and reverted first**: a separate
    GitHub Actions workflow (`cloud/score_industry_sentiment.py` +
    `.github/workflows/weekly-industry-sentiment.yml`) scored all ~30
    SIC categories unconditionally via the raw Anthropic API + hosted
    web_search tool, ahead of the local job, so the local step wouldn't
    need to do that part. It was deleted before ever running for real:
    it needs a separately-billed `ANTHROPIC_API_KEY` (pay-per-token API
    credits, distinct from a claude.ai subscription), and the cost of
    buying those credits wasn't something the user wanted to take on —
    so this was abandoned in favor of the fully-local version above,
    which reuses the Claude subscription/CLI login already logged in on
    this machine at no extra cost. If a cloud-side split is ever
    revisited, the unexplored alternative is a subscription-backed CI
    auth token (`claude setup-token` or similar) instead of a metered API
    key — not investigated, unclear if it works in GitHub Actions or
    within plan usage limits.
  - **Not yet verified end-to-end** — `claude -p` with this exact
    `--allowedTools` flag was smoke-tested (trivial prompt, returned
    correctly), and all new/edited files were compile-checked and
    import-checked, but nobody has watched a full real run produce an
    actual Gmail draft yet. First real Saturday run (or a manual
    `python scheduled_run.py weekly`) should be checked in
    `data/logs/` for the `email_draft` stage result, and the Gmail
    drafts folder checked for the actual output.

- **Quarterly revalidation** (task #34) is now built — see item 10 above.
  Not yet verified end-to-end (same caveat as the weekly email draft).

## Things worth knowing before touching anything

- **`config.py` calls `truststore.inject_into_ssl()`** — needed because
  Norton (this machine's antivirus) does TLS inspection with a
  certificate that Python's bundled OpenSSL validator rejects but
  Windows' native validator accepts. This is why every fetch script
  imports `config` first. It's a no-op if that's not an issue on
  whatever machine picks this up next. One known gap: `yfinance`
  internally uses `curl_cffi`, a separate TLS stack this fix doesn't
  reach — if Norton's protection is active, yfinance calls can still
  fail with cert errors even though everything else (SEC, Nasdaq) works
  fine through plain `requests`. Not fixed, just documented (see
  `pipeline/README.md`'s network note).
- **`SEC_CONTACT_EMAIL` env var** must be set (used to build the SEC
  User-Agent header) — set persistently on this machine already via
  `[Environment]::SetEnvironmentVariable(...)`. Also stored as a GitHub
  Actions secret of the same name for the cloud workflow. If picking
  this up on a different machine, set it there too.
- **git identity for this repo** is set locally (not global) to GitHub's
  privacy-preserving noreply address, specifically so a real email never
  ends up in public commit history. Don't change to a real email if this
  repo is going to stay public.
- **`gh auth setup-git`** was run so plain `git push`/`git pull`
  authenticate via the already-logged-in `gh` CLI token — no separate
  credentials needed for git operations on this machine.
- Every table in the local DB uses upsert logic and every script is
  idempotent/safe to rerun. There's no state that gets corrupted by
  running something twice.

## Quick reference

```bash
cd Projects/Stocks

python pipeline/check_status.py              # is data current? did the last scheduled run succeed?
python pipeline/scheduled_run.py weekly       # manually trigger the full weekly flow
python pipeline/scheduled_run.py monthly      # manually trigger fundamentals/insider/industry refresh
python pipeline/scheduled_run.py quarterly    # manually trigger backtest + validation + summary email
python model/rate_universe.py                 # today's cheapest/most expensive (valuation only)
python model/confluence.py                    # today's full recommendations, best/worst 15
git log --oneline -10                         # recent history
gh run list --workflow=weekly-price-fetch.yml # cloud fetch run history
Get-ScheduledTask -TaskName "StocksPipeline-*" # check all 3 local scheduled tasks (PowerShell)
```
