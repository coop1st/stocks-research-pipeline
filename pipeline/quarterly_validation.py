"""
Quarterly model revalidation (task #34 in HANDOVER.md) -- rerun the
backtest and indicator-validation scripts against the by-then-larger
local dataset, instead of just accepting the original 4-year backtest
forever, and email a plain-English summary of whether the ratings still
look sound.

Runs model/backtest.py (valuation) and model/validate_indicators.py
(trend, momentum, quality, RSI, 52-week range, insider buying) as
subprocesses -- both are interactive/manual scripts that print a report,
not something with a machine-checkable return value, so this captures
their full stdout to a dated log file under data/logs/validation/ rather
than trying to parse pass/fail out of them. A final local `claude -p`
stage reads those logs plus the documented baseline expectations in
model/README.md and drafts a Gmail summary -- "still healthy" or
"something's drifted, worth a look" -- the same low-cost local pattern
(reusing the Claude subscription/CLI login, no metered API key) as
draft_weekly_email.py.
"""
import subprocess
import sys
from datetime import date
from pathlib import Path

from config import PROJECT_DIR

VALIDATION_LOG_DIR = PROJECT_DIR / "data" / "logs" / "validation"
BACKTEST_SCRIPT = PROJECT_DIR / "model" / "backtest.py"
VALIDATE_INDICATORS_SCRIPT = PROJECT_DIR / "model" / "validate_indicators.py"
MODEL_README = PROJECT_DIR / "model" / "README.md"
RECIPIENT = "kcoopercscs@gmail.com"
ALLOWED_TOOLS = "Read,Glob,mcp__claude_ai_Gmail__create_draft"
SCRIPT_TIMEOUT_S = 1800
CLAUDE_TIMEOUT_S = 1800


def _run_report_script(script_path, log_name):
    VALIDATION_LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = VALIDATION_LOG_DIR / f"{log_name}_{date.today().isoformat()}.log"
    result = subprocess.run(
        [sys.executable, str(script_path)],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=SCRIPT_TIMEOUT_S,
    )
    log_path.write_text(result.stdout + "\n\nSTDERR:\n" + result.stderr, encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError(f"{script_path.name} exited {result.returncode} -- see {log_path}")
    return str(log_path)


def run_backtest_stage():
    return _run_report_script(BACKTEST_SCRIPT, "backtest")


def run_validate_indicators_stage():
    return _run_report_script(VALIDATE_INDICATORS_SCRIPT, "validate_indicators")


def build_summary_prompt(backtest_log, validate_log):
    return f"""You are checking whether this stock-rating model still looks
healthy, as a quarterly sanity check. Work through these steps and don't
stop until a Gmail draft has been created.

Inputs (read these files directly):
- Valuation backtest output (leave-one-year-out, out-of-sample IC per
  fold): {backtest_log}
- Other-indicator validation output (trend, momentum, quality, RSI,
  52-week range, insider buying -- rating 1 should beat rating 5 on
  forward return): {validate_log}
- Documented baseline expectations from when this model was first built
  and validated: {MODEL_README}

Steps:
1. Read the baseline expectations in the README (e.g. valuation's
   documented |IC|, which indicators were found predictive vs not, which
   are context-only and never expected to be strong).
2. Compare this quarter's backtest/validation output against that
   baseline. Look for meaningful drift: an indicator that used to show
   predictive power now showing ~0 or a sign flip, an out-of-sample IC
   noticeably weaker than the documented baseline, a previously-monotonic
   rating-bucket-vs-return relationship breaking down, etc. Minor
   quarter-to-quarter noise is expected and not worth flagging on its
   own -- focus on changes big enough to actually question whether the
   model is still doing its job. If either log file is missing, empty,
   or looks like an error trace rather than a report, say so explicitly
   in the email rather than guessing at numbers that aren't there.
3. Draft (do NOT send) a Gmail email to {RECIPIENT} using the
   create_draft tool. Subject: "Quarterly model check -- <this week's
   date>". Body: one or two sentences per indicator on whether it still
   looks consistent with its documented baseline, and a clear top-line
   verdict (e.g. "all indicators still look consistent with their
   original validation" or "worth a look: <specific indicator(s) and
   why>"). Keep it concise -- this is a personal sanity check, not a
   formal report.

Use only the tools you've been given (Read, Glob, and the Gmail
create_draft tool) -- everything you need is in the files above."""


def run_validation_summary_stage(backtest_log, validate_log):
    prompt = build_summary_prompt(backtest_log, validate_log)
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
