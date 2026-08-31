"""Broker-mode fills: the arm sends real orders and the account is the only
record of what it holds. These tests exist because the failure mode of the
old design was silent — two sets of books that drift apart while both look
internally consistent."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from tradebot.broker import BrokerError, assert_distinct_accounts
from tradebot.fastarm import run_fast_once
from tests.test_fastarm import day_bars

ET = ZoneInfo("America/New_York")


class AccountFake:
    """A paper account with $50 in it. Fills at the last price, no slippage —
    deliberately, because that is exactly what Alpaca's paper engine does."""

    def __init__(self, frames, now, start_cash=50.0, reject=False,
                 account_number="PA000001"):
        self._frames, self._now = frames, now
        self._cash = start_cash
        self._pos: dict[str, dict] = {}
        self.reject = reject
        self._acct = account_number
        self.orders: list[dict] = []

    def now_et(self):
        return self._now

    def account_number(self):
        return self._acct

    def intraday_5min(self, symbols):
        return {s: df[df["t"] <= self._now].reset_index(drop=True)
                for s, df in self._frames.items() if s in symbols}

    def _last(self, sym):
        return float(self._frames[sym][self._frames[sym]["t"] <= self._now]["c"].iloc[-1])

    def cash(self):
        return round(self._cash, 4)

    def equity(self):
        return round(self._cash + sum(p["qty"] * self._last(s)
                                      for s, p in self._pos.items()), 4)

    def positions_detail(self):
        return {s: {**p, "market_value": round(p["qty"] * self._last(s), 4)}
                for s, p in self._pos.items()}

    def submit(self, order):
        self.orders.append(order)
        if self.reject:
            return {**order, "status": "rejected"}
        px = self._last(order["symbol"])
        qty = order["notional"] / px
        self._cash -= order["notional"]
        self._pos[order["symbol"]] = {"qty": qty, "entry_px": px}
        return {**order, "status": "filled", "broker_order_id": "x"}

    def close(self, symbol):
        self.orders.append({"symbol": symbol, "side": "sell"})
        pos = self._pos.pop(symbol, None)
        if pos:
            self._cash += pos["qty"] * self._last(symbol)
        return {"symbol": symbol, "side": "sell", "status": "filled",
                "broker_order_id": "y"}


def _mk(patterns, hour, minute, **kw):
    date = datetime.now(ET).date()
    frames = {s: day_bars(date, p, base=100 + 10 * i)
              for i, (s, p) in enumerate(patterns.items())}
    now = datetime.combine(date, datetime.min.time(), ET).replace(
        hour=hour, minute=minute)
    return AccountFake(frames, now, **kw)


@pytest.fixture(autouse=True)
def _broker_mode(cfg):
    cfg.fast.fills_mode = "broker"
    cfg.fast.universe = ["SPY", "QQQ"]


def test_entry_goes_to_the_account_not_a_local_book(cfg):
    acct = _mk({"SPY": "breakout", "QQQ": "inside"}, 10, 5)
    result = run_fast_once(cfg, acct, arm="fast")
    assert result["status"] == "ok"
    assert "SPY" in acct.positions_detail()          # the account holds it
    assert acct.orders and acct.orders[0]["side"] == "buy"
    # local state must not be keeping a second copy of the position
    import json
    st = json.loads(cfg.fast_state_path.read_text())
    assert st["positions"] == {}


def test_cost_is_accrued_separately_and_nets_the_equity(cfg):
    acct = _mk({"SPY": "breakout", "QQQ": "inside"}, 10, 5)
    result = run_fast_once(cfg, acct, arm="fast")
    import json
    st = json.loads(cfg.fast_state_path.read_text())
    # 5 bps of a ~$25 notional, charged once on the way in
    assert st["cost_accrued"] == pytest.approx(25 * 0.0005, rel=0.2)
    assert result["equity"] == pytest.approx(acct.equity() - st["cost_accrued"])
    assert result["equity"] < acct.equity()          # net is always below gross


def test_a_rejected_order_is_recorded_and_not_treated_as_a_position(cfg):
    """A $50 cash account cannot recycle unsettled proceeds all day. If the
    venue says no, that is data — not a crash, and not a phantom holding."""
    acct = _mk({"SPY": "breakout", "QQQ": "inside"}, 10, 5, reject=True)
    result = run_fast_once(cfg, acct, arm="fast")
    assert result["status"] == "ok"
    assert acct.positions_detail() == {}
    import json
    events = [json.loads(l) for l in cfg.fast_ledger_path.read_text().splitlines()]
    assert any(e["type"] == "fast_rejected" for e in events)
    assert not any(e["type"] == "fast_order" for e in events)
    st = json.loads(cfg.fast_state_path.read_text())
    assert st["trades_today"] == 0
    assert st["cost_accrued"] == 0.0                 # nothing filled, nothing charged


def test_eod_flat_closes_through_the_account(cfg):
    acct = _mk({"SPY": "breakout", "QQQ": "inside"}, 10, 5)
    run_fast_once(cfg, acct, arm="fast")
    assert acct.positions_detail()
    acct._now = acct._now.replace(hour=15, minute=35)
    run_fast_once(cfg, acct, arm="fast")
    assert acct.positions_detail() == {}             # flat before the bell


def test_arms_sharing_an_account_is_refused_before_the_open(cfg):
    a = _mk({"SPY": "inside"}, 10, 5, account_number="PA1")
    b = _mk({"SPY": "inside"}, 10, 5, account_number="PA2")
    same = _mk({"SPY": "inside"}, 10, 5, account_number="PA1")
    assert assert_distinct_accounts({"fast": a, "movers": b}) == \
        {"fast": "PA1", "movers": "PA2"}
    with pytest.raises(BrokerError, match="share a paper account"):
        assert_distinct_accounts({"fast": a, "movers": same})


def test_missing_arm_account_degrades_to_simulated_rather_than_stopping(cfg, tmp_path, monkeypatch):
    """A missing or duplicated secret must not silently cancel a trading day.
    The arm drops to the weaker method and the ledger says so."""
    import tradebot.cli as cli
    from tradebot.ledger import Ledger

    class OneAccount:
        def __init__(self, key_env, secret_env):
            if key_env != "ALPACA_API_KEY":
                raise RuntimeError(f"{key_env} not set")
            self.n = "PA-ONLY"

        def account_number(self):
            return self.n

    monkeypatch.setattr("tradebot.broker.AlpacaBroker", OneAccount)
    ledger = Ledger(cfg.ledger_path)
    brokers = cli._build_brokers(cfg, ledger)

    assert cfg.fast.fills_mode == "simulated"
    assert cfg.movers.fills_mode == "simulated"
    assert brokers["fast"] is brokers["slow"]        # data only, no orders
    import json
    events = [json.loads(l) for l in cfg.ledger_path.read_text().splitlines()]
    assert {e["arm"] for e in events if e["type"] == "fills_mode_fallback"} == \
        {"fast", "movers"}


def test_two_arms_on_one_account_is_caught_not_pooled(cfg, monkeypatch):
    """The dangerous case: credentials that work but point at the same book."""
    import tradebot.cli as cli

    class SameAccount:
        def __init__(self, *_):
            pass

        def account_number(self):
            return "PA-SHARED"

    monkeypatch.setattr("tradebot.broker.AlpacaBroker", SameAccount)
    cli._build_brokers(cfg)
    assert cfg.fast.fills_mode == "simulated"        # refused, not pooled
    assert cfg.movers.fills_mode == "simulated"
