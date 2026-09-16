"""A tick is claimed and published before it is executed.

Several sessions run each trading day on purpose. The broker's client_order_id
already refused the second copy of every duplicate order — the
`duplicate_suppressed` events prove it. What it could not protect was the
record: each process appended to its own copy of the ledger, and when the
loser rebased onto the winner the appended lines conflicted and one side's
records were dropped. For a week real fills vanished from the ledger while
the broker quietly held the positions. AAPL was sold at the close on
2026-09-10 having never, according to the record, been bought.

Two changes. `.gitattributes` merges the append-only ledgers by union, so no
line is ever lost to a rebase. And a `tick_claim` is written and pushed before
the tick runs — a push is atomic, so whoever lands the claim owns the tick and
the other process sees it and stands down.
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tradebot.session import FAST, SLOW, _ticks_in, build_schedule, run_session

OPEN = datetime(2026, 9, 16, 13, 30, tzinfo=timezone.utc)
CLOSE = datetime(2026, 9, 16, 20, 0, tzinfo=timezone.utc)


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


def _events(cfg):
    return [json.loads(l) for l in cfg.ledger_path.read_text().splitlines()]


# --------------------------------------------------------------- the lock

def test_a_claim_is_written_and_published_before_the_tick_runs(cfg, ticks, monkeypatch):
    monkeypatch.setenv("GITHUB_RUN_ID", "run-A")
    order = []

    def hook():
        events = _events(cfg)
        order.append((events[-1]["type"], len(ticks)))

    now, sleep = _clock(OPEN - timedelta(minutes=10))
    run_session(cfg, SessionFake(), now=now, sleep=sleep,
                deadline_minutes=600, on_tick_done=hook)
    # Every tick: the hook sees a claim with N ticks executed, then a result
    # with N+1 executed. The claim always precedes the work.
    claims = [o for o in order if o[0] == "tick_claim"]
    results = [o for o in order if o[0] == "tick"]
    assert len(claims) == len(results) == len(build_schedule(OPEN, CLOSE, cfg))
    for (c, n_at_claim), (r, n_at_result) in zip(claims, results):
        assert n_at_result == n_at_claim + 1


def test_another_runs_claim_counts_as_taken_and_own_claim_does_not():
    lines = [json.dumps({"type": "tick_claim", "scheduled": "T1", "run": "run-B"}),
             json.dumps({"type": "tick_claim", "scheduled": "T2", "run": "run-A"}),
             json.dumps({"type": "tick", "scheduled": "T3", "run": "run-B"})]
    assert _ticks_in(lines, own_run="run-A") == {"T1", "T3"}
    assert _ticks_in(lines, own_run="run-B") == {"T2", "T3"}


def test_two_processes_waking_at_the_same_tick_execute_it_once(cfg, ticks, monkeypatch):
    """The 2026-09-08..15 failure in miniature. Process A claims and pushes;
    process B's push is rejected, it re-reads, sees A's claim, and stands
    down with a `tick_claim_lost` rather than doing the work again."""
    schedule = build_schedule(OPEN, CLOSE, cfg)
    victim = schedule[3]
    shared: list[str] = []          # what origin/main holds

    # --- process A runs the whole day and publishes everything.
    monkeypatch.setenv("GITHUB_RUN_ID", "run-A")

    def publish_a():
        shared[:] = cfg.ledger_path.read_text().splitlines()

    now_a, sleep_a = _clock(OPEN - timedelta(minutes=10))
    a = run_session(cfg, SessionFake(), now=now_a, sleep=sleep_a,
                    deadline_minutes=700, on_tick_done=publish_a,
                    peek=lambda: list(shared))
    assert a["ran"] == len(schedule)
    a_ticks = len(ticks)

    # --- process B: a fresh checkout that sees origin (A's record) only via
    # peek, wakes exactly at `victim`, and cannot publish (A got there first).
    monkeypatch.setenv("GITHUB_RUN_ID", "run-B")
    cfg.ledger_path.unlink()

    def publish_b():
        raise RuntimeError("rejected: origin has moved")

    # A's record before `victim` executed — as B would have seen it.
    shared[:] = [l for l in shared
                 if json.loads(l).get("scheduled", "") < victim.at.isoformat()
                 or json.loads(l)["type"] in ("session", "preflight", "data_plan")]
    # ...but by the time B's claim push fails, A's claim for `victim` is there.
    a_claim = json.dumps({"ts": "x", "type": "tick_claim", "kind": victim.kind,
                          "scheduled": victim.at.isoformat(), "run": "run-A"})

    peeks = {"n": 0}

    def peek_b():
        # B's first look at origin is a moment before A's claim lands; every
        # look after that sees it. That is the race, and the claim push is
        # what settles it.
        peeks["n"] += 1
        return list(shared) + ([a_claim] if peeks["n"] > 2 else [])

    now_b, sleep_b = _clock(victim.at)
    b = run_session(cfg, SessionFake(), now=now_b, sleep=sleep_b,
                    deadline_minutes=700, on_tick_done=publish_b, peek=peek_b)

    lost = [e for e in _events(cfg) if e["type"] == "tick_claim_lost"]
    assert [e["scheduled"] for e in lost] == [victim.at.isoformat()]
    assert len(ticks) == a_ticks + b["ran"]
    assert all(e["scheduled"] != victim.at.isoformat()
               for e in _events(cfg) if e["type"] == "tick")


def test_a_process_that_cannot_publish_and_sees_no_claim_still_trades(cfg, ticks, monkeypatch):
    """Blind spare rule, unchanged: a day with no process willing to trade is
    worse than a duplicate the broker will refuse and a line union-merge
    will keep."""
    monkeypatch.setenv("GITHUB_RUN_ID", "run-solo")

    def cannot_publish():
        raise RuntimeError("network down")

    now, sleep = _clock(OPEN - timedelta(minutes=10))
    r = run_session(cfg, SessionFake(), now=now, sleep=sleep,
                    deadline_minutes=700, on_tick_done=cannot_publish)
    assert r["ran"] == len(build_schedule(OPEN, CLOSE, cfg))
    assert not [e for e in _events(cfg) if e["type"] == "tick_claim_lost"]


# ------------------------------------------------------ per-arm isolation

def test_one_arms_failure_is_that_arms_failure(cfg, monkeypatch):
    """Thirteen tick errors in a week were `asset "TNON" is not fractionable`
    raised inside movers — after fast had already run and traded. The record
    said the tick failed. The truth was movers failed and fast was fine."""
    from tradebot import session as S

    def fake_fast(cfg, broker, arm="fast", now=None):
        if arm == "movers":
            raise RuntimeError('APIError: asset "TNON" is not fractionable')
        return {"status": "ok", "equity": 50.0}

    monkeypatch.setattr("tradebot.fastarm.run_fast_once", fake_fast)
    out = S._run_tick(cfg, SessionFake(), FAST)
    assert out["fast"]["status"] == "ok"
    assert out["movers"]["status"] == "error"
    assert "not fractionable" in out["movers"]["error"]


def test_the_tick_record_carries_the_arm_error(cfg, monkeypatch):
    from tradebot import session as S

    def fake_fast(cfg, broker, arm="fast", now=None):
        if arm == "movers":
            raise RuntimeError("boom")
        return {"status": "ok"}

    monkeypatch.setattr("tradebot.fastarm.run_fast_once", fake_fast)
    monkeypatch.setattr("tradebot.run.run_once", lambda cfg, b: {"status": "ok"})
    now, sleep = _clock(OPEN - timedelta(minutes=10))
    r = run_session(cfg, SessionFake(), now=now, sleep=sleep, deadline_minutes=700)
    assert r["missed"] == 0                          # no whole-tick errors
    fast_ticks = [e for e in _events(cfg) if e["type"] == "tick" and e["kind"] == FAST]
    assert fast_ticks and all(e["status"] == {"fast": "ok", "movers": "error"}
                              for e in fast_ticks)
    assert all("boom" in e["errors"]["movers"] for e in fast_ticks)


# ----------------------------------------------------------- the merge rule

def test_append_only_ledgers_merge_by_union_and_the_chain_does_not():
    attrs = (Path(__file__).resolve().parent.parent / ".gitattributes").read_text()
    for name in ("ledger.jsonl", "ledger_fast.jsonl", "ledger_movers.jsonl"):
        assert any(line.split()[:2] == [name, "merge=union"]
                   for line in attrs.splitlines() if line.strip() and not line.startswith("#"))
    assert any(line.split()[:2] == ["research_log.jsonl", "-merge"]
               for line in attrs.splitlines() if line.strip() and not line.startswith("#"))
