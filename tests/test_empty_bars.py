"""An empty bar window is a fact about the market, not an error.

2026-09-04, 06:19 UTC. The SIP entitlement had just been fixed, all three
accounts reported `effective_feed: sip`, and the preflight failed anyway:

    fast/intraday_5min    KeyError: 'symbol'
    movers/intraday_5min  KeyError: 'symbol'

The request now succeeded and came back empty, because it was two hours
before the opening bell and today had no bars yet. `resp.df` carries a
(symbol, timestamp) MultiIndex when there are rows and a bare empty frame
when there are none, so `reset_index()` produced no `symbol` column and the
per-symbol filter on the next line raised.

The crash had been hiding behind the entitlement error for three days —
the request was refused before it could ever return empty. Fixing one defect
uncovered the other, which is what commissioning is for.

It is not a preflight-only problem. A mid-session tick asking for a halted
symbol, or for a screener pick with no prints yet, dies exactly the same way.
"""
import pandas as pd
import pytest

from tradebot.broker import AlpacaBroker, bars_frame


class Resp:
    def __init__(self, df):
        self.df = df


def _multi(rows):
    idx = pd.MultiIndex.from_tuples([(r[0], pd.Timestamp(r[1], tz="UTC"))
                                     for r in rows],
                                    names=["symbol", "timestamp"])
    return pd.DataFrame({"open": [r[2] for r in rows],
                         "high": [r[2] for r in rows],
                         "low": [r[2] for r in rows],
                         "close": [r[2] for r in rows],
                         "volume": [100] * len(rows)}, index=idx)


def _broker():
    b = AlpacaBroker.__new__(AlpacaBroker)     # no credentials, no network
    b.key_env, b.secret_env, b.dry_run = "K", "S", False
    return b


# ------------------------------------------------------------- the frame

def test_an_empty_response_still_has_the_columns():
    df = bars_frame(Resp(pd.DataFrame()))
    assert df.empty
    assert "symbol" in df.columns and "timestamp" in df.columns
    assert df[df["symbol"] == "SPY"].empty          # the line that used to raise


def test_a_none_response_is_survivable():
    assert bars_frame(Resp(None)).empty


def test_rows_come_through_flat():
    df = bars_frame(Resp(_multi([("SPY", "2026-09-04 13:35", 100.0),
                                 ("QQQ", "2026-09-04 13:35", 110.0)])))
    assert set(df["symbol"]) == {"SPY", "QQQ"}
    assert float(df[df["symbol"] == "SPY"]["close"].iloc[0]) == 100.0


def test_a_response_with_no_symbol_column_gets_one():
    """Defensive: a single-symbol response shape must not reintroduce the
    same KeyError by another route."""
    df = pd.DataFrame({"timestamp": [pd.Timestamp("2026-09-04", tz="UTC")],
                       "open": [1.0], "high": [1.0], "low": [1.0],
                       "close": [1.0], "volume": [1]})
    assert "symbol" in bars_frame(Resp(df)).columns


# --------------------------------------------------------- the two callers

def test_intraday_before_the_open_returns_nothing_and_does_not_raise():
    """The exact 06:19 call: hours before the bell, SIP entitled, no rows."""
    b = _broker()
    b._data = type("D", (), {"get_stock_bars": lambda self, req: Resp(pd.DataFrame())})()
    assert b.intraday_5min(["SPY", "QQQ"]) == {}


def test_daily_closes_survives_an_empty_history():
    b = _broker()
    b._data = type("D", (), {"get_stock_bars": lambda self, req: Resp(pd.DataFrame())})()
    out = b.daily_closes(["SPY"], 60)
    assert list(out) == ["SPY"] and len(out["SPY"]) == 0


def test_a_symbol_with_no_prints_is_omitted_not_fatal():
    """A halted name, or a screener pick that has not traded yet. The other
    symbols in the same request must still come back."""
    b = _broker()
    frame = _multi([("SPY", "2026-09-04 13:35", 100.0)])
    b._data = type("D", (), {"get_stock_bars": lambda self, req: Resp(frame)})()
    out = b.intraday_5min(["SPY", "HALTED"])
    assert list(out) == ["SPY"]


def test_the_preflight_reads_an_empty_window_as_reachable(cfg):
    """Grading was already right — permission and reachability, not presence.
    It never got the chance, because the call raised before returning."""
    from tradebot.preflight import run_preflight

    class Pre:
        def daily_closes(self, symbols, days):
            return {s: [1.0] for s in symbols}

        def most_actives(self, n):
            return ["AAPL", "NVDA"][:n]

        def intraday_5min(self, symbols, day=None):
            b = _broker()
            b._data = type("D", (), {"get_stock_bars":
                                     lambda self, req: Resp(pd.DataFrame())})()
            return b.intraday_5min(symbols)

        def quote_snapshot(self, symbol):
            return {"bid": 1.0, "ask": 1.01, "mid": 1.005}

    report = run_preflight(cfg, {a: Pre() for a in ("slow", "fast", "movers")},
                           notify=lambda r: None)
    assert report.disabled == set()
    bars = [r for r in report.results if r.probe == "intraday_5min"]
    assert bars and all(r.ok and "no rows yet" in r.detail for r in bars)
