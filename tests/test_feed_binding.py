"""The declared tape is an enforced dependency, not an assumption.

Until 2026-09-04 no data call in this repository passed `feed=`. Alpaca's
documented behaviour is to serve "the best available feed based on the user's
subscription", so the data source was whatever each account happened to be
entitled to that day — invisible in the ledger, and free to change under us
without a line of code moving. A strategy registered against the consolidated
tape and run against one exchange is a different strategy with the same name.

These tests hold three things: every production data call carries the declared
feed, the preflight checks that same feed, and an arm that cannot obtain it
does not trade.
"""
import importlib.util

import pytest

from tradebot.preflight import ADVISORY, BLOCKING, run_preflight

# The three request-shape tests read the SDK's request objects, so they need
# alpaca-py present. CI installs requirements.txt and runs them; a laptop
# without the SDK skips them rather than pretending to have checked.
needs_sdk = pytest.mark.skipif(
    importlib.util.find_spec("alpaca") is None,
    reason="alpaca-py not installed; request-shape assertions need the SDK")


class RecordingData:
    """Captures the request objects instead of talking to Alpaca."""

    def __init__(self):
        self.requests = []

    def get_stock_bars(self, req):
        self.requests.append(req)
        import pandas as pd
        return type("R", (), {"df": pd.DataFrame(
            {"symbol": ["SPY"], "timestamp": [pd.Timestamp("2026-09-04 13:35", tz="UTC")],
             "open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0]})})()

    def get_stock_latest_quote(self, req):
        self.requests.append(req)
        q = type("Q", (), {"bid_price": 1.0, "ask_price": 1.01,
                           "timestamp": "t", "bid_exchange": "V",
                           "ask_exchange": "V"})()
        return {"SPY": q}


def _broker(feed="sip"):
    from tradebot.broker import AlpacaBroker
    b = AlpacaBroker.__new__(AlpacaBroker)       # no network, no credentials
    b.key_env, b.secret_env, b.feed = "K", "S", feed
    b._data = RecordingData()
    b.dry_run = False
    return b


@needs_sdk
def test_the_declared_feed_is_on_every_production_data_call():
    b = _broker("sip")
    b.daily_closes(["SPY"], 60)
    b.intraday_5min(["SPY"])
    b.quote_snapshot("SPY")
    assert len(b._data.requests) == 3
    for req in b._data.requests:
        assert getattr(req, "feed", None) is not None, \
            f"{type(req).__name__} went out without a feed"
        assert str(getattr(req, "feed")).lower().endswith("sip")


@needs_sdk
def test_no_declared_feed_means_no_feed_argument():
    """The old behaviour stays reachable for the sweep and for tests, but it
    has to be asked for explicitly now rather than being the default."""
    b = _broker(None)
    b.daily_closes(["SPY"], 60)
    assert getattr(b._data.requests[0], "feed", None) is None


@needs_sdk
def test_the_quote_records_what_it_asked_for_and_where_it_came_from():
    """Bars carry no source field, but a quote's exchange codes do — on the
    free plan both read 'V' for IEX. Recorded so the served source can be
    checked against the requested one instead of taken on faith."""
    snap = _broker("sip").quote_snapshot("SPY")
    assert snap["requested_feed"] == "sip"
    assert snap["bid_exchange"] == "V" and snap["ask_exchange"] == "V"


# ------------------------------------------------------- preflight enforcement

class PlanBroker:
    def __init__(self, served):
        self.served = served

    def daily_closes(self, symbols, days):
        return {s: [1.0] for s in symbols}

    def most_actives(self, n):
        return ["AAPL", "NVDA"][:n]

    def intraday_5min(self, symbols, day=None):
        return {s: [1.0] for s in symbols}

    def quote_snapshot(self, symbol):
        return {"bid": 1.0, "ask": 1.01, "mid": 1.005}

    def data_plan_probe(self, symbol="SPY"):
        if self.served is None:
            raise RuntimeError("probe unavailable")
        return {"effective_feed": self.served}


def _all(served):
    return {a: PlanBroker(served) for a in ("slow", "fast", "movers")}


def test_an_arm_served_the_wrong_tape_does_not_trade(cfg):
    """Everything else about the account works: it authenticates, it answers,
    it returns bars. They are the wrong bars."""
    report = run_preflight(cfg, _all("iex"), notify=lambda r: None)
    assert report.disabled == {"slow", "fast", "movers"}
    bad = [r for r in report.results if r.probe == "declared_feed"]
    assert all(r.severity == BLOCKING and not r.ok for r in bad)
    assert "declared sip, served iex" in bad[0].detail


def test_delayed_sip_is_not_sip(cfg):
    """Fifteen-minute-old consolidated data is a different information set
    from live consolidated data, and the intraday arms are built on the
    difference."""
    assert run_preflight(cfg, _all("sip_delayed"), notify=lambda r: None).disabled == \
        {"slow", "fast", "movers"}


def test_the_declared_tape_lets_every_arm_through(cfg):
    report = run_preflight(cfg, _all("sip"), notify=lambda r: None)
    assert report.disabled == set()
    assert report.ok


def test_an_unanswerable_probe_abstains_rather_than_guesses(cfg):
    """A provenance gap is not evidence that the wrong feed was served.
    Refusing to trade on the strength of a failed probe would be a different
    error from the one this check exists to prevent."""
    report = run_preflight(cfg, _all(None), notify=lambda r: None)
    assert report.disabled == set()
    check = [r for r in report.results if r.probe == "declared_feed"][0]
    assert check.severity == ADVISORY and "not enforced" in check.detail


def test_the_check_can_be_turned_off_only_by_amending_the_registration(cfg):
    """`require_declared_feed: false` is a deliberate amendment, visible in
    config.yaml and in the diff — not something the code decides at runtime."""
    cfg.data.require_declared_feed = False
    report = run_preflight(cfg, _all("iex"), notify=lambda r: None)
    assert report.disabled == set()
    assert not [r for r in report.results if r.probe == "declared_feed"]


def test_config_declares_sip():
    from pathlib import Path
    from tradebot.config import load_config
    cfg = load_config(Path(__file__).resolve().parent.parent)
    assert cfg.data.feed == "sip"
    assert cfg.data.require_declared_feed is True
