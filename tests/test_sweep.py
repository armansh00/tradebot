"""The replay harness. Its one job is to answer the threshold question without
spending eight weeks and without manufacturing a winner out of noise."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from tradebot.sweep import (DayResult, exact_permutation_p, replay_day, report,
                            spearman_rho, summarize)
from tests.test_fastarm import day_bars

ET = ZoneInfo("America/New_York")
DATE = datetime(2026, 9, 1).date()
RULES = dict(or_minutes=30, top_k=2, cost_bps_per_side=5, max_trades=6,
             daily_loss_stop_pct=3.0, flat_minutes_before_close=30,
             min_price=5.0)


def _bars(patterns):
    return {s: day_bars(DATE, p, base=100 + 10 * i)
            for i, (s, p) in enumerate(patterns.items())}


def test_a_higher_threshold_takes_fewer_trades(cfg):
    bars = _bars({"SPY": "breakout", "QQQ": "breakout"})
    counts = [replay_day(bars, threshold_pct=t, **RULES)[1]
              for t in (0.0, 0.5, 5.0)]
    assert counts[0] >= counts[1] >= counts[2]
    assert counts[0] > 0 and counts[2] == 0     # 5% filters everything out


def test_no_trades_means_no_return_and_no_cost(cfg):
    net, trades, gross = replay_day(_bars({"SPY": "inside"}),
                                    threshold_pct=1.0, **RULES)
    assert (net, trades, gross) == (0.0, 0, 0.0)


def test_cost_makes_net_worse_than_gross_whenever_it_trades(cfg):
    net, trades, gross = replay_day(_bars({"SPY": "breakout"}),
                                    threshold_pct=0.0, **RULES)
    assert trades > 0
    assert net < gross                          # the toll is always paid


def test_a_fading_day_stops_out_rather_than_riding_it_down(cfg):
    faded = replay_day(_bars({"SPY": "fade"}), threshold_pct=0.0, **RULES)[0]
    assert faded > -3.5                         # stop, then the daily loss cap


def test_spearman_and_exact_p_on_a_perfect_ordering():
    x = [0.0, 0.5, 1.0, 1.5, 2.0]
    assert spearman_rho(x, [1, 2, 3, 4, 5]) == pytest.approx(1.0)
    assert spearman_rho(x, [5, 4, 3, 2, 1]) == pytest.approx(-1.0)
    # 2/120 orderings are perfectly monotone in either direction
    assert exact_permutation_p(x, [1, 2, 3, 4, 5]) == pytest.approx(2 / 120)


def test_report_refuses_to_crown_a_winner_without_an_ordering():
    """The whole point. A scrambled grid must not produce a recommendation."""
    scrambled = [0.4, -0.2, 0.9, -0.5, 0.1, -0.3, 0.2]
    rows = [DayResult("2026-09-01", t, v, 1, v, 0.0)
            for t, v in zip([0.0, 0.25, 0.5, 1.0, 1.5, 2.0, 3.0], scrambled)]
    text = report(summarize(rows))
    assert "No ordering" in text
    assert "do not promote it" in text


def test_report_flags_a_real_monotone_ordering():
    rows = [DayResult("2026-09-01", t, v, 1, v, 0.0)
            for t, v in zip([0.0, 0.25, 0.5, 1.0, 1.5, 2.0, 3.0],
                            [-0.3, -0.1, 0.0, 0.2, 0.4, 0.5, 0.8])]
    text = report(summarize(rows))
    assert "monotone increasing" in text
    assert "ONE threshold" in text               # not the whole grid


def test_replay_matches_the_live_arm_on_the_same_day(cfg, tmp_path):
    """A replay that has drifted from the live rules answers a question about
    a strategy nobody is running. Threshold 0 is the live arm, so stepping the
    live engine through the same bars must produce the same entries."""
    import json
    from tradebot.fastarm import run_fast_once
    from tests.test_fastarm import FastFake

    cfg.fast.fills_mode = "simulated"
    cfg.fast.universe = ["SPY", "QQQ"]
    patterns = {"SPY": "breakout", "QQQ": "fade"}
    today = datetime.now(ET).date()
    frames = {s: day_bars(today, p, base=100 + 10 * i)
              for i, (s, p) in enumerate(patterns.items())}

    for t in sorted(frames["SPY"]["t"]):
        run_fast_once(cfg, FastFake(frames, t.to_pydatetime()), arm="fast")

    live = [(e["side"], e["symbol"]) for e in
            (json.loads(l) for l in cfg.fast_ledger_path.read_text().splitlines())
            if e["type"] == "fast_order"]

    log: list = []
    replayed_trades = replay_day(frames, threshold_pct=0.0, log=log,
                                 **{**RULES, "min_price": 5.0})[1]
    live_buys = [x for x in live if x[0] == "buy"]
    replay_buys = [x for x in log if x[0] == "buy"]

    assert live_buys, "live engine took no trades — test is not exercising anything"
    assert replayed_trades == len(live_buys)
    assert [s for _, s in replay_buys] == [s for _, s in live_buys]
