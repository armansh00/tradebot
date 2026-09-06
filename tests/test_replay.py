"""The replay runs past sessions through the production code, not a copy of it.

These tests hold that property and the two honesties the module insists on:
the vault boundary stays visible in the output, and the movers universe is
labeled as the proxy it is.
"""
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from tradebot.replay import (DayShim, SessionResult, _period, movers_proxy,
                             replay, replay_session, summarize, tick_times)

ET = ZoneInfo("America/New_York")


def _bars(day, base=100.0, rip=True, close_hour=16):
    t0 = datetime.combine(day, datetime.min.time(), ET).replace(hour=9, minute=30)
    end = datetime.combine(day, datetime.min.time(), ET).replace(hour=close_hour)
    px, rows, t = base, [], t0
    while t < end:
        px *= 1.0005 if (rip and (t - t0) >= timedelta(minutes=30)) else 1.0
        rows.append((t, px * .999, px * 1.001, px * .998, px))
        t += timedelta(minutes=5)
    return pd.DataFrame(rows, columns=["t", "o", "h", "l", "c"])


def _shim(day=date(2026, 3, 10), close_hour=16, universe=("SPY", "QQQ")):
    open_et = datetime.combine(day, datetime.min.time(), ET).replace(hour=9, minute=30)
    close_et = datetime.combine(day, datetime.min.time(), ET).replace(hour=close_hour)
    bars = {s: _bars(day, 100.0 + 10 * i) for i, s in enumerate(universe)}
    return DayShim(day, bars, open_et, close_et, list(universe))


@pytest.fixture(autouse=True)
def _simulated(cfg):
    cfg.fast.fills_mode = cfg.movers.fills_mode = "simulated"


# ---------------------------------------------------------------- the shim

def test_the_shim_never_shows_a_bar_from_the_future():
    s = _shim()
    s.at(s._open + timedelta(minutes=65))
    bars = s.intraday_5min(["SPY"])
    assert bars["SPY"]["t"].max() <= s.now_et()
    assert len(bars["SPY"]) == 14                     # 09:30 .. 10:35 inclusive


def test_the_shim_reports_the_real_close():
    s = _shim(close_hour=13)
    _, close_utc = s.session_today()
    assert close_utc.astimezone(ET).hour == 13


def test_tick_cadence_matches_the_live_scheduler(cfg):
    s = _shim()
    ticks = tick_times(s._open, s._close, cfg.fast)
    assert ticks[0] == s._open + timedelta(minutes=35)
    assert ticks[-1] == s._close - timedelta(minutes=2)
    assert len(ticks) == 13


# ------------------------------------------------------------- one session

def test_a_session_runs_through_run_fast_once_and_trades(cfg):
    r = replay_session(cfg, "fast", _shim(), "vault")
    assert isinstance(r, SessionResult)
    assert r.ticks == 13 and r.trades >= 1
    assert r.equity_end != r.equity_start
    assert r.session_close == "16:00"


def test_a_half_day_flattens_before_the_early_bell(cfg):
    """The replay inherits the calendar-aware flatten because it is the same
    function. Nothing is held at the end of a 13:00 session."""
    import json
    r = replay_session(cfg, "fast", _shim(close_hour=13), "pre_vault")
    assert r.session_close == "13:00"
    st = json.loads(cfg.fast_state_path.read_text())
    assert st["positions"] == {}


def test_state_carries_across_sessions_and_a_halt_is_released(cfg):
    import json
    day1 = _shim(date(2026, 3, 10))
    replay_session(cfg, "fast", day1, "vault")
    st = json.loads(cfg.fast_state_path.read_text())
    st["halted"] = True                              # as if the kill fired
    cfg.fast_state_path.write_text(json.dumps(st))

    r = replay_session(cfg, "fast", _shim(date(2026, 3, 11)), "vault")
    st = json.loads(cfg.fast_state_path.read_text())
    assert st["halted"] is False, "a replay that stops in week two yields nothing"
    assert r.ticks == 13


# --------------------------------------------------------------- universe

def test_the_movers_proxy_ranks_by_prior_day_dollar_volume():
    d = date(2026, 3, 10)
    daily = {
        "A": pd.DataFrame({"d": [d - timedelta(days=1)], "c": [10.0], "v": [1_000_000]}),
        "B": pd.DataFrame({"d": [d - timedelta(days=1)], "c": [100.0], "v": [500_000]}),
        "C": pd.DataFrame({"d": [d - timedelta(days=1)], "c": [3.0], "v": [9_000_000]}),
        "D": pd.DataFrame({"d": [d], "c": [50.0], "v": [9_000_000]}),   # today: unseen
    }
    out = movers_proxy(daily, d, ["A", "B", "C", "D"], n=2, min_price=5.0)
    assert out == ["B", "A"]           # C is under $5, D has no prior session


def test_the_vault_boundary_comes_from_config(cfg):
    assert _period(date(2026, 1, 31), cfg) == "pre_vault"
    assert _period(date(2026, 2, 1), cfg) == "vault"


# ---------------------------------------------------------------- summary

def test_summary_keeps_the_periods_apart_and_labels_the_proxy():
    rows = [SessionResult("2026-01-10", "movers", "pre_vault", "A,B", 13, 2,
                          1.0, 0.1, 0.9, 1.8, 50.0, 50.9, False, False, "16:00"),
            SessionResult("2026-03-10", "movers", "vault", "A,B", 13, 0,
                          0.0, 0.0, 0.0, 0.0, 50.9, 50.9, False, False, "16:00")]
    s = summarize(rows, "movers")
    assert s["pre_vault"]["sessions"] == 1 and s["vault"]["sessions"] == 1
    assert s["all"]["trades"] == 2
    assert "proxy" in s["universe"]
    assert s["all"]["max_drawdown_pct"] == 0.0
    assert summarize(rows, "fast")["universe"] == "as registered"


# ----------------------------------------------------------- end to end

def test_replay_writes_a_row_per_session_and_a_report(cfg, tmp_path):
    class CalBroker:
        def calendar(self, start, end):
            out = []
            for d in (date(2026, 1, 29), date(2026, 1, 30), date(2026, 2, 2)):
                o = datetime.combine(d, datetime.min.time(), ET).replace(hour=9, minute=30)
                c = datetime.combine(d, datetime.min.time(), ET).replace(hour=16)
                out.append((d, o, c))
            return out

        def intraday_5min(self, symbols, day=None):
            return {s: _bars(day, 100.0 + 10 * i) for i, s in enumerate(symbols)}

    out = tmp_path / "replays"
    summary = replay(cfg, CalBroker(), "fast", 1, out, log=lambda *_: None)
    assert summary["pre_vault"]["sessions"] == 2 and summary["vault"]["sessions"] == 1
    csvs = list(out.glob("fast-*.csv"))
    mds = list(out.glob("fast-*.md"))
    assert len(csvs) == 1 and len(mds) == 1
    assert csvs[0].read_text().count("\n") == 4            # header + 3 rows
    assert "one-shot confirmatory" in mds[0].read_text()
    assert not (out / ".work-fast").exists()                # scratch cleaned up
