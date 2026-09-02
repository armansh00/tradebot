"""Regression tests for the 2026-09-01 commissioning failure, defect 2 of 3.

Seven staggered cron entries exist because GitHub drops scheduled events. On
2026-09-01 all seven fired — and five of them were cancelled before they ran a
single tick, by this repo's own `concurrency: tradebot-session` block. GitHub
does not queue a superseded run in a concurrency group; it cancels it. The
redundancy was defeated by the thing that was supposed to make it safe, and
the one surviving run lost 5h45m of the session.

So: the workflow must not let a scheduler cancel the spares, and duplicate
work must be prevented inside the process instead.
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from tradebot.session import FAST, SLOW, build_schedule, run_session

OPEN = datetime(2026, 9, 1, 13, 30, tzinfo=timezone.utc)
CLOSE = datetime(2026, 9, 1, 20, 0, tzinfo=timezone.utc)
WORKFLOW = Path(__file__).resolve().parent.parent / ".github/workflows/session.yml"


class SessionFake:
    def __init__(self, window=(OPEN, CLOSE)):
        self.window = window

    def session_today(self):
        return self.window

    def daily_closes(self, symbols, days):
        return {s: [1.0] for s in symbols}

    def most_actives(self, n):
        return ["AAPL", "NVDA"][:n]

    def intraday_5min(self, symbols, day=None):
        return {s: [1.0] for s in symbols}

    def quote_snapshot(self, symbol):
        return {"bid": 1.0, "ask": 1.01, "mid": 1.005}


def _clock(start):
    state = {"now": start}
    return (lambda: state["now"],
            lambda secs: state.__setitem__("now", state["now"] + timedelta(seconds=secs)))


@pytest.fixture
def ticks(monkeypatch):
    seen = []

    def fake_tick(cfg, brokers, kind, disabled=frozenset()):
        seen.append(kind)
        return {kind: {"status": "ok"}}

    monkeypatch.setattr("tradebot.session._run_tick", fake_tick)
    return seen


# ------------------------------------------------------------- the workflow

def test_the_workflow_never_lets_the_scheduler_cancel_a_spare():
    """The exact line that cost 2026-09-01 its morning."""
    raw = WORKFLOW.read_text()
    spec = yaml.safe_load(raw)
    assert "concurrency" not in spec, (
        "a concurrency group cancels queued runs; the redundant starts must "
        "be allowed to coexist")
    assert all("concurrency" not in job for job in spec["jobs"].values())


def test_the_redundant_starts_are_still_there():
    spec = yaml.safe_load(WORKFLOW.read_text())
    crons = [e["cron"] for e in spec[True]["schedule"]]
    assert len(crons) >= 5, "the whole point of removing concurrency is spares"
    assert len(set(crons)) == len(crons)
    # Off the hour: top-of-hour is the documented congestion peak.
    assert all(c.split()[0] != "0" for c in crons)


# --------------------------------------------------------- the actual guard

def test_a_tick_another_process_already_ran_is_not_run_again(cfg, ticks):
    """The spare sees the leader's committed record and stands down per tick,
    not per day. This is the mechanism that replaces GitHub's cancellation."""
    schedule = build_schedule(OPEN, CLOSE, cfg)
    leader = [json.dumps({"ts": "x", "type": "tick", "kind": t.kind,
                          "scheduled": t.at.isoformat()}) for t in schedule[:5]]

    now, sleep = _clock(OPEN - timedelta(hours=4))
    result = run_session(cfg, SessionFake(), now=now, sleep=sleep,
                         deadline_minutes=700, peek=lambda: leader)

    assert result["status"] == "complete"
    assert result["resumed"] == 5
    assert result["ran"] == len(schedule) - 5
    assert result["missed"] == 0


def test_the_check_happens_at_execution_time_not_only_at_start_up(cfg, ticks):
    """The leader's record for a mid-day tick appears while the spare is
    already running and asleep. A start-up-only check would run it twice."""
    schedule = build_schedule(OPEN, CLOSE, cfg)
    victim = schedule[6]
    shared: list[str] = []

    now, sleep = _clock(OPEN - timedelta(hours=4))

    def peek():
        # The leader gets to this tick first, moments before the spare wakes.
        if now() >= victim.at - timedelta(seconds=1) and not shared:
            shared.append(json.dumps({"ts": "x", "type": "tick",
                                      "kind": victim.kind,
                                      "scheduled": victim.at.isoformat()}))
        return list(shared)

    result = run_session(cfg, SessionFake(), now=now, sleep=sleep,
                         deadline_minutes=700, peek=peek)

    assert result["resumed"] == 1
    assert result["ran"] == len(schedule) - 1
    assert result["missed"] == 0


def test_an_errored_tick_counts_as_taken(cfg, ticks):
    """A tick that another process attempted and lost is not re-run half an
    hour later. Off-cadence execution contaminates the arm it belongs to; the
    honest record is one failure, not one failure plus one late success."""
    schedule = build_schedule(OPEN, CLOSE, cfg)
    leader = [json.dumps({"ts": "x", "type": "tick_error", "kind": schedule[3].kind,
                          "scheduled": schedule[3].at.isoformat(),
                          "error": "RuntimeError: boom"})]
    now, sleep = _clock(OPEN - timedelta(hours=4))
    result = run_session(cfg, SessionFake(), now=now, sleep=sleep,
                         deadline_minutes=700, peek=lambda: leader)
    assert result["resumed"] == 1
    assert result["ran"] == len(schedule) - 1


def test_a_blind_spare_still_trades(cfg, ticks):
    """If the shared record cannot be read — network, a rejected fetch — the
    process runs its own schedule. Doing a tick twice is survivable; a day
    with no process willing to trade is not. Idempotency downstream
    (client_order_id, one slow run per date) is what makes that safe."""
    def peek():
        raise RuntimeError("fetch failed")

    now, sleep = _clock(OPEN - timedelta(hours=4))
    result = run_session(cfg, SessionFake(), now=now, sleep=sleep,
                         deadline_minutes=700, peek=peek)
    assert result["ran"] == len(build_schedule(OPEN, CLOSE, cfg))


def test_two_overlapping_processes_execute_each_tick_exactly_once(cfg, ticks):
    """Both runs start, both survive, and between them every scheduled tick is
    executed once. That is what the cancelled runs were supposed to give."""
    schedule = build_schedule(OPEN, CLOSE, cfg)
    shared: list[str] = []

    def record():
        # Stand-in for commit_state.sh: what this process did becomes visible
        # to the other one.
        shared[:] = [line for line in cfg.ledger_path.read_text().splitlines()]

    now1, sleep1 = _clock(OPEN - timedelta(hours=4))
    first = run_session(cfg, SessionFake(), now=now1, sleep=sleep1,
                        deadline_minutes=120, on_tick_done=record,
                        peek=lambda: list(shared))
    assert first["status"] == "handoff"

    now2, sleep2 = _clock(OPEN - timedelta(hours=3))
    second = run_session(cfg, SessionFake(), now=now2, sleep=sleep2,
                         deadline_minutes=700, on_tick_done=record,
                         peek=lambda: list(shared))

    assert second["status"] == "complete"
    assert first["ran"] + second["ran"] == len(schedule)
    assert second["missed"] == 0
    executed = [json.loads(line) for line in cfg.ledger_path.read_text().splitlines()]
    scheduled = [e["scheduled"] for e in executed if e["type"] == "tick"]
    assert len(scheduled) == len(set(scheduled)) == len(schedule)
    assert ticks.count(SLOW) == 1
    assert ticks.count(FAST) == len([t for t in schedule if t.kind == FAST])
