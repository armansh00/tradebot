"""One process per trading day — the schedule lives here, not in GitHub's cron.

Why: GitHub's `schedule` event is explicitly best-effort. On 2026-08-31 the
13:40 and 14:05 UTC ticks never fired at all, and the Sunday review cron ran
3h50m late. A fast-arm tick that fires hours late is not the tick the protocol
pre-registered — it is a different experiment with the same name. So GitHub is
demoted to "start this process sometime today" and the process does its own
timing off the exchange calendar.

Every tick that is missed anyway (late start, deadline handoff, API error) is
written to the ledger as a first-class event. A gap the record does not admit
to is worse than a gap.
"""
from __future__ import annotations

import json
import os
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .config import Config
from .ledger import Ledger
from .preflight import run_preflight

SLOW = "slow"
FAST = "fast"
ET = ZoneInfo("America/New_York")


def to_session_utc(day, value, tz=ET) -> datetime:
    """Exchange-local open/close -> UTC, whatever shape the SDK hands back.

    alpaca-py has returned `datetime.time` in some versions and a naive
    `datetime` in others; a tz-aware datetime is possible too. Guessing wrong
    here does not raise in an obvious place — it silently shifts the whole
    trading day — so the coercion is explicit and tested.
    """
    if isinstance(value, datetime):
        dt = value if value.tzinfo is None else value.astimezone(tz).replace(tzinfo=None)
        dt = datetime.combine(day, dt.time())
    else:
        dt = datetime.combine(day, value)
    return dt.replace(tzinfo=tz).astimezone(timezone.utc)


@dataclass(frozen=True)
class Tick:
    at: datetime
    kind: str  # SLOW or FAST


def build_schedule(open_utc: datetime, close_utc: datetime, cfg: Config) -> list[Tick]:
    """Pre-registered cadence, derived from config — not hand-typed into cron.

    Slow arm once, two minutes after the open. Fast/movers arms every
    `every_minutes` starting once the opening range has closed, through to a
    final tick two minutes before the bell so the flatten logic always gets a
    turn even on a half day.
    """
    or_minutes = int(cfg.fast.or_minutes)
    every = int(cfg.fast.tick_minutes)

    ticks = [Tick(open_utc + timedelta(minutes=2), SLOW)]

    t = open_utc + timedelta(minutes=or_minutes + 5)
    last_fast = close_utc - timedelta(minutes=2)
    while t < last_fast:
        ticks.append(Tick(t, FAST))
        t += timedelta(minutes=every)
    ticks.append(Tick(last_fast, FAST))

    return sorted(ticks, key=lambda x: (x.at, x.kind))


def _ticks_in(lines, own_run: str | None = None) -> set[str]:
    """Scheduled timestamps of ticks that are spoken for.

    A verdict (`tick`, `tick_error`) is spoken for. So is a `tick_claim` from
    any run other than this one: the claim is the lock, and it is written and
    pushed before the tick executes precisely so that a second process waking
    at the same minute sees it before doing the same work.

    An errored tick counts as done: it was attempted at its scheduled moment,
    and a second process re-running it minutes later would be executing the
    arm off-cadence — a different experiment wearing the same name.
    """
    done = set()
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue                                  # fail closed, not loud
        if not event.get("scheduled"):
            continue
        if event.get("type") in ("tick", "tick_error"):
            done.add(event["scheduled"])
        elif event.get("type") == "tick_claim" and event.get("run") != own_run:
            done.add(event["scheduled"])
    return done


def _completed_ticks(cfg: Config, peek=None, own_run: str | None = None) -> set[str]:
    """Which scheduled ticks already ran today, by scheduled timestamp.

    Two readers. The local ledger answers for this process — leg 2 resuming
    what leg 1 handed off has to tell "already done" from "missed", or the
    handoff itself manufactures a missing-data story about a day that went
    fine. `peek` answers for every OTHER process alive today.

    The second reader is what lets the redundant starts be genuinely
    redundant. Before 2026-09-02 duplicate work was prevented by a GitHub
    `concurrency` group, which does not prevent duplicate work — it cancels
    the queued run outright. On 2026-09-01 that turned seven independent
    launch chances into one, and when that one lost five hours, so did the
    day. Concurrency control belongs here, where it can let the spares run
    and still keep each tick to a single execution.
    """
    done = set()
    if cfg.ledger_path.exists():
        done |= _ticks_in(cfg.ledger_path.read_text().splitlines(), own_run)
    if peek:
        try:
            done |= _ticks_in(peek() or [], own_run)
        except Exception:                             # noqa: BLE001
            pass                                      # a blind spare beats none
    return done


def as_brokers(broker) -> dict:
    """Each arm has its own account now. A bare broker (tests, or a single
    account) is fanned out to all three."""
    if isinstance(broker, dict):
        return broker
    return {SLOW: broker, "fast": broker, "movers": broker}


def _run_tick(cfg: Config, brokers, kind: str, disabled=frozenset()) -> dict:
    """Run the arms that belong to this tick and that preflight cleared.

    A disabled arm still produces a record. Silence would be indistinguishable
    from a tick that never happened, which is the ambiguity this whole module
    exists to remove."""
    brokers = as_brokers(brokers)
    if kind == SLOW:
        if SLOW in disabled:
            return {"slow": {"status": "arm_disabled"}}
        from .run import run_once
        return {"slow": run_once(cfg, brokers[SLOW])}
    from .fastarm import run_fast_once
    out = {}
    for arm in ("fast", "movers"):
        if arm in disabled:
            out[arm] = {"status": "arm_disabled"}
            continue
        # One arm's failure is that arm's failure. For a week a screener pick
        # the venue would not trade fractionally raised inside movers and the
        # whole tick was recorded as an error — after fast had already run
        # and traded. The record said "tick failed"; the truth was "movers
        # failed, fast fine".
        try:
            out[arm] = run_fast_once(cfg, brokers[arm], arm=arm)
        except Exception as exc:                      # noqa: BLE001
            out[arm] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"[:300],
                        "traceback": traceback.format_exc()[-600:]}
    return out


def _persist(on_tick_done, sleep, attempts: int = 3) -> bool:
    """Push the record out of this runner and confirm it left.

    Returns True when there is nothing to persist (no hook — tests, a local
    run) or the hook acknowledged. A hook that returns None acknowledges by
    silence, which is how the plain callables in the tests behave; a
    `CompletedProcess` acknowledges with returncode 0.
    """
    if on_tick_done is None:
        return True
    for attempt in range(1, attempts + 1):
        try:
            result = on_tick_done()
        except Exception:                             # noqa: BLE001
            result = False
        code = getattr(result, "returncode", None)
        if code == 0 or (code is None and result is not False):
            return True
        if attempt < attempts:
            sleep(5 * attempt)
    return False


def run_session(
    cfg: Config,
    broker,
    *,
    now=None,
    sleep=None,
    deadline_minutes: float | None = None,
    on_tick_done=None,
    preflight=run_preflight,
    peek=None,
) -> dict:
    now = now or (lambda: datetime.now(timezone.utc))
    sleep = sleep or time.sleep
    ledger = Ledger(cfg.ledger_path)
    run_id = os.environ.get("GITHUB_RUN_ID") or f"local-{os.getpid()}"

    started = now()
    if deadline_minutes is None:
        deadline_minutes = float(os.getenv("TRADEBOT_SESSION_DEADLINE_MIN", "340"))
    deadline = started + timedelta(minutes=deadline_minutes)

    window = as_brokers(broker)[SLOW].session_today()
    if window is None:
        ledger.write("session", status="market_closed_today")
        return {"status": "market_closed_today", "ran": 0, "missed": 0, "resumed": 0}

    open_utc, close_utc = window
    if started >= close_utc:
        ledger.write("session", status="started_after_close",
                     started=started.isoformat(), close=close_utc.isoformat())
        return {"status": "started_after_close", "ran": 0, "missed": 0, "resumed": 0}

    schedule = build_schedule(open_utc, close_utc, cfg)
    ledger.write(
        "session", status="start", started=started.isoformat(),
        market_open=open_utc.isoformat(), market_close=close_utc.isoformat(),
        deadline=deadline.isoformat(), ticks_planned=len(schedule),
        late_minutes=round(max(0.0, (started - open_utc).total_seconds() / 60), 1),
    )

    # Commission the accounts before the bell. An arm that cannot read its own
    # data does not trade today; it is not quietly switched to another feed.
    brokers = as_brokers(broker)
    report = preflight(cfg, brokers, ledger=ledger)
    disabled = set(getattr(report, "disabled", ()) or ())
    if not getattr(report, "any_enabled", True):
        ledger.write("session", status="preflight_fail",
                     disabled=sorted(disabled), ended=now().isoformat())
        return {"status": "preflight_fail", "ran": 0, "missed": 0, "resumed": 0,
                "disabled": sorted(disabled)}

    done = _completed_ticks(cfg, peek, run_id)
    ran = missed = resumed = 0
    for tick in schedule:
        t = now()

        if tick.at.isoformat() in done:
            resumed += 1
            continue

        if tick.at < t - timedelta(minutes=2):
            # Already gone. Say so out loud rather than quietly running it late:
            # a tick executed off-cadence contaminates the arm it belongs to.
            ledger.write("tick_missed", scheduled=tick.at.isoformat(), kind=tick.kind,
                         late_minutes=round((t - tick.at).total_seconds() / 60, 1),
                         reason="process_started_after_tick")
            missed += 1
            continue

        if tick.at > deadline:
            # The one exit with no next tick behind it. Everywhere else a
            # failed push is retried in thirty minutes; here the process is
            # about to be destroyed, so the record has to be out of this
            # runner BEFORE the return. On 2026-09-01 it was not: the handoff
            # was written to a local file and the runner took it with it,
            # leaving 5h45m that nothing in the ledger admitted to.
            remaining = [x for x in schedule if x.at > deadline]
            ledger.write("session", status="handoff", at=t.isoformat(),
                         deadline=deadline.isoformat(),
                         ticks_deferred=len(remaining),
                         deferred=[x.at.isoformat() for x in remaining],
                         ran=ran, missed=missed, resumed=resumed)
            persisted = _persist(on_tick_done, sleep)
            if not persisted:
                ledger.write("session", status="handoff_unconfirmed",
                             at=now().isoformat(),
                             detail="handoff written locally but not acknowledged "
                                    "by the commit hook; the successor may not "
                                    "see it")
            status = "handoff" if persisted else "handoff_unconfirmed"
            return {"status": status, "ran": ran, "missed": missed,
                    "resumed": resumed, "disabled": sorted(disabled),
                    "persisted": persisted}

        wait = (tick.at - t).total_seconds()
        if wait > 0:
            sleep(wait)

        # Re-check at the moment of execution, not only at start-up. Several
        # processes legitimately share a trading day; the one that gets here
        # first does the work and the rest record it as already done. Any
        # residual race is caught downstream by broker-enforced idempotency:
        # every intraday order carries a deterministic client_order_id and the
        # slow arm refuses a second run on the same date.
        done |= _completed_ticks(cfg, peek, run_id)
        if tick.at.isoformat() in done:
            resumed += 1
            continue

        # Claim it, and get the claim off this machine before doing the work.
        # A git push is atomic: whoever lands the claim first owns the tick.
        # Two processes waking at the same minute used to both execute; the
        # broker refused the second order (client_order_id) but the second
        # process's ledger lines then lost a rebase against the first's, and
        # real fills vanished from the record. Now the loser sees the claim.
        ledger.write("tick_claim", kind=tick.kind, scheduled=tick.at.isoformat(),
                     run=run_id, at=now().isoformat())
        if on_tick_done and not _persist(on_tick_done, sleep, attempts=2):
            done |= _completed_ticks(cfg, peek, run_id)
            if tick.at.isoformat() in done:
                ledger.write("tick_claim_lost", kind=tick.kind,
                             scheduled=tick.at.isoformat(), run=run_id)
                resumed += 1
                continue
            # Could not publish and nobody else has it: trade anyway. A day
            # with no process willing to trade is worse than a duplicate the
            # broker will refuse and a ledger line union-merge will keep.

        try:
            result = _run_tick(cfg, broker, tick.kind, disabled)
            ran += 1
            ledger.write("tick", kind=tick.kind, scheduled=tick.at.isoformat(),
                         run=run_id,
                         status={k: v.get("status") for k, v in result.items()},
                         errors={k: v.get("error") for k, v in result.items()
                                 if v.get("status") == "error"} or None)
        except Exception as exc:                      # one bad tick != a lost day
            missed += 1
            ledger.write("tick_error", kind=tick.kind, scheduled=tick.at.isoformat(),
                         error=f"{type(exc).__name__}: {exc}",
                         traceback=traceback.format_exc()[-800:])

        if on_tick_done:
            # Mid-day this is best-effort on purpose: a failed commit is
            # retried by the next tick, and a git problem must never end a
            # trading day. The deadline handoff is the one place that cannot
            # take that view, and it uses _persist instead.
            try:
                on_tick_done()
            except Exception as exc:                  # noqa: BLE001
                ledger.write("persist_error", scheduled=tick.at.isoformat(),
                             error=f"{type(exc).__name__}: {exc}"[:200])

    ledger.write("session", status="complete", ran=ran, missed=missed,
                 already_done=resumed, disabled=sorted(disabled),
                 ended=now().isoformat())
    return {"status": "complete", "ran": ran, "missed": missed,
            "resumed": resumed, "disabled": sorted(disabled)}
