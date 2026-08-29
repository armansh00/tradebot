import numpy as np
import pandas as pd
import pytest
from tradebot.config import load_config


class FakeBroker:
    """Same surface as AlpacaBroker, no network. Deterministic."""
    def __init__(self, closes: dict[str, pd.Series], equity: float = 50.0,
                 positions: dict[str, float] | None = None, market_is_open=True):
        self._closes = closes
        self._equity = equity
        self._positions = dict(positions or {})
        self._open = market_is_open
        self.submitted: list[dict] = []

    def market_open(self):
        return self._open

    def equity(self):
        return self._equity

    def positions(self):
        return dict(self._positions)

    def daily_closes(self, symbols, days):
        return {s: self._closes[s] for s in symbols if s in self._closes}

    def submit(self, order):
        self.submitted.append(order)
        mv = self._positions.get(order["symbol"], 0.0)
        delta = order["notional"] if order["side"] == "buy" else -order["notional"]
        new = mv + delta
        if abs(new) < 0.01:
            self._positions.pop(order["symbol"], None)
        else:
            self._positions[order["symbol"]] = new
        return {**order, "status": "filled_fake"}


def trending_series(n=300, start=100.0, drift=0.003, seed=1):
    rng = np.random.default_rng(seed)
    rets = rng.normal(drift, 0.005, n)
    return pd.Series(start * np.cumprod(1 + rets))


def falling_series(n=300, start=100.0, seed=2):
    return trending_series(n=n, start=start, drift=-0.003, seed=seed)


@pytest.fixture
def cfg(tmp_path):
    import shutil
    shutil.copy("/home/claude/tradebot/config.yaml", tmp_path / "config.yaml")
    return load_config(tmp_path)
