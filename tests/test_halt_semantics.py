"""`.halt` must stop new risk without abandoning open risk.

Two defects, opposite in shape, both real. Until 2026-09-04 the intraday arms
never looked at the halt file at all: the slow arm stopped and these two kept
trading, which is the worst possible reading of an emergency switch. The
obvious repair — `if halted: return` at the top of run_fast_once — would have
been worse, because the same early return also cancels the stop-loss, the
daily loss stop and the end-of-day flatten. Pulling the switch would freeze
the arm holding whatever it happened to hold, with every protective exit
disabled, which is not what anyone means by "stop".

So: a halted tick liquidates, records what it liquidated, and stands down.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from tradebot.fastarm import run_fast_once
from tradebot.ledger import Ledger

ET = ZoneInfo("America/New_York")


def _bars(date, base=100.0, rip=True):
    t0 = datetime.combine(date, datetime.min.time(), ET).replace(hour=9, minute=30)
    px, rows = base, []
    for i in range(78):
        px *= 1.0005 if (rip and i >= 6) else 1.0
        rows.append((t0 + timedelta(minutes=5 * i), px * .999, px * 1.001,
                     px * .998, px))
    return pd.DataFrame(rows, columns=["t", "o", "h", "l", "c"])


class Fake:
    def __init__(self, now, refuse=False):
        self._now = now
        self.refuse = refuse
        self.closed = []
        date = now.date()
        self._frames = {"SPY": _bars(date, 100.0), "QQQ": _bars(date, 110.0)}

    def now_et(self):
        return self._now

    def at(self, hour, minute):
        self._now = self._now.replace(hour=hour, minute=minute)
        return self._now

    def intraday_5min(self, symbols, day=None):
        return {s: df[df["t"] <= self._now].reset_index(drop=True)
                for s, df in self._frames.items() if s in symbols}


@pytest.fixture(autouse=True)
def _simulated(cfg):
    cfg.fast.fills_mode = cfg.movers.fills_mode = "simulated"


def _fake(cfg, hour, minute):
    date = datetime.now(ET).date()
    return Fake(datetime.combine(date, datetime.min.time(), ET)
                .replace(hour=hour, minute=minute))


def _events(cfg):
    return Ledger(cfg.fast_ledger_path).read()


def _positions(cfg):
    import json
    return json.loads(cfg.fast_state_path.read_text())["positions"]


def _enter(cfg):
    """Get the arm into a position so the halt has something to act on."""
    b = _fake(cfg, 11, 5)
    run_fast_once(cfg, b, now=b.now_et())
    assert _positions(cfg), "test setup failed: no position to halt on"
    return b


def test_a_halt_flattens_what_is_held(cfg):
    b = _enter(cfg)
    held = set(_positions(cfg))
    cfg.halt_path.write_text("manual kill\n")

    result = run_fast_once(cfg, b, now=b.at(11, 35))

    assert result["status"] == "halted" and result["reason"] == "halt_file"
    assert set(result["flattened"]) == held
    assert _positions(cfg) == {}


def test_a_halt_blocks_new_entries(cfg):
    cfg.halt_path.write_text("manual kill\n")
    b = _fake(cfg, 11, 5)
    result = run_fast_once(cfg, b, now=b.now_et())
    assert result["status"] == "halted"
    assert _positions(cfg) == {}
    assert not [e for e in _events(cfg) if e["type"] == "fast_order"
                and e.get("side") == "buy"]


def test_the_halt_is_recorded_not_just_obeyed(cfg):
    b = _enter(cfg)
    cfg.halt_path.write_text("manual kill\n")
    run_fast_once(cfg, b, now=b.at(11, 35))
    rec = [e for e in _events(cfg) if e["type"] == "fast_halted"]
    assert rec and rec[-1]["reason"] == "halt_file"
    assert rec[-1]["flattened"] and rec[-1]["still_held"] == []


def test_a_refused_liquidation_is_retried_on_the_next_halted_tick(cfg):
    """The point of a kill switch is that it keeps being true. A close the
    venue refused must not be assumed done."""
    b = _enter(cfg)
    cfg.halt_path.write_text("manual kill\n")

    import tradebot.fastarm as fa
    real_fill, calls = fa._fill, {"n": 0}

    def refuse_once(*a, **kw):
        calls["n"] += 1
        return False if calls["n"] == 1 else real_fill(*a, **kw)

    fa._fill = refuse_once
    try:
        first = run_fast_once(cfg, b, now=b.at(11, 35))
        assert len(first["still_held"]) == 1
        second = run_fast_once(cfg, b, now=b.at(12, 5))
    finally:
        fa._fill = real_fill

    assert second["flattened"] == first["still_held"], \
        "a refused liquidation was never retried"
    assert _positions(cfg) == {}


def test_clearing_the_file_lets_the_arm_work_again(cfg):
    """The file is the human switch. It lifts when the human lifts it."""
    cfg.halt_path.write_text("manual kill\n")
    b = _fake(cfg, 11, 5)
    assert run_fast_once(cfg, b, now=b.now_et())["status"] == "halted"

    cfg.halt_path.unlink()
    assert run_fast_once(cfg, b, now=b.at(11, 35))["status"] == "ok"


def test_the_drawdown_kill_stays_down_after_the_file_is_gone(cfg):
    """Two halts, deliberately not merged. The arm's own kill is stickier
    than the human one: clearing a file nobody set must not undo it."""
    import json
    b = _enter(cfg)
    st = json.loads(cfg.fast_state_path.read_text())
    st["halted"] = True
    cfg.fast_state_path.write_text(json.dumps(st))

    result = run_fast_once(cfg, b, now=b.at(11, 35))
    assert result["status"] == "halted" and result["reason"] == "drawdown_kill"
    assert not cfg.halt_path.exists()
    assert run_fast_once(cfg, b, now=b.at(12, 5))["status"] == "halted"


def test_the_slow_arms_halt_reaches_the_intraday_arms(cfg):
    """One switch, one file, all three arms. That was the whole complaint."""
    from tradebot.risk import RiskManager
    b = _enter(cfg)
    RiskManager(cfg, Ledger(cfg.ledger_path)).halt("drawdown 11.2%")
    assert run_fast_once(cfg, b, now=b.at(11, 35))["status"] == "halted"
    assert _positions(cfg) == {}
