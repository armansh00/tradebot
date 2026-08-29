import pandas as pd
from tradebot import signals
from conftest import trending_series


def test_sma_needs_enough_data():
    assert signals.sma(pd.Series([1.0, 2.0]), 200) is None


def test_sma_value():
    s = pd.Series([1.0] * 100 + [2.0] * 100)
    assert signals.sma(s, 200) == 1.5


def test_momentum_positive_on_uptrend():
    m = signals.momentum_12_1(trending_series(300), 252, 21)
    assert m is not None and m > 0


def test_momentum_skips_recent_month():
    # Flat for a year then a crash in the last 21 days: 12-1 momentum
    # should NOT see the crash.
    s = pd.Series([100.0] * 280 + [50.0] * 21)
    m = signals.momentum_12_1(s, 252, 21)
    assert abs(m) < 1e-9


def test_snapshot_fields():
    snap = signals.snapshot(trending_series(300), 200, 252, 21)
    assert snap["enough_data"] and snap["above_sma"] is not None
    assert set(snap) >= {"price", "sma", "above_sma", "momentum"}
