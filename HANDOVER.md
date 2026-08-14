# Handover — pick this up from here

Written 2026-08-14 to hand this project off from one Claude Code session to
another (cmd prompt, local). Everything referenced here is committed and
pushed to `main` at https://github.com/coop1st/stocks-research-pipeline
(public repo). Read this file first, then the READMEs it points to.

## What this project is

A free-data stock research pipeline: fetches prices (Yahoo/yfinance) and
fundamentals/insider trading (SEC EDGAR) for ~5,900 US common stocks,
computes 8 independent 1-5 rated indicators, combines them into a
weighted "confluence" recommendation score, and is being wired up to
produce a weekly recommendation email.

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
   `StocksPipeline-WeeklyUpdate`, runs Saturdays 8am, calls
   `python pipeline/scheduled_run.py weekly`, which: pulls the GitHub
   Actions price snapshot and merges it in
   (`pull_github_updates.py`) → local incremental price fetch as a
   fallback → recomputes moving averages/RSI/52w-range → recomputes all
   ratings + confluence → publishes the snapshot back to GitHub
   (`publish_to_github.py`). Fully logged (`data/logs/`), has a
   post-run health check, and every stage is isolated so one failure
   doesn't kill the rest. Check anytime with `python pipeline/check_status.py`.
7. **Cloud weekly price fetch**: GitHub Actions workflow, runs
   Saturdays 13:00 UTC (a few hours ahead of the local job), fetches
   prices with no local PC needed, commits to the repo. Tested
   end-to-end successfully (a manual run completed in ~4 minutes with
   47,205 rows).
8. **Monthly local automation** (not yet on a Scheduled Task — currently
   manual): `python pipeline/run_pipeline.py --stage fundamentals` and
   `--stage insider_transactions` and `--stage industry`.

## What's NOT done yet

- **The weekly research + Gmail draft step is unbuilt.** This was being
  designed when the handover happened. Two features were agreed on but
  not yet implemented:
  1. **Per-stock headline check**: for each ticker in that week's STRONG
     BUY/STRONG SELL shortlist, search recent news and note anything
     interesting in the eventual email.
  2. **Industry sentiment score**: for each *category* represented in
     that week's shortlist (not every category, just the ones with a
     shortlisted stock), score sentiment 1-5 (same convention: 1 =
     bullish, 5 = bearish) via news search. Used only to **exclude**
     stocks, never to add them or feed the recommendation_score: drop a
     STRONG BUY if its industry score is 5, drop a STRONG SELL if its
     industry score is 1. Otherwise just mention the industry score in
     the email as context.
  - Both of these need genuine web search + LLM judgment, which is why
    they can't be plain Python in `scheduled_run.py` — they need to run
    as an actual Claude agent turn.

- **Open decision on how that step runs** — this is exactly where the
  conversation was when the handover request came in:
  - **Option A**: a claude.ai-level MCP Gmail connector (for cloud
    routines via `/schedule`) — was being set up but the connector
    wasn't showing up as available to routines, even though a Gmail
    connector exists and works for *this* (local, cmd-prompt) Claude
    Code. Those are two different systems: local MCP config (what you
    have) vs. account-level connector for cloud routines (what
    `/schedule` needs, currently unconfirmed/not working).
  - **Option B (leaning towards this one)**: keep it local. Add one more
    step to the same Windows Scheduled Task, using Claude Code's
    non-interactive mode (`claude -p "prompt"`) to run the research +
    Gmail-draft step locally, right after `scheduled_run.py weekly`
    finishes — reusing the Gmail MCP connection that already
    demonstrably works in this environment. This was the direction the
    conversation was heading but never got built.
  - **Next step**: decide A vs B, then build it. If B, the prompt for
    `claude -p` needs to be self-contained (no memory of this
    conversation) — describe: read the latest
    `data/github_sync/ratings/*.csv` from the repo, filter to STRONG
    BUY/STRONG SELL, look up each ticker's `industry_category`, search
    sentiment per represented category, apply the exclusion rule, search
    headlines per remaining ticker, draft the Gmail email (recipient:
    kcoopercscs@gmail.com).

- **Quarterly revalidation** (task #34) — not started. The idea: every 3
  months, rerun `model/backtest.py` and `model/validate_indicators.py`
  against the by-then-larger local dataset to confirm the indicators
  (and confluence weights) still hold up, not just accept the original
  4-year backtest forever. No cadence/automation decided yet.

- **Monthly fundamentals/insider/industry refresh** isn't on a Scheduled
  Task yet, just documented as a manual command (see above).

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
python model/rate_universe.py                 # today's cheapest/most expensive (valuation only)
python model/confluence.py                    # today's full recommendations, best/worst 15
git log --oneline -10                         # recent history
gh run list --workflow=weekly-price-fetch.yml # cloud fetch run history
```
