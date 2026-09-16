"""Ask the venue what it will trade before deciding, not after.

First live week, 2026-09-08..15: thirteen of fifteen tick errors were

    APIError: asset "TNON" is not fractionable

and its cousins FTFT and VEEA. The most-actives screener ranks by activity
and knows nothing about what Alpaca will let a $50 book do; the arm sized in
dollars, the quantity came out fractional, the venue refused, and the tick
died — after the decision had been made and after the fast arm had already
traded. Two fixes, two layers: the universe is filtered by what the venue
says it will trade, and a refusal that does get through is recorded as a
rejection rather than raised as an error.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from tradebot.fastarm import run_fast_once
from tradebot.ledger import Ledger

ET = ZoneInfo("America/New_York")


def _bars(day, base):
    t0 = datetime.combine(day, datetime.min.time(), ET).replace(hour=9, minute=30)
    px, rows = base, []
    for i in range(78):
        px *= 1.0005 if i >= 6 else 1.0
        rows.append((t0 + timedelta(minutes=5 * i), px * .999, px * 1.001, px * .998, px))
    return pd.DataFrame(rows, columns=["t", "o", "h", "l", "c"])


class Screener:
    def __init__(self, now, picks, info):
        self._now, self.picks, self.info = now, picks, info
        self._frames = {s: _bars(now.date(), 10.0 + i) for i, s in enumerate(picks)}
        self.asked = []

    def now_et(self):
        return self._now

    def most_actives(self, n):
        return self.picks[:n]

    def tradable(self, symbols):
        self.asked.append(list(symbols))
        return {s: self.info.get(s, {"tradable": True, "fractionable": True}) for s in symbols}

    def intraday_5min(self, symbols, day=None):
        return {s: df[df["t"] <= self._now].reset_index(drop=True)
                for s, df in self._frames.items() if s in symbols}


@pytest.fixture(autouse=True)
def _simulated(cfg):
    cfg.fast.fills_mode = cfg.movers.fills_mode = "simulated"


def _screener(cfg, picks, info):
    now = datetime.combine(datetime.now(ET).date(), datetime.min.time(), ET).replace(hour=11, minute=5)
    return Screener(now, picks, info)


def test_non_fractionable_names_never_enter_the_universe(cfg):
    b = _screener(cfg, ["NOK", "TNON", "INTC", "FTFT"],
                  {"TNON": {"tradable": True, "fractionable": False},
                   "FTFT": {"tradable": True, "fractionable": False}})
    run_fast_once(cfg, b, now=b.now_et(), arm="movers")
    uni = Ledger(cfg.movers_ledger_path).last("universe")
    assert uni["symbols"] == ["NOK", "INTC"]
    assert uni["screened"] == ["NOK", "TNON", "INTC", "FTFT"]
    assert uni["excluded"] == {"TNON": "not_fractionable", "FTFT": "not_fractionable"}
    assert b.asked == [["NOK", "TNON", "INTC", "FTFT"]]


def test_untradable_names_are_excluded_and_labeled_separately(cfg):
    b = _screener(cfg, ["NOK", "HALT"], {"HALT": {"tradable": False, "fractionable": False}})
    run_fast_once(cfg, b, now=b.now_et(), arm="movers")
    assert Ledger(cfg.movers_ledger_path).last("universe")["excluded"] == {"HALT": "not_tradable"}


def test_a_broker_without_the_lookup_is_not_broken(cfg):
    class Plain(Screener):
        tradable = property(lambda self: (_ for _ in ()).throw(AttributeError))

    b = Plain(_screener(cfg, ["NOK"], {}).now_et(), ["NOK"], {})
    r = run_fast_once(cfg, b, now=b.now_et(), arm="movers")
    assert r["status"] == "ok"
    assert Ledger(cfg.movers_ledger_path).last("universe")["excluded"] is None


def test_the_static_fast_universe_is_not_screened(cfg):
    b = _screener(cfg, ["SPY"], {})
    b._frames = {s: _bars(b.now_et().date(), 100.0 + i) for i, s in enumerate(cfg.fast.universe)}
    run_fast_once(cfg, b, now=b.now_et(), arm="fast")
    assert b.asked == []


def test_a_venue_refusal_at_submit_is_a_rejection_not_an_exception():
    from tradebot.broker import AlpacaBroker

    class Trading:
        def submit_order(self, req):
            raise RuntimeError('{"code":40310000,"message":"asset \\"TNON\\" is not fractionable"}')

    b = AlpacaBroker.__new__(AlpacaBroker)
    b.dry_run, b._trading = False, Trading()
    r = b.submit({"symbol": "TNON", "side": "buy", "notional": 25.0, "client_order_id": "x"})
    assert r["status"] == "rejected_by_venue" and "not fractionable" in r["detail"]


def test_other_submit_errors_still_raise():
    from tradebot.broker import AlpacaBroker

    class Trading:
        def submit_order(self, req):
            raise RuntimeError("backend request timeout")

    b = AlpacaBroker.__new__(AlpacaBroker)
    b.dry_run, b._trading = False, Trading()
    with pytest.raises(RuntimeError, match="timeout"):
        b.submit({"symbol": "SPY", "side": "buy", "notional": 25.0})
