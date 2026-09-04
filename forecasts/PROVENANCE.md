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

## Open scoring problems

1. **No preregistered climatology.** Brier alone is not skill. The base rate
   for an SPY down day has to be declared from repository data, before the
   next forecast is scored, and then left alone. Picking it afterwards makes
   any skill score meaningless.
2. **Hit/miss on an interval is a coarse rule.** A range forecast graded
   binary treats a 0.01pp miss and a 0.50pp miss identically. An interval
   score (Winkler, or pinball loss on the two endpoints as quantiles) is
   strictly better and costs nothing to compute.
3. **Unadjudicable invalidation conditions.** The 2026-09-03 invalidation was
   `WTI >= 92 before 09:30 ET` and could not be verified from available
   sources; the 2026-09-04 condition has the same shape. A commitment nobody
   can adjudicate is not a commitment. Future invalidations should reference
   an instrument this repository can query directly.
