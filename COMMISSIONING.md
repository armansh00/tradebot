# Commissioning log

An instrument is commissioned before it is used, and the commissioning result
is written down whether or not it is flattering. Nothing in this file is ever
edited after the fact; a failed run stays exactly as it read on the day.

The specification is fixed and identical for every run:

> On a normal trading session, with no manual intervention, the system starts
> a process, executes the pre-registered cadence on all three arms, records
> every tick and every miss, and leaves a record in the repository that
> accounts for the whole session. A gap the record does not admit to is a
> failure regardless of P&L.

P&L carries zero weight here. A profitable day on a broken instrument is a
failed commissioning run.

---

## Run 1 — 2026-09-01 — **FAIL**

Tag: `commissioning-freeze-2026-09-01`

### What worked

- All seven start crons fired, against zero of four the previous day.
- Session started 08:35:49 UTC, `late_minutes: 0.0`, 14 ticks planned,
  deadline 14:15:49.
- Slow arm ran at 13:32:01 (open + 2 min) on the correct account: equity
  $49.62, holdings IWM $23.72 / QQQ $23.49, day −0.76%, no orders — the
  target portfolio was unchanged, so placing none was correct.
- Missed ticks were logged with their exact lateness.

### Defects

1. **The fast and movers arms never traded.** Both ticks died on
   `APIError: subscription does not permit querying recent SIP data`, at
   14:05 and 19:58. Direct consequence of moving those arms onto their own
   paper accounts: the new accounts authenticate, report equity and accept
   orders, but carry no market-data entitlement — and nothing checked before
   the session that each account could read the data its strategy needs.
2. **The workflow's own `concurrency` group cancelled the redundancy.** Runs
   4 through 8 all ended `cancelled`; only 3 and 9 ran. A concurrency group
   does not queue a superseded run, it kills it. Seven independent launch
   chances became one.
3. **A 5h45m hole with no record.** Leg 1's deadline was 14:15:49 and the
   next session start is stamped 19:59:44 — one minute before the close. A
   handoff event should sit in that gap. It does not: the handoff path wrote
   its record locally and returned without calling the commit hook, so the
   record died with the runner.

### Verdict

**FAIL.** Commissioning did its job: it found three blocking defects before
live use. The instrument is not approved for live execution.

---

## Run 2 — candidate

Tag: `commissioning-candidate-2026-09-02`

Three fixes, one commit and one regression suite each:

| Defect | Commit | Guard |
|---|---|---|
| 1. no data preflight | `cf846c3` | `tests/test_preflight.py` (14) |
| 2. concurrency cancels spares | `e83fe76` | `tests/test_concurrent_starts.py` (7) |
| 3. handoff not persisted | `39a02a5` | `tests/test_handoff_persistence.py` (7) |

### Open, and not a code question

The SIP entitlement itself is unresolved. The preflight now refuses to trade
an arm that cannot read its own data, and deliberately does **not** fall back
to another feed: IEX is a small share of consolidated volume, so its NBBO,
its prints and its bar closes are not the SIP's. An arm switched quietly to
IEX would be running against a pre-registration written on SIP.

Two admissible resolutions, both of which are decisions rather than repairs:

- obtain the SIP entitlement for the fast and movers accounts, and re-run
  commissioning unchanged; or
- declare IEX as those arms' data source, amend the pre-registration, and
  re-run the design work that was done on SIP data.

Until one of those happens, run 2 will commission the slow arm and report the
intraday arms as `DATA_PREFLIGHT_FAIL` — which is the correct behaviour, and
still not a pass.

### Result

_Pending._ Blocked on the data entitlement. On 2026-09-02 and 2026-09-03 every
session ended `DATA_PREFLIGHT_FAIL` with fast and movers disabled, exactly as
designed: seven launches per day, zero cancelled, handoffs recorded with their
deferred tick lists, 12/14 and 13/14 ticks executed. The scheduler and
persistence defects are closed in production. The intraday arms have generated
no evidence because they have not been permitted to run.

Feed decision recorded in `DECISION-2026-09-04-data-feed.md`: SIP retained,
IEX rejected, entitlement to be purchased.


---

## Run 3 — candidate

Tag: `commissioning-candidate-2026-09-04`

Three execution-safety defects, one commit and one regression suite each.
Separate from run 2 on purpose: September 1's FAIL and each fixed candidate
stay distinct records, and no fix is retrofitted into a tag that was already
run.

| Defect | Commit | Guard |
|---|---|---|
| halt file ignored by the intraday arms | `0c00b0c` | `tests/test_halt_semantics.py` (7) |
| session close hardcoded at 16:00 | `094c767` | `tests/test_half_day.py` (8) |
| exits indexed the universe snapshot | `c7e586e` | `tests/test_missing_bar_exits.py` (9) |

Halt semantics, settled before the code was written rather than after:
`.halt` blocks new entries, never blocks protective exits or reconciliation,
and — being an emergency switch — flattens open intraday positions and keeps
the arm down until it is lifted. The naive `if halted: return` would have
fixed the reported bug while disabling every stop in the process.

### Still open

- **Data entitlement.** Unpurchased. The intraday arms remain
  NOT COMMISSIONED and are producing no evidence; the safety work above is
  what has to be true *before* they run, not a substitute for running them.
- **`feed-binding` branch.** Unmerged by design until the subscription exists
  and the probe shows SIP for the credentials in use. Merging first would
  disable the slow arm, which is the only arm currently producing evidence.
- **`book_equity` rebaseline.** A large equity drop is treated as an account
  reset. Not reachable in the present configuration — a $50 book inside a
  $100k account cannot drag raw equity into the trigger band — but on a
  genuinely $50 account a 50% trading loss would re-anchor instead of
  halting. Account resets should be an explicit event, never inferred from a
  number. Documented, deliberately not folded into the execution patch.
- **`max_positions`** is counted on buy orders rather than on the resulting
  set of held symbols.
