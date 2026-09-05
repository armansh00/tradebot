# Which forecasts are actually frozen

A forecast is only evidence if it existed, unedited, before the session it
describes. A forecast that lives in a chat log is not that: the transcript can
be re-read selectively, the wording remembered generously, and nobody has to be
dishonest for the record to drift.

So this directory holds two categories, and they are never mixed.

**Frozen in the repository (scoreable)**

| Session | File | Committed |
|---|---|---|
| 2026-09-04 | `2026-09-04.json` | 05:06 UTC, 8h24m before the open |

**Made in conversation only (NOT scoreable)**

- 2026-09-02 and 2026-09-03. Both were produced and adjudicated in chat. They
  may well have been written honestly and in advance — the point is that
  nothing here can demonstrate it, so they are excluded from any skill
  computation rather than counted on trust. They are mentioned in this file
  and nowhere else.

Backfilling those two as JSON now would produce files indistinguishable from
genuinely frozen ones. That is exactly the corruption this directory exists to
prevent, so it is not done.

## How scoring works

`python -m tradebot score-forecast [DATE]` reads the frozen file, pulls the
session's daily bars from Alpaca, and writes `DATE.scored.json`. It refuses to
re-score a session that already has a verdict: the inputs are not going to
change, and re-scoring is how a record drifts. `forecast-report` aggregates
across every scored session. `.github/workflows/forecast-score.yml` runs both
at 17:40 ET so the verdict never waits on someone deciding it is a good moment
to look.

No number in a scored file is typed by a human or by a model. That is the
whole point — the forecaster does not hold the pen on its own verdict.

## The three scoring problems, and where each stands

1. **Climatology — resolved.** `python -m tradebot climatology` declares the
   unconditional SPY down-day rate from every daily bar up to the
   pre-registered research cutoff, writes it to `CLIMATOLOGY.json`, and
   refuses to redeclare it. The window is fixed mechanically rather than
   chosen, and it ends before any forecast in this repository existed.
2. **Interval grading — partly resolved, deliberately.** Each range verdict
   now carries `miss_pct`, the distance outside the interval, so a 0.01pp miss
   and a rout are no longer the same result. A proper interval score (Winkler)
   needs a declared coverage level and the forecasts state none, so
   `interval_score` stays null with the reason attached rather than assuming
   80% or 90% and quietly making the number up.
3. **Invalidation — unresolved, and recorded as such every day.** Both
   conditions so far have referenced WTI before 09:30 ET. This repository has
   no source for crude futures, so every verdict marks the condition
   `unadjudicable`. A commitment nobody can settle is not a commitment. Future
   invalidations should name an instrument this repository can query.

**Skill scores are not reported for a single session.** With n=1 the reference
Brier can sit arbitrarily close to zero and the ratio is noise wearing a
decimal point. `aggregate` computes the Brier skill score across a run of
sessions, with n attached.
