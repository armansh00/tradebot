# Nightly forecasting layer — specified, NOT built

Written 2026-09-01. **No code implemented.** A probability forecast, not an
oracle.

## Division of labour

    market and event data
        -> statistical / ML models  -> probability estimates
        -> language model           -> explanation for a human

The model computing P(SPY up tomorrow) is a statistical model trained and
scored for that task. The language model structures news and filings into
variables and explains the output afterwards. It is never asked "will SPY go
up tomorrow".

## Score against a baseline, not against zero

A Brier score of 0.24 sounds respectable and is **worse than a forecaster that
ignores every input** and predicts the base rate every night. Report the
**Brier skill score** against that climatological reference:

    BSS = 1 - BS / BS_reference

**The reference itself must not look ahead.** Scoring a 2026 forecast against
a 53.5% up-day rate partly learned from 2027 is a look-ahead in the baseline —
subtler than a look-ahead in the model, and it flatters or punishes the model
depending on which way the future went. Two references, both declared before
launch:

    primary    a fixed base rate from a training period strictly BEFORE the
               study begins. Cannot drift, cannot be revised.
    secondary  expanding window, using only days observed before t. Honest,
               but very noisy in the first weeks, so it is a check rather
               than the headline.

Calibration alone is also insufficient. A model that says 55% every single
night is perfectly calibrated and worthless. Report reliability and resolution
separately (Murphy decomposition), or Brier skill alongside AUC. The question
is not "was yesterday right" but "when it says 60%, does it happen 60% of the
time, and does it say different numbers on different days".

## Forecast targets, ordered by what is actually forecastable

1. **Range** — tomorrow's expected high-low move.
2. **Volatility** — next-day realised variance. Volatility clustering is one
   of the most robust empirical facts in finance, but that is not a reason to
   assume GARCH wins: model rankings depend on the loss function, and simple
   realised-measure or EWMA forecasts beat GARCH variants in some samples.
   Baselines declared before launch — yesterday's realised volatility, and an
   EWMA — and scored with **QLIKE** alongside a squared-error measure, since
   the target is itself a proxy and error measures disagree about which model
   wins.
3. **Relative ranking** — P(name beats SPY tomorrow), cross-sectional.
4. **Gap** — P(gap up / down) for watchlist names.
5. **Direction** — P(index up). Last, with low expectations. Daily index
   direction is close to unforecastable and the base rate is already ~53.5%,
   so a claimed 57% must beat 53.5% by enough to survive the number of nights
   it was attempted.

For a breakout strategy this ordering is also the useful one: whether tomorrow
*moves* matters far more than which way it closes.

## The forecaster is a hypothesis, not a privileged input

The moment a forecast enables a strategy — "turn on momentum when
P(high vol) > 78%" — that threshold is a researcher degree of freedom, and 78
was chosen somehow. Five forecasts across a dozen names nightly is a larger
multiple-testing surface than anything tested so far. It goes through the same
gates, the same pre-registration and the same vault, or the validation engine
has an unvalidated oracle bolted onto its input.

Every forecast is frozen before the session with a hash, and scored the
following night. Yesterday's forecast is never rewritten.

**Bind the hash to the information set, not the wall clock.** Two fields, not
one:

    forecast_created_at: 22:30:04
    information_as_of:   22:29:30

A program running at 22:30 can consume a source published, revised or
timestamped later. The stronger version: hash the actual input data, not only
its timestamp — vendors revise bars, and a revision that silently changes
history is then detectable by re-fetching and comparing. This is the vault
rule applied to forecasting.

## What the forecast may and may not do

    forecast -> premarket configuration -> strategy engine -> risk -> broker

It prepares: which strategies are enabled, what is on the watchlist, what
would invalidate the expectation. It never creates a trade. Every watchlist
entry carries `TRADE STATUS: WATCH ONLY`.

## Buildable v1 with data we actually have

Implied volatility, put/call positioning, skew and term structure are not in
Alpaca's free tier; the economic calendar and earnings need another source. So
v1 is:

- daily bars for the six ETFs and the eight fast-arm names
- realised-volatility forecast and range forecast
- cross-sectional relative-strength ranking
- each frozen nightly with a hash, each scored against climatology

Ranking is evaluated by rank correlation with next-day returns AND by
something economic: top group minus bottom group, after costs. Direction is
recorded but does not carry the system.

**No forecast may enable or disable a trading strategy in v1.** These are two
experiments and running them as one destroys both:

    1. Does the forecast contain information?
    2. Does conditioning a strategy on it improve trading?

The second is only worth asking after the first says yes.

This answers, within a couple of months and at zero data cost, whether the
forecasting layer contains any information at all — before paying for options
data to feed a layer that may contain none.
