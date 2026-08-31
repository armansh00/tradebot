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

from .config import Config
from .ledger import Ledger

SLOW = "slow"
FAST = "fast"


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


def _completed_ticks(cfg: Config) -> set[str]:
    """Which scheduled ticks already ran today, by scheduled timestamp.

    Leg 2 resumes what leg 1 handed off, so it has to tell "already done" from
    "missed". Without this the handoff itself would manufacture a missing-data
    story about a day that went fine.
    """
    path = cfg.ledger_path
    if not path.exists():
        return set()
    done = set()
    for line in path.read_text().splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue                                  # fail closed, not loud
        if event.get("type") == "tick" and event.get("scheduled"):
            done.add(event["scheduled"])
    return done


def _run_tick(cfg: Config, broker, kind: str) -> dict:
    if kind == SLOW:
        from .run import run_once
        return {"slow": run_once(cfg, broker)}
    from .fastarm import run_fast_once
    return {
        "fast": run_fast_once(cfg, broker, arm="fast"),
        "movers": run_fast_once(cfg, broker, arm="movers"),
    }


def run_session(
    cfg: Config,
    broker,
    *,
    now=None,
    sleep=None,
    deadline_minutes: float | None = None,
    on_tick_done=None,
) -> dict:
    now = now or (lambda: datetime.now(timezone.utc))
    sleep = sleep or time.sleep
    ledger = Ledger(cfg.ledger_path)

    started = now()
    if deadline_minutes is None:
        deadline_minutes = float(os.getenv("TRADEBOT_SESSION_DEADLINE_MIN", "340"))
    deadline = started + timedelta(minutes=deadline_minutes)

    window = broker.session_today()
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

    done = _completed_ticks(cfg)
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
            remaining = [x for x in schedule if x.at > deadline]
            ledger.write("session", status="handoff", at=t.isoformat(),
                         deadline=deadline.isoformat(), ticks_deferred=len(remaining))
            return {"status": "handoff", "ran": ran, "missed": missed,
                    "resumed": resumed}

        wait = (tick.at - t).total_seconds()
        if wait > 0:
            sleep(wait)

        try:
            result = _run_tick(cfg, broker, tick.kind)
            ran += 1
            ledger.write("tick", kind=tick.kind, scheduled=tick.at.isoformat(),
                         status={k: v.get("status") for k, v in result.items()})
        except Exception as exc:                      # one bad tick != a lost day
            missed += 1
            ledger.write("tick_error", kind=tick.kind, scheduled=tick.at.isoformat(),
                         error=f"{type(exc).__name__}: {exc}",
                         traceback=traceback.format_exc()[-800:])

        if on_tick_done:
            on_tick_done()

    ledger.write("session", status="complete", ran=ran, missed=missed,
                 already_done=resumed, ended=now().isoformat())
    return {"status": "complete", "ran": ran, "missed": missed, "resumed": resumed}
