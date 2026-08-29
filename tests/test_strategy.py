from tradebot import signals, strategy
from conftest import trending_series, falling_series


def make_snaps(cfg):
    st = cfg.strategy
    ups = {s: trending_series(seed=i) for i, s in enumerate(["SPY", "QQQ", "IWM"])}
    downs = {s: falling_series(seed=i + 10) for i, s in enumerate(["EFA", "TLT", "GLD"])}
    closes = {**ups, **downs}
    return {s: signals.snapshot(c, st.sma_days, st.mom_lookback_days,
                                st.mom_skip_days) for s, c in closes.items()}


def test_picks_top_n_uptrends_only(cfg):
    out = strategy.decide(cfg, make_snaps(cfg), equity=50.0)
    assert len(out["targets"]) == cfg.strategy.top_n
    for sym in out["targets"]:
        assert sym in {"SPY", "QQQ", "IWM"}
    for sym in ["EFA", "TLT", "GLD"]:
        assert not out["decisions"][sym]["eligible"]


def test_all_cash_when_nothing_qualifies(cfg):
    st = cfg.strategy
    snaps = {s: signals.snapshot(falling_series(seed=i), st.sma_days,
                                 st.mom_lookback_days, st.mom_skip_days)
             for i, s in enumerate(cfg.universe)}
    out = strategy.decide(cfg, snaps, equity=50.0)
    assert out["targets"] == {}


def test_targets_respect_cash_buffer(cfg):
    out = strategy.decide(cfg, make_snaps(cfg), equity=50.0)
    assert sum(out["targets"].values()) <= 50.0 * 0.951


def test_diff_orders_sells_before_buys(cfg):
    orders = strategy.diff_orders(cfg, {"SPY": 23.75}, {"GLD": 20.0})
    assert [o["side"] for o in orders] == ["sell", "buy"]


def test_diff_ignores_dust(cfg):
    assert strategy.diff_orders(cfg, {"SPY": 20.5}, {"SPY": 20.0}) == []
