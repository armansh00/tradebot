"""The rotation test. Its job is to be honest about a hypothesis the user
wants to be true, so the tests check that it can say no."""
import numpy as np
import pandas as pd

from tradebot.reversal import daily_returns, report, rotate


def _rets(frame):
    return pd.DataFrame(frame)


def test_detects_a_planted_reversal(cfg):
    """If tomorrow really does undo today, the rule must find it."""
    rng = np.random.default_rng(0)
    n, phi = 800, -0.5          # today undoes half of yesterday, per name
    frame = {}
    for sym in "ABCD":
        r = [0.0]
        for _ in range(n):
            r.append(phi * r[-1] + rng.normal(0, 0.01))
        frame[sym] = np.array(r[1:])
    r = rotate(_rets(frame), direction="losers", k=1, cost_bps_per_side=0)
    assert r.mean_daily_gross_pct > 0
    assert r.t_stat_gross > 2
    # and the opposite rule must lose on the same data
    w = rotate(_rets(frame), direction="winners", k=1, cost_bps_per_side=0)
    assert w.mean_daily_gross_pct < 0


def test_finds_nothing_in_pure_noise(cfg):
    """Across many noise samples, not one.

    A single seed here produced |t| = 2.23 on pure noise — which is the whole
    lesson of this project in miniature: at conventional thresholds, roughly
    one run in twenty looks significant when nothing is there. Asserting on
    one draw would have encoded that accident as an expectation.
    """
    ts = []
    for seed in range(40):
        rng = np.random.default_rng(seed)
        frame = {s: rng.normal(0, 0.01, 500) for s in "ABCDE"}
        r = rotate(_rets(frame), direction="losers", k=2, cost_bps_per_side=5)
        ts.append(r.t_stat_gross)
        assert r.t_stat_net < r.t_stat_gross       # tolls always bite
    assert abs(np.mean(ts)) < 0.5                  # centred on zero
    assert sum(1 for t in ts if abs(t) > 2) <= 6   # ~5% false positives


def test_costs_are_charged_every_single_day(cfg):
    frame = {s: np.zeros(200) for s in "ABC"}
    r = rotate(_rets(frame), direction="losers", k=1, cost_bps_per_side=5)
    assert r.mean_daily_gross_pct == 0.0
    assert r.mean_daily_net_pct == -0.1       # 10 bps round trip = 0.1%/day
    assert r.total_net_pct < -15              # bleeds ~18% over 200 days


def test_report_refuses_to_endorse_a_weak_result():
    from tradebot.reversal import Result
    weak = [Result("losers", 1, 400, 0.02, 0.01, 4.0, 1.3, 1.1, 0.51),
            Result("winners", 1, 400, -0.01, -0.02, -7.0, -1.2, -1.4, 0.48)]
    text = report(weak, ["A", "B"], 5)
    assert "Nothing clears the bar" in text
    assert "story about this particular stretch of history" in text


def test_report_hedges_even_when_something_clears_the_bar():
    from tradebot.reversal import Result
    strong = [Result("losers", 1, 400, 0.09, 0.08, 30.0, 3.6, 3.4, 0.56)]
    text = report(strong, ["A", "B"], 5)
    assert "several variants were tried" in text
    assert "out-of-sample window, not a live account" in text


def test_daily_returns_survives_ragged_series():
    closes = {"A": pd.Series([1.0, 1.1, 1.2]), "B": pd.Series([5.0]),
              "C": pd.Series([2.0, 2.2, 2.1])}
    df = daily_returns(closes)
    assert list(df.columns) == ["A", "C"]      # B too short, dropped
    assert len(df) == 2
