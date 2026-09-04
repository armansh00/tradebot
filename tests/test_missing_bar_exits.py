"""The safety path must not depend on the discovery path.

Every exit in `run_fast_once` used to index `lasts[sym]` — the closes of the
bars fetched for *today's universe*. A held position is not guaranteed to be
in today's universe: the movers arm re-screens every morning, and a bar
response can simply come back short for one symbol. When that happened, the
daily loss stop and the end-of-day flatten raised KeyError before liquidating
anything. A kill switch defeated by a runtime failure is worse than one that
was never written, because the code reads as though the protection is there.

It compounds with the half-day defect: the flatten that should have run at an
early close does not, the position survives to the next session, and by then
the symbol may well have dropped out of the universe.

So exits now work from the broker's held positions and source their own
price.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from tradebot.fastarm import book_px, live_px, run_fast_once
from tradebot.ledger import Ledger

ET = ZoneInfo("America/New_York")


def _bars(date, base=100.0):
    t0 = datetime.combine(date, datetime.min.time(), ET).replace(hour=9, minute=30)
    px, rows = base, []
    for i in range(78):
        px *= 1.0005 if i >= 6 else 1.0
        rows.append((t0 + timedelta(minutes=5 * i), px * .999, px * 1.001,
                     px * .998, px))
    return pd.DataFrame(rows, columns=["t", "o", "h", "l", "c"])


class RotatingFake:
    """A movers-style broker: the universe it screens today need not contain
    what the arm is holding from earlier."""

    def __init__(self, now, drop=(), quotes=True):
        self._now = now
        self.drop = set(drop)
        self.quotes = quotes
        self.quoted = []
        self._frames = {"SPY": _bars(now.date(), 100.0),
                        "QQQ": _bars(now.date(), 110.0)}

    def now_et(self):
        return self._now

    def at(self, hour, minute):
        self._now = self._now.replace(hour=hour, minute=minute)
        return self._now

    def intraday_5min(self, symbols, day=None):
        return {s: df[df["t"] <= self._now].reset_index(drop=True)
                for s, df in self._frames.items()
                if s in symbols and s not in self.drop}

    def quote_snapshot(self, symbol):
        self.quoted.append(symbol)
        if not self.quotes:
            return {"quote": None, "quote_error": "no quote"}
        return {"bid": 99.0, "ask": 99.02, "mid": 99.01}


@pytest.fixture(autouse=True)
def _simulated(cfg):
    cfg.fast.fills_mode = cfg.movers.fills_mode = "simulated"


def _fake(cfg, hour, minute, **kw):
    date = datetime.now(ET).date()
    return RotatingFake(datetime.combine(date, datetime.min.time(), ET)
                        .replace(hour=hour, minute=minute), **kw)


def _positions(cfg):
    import json
    return json.loads(cfg.fast_state_path.read_text())["positions"]


def _events(cfg):
    return Ledger(cfg.fast_ledger_path).read()


def _enter(cfg, b):
    run_fast_once(cfg, b, now=b.now_et())
    assert _positions(cfg), "test setup failed: nothing held"
    return set(_positions(cfg))


# ------------------------------------------------------------ price sourcing

def test_a_bar_is_preferred():
    px, src = live_px("SPY", {"SPY": 101.0}, None)
    assert (px, src) == (101.0, "bar")


def test_a_quote_covers_a_symbol_with_no_bar(cfg):
    b = _fake(cfg, 11, 5)
    px, src = live_px("SPY", {}, b)
    assert (px, src) == (99.01, "quote") and b.quoted == ["SPY"]


def test_a_stale_mark_is_not_a_live_price(cfg):
    """A stop compares price to a level. Fed yesterday's number it fires at
    the wrong one, and wrong is worse than absent."""
    b = _fake(cfg, 11, 5, quotes=False)
    assert live_px("SPY", {}, b) == (None, "unavailable")
    px, src = book_px("SPY", {"last_px": 98.0, "entry_px": 97.0}, {}, b)
    assert (px, src) == (98.0, "last_px")


def test_the_books_always_get_an_answer(cfg):
    b = _fake(cfg, 11, 5, quotes=False)
    assert book_px("SPY", {"entry_px": 97.0}, {}, b) == (97.0, "entry_px")
    assert book_px("SPY", {}, {}, b) == (0.0, "none")


# -------------------------------------------------------------- the defect

def test_eod_flatten_works_when_the_held_symbol_left_the_universe(cfg):
    """The original KeyError, end to end."""
    b = _fake(cfg, 11, 5)
    held = _enter(cfg, b)
    b.drop = set(held)                                # universe rotates away

    result = run_fast_once(cfg, b, now=b.at(15, 35))

    assert result["status"] == "ok"
    assert _positions(cfg) == {}, "positions survived because bars were missing"
    flat = [e for e in _events(cfg)
            if e["type"] == "fast_order" and e.get("reason") == "eod_flat"]
    assert flat and all(e["px_source"] == "quote" for e in flat)


def test_the_daily_loss_stop_works_without_bars(cfg):
    import json
    b = _fake(cfg, 11, 5)
    held = _enter(cfg, b)
    st = json.loads(cfg.fast_state_path.read_text())
    st["day_start_equity"] = 1000.0                   # force the stop
    cfg.fast_state_path.write_text(json.dumps(st))
    b.drop = set(held)

    run_fast_once(cfg, b, now=b.at(12, 5))
    assert _positions(cfg) == {}
    assert [e for e in _events(cfg) if e["type"] == "fast_order"
            and e.get("reason") == "daily_loss_stop"]


def test_liquidation_happens_even_with_no_price_at_all(cfg):
    """In broker mode the exit goes through close_position, which needs no
    price. A missing quote must never be the reason a position stays open."""
    b = _fake(cfg, 11, 5)
    held = _enter(cfg, b)
    b.drop, b.quotes = set(held), False

    run_fast_once(cfg, b, now=b.at(15, 35))
    assert _positions(cfg) == {}
    flat = [e for e in _events(cfg)
            if e["type"] == "fast_order" and e.get("reason") == "eod_flat"]
    assert flat and all(e["px_source"] in ("last_px", "entry_px") for e in flat)


def test_an_unevaluable_stop_is_recorded_not_skipped(cfg):
    """A protective exit that cannot be evaluated has to say so. Silently
    passing over it is how a stop disappears without anyone noticing."""
    b = _fake(cfg, 11, 5)
    held = _enter(cfg, b)
    b.drop, b.quotes = set(held), False

    run_fast_once(cfg, b, now=b.at(12, 5))
    unevaluable = [e for e in _events(cfg) if e["type"] == "fast_stop_unevaluable"]
    assert {e["symbol"] for e in unevaluable} == held
    assert set(_positions(cfg)) == held, "the stop should abstain, not liquidate"


def test_entries_still_require_a_bar(cfg):
    """Only the exits are freed from the universe snapshot. An entry is a
    decision about a symbol we chose to look at, and it needs the opening
    range that only bars provide."""
    b = _fake(cfg, 11, 5, drop=("SPY", "QQQ"))
    run_fast_once(cfg, b, now=b.now_et())
    assert _positions(cfg) == {}
    assert b.quoted == [], "no quote should be pulled for a symbol we do not hold"
