# Nightly forecast — schema and scoring separation

The forecaster produces commitments only. A separate scorer reads the frozen
file after the close and computes the outcome. **A model does not determine
whether its own prediction was right** — the same sentence can be written up
as "close" or as "a miss" depending on who holds the pen, and nobody has to be
lying for that to corrupt the record.

    forecast          -> immutable input
    actual market data -> immutable input
    scoring code      -> deterministic adjudication
    commentary        -> optional, separate field, never the verdict

## The nightly file: `forecasts/YYYY-MM-DD.json`

Machine-readable fields only. Prose belongs elsewhere.

```json
{
  "forecast_for": "2026-09-02",
  "created_at": "2026-09-01T22:00:00-04:00",
  "spy_expected_range_low_pct": 0.8,
  "spy_expected_range_high_pct": 1.2,
  "spy_p_down": 0.57,
  "relative_call": "energy_over_semiconductors",
  "invalidation": {"instrument": "US10Y", "condition": "<", "level": 4.65}
}
```

Written before the session, never edited afterwards. Scored against the
climatological references declared in DESIGN-forecast.md — a fixed pre-study
base rate as primary, expanding-window as a secondary check.

The forecast may not enable, disable, approve, reject or skip a trade. Two
questions, in order: can the overnight forecast predict anything, and only
then, does using it improve trading.

---

# What is enforced, and what is only designed

Worth keeping honest, because the difference is invisible from the outside and
it is the kind of thing that quietly rots.

**Enforced by code today**

- research records, hash-chained and verifiable
- strategy freezing, pre-registration and the strategy-bound vault
- the seven research gates, REJECT by default
- the commissioning execution instrument, frozen at
  `commissioning-freeze-2026-09-01`
- per-order logging: intent id, decision and submit timestamps, quote at
  decision, intended vs filled quantity, expected vs fill price
- broker-enforced idempotency via deterministic client_order_id

**Designed, documented, NOT enforced**

- nightly forecast storage and scoring (this file)
- human-supervision states, including `USER_SKIPPED`
- ledger v2: evaluability rates, expected power, precision-weighted delta
- the always-on execution service and real-time fill reconciliation

Until the supervision layer exists, a manual deviation is recorded by hand or
not at all. Better to label the gap than to let the ledger imply a
completeness it does not have.
