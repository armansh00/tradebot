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
ignores every input** and says 53.5% every night — roughly the unconditional
base rate of up days. Report the **Brier skill score** against that
climatological reference:

    BSS = 1 - BS / BS_reference

Calibration alone is also insufficient. A model that says 55% every single
night is perfectly calibrated and worthless. Report reliability and resolution
separately (Murphy decomposition), or Brier skill alongside AUC. The question
is not "was yesterday right" but "when it says 60%, does it happen 60% of the
time, and does it say different numbers on different days".

## Forecast targets, ordered by what is actually forecastable

1. **Range** — tomorrow's expected high-low move.
2. **Volatility** — P(high-volatility session). Volatility clustering is one
   of the most robust empirical facts in finance; a 1986 GARCH still works.
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

That answers, within a couple of months and at zero data cost, whether the
forecasting layer contains any information at all — before paying for options
data to feed a layer that may contain none.
