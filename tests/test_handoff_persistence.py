"""Regression tests for the 2026-09-01 commissioning failure, defect 3 of 3.

Leg 1 started at 08:35 UTC with a 14:15:49 deadline. The next record in the
repository is a session start at 19:59:44 — 5h45m later, one minute before the
close. A handoff event should sit in that gap. It does not, because the
handoff path wrote its record to a local file and returned without ever
calling the commit hook, so the record was destroyed with the runner.

The gap is worse than the outage. An outage you can see is a data point; a gap
the record does not admit to corrupts every count derived from it.

Order of operations, enforced here: write the event, get it onto disk, get an
acknowledgement that it left this runner, and only then return.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

from tradebot.session import build_schedule, run_session

OPEN = datetime(2026, 9, 1, 13, 30, tzinfo=timezone.utc)
CLOSE = datetime(2026, 9, 1, 20, 0, tzinfo=timezone.utc)


class SessionFake:
    def session_today(self):
        return (OPEN, CLOSE)

    def daily_closes(self, symbols, days):
        return {s: [1.0] for s in symbols}

    def most_actives(self, n):
        return ["AAPL", "NVDA"][:n]

    def intraday_5min(self, symbols, day=None):
        return {s: [1.0] for s in symbols}

    def quote_snapshot(self, symbol):
        return {"bid": 1.0, "ask": 1.01, "mid": 1.005}


class Runner:
    """Stands in for the runner and its filesystem. `published` is what
    survives the process; anything only in the local file dies with it."""

    def __init__(self, path, fail_times=0):
        self.path = path
        self.fail_times = fail_times
        self.published: list[str] = []
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self.calls <= self.fail_times:
            return False                     # push refused
        self.published = self.path.read_text().splitlines()
        return None                          # acknowledged by silence

    def destroy(self):
        """The job is killed. Only what was pushed remains."""
        self.path.write_text("\n".join(self.published) + "\n")


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


def _events(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_the_handoff_record_survives_the_runner(cfg, ticks):
    """The 2026-09-01 failure, reproduced end to end: leg 1 hands off and is
    then destroyed. Leg 2 must be able to see that it happened."""
    runner = Runner(cfg.ledger_path)
    now, sleep = _clock(OPEN - timedelta(minutes=10))
    result = run_session(cfg, SessionFake(), now=now, sleep=sleep,
                         deadline_minutes=120, on_tick_done=runner)

    assert result["status"] == "handoff" and result["persisted"] is True

    runner.destroy()
    handoffs = [e for e in _events(cfg.ledger_path) if e.get("status") == "handoff"]
    assert handoffs, "the handoff died with the runner — the 5h45m hole"
    assert handoffs[0]["ticks_deferred"] > 0
    assert len(handoffs[0]["deferred"]) == handoffs[0]["ticks_deferred"]


def test_persistence_is_attempted_after_the_event_is_written(cfg, ticks):
    """Order matters. A commit that runs before the write publishes a record
    that does not yet contain the handoff."""
    seen_at_commit = []
    path = cfg.ledger_path

    def hook():
        seen_at_commit.append(
            [e.get("status") for e in _events(path) if e["type"] == "session"])

    now, sleep = _clock(OPEN - timedelta(minutes=10))
    run_session(cfg, SessionFake(), now=now, sleep=sleep,
                deadline_minutes=120, on_tick_done=hook)

    assert "handoff" in seen_at_commit[-1]


def test_a_refused_push_is_retried_before_giving_up(cfg, ticks):
    runner = Runner(cfg.ledger_path, fail_times=2)
    now, sleep = _clock(OPEN - timedelta(minutes=10))
    result = run_session(cfg, SessionFake(), now=now, sleep=sleep,
                         deadline_minutes=120, on_tick_done=runner)

    # One call per completed tick, then three at the handoff: fail, fail, ok.
    assert result["status"] == "handoff" and result["persisted"] is True
    runner.destroy()
    assert any(e.get("status") == "handoff" for e in _events(cfg.ledger_path))


def test_an_unconfirmed_handoff_says_so_instead_of_pretending(cfg, ticks):
    """If the record cannot be got out of the runner, the exit status has to
    carry that. A silent 'handoff' would be a promise of a successor that may
    have nothing to resume from."""
    class Deaf:
        def __call__(self):
            return False

    now, sleep = _clock(OPEN - timedelta(minutes=10))
    result = run_session(cfg, SessionFake(), now=now, sleep=sleep,
                         deadline_minutes=120, on_tick_done=Deaf())

    assert result["status"] == "handoff_unconfirmed"
    assert result["persisted"] is False
    assert any(e.get("status") == "handoff_unconfirmed" for e in _events(cfg.ledger_path))


def test_a_raising_hook_does_not_take_the_process_with_it(cfg, ticks):
    def boom():
        raise RuntimeError("git exploded")

    now, sleep = _clock(OPEN - timedelta(minutes=10))
    result = run_session(cfg, SessionFake(), now=now, sleep=sleep,
                         deadline_minutes=120, on_tick_done=boom)
    assert result["status"] == "handoff_unconfirmed"


def test_the_successor_resumes_from_the_persisted_record(cfg, ticks):
    """The whole point of the record: leg 2 reconstructs the day from what
    leg 1 managed to publish, and neither repeats work nor invents losses."""
    runner = Runner(cfg.ledger_path)
    now1, sleep1 = _clock(OPEN - timedelta(minutes=10))
    first = run_session(cfg, SessionFake(), now=now1, sleep=sleep1,
                        deadline_minutes=120, on_tick_done=runner)
    runner.destroy()

    now2, sleep2 = _clock(OPEN + timedelta(minutes=110))
    second = run_session(cfg, SessionFake(), now=now2, sleep=sleep2,
                         deadline_minutes=600, on_tick_done=runner)

    assert second["status"] == "complete"
    assert second["missed"] == 0
    assert second["resumed"] == first["ran"]
    assert first["ran"] + second["ran"] == len(build_schedule(OPEN, CLOSE, cfg))


def test_ledger_writes_reach_the_disk(cfg, monkeypatch):
    """Durability is what makes the ordering meaningful — a buffered line is
    not a record."""
    from tradebot.ledger import Ledger
    synced = []
    real = __import__("os").fsync
    monkeypatch.setattr("tradebot.ledger.os.fsync",
                        lambda fd: (synced.append(fd), real(fd))[1])
    Ledger(cfg.ledger_path).write("session", status="handoff")
    assert synced, "ledger.write returned before the line was on disk"
