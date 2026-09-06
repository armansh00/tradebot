"""Each arm trades its own account, and the routing lives in configuration.

2026-09-04: the movers arm asked its broker which account it was on and got
back PA34GE36D3Q5 — the slow arm's. `_build_brokers` caught it, degraded that
arm to simulated fills and wrote a line saying so, which kept the day alive.
The arm then took the first intraday trade in the project's history, and that
trade was not a broker fill. Nothing downstream knew the difference, and the
number went into the ledger looking exactly like a real one.

An arm running a different fill model from the one it is registered under is
not that arm. So a collision now stops the arm instead of quietly changing
what it means.
"""
import json

import pytest

from tradebot.preflight import ADVISORY, BLOCKING, account_identity, run_preflight


class Acct:
    """Only what the routing and the probes touch."""

    def __init__(self, number):
        self.number = number

    def account_number(self):
        return self.number

    def daily_closes(self, symbols, days):
        return {s: [1.0] for s in symbols}

    def most_actives(self, n):
        return ["AAPL", "NVDA"][:n]

    def intraday_5min(self, symbols, day=None):
        return {s: [1.0] for s in symbols}

    def quote_snapshot(self, symbol):
        return {"bid": 1.0, "ask": 1.01, "mid": 1.005}


def _brokers(slow="PA-SLOW", fast="PA-FAST", movers="PA-MOVERS"):
    return {"slow": Acct(slow), "fast": Acct(fast), "movers": Acct(movers)}


# ------------------------------------------------------------- the mapping

def test_each_arm_resolves_to_its_own_configured_credentials(cfg):
    """The routing is the `accounts:` block, and nothing else."""
    assert cfg.creds("slow") == ("ALPACA_API_KEY", "ALPACA_SECRET_KEY")
    assert cfg.creds("fast") == ("ALPACA_FAST_KEY", "ALPACA_FAST_SECRET")
    assert cfg.creds("movers") == ("ALPACA_MOVERS_KEY", "ALPACA_MOVERS_SECRET")
    assert len({cfg.creds(a) for a in ("slow", "fast", "movers")}) == 3


def test_build_brokers_asks_for_the_configured_env_pair(cfg, monkeypatch):
    from tradebot import cli
    asked = []

    class Fake:
        def __init__(self, key_env, secret_env, **kw):
            asked.append((key_env, secret_env))
            self.n = f"PA-{key_env}"

        def account_number(self):
            return self.n

    monkeypatch.setattr("tradebot.broker.AlpacaBroker", Fake)
    cli._build_brokers(cfg)
    assert asked == [cfg.creds("slow"), cfg.creds("fast"), cfg.creds("movers")]


def test_no_account_identifier_is_written_into_the_code():
    """Routing belongs in the account mapping. An account number compiled into
    strategy or risk logic is a fact about one Alpaca dashboard pretending to
    be a fact about the strategy."""
    import pathlib
    import re
    src = pathlib.Path(__file__).resolve().parent.parent / "src" / "tradebot"
    pattern = re.compile(r"\bPA[0-9A-Z]{8,}\b")
    for path in src.glob("*.py"):
        assert not pattern.search(path.read_text()), f"account id hard-coded in {path.name}"


# ------------------------------------------------------------ the identity

def test_distinct_accounts_report_no_conflict():
    ident = account_identity(_brokers())
    assert [ident[a]["conflict"] for a in ("slow", "fast", "movers")] == [None] * 3


def test_the_first_claimant_keeps_the_account():
    """Fixed order — slow, fast, movers — so the verdict does not depend on
    dictionary ordering."""
    ident = account_identity(_brokers(movers="PA-SLOW"))
    assert ident["slow"]["conflict"] is None
    assert ident["movers"]["conflict"] == "slow"


def test_the_2026_09_04_collision_disables_only_the_borrowing_arm(cfg):
    report = run_preflight(cfg, _brokers(movers="PA-SLOW"), notify=lambda r: None)
    assert report.disabled == {"movers"}
    bad = [r for r in report.results
           if r.arm == "movers" and r.probe == "account_identity"][0]
    assert bad.severity == BLOCKING and "shares an account with the slow arm" in bad.detail


def test_two_arms_on_the_slow_account_leaves_the_slow_arm_running(cfg):
    report = run_preflight(cfg, _brokers(fast="PA-SLOW", movers="PA-SLOW"),
                           notify=lambda r: None)
    assert report.disabled == {"fast", "movers"}
    assert report.any_enabled is True


def test_a_collision_is_written_to_the_ledger(cfg):
    from tradebot.ledger import Ledger
    run_preflight(cfg, _brokers(movers="PA-SLOW"), ledger=Ledger(cfg.ledger_path),
                  notify=lambda r: None)
    events = [json.loads(l) for l in cfg.ledger_path.read_text().splitlines()]
    fail = [e for e in events if e.get("status") == "DATA_PREFLIGHT_FAIL"]
    assert fail and fail[-1]["disabled"] == ["movers"]


def test_an_unknown_account_abstains_rather_than_blocking(cfg):
    """Same principle as the feed check: a gap in the record is not evidence
    that two arms are sharing a book, and refusing to trade on a failed lookup
    would be a different error from the one this catches."""
    class Mute(Acct):
        def account_number(self):
            raise RuntimeError("account endpoint down")

    brokers = _brokers()
    brokers["fast"] = Mute("PA-FAST")
    report = run_preflight(cfg, brokers, notify=lambda r: None)
    assert report.disabled == set()
    probe = [r for r in report.results
             if r.arm == "fast" and r.probe == "account_identity"][0]
    assert probe.severity == ADVISORY and "not enforced" in probe.detail


# ------------------------------------------------- how the mapping got broken

def test_config_yaml_has_no_duplicate_keys():
    """The failure that caused all of this, and it was silent.

    On 2026-09-04 a comment block was inserted one line too early and pushed
    the movers account entry out of the `accounts:` mapping and up to the top
    level, where a later `movers:` key overwrote it. PyYAML resolves duplicate
    keys by keeping the last one and says nothing, so `accounts` quietly lost
    an arm, `creds("movers")` fell back to the slow arm's environment
    variables, and both arms pointed at the same account. The whole day's
    movers result was a simulated fill wearing a real one's clothes.

    Nothing in the file looked wrong. This is the check that would have said
    so.
    """
    import pathlib
    import yaml

    path = pathlib.Path(__file__).resolve().parent.parent / "config.yaml"

    class Strict(yaml.SafeLoader):
        pass

    def no_duplicates(loader, node, deep=False):
        seen, out = set(), {}
        for k, v in node.value:
            key = loader.construct_object(k, deep=deep)
            assert key not in seen, f"duplicate key in config.yaml: {key!r}"
            seen.add(key)
            out[key] = loader.construct_object(v, deep=deep)
        return out

    Strict.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
                           no_duplicates)
    yaml.load(path.read_text(), Strict)


def test_every_arm_is_present_in_the_accounts_mapping(cfg):
    """`creds()` falls back to a default when an arm is missing, and the
    default points every arm at the slow account. A missing entry therefore
    does not fail — it pools the books."""
    assert set(cfg.accounts) >= {"slow", "fast", "movers"}
    for arm in ("slow", "fast", "movers"):
        assert cfg.accounts[arm]["key_env"].startswith("ALPACA_")
