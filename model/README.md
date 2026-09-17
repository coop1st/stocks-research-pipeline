# Valuation / screening layer

Multiple independent indicators, each rated 1-5 on the **same shared
convention**: 1 = most bullish/buy-like signal, 5 = most bearish/sell-like
signal. The idea is confluence -- a stock rated 1-2 on several
independent indicators at once is a higher-confidence signal than any one
indicator alone. Currently built:

| Indicator | File | What it measures | Cross-sectional? |
|---|---|---|---|
| Valuation | `snapshot.py` + `rating.py` | cheap vs expensive (P/E, P/B, P/S composite) | Yes -- relative to peers on that date |
| Trend | `trend.py` | price vs its own 20/50/200-day moving averages, golden/death cross | No -- absolute, about the stock's own history |
| Momentum | `momentum.py` | trailing 6/12-month total return | Yes -- relative to peers on that date |
| Quality | `quality.py` | Piotroski F-Score (profitability/leverage/efficiency trend) | No -- absolute, about the company's own trend |
| RSI | `rsi.py` | 14-day overbought/oversold oscillator | No -- fixed 30/70 thresholds |
| 52-week range | `range52w.py` | position within trailing 1-year price range | No -- absolute, about the stock's own range |
| Insider buying | `insider_flag.py` | boolean 1/0: insider open-market purchase disclosed in trailing 6 months | No -- event flag, not a 1-5 rating |
| Congress buy/sell | `congress_flag.py` | two booleans: member of Congress bought/sold, trailing 6 months | No -- event flags; hand-compiled, unvalidated, not fed into confluence |

**Combining them**: `confluence.py` turns the 8 indicators into one
recommendation (STRONG BUY / BUY / HOLD / SELL / STRONG SELL) per ticker,
weighted by each indicator's *validated* |IC| (valuation 0.26, 52-week
range 0.20, momentum 0.15, quality 0.15, trend 0.09) rather than a naive
majority vote -- RSI (~0 validated IC) and congress trading (never
validated) are shown as context but don't move the score; insider buying
gets a small fixed nudge rather than a full weighted vote, since it's a
boolean not a continuous rating. Needs at least 3 of the 5 weighted
indicators present to produce a recommendation at all. `compute_all_ratings.py`
runs every indicator plus confluence and persists one row per ticker into
the `ratings` table (this is what the weekly scheduled job calls).

---

## Valuation indicator

Rates every ticker 1-5 on cheapness relative to the rest of the universe on
a given date: 1 = extremely cheap ... 5 = extremely expensive.

**Three yield metrics**, each "fundamental / price" so higher = cheaper:
`earnings_yield` (~inverse P/E, only when EPS > 0), `book_yield` (inverse
P/B), `sales_yield` (inverse P/S).

**Point-in-time correctness**: fundamentals are only used if their SEC
filing date was on or before the "as of" date, so nothing from the future
leaks into a historical rating (`snapshot.py`). Annual figures come
specifically from 10-Ks (not summed quarters) to sidestep fiscal-year
misalignment and restatement noise.

**Composite score**: each yield is converted to a cross-sectional
percentile rank *within that date's snapshot* (never pooled across dates).
The composite is a weighted average of whichever percentiles are available
for a ticker (`rating.py`), bucketed into quintiles 1-5.

**Weights are trained, not assumed**: `backtest.py` measures each yield
metric's historical Spearman rank-IC (correlation between that metric's
percentile and the *forward 1-year return*) and weights metrics by their
average IC on the training years -- a metric with no historical
relationship to forward returns gets ~0 weight instead of diluting the
score.

**Validation**: leave-one-year-out backtest over the 4 non-overlapping
annual windows in our price history (2022-2025) -- train weights on 3
years, apply the fixed weights to the held-out year, check whether
rating-1 stocks actually beat rating-5 stocks that year. `python
backtest.py` reports forward return by bucket, the bucket1-bucket5 spread,
and out-of-sample rank IC, per fold and averaged.

**Results**: average bucket1-bucket5 spread **+40%**, average
out-of-sample IC **+0.26**, directionally consistent every year, and the
sign flipped sensibly with the macro regime (in the 2022 bear market every
bucket lost money, but "expensive" lost far more than "cheap," matching
what happened to richly-valued growth stocks that year).

**But there's a real size confound**: median market cap by rating (2025
snapshot) is $210M (rating 1) -> $905M -> $1.56B -> $3.0B (rating 4) ->
$1.5B (rating 5). The "cheap" bucket is disproportionately micro-cap and
the "expensive" bucket skews toward larger companies. Micro-caps got hit
hardest in the 2022 selloff and rebounded hardest in 2023-2025 -- so a
meaningful chunk of the measured spread is plausibly a size/risk effect
riding along with valuation, not a clean, size-neutral signal. Read the
numbers above as "worth investigating further," not "this returns
40%/year." A market-cap floor or size-neutralizing the score is the
natural next step before using this for anything beyond exploration.

---

## Trend indicator

Rates 1-5 from price position relative to its own 20/50/200-day moving
averages -- **not** cross-sectional (a stock is either above or below its
own 200-day average regardless of what peers are doing). 4-point
checklist (price > sma200, sma50 > sma200, price > sma50, sma20 > sma50)
maps to rating 1 (all 4, strong uptrend) through 5 (none, strong
downtrend). Also flags `golden_cross` / `death_cross` (sma50 crossed
sma200 within the last 15 trading days) as separate boolean columns.
(`trend.py`)

**Validation**: same idea as the valuation backtest, but there's no
weight-fitting step here since it's a fixed rule, not a trained score --
`validate_indicators.py` just checks the sign and strength of the
correlation with forward return, using the same 2022-2025 windows. Only
2023-2025 are usable: a 200-day moving average needs 200 trading days of
history, and our price data only starts 2021-08-12, so no ticker has a
valid sma_200 as of the 2022-01-03 anchor.

**Results**: correctly signed every year (rating 1 outperforms rating 5)
but the effect is much weaker and noisier than valuation -- average IC
**-0.09** vs valuation's +0.26 in magnitude, and bucket ordering isn't
strictly monotonic (middle buckets are noisy). Consistent with how
moving-average trend signals are generally used in practice: more a
risk/timing filter (don't buy something in freefall) than a strong
standalone return predictor over a full year.

---

## Momentum indicator

Rates 1-5 from trailing 6-month and 12-month total return (adjusted
close, so splits/dividends don't distort it), cross-sectionally
percentile-ranked against the rest of the universe like valuation --
1 = strongest relative momentum, 5 = weakest. (`momentum.py`)

**Validation**: same setup as trend -- 2022 unusable since a 12-month
lookback from 2022-01-03 needs price history back to 2021-01, before our
data starts (2021-08-12).

**Results**: correctly signed every year, average IC **-0.15** -- stronger
than trend, weaker than valuation. Bucket 5 (weakest momentum) was clearly
the worst performer each year; middle buckets are noisier.

---

## Quality indicator (Piotroski F-Score)

Rates 1-5 from a 9-point year-over-year checklist across profitability
(ROA positive/improving, cash-flow-positive, cash flow > net income),
leverage/liquidity (debt ratio falling, current ratio rising, no new share
dilution), and efficiency (gross margin and asset turnover improving) --
comparing the two most recent annual (10-K) filings, both gated on
filed_date so nothing leaks from the future. Not cross-sectional, like
trend: this is about whether *this* company is getting healthier or
sicker, independent of peers. (`quality.py`)

Coverage of the underlying XBRL tags isn't uniform (e.g. gross_profit is
only tagged by ~2,200 of ~5,900 tickers -- many companies, especially
financials, don't report a "gross profit" line), so the score is
normalized to a 0-9 scale using however many of the 9 criteria are
actually answerable (minimum 5 of 9 required, else no rating) rather than
penalizing a data gap as if it were a failed criterion.

**Validation**: unlike trend/momentum, this doesn't need deep price
history -- it only needs two years of annual filings, which the extended
`FUNDAMENTALS_HISTORY_YEARS` window covers -- so all 4 anchor years
(2022-2025) are usable.

**Results**: the strongest and most consistent of the three new
indicators -- correctly signed every year, average IC **-0.15**, and
median forward return declines cleanly from rating 1 to rating 5 in every
single year (means are noisier in a couple of years from the same
fat-tail skew seen elsewhere in this project). Notably clean in the 2022
bear market: rating 1 median -17%, rating 5 median -49% -- quality mattered
most when things got ugly, which is exactly the kind of "downside
protection" this indicator is meant to add to a portfolio that also owns
statistically cheap, potentially distressed names.

---

## RSI indicator

Rates 1-5 from 14-day RSI (Wilder smoothing) using the conventional
30/70 overbought/oversold thresholds -- but note the direction is
**inverted** relative to trend/momentum: RSI is a mean-reversion signal,
so oversold (low RSI, recent weakness) gets rating 1 (bullish, "due for a
bounce") and overbought (high RSI, recent strength) gets rating 5
(bearish, "due for a pullback"). This is meant to sometimes disagree with
trend/momentum -- that disagreement is informative ("strong uptrend but
overbought" reads differently than "strong uptrend, not yet stretched").
(`rsi.py`)

**Validation caveat that matters here**: RSI's mean-reversion effect is
understood to be a short-horizon phenomenon (days to weeks), unlike the
other indicators which were validated against 1-year forward returns.
Testing it the same way would be testing the wrong timeframe, so
`validate_indicators.py` instead checks it against a ~1-month forward
return at the same 4 anchor dates.

**Results**: the IC flips sign every single year (+0.29, -0.20, +0.09,
-0.17), averaging to **~0.00**. Read this as an honest null result, with
two things worth knowing before writing RSI off: (1) short-horizon
mean-reversion effects are widely understood to be unstable/regime-
dependent even when real, so this isn't surprising, and (2) 4 annual
snapshot dates is a very weak test design for a signal whose effect plays
out over weeks -- a fair test would sample many more (e.g. weekly) dates
rather than one per year, which we haven't built yet. Current takeaway:
don't trust RSI as a standalone scored signal at this validation quality;
it may still be useful as a qualitative "don't chase an extended move"
flag, which is closer to how it's used in practice anyway.

---

## 52-week range indicator

Exposes two standalone normalized metrics -- `pct_from_52w_high` (<=0)
and `pct_from_52w_low` (>=0) -- plus a combined `range_position_52w` in
[0,1] (0 = at the 52-week low, 1 = at the 52-week high) that the 1-5
rating is built from: near the high = rating 1 (bullish), near the low =
rating 5 (bearish). This is the well-documented "52-week high effect"
(George & Hwang 2004) -- stocks near their highs have tended to keep
outperforming, more than plain momentum explains. Same direction as
momentum, opposite of RSI's mean-reversion framing -- "overbought RSI +
near 52-week high" isn't a contradiction, it's "strong, and possibly due
for a pause" rather than RSI alone reading as "reversing." Uses adjusted
close over a trailing 252-trading-day window (minimum 63 days, ~1
quarter, before a ticker gets a rating at all) so a stock split partway
through the window doesn't fake a false high/low. (`range52w.py`)

**Validation**: same 1-year forward-return setup as trend/momentum/
quality, and usable across all 4 years since the minimum history bar
(63 days) is much lower than trend's 200-day requirement.

**Results**: the strongest of the self-referential (non-cross-sectional)
indicators -- correctly signed every year, average IC **-0.20**, beating
trend (-0.09) and matching/beating momentum (-0.15). 2022 in particular
is close to a clean staircase: rating 1 mean -14% down to rating 5 mean
-44%.

---

## Insider buying flag

A boolean, not a 1-5 rating: 1 if at least one insider disclosed an
open-market stock purchase (SEC Form 4, `TRANS_CODE == 'P'`) within the
trailing 180 days (~6 months) of the as-of date, 0 otherwise. Only counts
open-market purchases -- grants, option exercises, gifts, and tax
withholding (other Form 4 transaction codes) aren't a "spent their own
money because they think it's going up" signal the way a purchase is.

**Data source**: SEC's free bulk quarterly Form 3/4/5 datasets
(`fetch_insider_transactions.py`) -- structured TSV files covering the
entire market's insider filings, ~20-27 quarterly downloads instead of
tens of thousands of individual filing fetches. Goes back to at least
2020.

**Point-in-time correctness**: gated on `filed_date` (when the purchase
became public), not `trans_date` (when it actually happened) -- Form 4
requires disclosure within ~2 business days, so the gap is usually small,
but this is what avoids lookahead bias.

**Also stored, for future refinement**: `cluster_buy_count` (how many
distinct insiders bought within the window -- multiple insiders buying
together is generally a stronger signal than one lone purchase) and
`days_since_last_purchase`, so a later move from a flat on/off flag to a
decaying weight, or a "require 2+ buyers" filter, doesn't need new data.

**Validation**: two-group comparison (flagged vs unflagged mean/median
forward 1-year return) at the same 4 anchor dates.

**Results**: modest and inconsistent -- average spread **+3.5%**
(flagged minus unflagged), positive in 3 of 4 years but *negative* in
2022 (flagged -25.1% vs unflagged -23.5% -- insiders buying didn't save
you in the bear market that year). The cluster-buying breakout is more
interesting: in 2022 and 2024, stocks with 2+ distinct insiders buying
clearly beat single-buyer flags (e.g. 2024: +24.8% vs +14.6%), though
2023 and 2025 didn't show that pattern as cleanly. Read this as: the raw
"did anyone buy" flag is a weak standalone signal, but there's a hint
that requiring cluster buying would be a meaningfully stronger version of
this indicator -- worth a follow-up if this one gets used for anything
beyond confluence-counting.

**Known limitation for live use**: SEC's bulk dataset is published on a
lag -- as of this build, the most recent quarter available is 2026q1
(through March), so live scoring is currently running ~4-5 months stale
relative to real disclosures, even though the underlying Form 4 filings
themselves are near-real-time. Fine for backtesting; worth knowing if
using this for current decisions. A future refinement could supplement
the bulk quarterly file with SEC's real-time full-text search for the
most recent quarter not yet in the bulk dataset.

---

## Congress buy/sell flags

Two booleans, `congress_buy_flag` and `congress_sell_flag` -- same
mechanics as insider buying (trailing 6-month decay window, one row per
flagged ticker), but built on a fundamentally different, much weaker data
foundation: a **hand-compiled list of 42 trades (2023-2026)**, gathered by
manually searching news coverage year by year until the yield dropped to
near zero (1 trade found for all of 2023). See
`data/congress_trades/README.md` for the full methodology and honest
caveats.

**This one is explicitly not validated and not fed into the confluence
model** -- the sample is far too sparse and skewed toward a handful of
frequently-covered members (Pelosi, Gottheimer, Whitehouse, Khanna) to
mean anything statistically. It's present for visibility ("did any member
of Congress publicly buy/sell this ticker recently"), not as a scored
signal. Also carries `politician_names` and `trade_count` per flagged
ticker, so a cluster of multiple members trading the same stock (which
did show up live -- 4 different House members buying PLTR) is visible
even though it isn't scored any differently.

Update with `python pipeline/load_congress_trades.py` after adding rows
to the CSV.

---

## Day trading model (five attempts -- first four null, fifth found a real signal, nothing live yet)

Explored whether short-horizon price moves could be forecast well enough
to trade -- a quick buy-open/take-profit-or-stop-loss strategy. The first
four attempts found no exploitable edge (documented below so the same
dead ends aren't re-explored without knowing why they failed); the fifth
found a real, unusually robust signal in a feature everywhere else in
this document only got to ~0 validated IC (see the RSI indicator row
above -- that's a different target/horizon, a cross-sectional weekly
score, not a contradiction, but worth flagging explicitly since it reads
like one at a glance). **Nothing built from any of this is live** -- the
fifth attempt is a backtested finding, not a deployed strategy.

**Setup**: `price_indicators` gained `volatility_3d`/`volatility_7d`
(trailing stdev of daily adj_close returns) and `dollar_volume_avg_20d`
(a liquidity proxy, since no market-cap field exists in this pipeline) --
see `compute_price_indicators.py`. `model/daytrade_features.py` builds a
feature panel (volatility_3d/7d, rsi_14, range_position_52w,
pct_from_52w_high, today's opening gap, today's own intraday range,
volume vs. its 20-day average) with 3 candidate labels: next day's
`(high - open) / open >= {3%, 5%, 7%}`. `model/daytrade_backtest.py` fits
logistic regression and gradient boosting on an expanding walk-forward
window (12-month warm-up, 3-month test blocks, 5-trading-day embargo --
model/backtest.py's leave-one-year-out design doesn't transfer to a
1-day-horizon target since daily observations are heavily autocorrelated
and a naive split would leak).

**First result looked implausibly good**: out-of-sample Spearman IC of
**+0.38 average, correctly signed in 15/15 folds** -- far stronger than
any validated indicator here (valuation's +0.26 is the strongest, on a
much easier 1-year-forward target). A permutation test (shuffling the
label) correctly collapsed IC to ~0, ruling out a pipeline bug. Digging
into which feature drove it (`volatility_7d` alone: IC +0.35) exposed the
real explanation: **the label measures next-day volatility, not next-day
direction**. `(high - open) / open` is mechanically large whenever a
stock is volatile that day, whether it closes up or down -- checked
directly: `volatility_7d` vs. next day's actual close-to-close return has
IC **-0.02** (essentially nothing), and the highest-volatility quintile's
mean net day is **-0.08%** (not positive). The model was correctly
learning volatility clustering (a well-documented, unremarkable market
effect -- current volatility predicts near-term volatility), not
"forecasts a price increase."

**The real test**: extended `daytrade_backtest.py` to simulate the actual
trade rule instead of trusting the regression IC -- enter at next day's
open, exit at whichever of a take-profit or a fixed -3% stop-loss the
day's high/low touches first (assuming, pessimistically, that the
stop-loss triggers first if both are touched the same day, since only
OHLC bars are available and there's no real intraday sequencing), else
exit at close. Compared the model's flagged (top-decile probability)
trades against a same-size **random** selection from the same liquid
universe/day, across all 3 thresholds x 2 models x 15 folds (90
fold-combinations total).

**Results**: the model's picks underperformed random selection in
**every single one of the 90 fold-combinations** -- e.g. at the 3%
threshold, model trades averaged -0.72%/trade vs. random's -0.09%/trade.
Not "no better than random" -- consistently *worse*. This makes sense
given the volatility-clustering finding above: the model is selecting
more-volatile stocks without any offsetting directional edge, so under
the pessimistic (but realistic, given the data available) stop-loss-first
assumption, its picks get whipsawed and stopped out more often than a
random pick would.

**Verdict: no-go.** This does not clear the bar the other indicators were
held to, and the daily automation this would have needed (shortlist,
cloud price fetch, paper-trade ledger, news-catalyst scan, Gmail alerts)
was never built.

### Second attempt: fixed the label, added trend/moving-average features -- still no-go

Retried with two changes meant to directly address the first attempt's
diagnosis: (1) trained on genuine next-day direction instead of intraday
range -- new `close_up_{1,2,3}` labels, `close_to_close_next =
(next_close - next_open) / next_open >= {1%, 2%, 3%}` -- and (2) added
features that might actually carry directional information:
`trend_points` (trend.py's exact 4-point bull/bear moving-average
checklist, recomputed here across full history since `moving_averages`
-- unlike `ratings` -- is recomputed in full every run, not weekly-
overwritten), `price_vs_sma20/50/100/200`, and `return_20d` (short-
horizon momentum, closer to the trading horizon than the existing 6/12-
month momentum indicator). Trade simulation reused the same TP/SL/close
rule, with stop-loss matched 1:1 to each smaller direction threshold
(rather than the first attempt's fixed 3% stop against larger 3/5/7%
targets).

**Before even running the full backtest**, checking each feature's raw
Spearman IC against genuine next-day direction was cheap and telling:
every single feature -- including the new trend/moving-average ones --
came back within noise of zero (all |IC| <= 0.02, matching the noise
floor a shuffled-label permutation test showed). `trend_points`: -0.004.
`price_vs_sma20`: -0.004. `return_20d`: -0.002. None of the (otherwise
useful, validated-elsewhere) technical indicators available in this
pipeline carry next-day directional information on their own.

**Full backtest confirmed it, more cleanly than the first attempt**:
average OOS IC came back *negative* (-0.02 to -0.03 depending on
threshold/model, correctly signed in only 1-3 of 13 folds -- the model is
essentially fitting in-sample noise that doesn't generalize, not finding
a real inverse relationship either). Trade simulation: the model's
flagged picks underperformed a same-size random selection in **all 78
fold-combinations tested** (3 thresholds x 2 models x 13 folds), by
-0.36 to -0.67 percentage points per trade on average.

**Takeaway**: this isn't "the first attempt's label was the whole
problem" -- it's that this pipeline's available features (price/volume-
derived technicals: volatility, RSI, 52-week range, moving averages,
short-horizon momentum) carry no next-day directional signal for
individual liquid stocks, under either framing tried. That's the
expected result for well-known technical signals in liquid markets (any
edge from information this public would already be arbitraged away at a
1-day horizon) -- not a bug, not a modeling failure, a real null result
across two honest attempts. A third attempt would need genuinely
different information, not a different label or a different classifier
on the same technical inputs -- e.g. news/sentiment features (this
pipeline's existing `claude -p` + WebSearch pattern, used for the weekly
industry-sentiment overlay, is a plausible source), order-flow/options-
market data, or earnings-surprise proximity -- none of which are
currently available in this pipeline and would need new data sources
built first, not just new feature engineering on data already collected.

### Third attempt: real new data (PEAD) + a wider 5-day target -- also no-go, but not as cleanly null

Post-earnings-announcement drift (PEAD) -- prices keep drifting in the
direction of an earnings surprise for weeks afterward -- is one of the
most robust, widely-replicated anomalies in finance, unlike the
well-known technical signals attempts 1-2 already ruled out. Built
properly this time, with new data, not just new feature engineering:

- **New `earnings_events` table** (`pipeline/fetch_earnings_events.py`),
  populated from `yfinance`'s free `get_earnings_dates()` (EPS estimate,
  actual, surprise %) across the full ~5,920-symbol universe: 4,675
  symbols (79%) have coverage, 146,204 historical surprise data points,
  zero fetch failures. Point-in-time correct via a computed
  `effective_date` -- before-market-open announcements count from that
  same trading day, after-close (or ambiguous-time) announcements count
  from the next trading day, verified directly against real timestamps
  (DDOG's 7am announcements vs. AAPL's 4pm ones).
- **Wider, differently-shaped target** (Kevin's direction, moving away
  from the single-day framing both prior attempts used): `label_5day` =
  at least 3 of the next 5 trading days close up day-over-day AND the
  high somewhere in that window reaches >=3% above the origin close.
  Unlike attempt 1's flawed label, this one's construction was sanity-
  checked against genuine direction before trusting it (IC of the label
  itself vs. `forward_return_5` = +0.68, strongly positive as expected).
- **PEAD features**: `earnings_surprise_pct` and `days_since_earnings`
  (trading days since the most recent known surprise, not calendar days),
  added to the existing technical feature set (attempt 2's set, kept as
  a baseline for comparison, not because it was expected to help).
- **Multi-day trade simulation**: entry at day 1's open, walks all 5 days
  checking take-profit (3%, matching the label) / stop-loss (5%, wider
  than before given the longer hold) in sequence, same pessimistic
  same-day tie-break as attempts 1-2. 10-trading-day embargo (widened
  from 5, since the label now depends on 5 future days, not 1).

**Raw per-feature IC against genuine direction** (`forward_return_5`):
every feature, PEAD included, came back within noise
(`earnings_surprise_pct` +0.014, `days_since_earnings` -0.002, both
comparable to every technical feature already ruled out).

**Full backtest**: logistic regression came back essentially null again
(IC -0.002, 7/13 folds positive -- a coin flip; trade-sim edge -0.361%/
trade, 0/13 folds positive). Gradient boosting showed something weakly
different from pure noise for the first time across all three
attempts -- IC +0.016 (real but tiny; for scale, the validated valuation
indicator sits at 0.26, and the go/no-go bar set in attempt 1 was
0.05-0.08), correctly signed in 9/13 folds. But the trade simulation
still says no: average edge over random was **-0.068%/trade**, and only
7/13 folds (barely better than a coin flip) showed positive edge, with
the losing folds losing more than the winning folds won -- the classic
signature of a model fitting noise, not a real, exploitable edge.

**Verdict: no-go, same as attempts 1-2**, but worth being precise about
what's different this time: this isn't a clean null result like the
first two (where the deciding metric was flatly zero or the model lost
every single fold). Gradient boosting with PEAD features produced a
faint, real-but-tiny signal that doesn't survive contact with an honest
trade simulation. That's still a no -- don't build the automation this
would have needed (daily fetch, shortlist, paper-trade ledger, news
scan, alerts) -- but it's a different flavor of no than "nothing here at
all," and if this is ever revisited, gradient boosting with a richer
PEAD feature set (e.g. surprise magnitude interacted with days-since,
rather than as separate linear features; a revenue-surprise counterpart,
not just EPS) is a more promising next step than the technical-indicator
dead end attempts 1-2 already closed off. The `earnings_events` table
and PEAD feature-building code are kept in the pipeline (not deleted)
specifically so that follow-up doesn't have to redo this data-gathering
work.

### Fourth attempt: market-cap-bucketed multi-indicator model -- still no-go, but the calibration work is reusable

Kevin's direction: (1) a 90-day volume Z-score feature, (2) an RSP/SPY-
style ratio (equal-weight vs. cap-weight breadth, and MDY/SPY, IJR/SPY
for mid/small-cap rotation) as a market-regime signal, (3) separate
models per market-cap bucket instead of one pooled model. Built all
three, plus fixed a real bug found along the way:

- **Liquidity-filter-ordering bug fixed**: `daytrade_features.py`'s
  liquidity filter ran *after* the four expensive confluence-feature
  `merge_asof` calls instead of before, so those merges operated on the
  full ~6.37M-row universe instead of the ~2M-row liquid subset -- the
  root cause of two prior OOM crashes (documented in HANDOVER.md, not
  reproduced here). Fixed by reordering: momentum (needs full per-symbol
  row-contiguity for its row-position `pct_change`) stays before the
  filter; valuation/quality/insider/cap-rotation (all `merge_asof`-by-
  actual-date, unaffected by which rows survive filtering) moved after
  it. Confirmed fixed by memory-monitored reruns: full panel build now
  completes reliably in ~110-130s, peaking a consistent ~9.3-9.7GB (this
  machine now has enough free memory to absorb that peak; the earlier
  crashes happened when only ~2.2GB was free -- the fix is that the
  process now *returns* instead of growing unboundedly, not a claim that
  peak memory dropped).
- **Market-cap buckets**: Large (>=$10B) / Mid ($2-10B) / Small ($300M-
  2B), micro/nano (<$300M) excluded entirely, not folded into Small --
  checked against the actual liquid-symbol distribution first: only ~143
  of 2,132 liquid symbols fall under $300M, too thin to model separately
  once the existing $5M/day liquidity floor is applied.
- **`benchmark_prices` table** (`pipeline/fetch_benchmark_prices.py`,
  SPY/MDY/IJR) -- deliberately a separate table from `prices`, wired into
  the weekly scheduled job, because `snapshot.py` and other live-ratings
  scripts read `FROM prices` with no symbol allowlist; benchmark ETFs
  stored there would leak into the live weekly recommendation output.
- **`cap_rotation_momentum` feature** (`daytrade_confluence_features.py`):
  quarter-over-quarter (63-trading-day) rate of change of each ratio's
  1-year-smoothed level, signed so positive always means "capital
  rotating toward this stock's bucket" -- Mid = +MDY/SPY, Small =
  +IJR/SPY, Large = -mean(MDY/SPY, IJR/SPY) (no natural "Large/SPY" ratio
  exists since SPY effectively *is* large-cap; MDY and IJR are 0.81-
  correlated, so averaging rather than picking one arbitrarily).
  `volume_zscore_90d` (per-symbol 90-day rolling Z-score of raw volume)
  added alongside the existing `volume_vs_avg20d` ratio feature.
- **Trade-rule calibration was a real, separate confound worth
  documenting on its own**: the original 2%-take-profit/5%-stop-loss rule
  (inherited from attempt 3) has a 2.5x risk:reward ratio that turned out
  to be structurally unfavorable -- an *unconditional* "enter every trade,
  no model" test showed the random baseline itself was flat-to-negative,
  worse for smaller/more volatile buckets, because the 5% stop was
  *tighter than ordinary 5-day price noise* (median max-downside
  excursion from entry alone was -2.4% to -3.9% across buckets) and was
  catching genuine future winners as false stop-outs. Progressively
  widening the stop (up to 20%) and varying take-profit (1-2%) and
  horizon (5/10/20 trading days) took the unconditional baseline from
  negative to consistently positive across every bucket -- but the full
  23-feature ensemble never once cleared this project's go/no-go bar
  (edge > 0 in a clear majority of folds) at any calibration tested, in
  any bucket, with either model (logistic regression or gradient
  boosting). A no-stop-loss variant (exit only on take-profit, ride the
  full horizon otherwise, label switched from "best close reached" to
  "best intraday high reached") was also tried and came back worse than
  every stop-loss variant tested -- confirming the stop genuinely caps
  real tail risk, not just adding drag from being too tight.

**Verdict: no-go for the full multi-feature ensemble**, consistent with
attempts 1-3. But unlike those, the *trade-rule calibration itself* -- not
the feature set -- was doing a lot of the damage, and that finding
generalizes: any future attempt on this model should start from a wide
stop-loss (12.5-20%, not 5%) matched to the horizon's actual volatility,
not the original 2%/5% rule. The bucket infrastructure, `benchmark_prices`
table, and `cap_rotation_momentum`/`volume_zscore_90d` features are kept,
not deleted -- they fed directly into the fifth attempt below.

### Fifth attempt: RSI alone -- the first real signal across five attempts, not yet live

Dissecting why the fourth attempt's ensemble kept losing to random led to
testing each of the 23 `FEATURE_COLS` **individually** -- one feature,
one gradient-boosting model, per bucket, at 1% take-profit / 17.5%
stop-loss / 20-trading-day horizon (the calibration space attempt four
had already validated as reasonable). One feature stood out immediately:

**`rsi_14` alone was the #1 feature independently in all three buckets**,
and beat the full 23-feature ensemble everywhere:

| Bucket | rsi_14-alone edge | rsi_14-alone folds beating random | Full-ensemble edge |
|---|---|---|---|
| Large | +0.131%/trade | 10/13 | -0.035%/trade |
| Mid | +0.152%/trade | 12/13 | +0.050%/trade |
| Small | +0.122%/trade | 11/13 | -0.018%/trade |

The same feature ranking #1 independently in three separately-tested
buckets is much harder to explain as a multiple-comparisons artifact than
a single lucky result would be -- if it were noise, the three buckets'
winners wouldn't be expected to agree.

**Stability-tested across the entire calibration space already explored
in attempt four**, not just the one setup where it was found: 6 TP/SL
combinations (spanning the original 2%/5% rule through the widest 2%/10%
and 1.25%/20% variants) x 3 buckets at a 10-trading-day horizon --
**18/18 positive-edge results**, every one with a majority of folds
beating random. Repeated at a 5-trading-day horizon (5 combinations x 3
buckets) -- **15/15 positive**, if anything *stronger and more
consistent* than the longer horizons (Mid hit 12/13 folds, 92%, on two
separate calibrations). **33/33 tests positive overall**, across three
different horizons and six different TP/SL combinations, with no
exceptions. The pattern (strongest at the shortest horizon, weakening
gradually at 10 and 20 days) matches RSI's textbook role as a short-term
overbought/oversold oscillator -- not just a number that happened to
survive, a result with a coherent mechanistic story behind it.

**Adding features back in did not help, and sometimes hurt --** tried
three ways, all confirming RSI alone is the right stopping point, not an
underbuilt starting point:
1. A curated 8-11-feature "slim" set (every feature that individually
   showed a positive edge and a fold-majority) underperformed rsi_14
   alone in every bucket, and in Large was even worse than the full
   23-feature kitchen sink (-0.063% vs. -0.035% vs. rsi_14 alone's
   +0.131%).
2. `rsi_14` + `days_since_earnings` (the one other feature that was
   independently positive in all three buckets) for Large/Mid: edge and
   fold-count both dropped in both buckets -- Large's fold-win-rate fell
   from a 10/13 majority to 5/13, a minority.
3. `rsi_14` + `volume_vs_avg20d` + `volume_zscore_90d` for Small: no
   meaningful change either direction (+0.126% vs. +0.122% alone).

**Verdict: the first genuinely positive result across five attempts on
this model.** Not a large edge in absolute terms (best average ~+0.2%/
trade before any transaction costs), and every test above comes from the
same ~4.2-year local price history (2022-2026) -- one macro regime (the
AI/mega-cap-concentration era identified in this session's RSP/SPY
breadth analysis), not a genuinely independent out-of-sample era; the
pipeline's `HISTORY_YEARS=5` config bounds what's available locally, and
testing an earlier regime would need a much longer historical fetch, not
attempted here. Nothing built from this is live -- no automation, no
paper-trading, no slippage/cost modeling. Promising enough to be worth
carrying to the next stage (a cost-aware simulation or live paper-
trading) rather than shelving the way attempts 1-3 were, but "backtested
well across many calibrations" and "ready to trade real money" are not
the same claim.

---

## Live scoring

```bash
python rate_universe.py                     # cheapest/most expensive 20 (valuation)
python rate_universe.py --symbol AAPL
python rate_universe.py --out ratings.csv    # full ranked table
python trend.py                              # today's trend ratings
python momentum.py                           # today's momentum ratings
python quality.py                            # today's quality ratings
python insider_flag.py                       # today's insider buying flags
python congress_flag.py                      # today's congress buy/sell flags
python rsi.py                                # today's RSI ratings
python range52w.py                           # today's 52-week range ratings
```

`rate_universe.py` fits valuation weights on the entire 2022-2025 history
(no held-out fold -- that's only for evaluation) and rates every ticker as
of the latest stored price. None of the other five need fitting (trend,
quality, RSI, and 52-week range are fixed rules, momentum is a pure
cross-sectional rank), so their scripts just compute directly as-of today.

---

## Caveats (read before trusting any of this)

- **Survivorship bias**: the universe is today's listed companies, not
  who-was-listed-back-then. Every past snapshot is missing whatever
  delisted/went bankrupt since -- which tends to make "cheap"/"declining
  trend" stocks in the backtest look safer than they really were
  historically, since some genuine value/downtrend stocks were exactly
  that because they were dying.
- **Tiny sample**: at most 4, usually only 3, non-overlapping years is
  barely enough to say anything statistically. Treat every backtest here
  as a sanity check ("is the sign right, is it wildly overfit"), not proof
  of a durable edge.
- **Coverage isn't uniform**: earnings_yield only exists for profitable
  companies; trend/momentum need 200 days / 12 months of price history a
  newly-listed ticker won't have yet. A ticker missing one indicator's
  inputs just won't get a rating from that indicator.
- **Data quality**: Yahoo's adjusted-close feed has occasionally produced
  corrupted values (seen: exactly 0, even negative, for 2 tickers out of
  ~5,900) that would otherwise blow up a return calc into infinity --
  `snapshot.py`'s `load_prices()` now guards against non-positive
  close/adj_close. Worth remembering this is free, unofficial data, not a
  vetted feed -- spot-check anything that looks like an extreme outlier
  before trusting it.
- **No price/earnings-quality adjustments**: these are pure statistical
  screens. They don't know about debt loads, accounting red flags,
  one-off items, or moat quality -- a "rating 1" stock can be cheap (or
  uptrending, or high-momentum) for a very good reason.
- **Not investment advice.** These are research/backtesting tools for
  exploring whether these signals have historically correlated with
  forward returns in this dataset -- not a recommendation to buy or sell
  anything, and past correlation in a 3-4 year sample says very little
  about the future.
