# 2026-08-31 — discarded, and why

This is the first weekday of the intended eight-week window. It is not part
of the record. Two things happened on the same day and both of them make the
data uninterpretable, so it is archived rather than analysed.

## The scheduler did not fire

Four scheduled events were due — `daily.yml` at 13:40 UTC and `intraday.yml`
at 14:05, 14:35 and 15:05 UTC. GitHub delivered none of them. `fast-arm-
intraday` had zero runs in its entire history. The previous day's review cron
ran 3h50m behind its 12:00 UTC slot, which is the same failure in a milder
form. The session was started by hand at 16:00 UTC — 150 minutes after the
open — and its first five ticks are recorded here as `tick_missed`. An
opening-range arm that starts at noon is not a late version of itself.

Fix: the cadence moved out of GitHub's cron and into the process
(`session.py`), started by five staggered crons instead of one.

## The fill model changed

Until today the intraday arms simulated their own fills, because all three
arms shared one $100k paper account and real orders would have pooled their
positions into one unattributable pile. That constraint no longer exists:
Alpaca now allows additional paper accounts with a chosen starting balance.
Each arm now has its own $50 account and sends real orders, with the modeled
cost kept as a separate accrued line so both gross and net are reportable.

Mixing simulated-fill days with broker-fill days inside one pre-registered
window would make the comparison meaningless. Day one is the only clean place
to make this change, so it was made here and the clock restarts.

## What is kept

Everything: ledgers, state, the day's report, the missed-tick events. A
discarded day that cannot be inspected is just a gap.
