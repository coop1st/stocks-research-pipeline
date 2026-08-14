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
