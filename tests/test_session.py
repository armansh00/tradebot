"""The in-process scheduler. These tests exist because the failure they guard
against is silent: a session that runs three ticks instead of twelve still
exits 0, still commits, and still looks healthy on the Actions page."""
from datetime import datetime, timedelta, timezone

import pytest

from tradebot.session import FAST, SLOW, build_schedule, run_session

OPEN = datetime(2026, 9, 1, 13, 30, tzinfo=timezone.utc)   # 09:30 ET
CLOSE = datetime(2026, 9, 1, 20, 0, tzinfo=timezone.utc)   # 16:00 ET


class SessionFake:
    """Only what run_session touches."""
    def __init__(self, window=(OPEN, CLOSE), fail_on=()):
        self.window = window
        self.fail_on = set(fail_on)
        self.calls = []

    def session_today(self):
        return self.window


@pytest.fixture
def patched(monkeypatch):
    calls = []

    def fake_tick(cfg, broker, kind):
        calls.append(kind)
        if kind in getattr(broker, "fail_on", ()):
            raise RuntimeError("alpaca timeout")
        return {kind: {"status": "ok"}}

    monkeypatch.setattr("tradebot.session._run_tick", fake_tick)
    return calls


def _clock(start):
    """Virtual time: sleeping advances the clock instead of blocking."""
    state = {"now": start}
    return (lambda: state["now"],
            lambda secs: state.__setitem__("now", state["now"] + timedelta(seconds=secs)),
            state)


def test_schedule_covers_the_whole_session(cfg):
    ticks = build_schedule(OPEN, CLOSE, cfg)
    assert ticks[0].kind == SLOW
    assert ticks[0].at == OPEN + timedelta(minutes=2)

    fast = [t for t in ticks if t.kind == FAST]
    # First fast tick only after the 30-minute opening range has closed.
    assert fast[0].at == OPEN + timedelta(minutes=35)
    # Last one before the bell, so the flatten logic always gets a turn.
    assert fast[-1].at == CLOSE - timedelta(minutes=2)
    assert all(OPEN < t.at < CLOSE for t in ticks)
    # 09:30-16:00 on a 30-minute cadence: 10:05 ... 15:35, plus 15:58.
    assert len(fast) == 13


def test_half_day_schedule_still_ends_before_the_early_bell(cfg):
    early = OPEN + timedelta(hours=3, minutes=30)          # 13:00 ET close
    ticks = build_schedule(OPEN, early, cfg)
    assert all(t.at < early for t in ticks)
    assert ticks[-1].at == early - timedelta(minutes=2)


def test_full_day_runs_every_tick(cfg, patched):
    # Four hours early, as the cron is deliberately set: the process sleeps
    # through the wait and still catches the open.
    now, sleep, _ = _clock(OPEN - timedelta(hours=4))
    broker = SessionFake()
    result = run_session(cfg, broker, now=now, sleep=sleep, deadline_minutes=700)
    assert result["status"] == "complete"
    assert result["missed"] == 0
    assert result["ran"] == len(build_schedule(OPEN, CLOSE, cfg))
    assert patched.count(SLOW) == 1


def test_late_start_records_every_missed_tick(cfg, patched):
    """The thing that actually went wrong on 2026-08-31. A late process must
    not quietly back-fill ticks at the wrong time — it must say what it lost."""
    now, sleep, _ = _clock(OPEN + timedelta(hours=3))
    result = run_session(cfg, SessionFake(), now=now, sleep=sleep, deadline_minutes=600)
    assert result["missed"] > 0
    assert result["ran"] > 0
    assert SLOW not in patched                      # 09:32 is long gone
    events = [__import__("json").loads(l)
              for l in cfg.ledger_path.read_text().splitlines()]
    missed = [e for e in events if e["type"] == "tick_missed"]
    assert len(missed) == result["missed"]
    assert all(e["late_minutes"] > 0 for e in missed)
    assert any(e["type"] == "session" and e["late_minutes"] == 180.0 for e in events)


def test_deadline_hands_off_instead_of_dying_mid_day(cfg, patched):
    now, sleep, _ = _clock(OPEN - timedelta(minutes=10))
    result = run_session(cfg, SessionFake(), now=now, sleep=sleep, deadline_minutes=120)
    assert result["status"] == "handoff"
    assert 0 < result["ran"] < len(build_schedule(OPEN, CLOSE, cfg))
    events = [__import__("json").loads(l)
              for l in cfg.ledger_path.read_text().splitlines()]
    handoff = [e for e in events if e.get("status") == "handoff"][0]
    assert handoff["ticks_deferred"] > 0


def test_a_failing_tick_does_not_end_the_day(cfg, patched):
    now, sleep, _ = _clock(OPEN - timedelta(minutes=10))
    broker = SessionFake(fail_on={FAST})
    result = run_session(cfg, broker, now=now, sleep=sleep, deadline_minutes=600)
    assert result["status"] == "complete"
    assert result["ran"] == 1                       # only the slow tick succeeded
    assert result["missed"] == 13
    events = [__import__("json").loads(l)
              for l in cfg.ledger_path.read_text().splitlines()]
    assert all("alpaca timeout" in e["error"]
               for e in events if e["type"] == "tick_error")


def test_closed_day_and_after_hours_start_are_no_ops(cfg, patched):
    now, sleep, _ = _clock(OPEN)
    closed = SessionFake(window=None)
    closed.session_today = lambda: None
    assert run_session(cfg, closed, now=now, sleep=sleep)["status"] == "market_closed_today"

    now2, sleep2, _ = _clock(CLOSE + timedelta(minutes=5))
    assert run_session(cfg, SessionFake(), now=now2, sleep=sleep2)["status"] == \
        "started_after_close"
    assert patched == []


def test_commit_hook_fires_after_every_tick(cfg, patched):
    now, sleep, _ = _clock(OPEN - timedelta(minutes=10))
    hits = []
    result = run_session(cfg, SessionFake(), now=now, sleep=sleep,
                         deadline_minutes=600, on_tick_done=lambda: hits.append(1))
    assert len(hits) == result["ran"]


def test_leg_two_resumes_a_handoff_without_inventing_missed_ticks(cfg, patched):
    """Two chained jobs must produce the same record as one uninterrupted run."""
    now1, sleep1, _ = _clock(OPEN - timedelta(minutes=10))
    first = run_session(cfg, SessionFake(), now=now1, sleep=sleep1,
                        deadline_minutes=120)
    assert first["status"] == "handoff"

    # Leg 2 starts where leg 1 stopped, reading the same ledger.
    now2, sleep2, _ = _clock(OPEN + timedelta(minutes=110))
    second = run_session(cfg, SessionFake(), now=now2, sleep=sleep2,
                         deadline_minutes=600)

    assert second["status"] == "complete"
    assert second["missed"] == 0                     # nothing was actually lost
    assert second["resumed"] == first["ran"]
    assert first["ran"] + second["ran"] == len(build_schedule(OPEN, CLOSE, cfg))


@pytest.mark.parametrize("shape", ["time", "naive_datetime", "aware_datetime"])
def test_calendar_open_close_coerces_to_utc_whatever_the_sdk_returns(shape):
    """alpaca-py has returned `time` in some versions and `datetime` in others.
    Guessing wrong does not fail loudly — it moves the whole trading day."""
    from datetime import date, time as dtime
    from tradebot.session import ET, to_session_utc

    day = date(2026, 9, 1)
    value = {
        "time": dtime(9, 30),
        "naive_datetime": datetime(2026, 9, 1, 9, 30),
        "aware_datetime": datetime(2026, 9, 1, 9, 30, tzinfo=ET),
    }[shape]
    assert to_session_utc(day, value) == datetime(2026, 9, 1, 13, 30, tzinfo=timezone.utc)


def test_half_day_close_survives_the_same_coercion():
    from datetime import date, time as dtime
    from tradebot.session import to_session_utc
    assert to_session_utc(date(2026, 11, 27), dtime(13, 0)) == \
        datetime(2026, 11, 27, 18, 0, tzinfo=timezone.utc)   # EST, not EDT
