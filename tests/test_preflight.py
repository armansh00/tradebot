"""Regression tests for the 2026-09-01 commissioning failure.

On that day the fast and movers arms ran on brand-new paper accounts that
authenticated fine, reported equity fine, and would have accepted orders — but
had no entitlement for the market data their strategy reads. Every intraday
tick died on `subscription does not permit querying recent SIP data`, all day,
and nothing in the system complained. These tests exist so that cannot recur
silently, and so that nobody "fixes" it by quietly switching feeds.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

from tradebot.preflight import ADVISORY, BLOCKING, run_preflight
from tradebot.session import build_schedule, run_session

OPEN = datetime(2026, 9, 1, 13, 30, tzinfo=timezone.utc)
CLOSE = datetime(2026, 9, 1, 20, 0, tzinfo=timezone.utc)

DENIED = "subscription does not permit querying recent SIP data"


class DataFake:
    """Records every data call so a test can assert on the SHAPE of the probe,
    not just its verdict. A probe that queries something other than what the
    strategy queries is the exact bug being guarded against."""

    def __init__(self, denied=(), empty=(), quote_error=None):
        self.denied = set(denied)
        self.empty = set(empty)
        self.quote_error = quote_error
        self.calls = []

    def _guard(self, name, *args):
        self.calls.append((name, *args))
        if name in self.denied:
            raise RuntimeError(DENIED)

    def session_today(self):
        return (OPEN, CLOSE)

    def daily_closes(self, symbols, days):
        self._guard("daily_closes", tuple(symbols), days)
        return {} if "daily_closes" in self.empty else {s: [1.0] for s in symbols}

    def most_actives(self, n):
        self._guard("most_actives", n)
        return [] if "most_actives" in self.empty else ["AAPL", "NVDA", "TSLA"][:n]

    def intraday_5min(self, symbols, day=None):
        self._guard("intraday_5min", tuple(symbols), day)
        return {} if "intraday_5min" in self.empty else {s: [1.0] for s in symbols}

    def quote_snapshot(self, symbol):
        self._guard("quote_snapshot", symbol)
        if self.quote_error:
            return {"quote": None, "quote_error": self.quote_error}
        return {"bid": 1.0, "ask": 1.01, "mid": 1.005}


def _brokers(**kw):
    return {"slow": DataFake(**kw.get("slow", {})),
            "fast": DataFake(**kw.get("fast", {})),
            "movers": DataFake(**kw.get("movers", {}))}


def _clock(start):
    state = {"now": start}
    return (lambda: state["now"],
            lambda secs: state.__setitem__("now", state["now"] + timedelta(seconds=secs)))


def _events(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


# ---------------------------------------------------------------- probe level

def test_every_arm_is_probed_through_its_own_account(cfg):
    brokers = _brokers()
    report = run_preflight(cfg, brokers)
    assert report.ok and report.disabled == set()
    assert {r.arm for r in report.results} == {"slow", "fast", "movers"}
    # Each account answered for itself. Sharing one entitled account would have
    # hidden the 2026-09-01 defect completely.
    for arm in ("slow", "fast", "movers"):
        assert brokers[arm].calls, f"{arm} account was never asked for data"


def test_the_intraday_probe_issues_the_same_request_the_strategy_issues(cfg):
    """A probe aimed at a historical day would have passed on 2026-09-01 while
    the live call was refused. It must ask for today's session bars."""
    brokers = _brokers()
    run_preflight(cfg, brokers)
    bars = [c for c in brokers["fast"].calls if c[0] == "intraday_5min"]
    assert len(bars) == 1
    _, symbols, day = bars[0]
    assert day is None, "probe must request the live session, not a safe past day"
    assert set(symbols) <= set(cfg.fast.universe) | set(cfg.universe)


def test_denied_market_data_disables_that_arm_only(cfg):
    brokers = _brokers(fast={"denied": ["intraday_5min"]})
    report = run_preflight(cfg, brokers)
    assert report.disabled == {"fast"}
    assert report.any_enabled is True
    assert not report.ok
    failure = report.failures[0]
    assert failure.arm == "fast" and DENIED in failure.detail


def test_a_dead_screener_entitlement_disables_movers(cfg):
    report = run_preflight(cfg, _brokers(movers={"denied": ["most_actives"]}))
    assert "movers" in report.disabled


def test_no_feed_fallback_is_attempted(cfg):
    """SIP and IEX are different information sets. Degrading to the feed that
    happens to work would keep the arm trading under a pre-registration it no
    longer satisfies. The only permitted response is to stop."""
    brokers = _brokers(fast={"denied": ["intraday_5min"]})
    run_preflight(cfg, brokers)
    bars = [c for c in brokers["fast"].calls if c[0] == "intraday_5min"]
    assert len(bars) == 1, "a refusal must not be retried with different data"


def test_empty_intraday_before_the_open_is_not_a_failure(cfg):
    """Preflight runs hours before the bell, when today has no bars yet.
    It tests permission and reachability, not data presence."""
    report = run_preflight(cfg, _brokers(fast={"empty": ["intraday_5min"]}))
    assert "fast" not in report.disabled


def test_empty_history_is_a_failure(cfg):
    """Daily closes are never legitimately empty — an empty answer there is an
    entitlement problem in disguise."""
    report = run_preflight(cfg, _brokers(slow={"empty": ["daily_closes"]}))
    assert "slow" in report.disabled


def test_a_missing_quote_is_advisory_not_disqualifying(cfg):
    """Quotes are recorded for execution-cost measurement and feed no
    pre-registered metric. Losing them costs a measurement, not a decision."""
    report = run_preflight(cfg, _brokers(fast={"quote_error": "no quote"}))
    assert "fast" not in report.disabled
    quote = [r for r in report.results if r.arm == "fast" and r.probe == "quote_snapshot"]
    assert quote[0].ok is False and quote[0].severity == ADVISORY


def test_a_missing_broker_disables_the_arm(cfg):
    brokers = _brokers()
    brokers["movers"] = None
    assert "movers" in run_preflight(cfg, brokers).disabled


def test_failure_is_written_to_the_ledger_and_announced(cfg):
    from tradebot.ledger import Ledger
    seen = []
    report = run_preflight(cfg, _brokers(fast={"denied": ["intraday_5min"]}),
                           ledger=Ledger(cfg.ledger_path), notify=seen.append)
    events = _events(cfg.ledger_path)
    assert any(e.get("status") == "DATA_PREFLIGHT_FAIL" and e["disabled"] == ["fast"]
               for e in events)
    assert [r for r in events if r["type"] == "preflight" and r.get("probe")]
    assert seen == [report]
    assert "DATA_PREFLIGHT_FAIL" in report.summary()


def test_a_healthy_preflight_stays_quiet(cfg):
    from tradebot.ledger import Ledger
    seen = []
    run_preflight(cfg, _brokers(), ledger=Ledger(cfg.ledger_path), notify=seen.append)
    assert seen == []
    assert all(e.get("status") != "DATA_PREFLIGHT_FAIL" for e in _events(cfg.ledger_path))


# -------------------------------------------------------------- session level

@pytest.fixture
def ticks(monkeypatch):
    seen = []

    def fake_tick(cfg, brokers, kind, disabled=frozenset()):
        seen.append((kind, tuple(sorted(disabled))))
        return {kind: {"status": "ok"}}

    monkeypatch.setattr("tradebot.session._run_tick", fake_tick)
    return seen


def test_a_disabled_arm_does_not_trade_but_the_others_do(cfg, ticks):
    """2026-09-01 in miniature: the slow account was fine, the two intraday
    accounts were not. The day should have run one arm, not three broken ones."""
    now, sleep = _clock(OPEN - timedelta(hours=4))
    brokers = _brokers(fast={"denied": ["intraday_5min"]},
                       movers={"denied": ["intraday_5min"]})
    result = run_session(cfg, brokers, now=now, sleep=sleep, deadline_minutes=700)

    assert result["status"] == "complete"
    assert result["disabled"] == ["fast", "movers"]
    assert all(d == ("fast", "movers") for _, d in ticks)
    assert any(kind == "slow" for kind, _ in ticks)


def test_all_arms_denied_ends_the_session_before_any_trading(cfg, ticks):
    now, sleep = _clock(OPEN - timedelta(hours=4))
    brokers = _brokers(slow={"denied": ["daily_closes"]},
                       fast={"denied": ["intraday_5min"]},
                       movers={"denied": ["intraday_5min"]})
    result = run_session(cfg, brokers, now=now, sleep=sleep, deadline_minutes=700)

    assert result["status"] == "preflight_fail"
    assert result["ran"] == 0
    assert ticks == []
    assert any(e.get("status") == "preflight_fail" for e in _events(cfg.ledger_path))


def test_preflight_runs_before_the_first_tick(cfg, ticks):
    """Order matters: probing after the open would let a broken arm place its
    first orders before anyone objected."""
    now, sleep = _clock(OPEN - timedelta(hours=4))
    brokers = _brokers()
    run_session(cfg, brokers, now=now, sleep=sleep, deadline_minutes=700)
    events = _events(cfg.ledger_path)
    kinds = [e["type"] for e in events]
    assert kinds.index("preflight") < (kinds.index("tick") if "tick" in kinds
                                       else len(kinds))
    assert len(ticks) == len(build_schedule(OPEN, CLOSE, cfg))
