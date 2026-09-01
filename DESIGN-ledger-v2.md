# Ledger v2 — specified, deliberately NOT built

Written 2026-09-01, before the first broker-facing run. **No code in this
document has been implemented.** It is recorded here so the design is not lost
and so the temptation to build it tonight is answered by writing it down
instead.

The reason for restraint: commissioning is worth more if the instrument is
frozen before the first live run than if it is patched in anticipation of
every failure I can imagine. Tomorrow should be allowed to break the system.
A failure that happens to a frozen implementation is data. A failure that
happens to an implementation I adjusted in the small hours to prevent it is
not.

## 1. UNSUPPORTED must not be excluded from the denominator

My earlier instinct — drop UNSUPPORTED so the selector is not blamed for the
calendar — creates a worse bias than the one it fixes. Consider:

```
Promoted:  10 strategies,  2 evaluable,  8 unsupported
Controls:  10 strategies,  9 evaluable,  1 unsupported
```

Reporting 2/2 = 100% against 3/9 = 33% makes the selector look excellent while
concealing that four fifths of what it chose could not be adjudicated at all.
If the selector systematically prefers hypotheses that generate few
observations, it is consuming future windows without producing information,
and that is a selector failure — a different one, not an absent one.

So report two quantities, both against the selector-null controls:

    P(evaluable | selected)
    P(survives | evaluable, selected)

```
PROMOTED
  Survives              2
  Falsified             0
  Unsupported           8
  Evaluability        20%
  Survival | evaluable 100%
```

Two questions, kept apart: did the selector choose hypotheses the window could
test, and among those it could resolve, did it choose well? Sample efficiency
is part of research quality, not a nuisance to be normalised away.

## 2. Expected power belongs in Commit A

Extend the specification:

```yaml
adjudication:
  expected_observations: 180
  minimum_observations: 120
  target_precision: ...
  expected_power: ...
```

Which splits UNSUPPORTED in two:

- **EXPECTED_UNSUPPORTED** — known in advance to be underpowered. Says
  something about the research plan.
- **UNEXPECTED_UNSUPPORTED** — expected to resolve and did not: signals
  occurred less often than predicted, data were missing, execution failed,
  volatility shifted. Says something about the hypothesis or the design, and
  is the more interesting of the two.

## 3. Precision-weighted delta

+20 bp from 8 trades and +20 bp from 800 trades must not contribute equally.
Every strategy record retains both the estimate and its standard error, and
the vintage metric is precision-weighted — without collapsing anything into
PASS/FAIL.

## 4. Adjudication efficiency as a process metric

    (SURVIVES + FALSIFIED) / all preregistered hypotheses

Tracked per mechanism family. If M01 resolves 90% of the time and M05 resolves
30%, that is information about where the research budget is being spent, and
it is invisible unless measured.

## The three reports, kept separate

```
PROCESS PERFORMANCE      continuous selector delta
ADJUDICATION PERFORMANCE fraction resolvable
CONFIRMATORY OUTCOME     SURVIVES / FALSIFIED / UNSUPPORTED
```

## Commissioning note

P&L carries zero weight in tomorrow's verdict and is recorded unchanged
anyway. Six months from now the commissioning period should not be missing its
execution economics merely because they were not inferential evidence at the
time.
