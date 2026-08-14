"""
Weekly recommendation email draft -- the local half of the research step
described in HANDOVER.md.

This can't be plain Python like the rest of scheduled_run.py's stages: it
needs genuine web search (industry sentiment + per-stock headline check)
and LLM judgment (applying the exclusion rule, writing the email), so it
shells out to `claude -p` as a scoped, non-interactive agent turn. This
covers the whole research step in one local pass, reusing the existing
Claude subscription/CLI login rather than a separately-billed Anthropic
API key -- a cloud-side split (industry sentiment scored in GitHub
Actions, ahead of the local job) was tried first but dropped because it
needed a paid API key; this version scores sentiment only for categories
actually represented in that week's shortlist, so the cost of folding it
into the local step is small.

The top/bottom-15 shortlist itself is computed here in pandas, not by the
agent: a first real test run found the full STRONG BUY/STRONG SELL bucket
can be 1,000+ tickers with many exact ties (every core indicator maxed
out), which the agent couldn't reliably sort/filter within its given
tools (Read/Glob/WebSearch/create_draft) and had to improvise around with
Grep. Doing the filtering deterministically in Python and handing the
agent a short, pre-resolved list removes that ambiguity, so its tool
scope no longer needs file access at all -- just WebSearch and the Gmail
draft tool.

Runs as the last stage of the weekly job, after ratings are published, so
it's isolated the same way every other stage is: if this fails (e.g. no
`claude` on PATH, or a bad response), it doesn't take down the stages
that already succeeded.
"""
import subprocess

import pandas as pd

from config import PROJECT_DIR

RATINGS_DIR = PROJECT_DIR / "data" / "github_sync" / "ratings"
RECIPIENT = "kcoopercscs@gmail.com"
ALLOWED_TOOLS = "WebSearch,mcp__claude_ai_Gmail__create_draft"
CLAUDE_TIMEOUT_S = 1800
SHORTLIST_SIZE = 15  # per direction -- matches model/confluence.py's own "best/worst 15" convention


def _latest_file(directory):
    if not directory.exists():
        return None
    files = sorted(directory.glob("*.csv"))
    return files[-1] if files else None


def build_shortlist():
    ratings_path = _latest_file(RATINGS_DIR)
    if ratings_path is None:
        raise RuntimeError(f"No ratings CSV found under {RATINGS_DIR}")

    df = pd.read_csv(ratings_path)
    buys = df[df["recommendation"] == "STRONG BUY"].nsmallest(SHORTLIST_SIZE, "recommendation_score")
    sells = df[df["recommendation"] == "STRONG SELL"].nlargest(SHORTLIST_SIZE, "recommendation_score")
    cols = ["symbol", "recommendation", "recommendation_score", "industry_category"]
    shortlist = pd.concat([buys[cols], sells[cols]], ignore_index=True)
    return ratings_path, shortlist


def build_prompt():
    ratings_path, shortlist = build_shortlist()

    if shortlist.empty:
        shortlist_block = "(no tickers currently rated STRONG BUY or STRONG SELL)"
    else:
        shortlist_block = shortlist.to_csv(index=False)

    return f"""You are drafting this week's stock recommendation email. Work
through these steps and don't stop until a Gmail draft has been created.

This week's shortlist (already filtered and capped to the top 15 STRONG
BUY and top 15 STRONG SELL by recommendation_score, from
{ratings_path.name}; recommendation_score is 1-5, 1=bullish/5=bearish):

{shortlist_block}

Steps:
1. Collect the distinct industry_category values represented in the
   shortlist above (only these -- don't bother with categories that have
   no shortlisted stock). For each one, do a web search for current
   (this week's) news and market sentiment for that industry sector (US
   public companies), and rate it 1-5 using the same convention as
   everything else: 1 = strongly bullish, 2 = mildly bullish, 3 =
   neutral/mixed, 4 = mildly bearish, 5 = strongly bearish.
2. Apply this exclusion rule using the scores from step 1 (industry
   sentiment only ever removes stocks, never adds them or changes their
   score):
   - Drop a STRONG BUY ticker if its industry's sentiment score is 5.
   - Drop a STRONG SELL ticker if its industry's sentiment score is 1.
   - Otherwise keep it, and note the industry sentiment score/rationale
     as context in the email.
3. For each ticker remaining after step 2, do a web search for recent
   (last 1-2 weeks) headlines/news and note anything interesting or
   relevant to the recommendation -- especially anything that seems to
   run counter to the model's call.
4. Draft (do NOT send) a Gmail email to {RECIPIENT} using the
   create_draft tool. Subject: "Weekly stock picks -- <this week's
   date>". Body: for each shortlisted ticker, its recommendation, score,
   industry category + sentiment context, and any notable headline found
   in step 3. Keep it concise and scannable -- this is a personal
   research email, not a formal report. If the shortlist is empty (either
   from the start or after exclusions), still draft a short email saying
   so.

Use only the tools you've been given (WebSearch and the Gmail
create_draft tool) -- everything you need besides web search is already
in the shortlist above."""


def run_email_draft_stage():
    prompt = build_prompt()
    result = subprocess.run(
        [
            "claude", "-p", prompt,
            "--allowedTools", ALLOWED_TOOLS,
        ],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=CLAUDE_TIMEOUT_S,
    )
    print(result.stdout)
    if result.returncode != 0:
        raise RuntimeError(
            f"claude -p exited {result.returncode}\nstderr: {result.stderr[-2000:]}"
        )
    return result.stdout[-500:]


if __name__ == "__main__":
    print(run_email_draft_stage())
