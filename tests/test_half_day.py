"""The close comes from the exchange calendar, not from a constant.

`OPEN_T`/`CLOSE_T` were hardcoded 9:30 and 16:00. On a 13:00 half day that
puts the flatten time at 15:30 — two and a half hours after the bell. The arm
never reaches its own flatten branch, goes on believing the session is open
until 16:00, and carries positions overnight, in exact violation of the
invariant it advertises. Thanksgiving Friday, Christmas Eve and July 3rd are
not edge cases; they are three sessions a year, every year.

A list of early-close dates would fix this year and rot. These tests hold the
calendar as the mechanism.
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from tradebot.fastarm import run_fast_once, session_bounds
from tradebot.ledger import Ledger

ET = ZoneInfo("America/New_York")
DAY = datetime(2026, 11, 27)                       # a real half day: 13:00 ET


def _bars(date, base=100.0, last_bar_hour=13):
    t0 = datetime.combine(date, datetime.min.time(), ET).replace(hour=9, minute=30)
    px, rows = base, []
    t = t0
    while t.hour < last_bar_hour or (t.hour == last_bar_hour and t.minute == 0):
        px *= 1.0005 if (t - t0) >= timedelta(minutes=30) else 1.0
        rows.append((t, px * .999, px * 1.001, px * .998, px))
        t += timedelta(minutes=5)
    return pd.DataFrame(rows, columns=["t", "o", "h", "l", "c"])


class HalfDayFake:
    """Reports the real 13:00 close, the way the exchange calendar does."""

    def __init__(self, now, close_hour=13):
        self._now = now
        self.close_hour = close_hour
        self._frames = {"SPY": _bars(now.date(), 100.0, close_hour),
                        "QQQ": _bars(now.date(), 110.0, close_hour)}

    def now_et(self):
        return self._now

    def at(self, hour, minute):
        self._now = self._now.replace(hour=hour, minute=minute)
        return self._now

    def session_today(self):
        d = self._now.date()
        return (datetime.combine(d, datetime.min.time(), ET)
                .replace(hour=9, minute=30).astimezone(timezone.utc),
                datetime.combine(d, datetime.min.time(), ET)
                .replace(hour=self.close_hour).astimezone(timezone.utc))

    def intraday_5min(self, symbols, day=None):
        return {s: df[df["t"] <= self._now].reset_index(drop=True)
                for s, df in self._frames.items() if s in symbols}


class NoCalendar(HalfDayFake):
    """A broker with no calendar at all — the simulated-mode fakes, and any
    future data source that cannot answer."""

    def __getattribute__(self, name):
        if name == "session_today":
            raise AttributeError(name)
        return super().__getattribute__(name)


@pytest.fixture(autouse=True)
def _simulated(cfg):
    cfg.fast.fills_mode = cfg.movers.fills_mode = "simulated"


def _at(hour, minute, cls=HalfDayFake, **kw):
    return cls(datetime.combine(DAY.date(), datetime.min.time(), ET)
               .replace(hour=hour, minute=minute), **kw)


def _positions(cfg):
    import json
    return json.loads(cfg.fast_state_path.read_text())["positions"]


# ----------------------------------------------------------------- the bounds

def test_the_close_comes_from_the_calendar():
    b = _at(11, 0)
    open_dt, close_dt, source = session_bounds(b, b.now_et())
    assert close_dt.hour == 13 and close_dt.minute == 0
    assert open_dt.hour == 9 and open_dt.minute == 30
    assert source == "exchange_calendar"


def test_regular_hours_are_assumed_only_when_no_calendar_is_reachable():
    b = _at(11, 0, cls=NoCalendar)
    _, close_dt, source = session_bounds(b, b.now_et())
    assert close_dt.hour == 16
    assert source == "assumed_regular_hours"


def test_a_broken_calendar_does_not_take_the_arm_down():
    class Angry(HalfDayFake):
        def session_today(self):
            raise RuntimeError("calendar API down")

    b = _at(11, 0)
    b.__class__ = Angry
    _, close_dt, source = session_bounds(b, b.now_et())
    assert close_dt.hour == 16 and source == "assumed_regular_hours"


# ------------------------------------------------------------------ behaviour

def test_the_arm_flattens_before_the_early_bell(cfg):
    """The defect, end to end. Flatten is 30 minutes before the close, so on
    a 13:00 day that is 12:30 — not 15:30."""
    b = _at(11, 0)
    run_fast_once(cfg, b, now=b.now_et())
    assert _positions(cfg), "test setup failed: nothing held going into the close"

    run_fast_once(cfg, b, now=b.at(12, 35))
    assert _positions(cfg) == {}, "positions survived an early close"


def test_the_arm_knows_the_session_is_over_after_the_early_bell(cfg):
    b = _at(13, 5)
    assert run_fast_once(cfg, b, now=b.now_et())["status"] == "market_closed"


def test_no_entries_are_taken_after_the_flatten_time(cfg):
    b = _at(12, 35)
    run_fast_once(cfg, b, now=b.now_et())
    assert _positions(cfg) == {}


def test_the_session_used_is_written_down(cfg):
    """Which clock the arm ran on is part of the record, not an assumption a
    reader has to make."""
    b = _at(11, 0)
    run_fast_once(cfg, b, now=b.now_et())
    rec = Ledger(cfg.fast_ledger_path).last("fast_run")
    assert rec["session_source"] == "exchange_calendar"
    assert rec["session_close"].startswith("2026-11-27T13:00")


def test_a_full_day_is_unchanged(cfg):
    """The calendar has to be right on ordinary days too, or the fix trades
    one silent failure for another."""
    b = _at(11, 0, close_hour=16)
    run_fast_once(cfg, b, now=b.now_et())
    assert _positions(cfg), "a normal session should still take entries"
    assert run_fast_once(cfg, b, now=b.at(15, 35))["status"] == "ok"
    assert _positions(cfg) == {}
