from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd
from tradebot.fastarm import run_fast_once, opening_range
from tradebot.ledger import Ledger
from tradebot.compare import compare_report

ET = ZoneInfo("America/New_York")


def day_bars(date, pattern, base=100.0):
    """5-min bars 9:30->16:00. Patterns: breakout / fade / inside."""
    t0 = datetime.combine(date, datetime.min.time(), ET).replace(hour=9, minute=30)
    ts, o, h, l, c = [], [], [], [], []
    px = base
    for i in range(78):
        t = t0 + timedelta(minutes=5 * i)
        if pattern == "breakout":
            drift = 0.0005 if i >= 6 else 0.0      # rips after the OR
        elif pattern == "fade":
            drift = 0.002 if 6 <= i < 10 else (-0.004 if i >= 10 else 0.0)
        else:
            drift = 0.0
        px *= (1 + drift)
        ts.append(t); o.append(px * 0.999); h.append(px * 1.001)
        l.append(px * 0.998); c.append(px)
    return pd.DataFrame({"t": ts, "o": o, "h": h, "l": l, "c": c})


class FastFake:
    def __init__(self, frames, now):
        self._frames = frames
        self._now = now

    def now_et(self):
        return self._now

    def intraday_5min(self, symbols):
        # only bars up to "now" are visible
        return {s: df[df["t"] <= self._now].reset_index(drop=True)
                for s, df in self._frames.items() if s in symbols}


def _mk(cfg, pattern_map, hour, minute):
    date = datetime.now(ET).date()
    frames = {s: day_bars(date, p, base=100 + 10 * i)
              for i, (s, p) in enumerate(pattern_map.items())}
    now = datetime.combine(date, datetime.min.time(), ET).replace(
        hour=hour, minute=minute)
    return FastFake(frames, now)


def test_opening_range_math():
    date = datetime.now(ET).date()
    df = day_bars(date, "inside")
    or_end = df["t"].iloc[0] + timedelta(minutes=30)
    hi, lo = opening_range(df, or_end)
    assert hi > lo > 0


def test_no_entries_before_or_complete(cfg):
    b = _mk(cfg, {"SPY": "breakout", "QQQ": "inside"}, 9, 45)
    assert run_fast_once(cfg, b, now=b.now_et())["status"] == "ok"
    assert Ledger(cfg.fast_ledger_path).last("fast_order") is None


def test_enters_breakouts_with_costs(cfg):
    b = _mk(cfg, {"SPY": "breakout", "NVDA": "breakout", "QQQ": "inside"}, 11, 0)
    result = run_fast_once(cfg, b, now=b.now_et())
    assert set(result["positions"]) == {"SPY", "NVDA"}
    orders = [r for r in Ledger(cfg.fast_ledger_path).read()
              if r["type"] == "fast_order"]
    assert len(orders) == 2
    assert all(o["modeled_cost"] > 0 for o in orders)
    assert all(o["px"] > 0 for o in orders)


def test_inside_day_stays_flat(cfg):
    b = _mk(cfg, {"SPY": "inside", "QQQ": "inside"}, 12, 0)
    result = run_fast_once(cfg, b, now=b.now_et())
    assert result["positions"] == []


def test_stop_out_on_fade(cfg):
    date = datetime.now(ET).date()
    frames = {"SPY": day_bars(date, "fade")}
    entry_now = datetime.combine(date, datetime.min.time(), ET).replace(
        hour=10, minute=15)
    b = FastFake(frames, entry_now)
    r1 = run_fast_once(cfg, b, now=entry_now)
    assert r1["positions"] == ["SPY"]           # bought the early breakout
    later = entry_now.replace(hour=13, minute=0)
    b2 = FastFake(frames, later)
    r2 = run_fast_once(cfg, b2, now=later)
    assert r2["positions"] == []                # stopped below OR low
    closes = [r for r in Ledger(cfg.fast_ledger_path).read()
              if r["type"] == "fast_close"]
    assert closes and closes[-1]["reason"] in {"or_low_stop", "daily_loss_stop"}


def test_eod_flat(cfg):
    b = _mk(cfg, {"SPY": "breakout"}, 11, 0)
    run_fast_once(cfg, b, now=b.now_et())
    late = b.now_et().replace(hour=15, minute=50)
    b2 = _mk(cfg, {"SPY": "breakout"}, 15, 50)
    b2._frames = b._frames
    b2._now = late
    result = run_fast_once(cfg, b2, now=late)
    assert result["positions"] == []
    closes = [r for r in Ledger(cfg.fast_ledger_path).read()
              if r["type"] == "fast_close"]
    assert closes[-1]["reason"] == "eod_flat"


def test_trade_cap_respected(cfg):
    # 8 breakout symbols but top_k=2 and cap=6: never more than 2 held,
    # trades_today never exceeds the cap across repeated ticks
    pattern = {s: "breakout" for s in cfg.fast.universe}
    for minute in (5, 20, 35, 50):
        b = _mk(cfg, pattern, 11, minute)
        run_fast_once(cfg, b, now=b.now_et())
    decisions = [r for r in Ledger(cfg.fast_ledger_path).read()
                 if r["type"] == "fast_decision"]
    assert all(d["trades_today"] <= cfg.fast.max_trades_per_day
               for d in decisions)


def test_compare_report_runs(cfg):
    # seed both ledgers with a few days of equity and produce a verdict
    slow = Ledger(cfg.ledger_path)
    fast = Ledger(cfg.fast_ledger_path)
    for i, (se, fe) in enumerate([(50, 50), (50.5, 49.6), (50.8, 49.9)]):
        slow.write("run", equity=se)
        fast.write("fast_run", equity=fe, day=f"2026-08-{24 + i:02d}")
    out = compare_report(cfg)
    assert "Arm comparison" in out and "Fast-vs-slow verdict" in out
    assert "INSUFFICIENT DATA" in out or "ARM" in out


def test_movers_arm_uses_screener_universe(cfg):
    from tradebot.ledger import Ledger as L
    date = datetime.now(ET).date()
    frames = {"GME": day_bars(date, "breakout", 25), "AMC": day_bars(date, "inside", 8),
              "PLTR": day_bars(date, "breakout", 40)}
    b = FastFake(frames, datetime.combine(date, datetime.min.time(), ET)
                 .replace(hour=11, minute=0))
    b.most_actives = lambda n: ["GME", "AMC", "PLTR"]
    result = run_fast_once(cfg, b, now=b.now_et(), arm="movers")
    assert set(result["positions"]) <= {"GME", "PLTR", "AMC"}
    recs = L(cfg.movers_ledger_path).read()
    assert any(r["type"] == "universe" for r in recs)          # universe logged
    orders = [r for r in recs if r["type"] == "fast_order"]
    assert orders and all(o["modeled_cost"] > 0 for o in orders)
    # movers cost model is 15 bps/side: cost ≈ notional * 0.0015
    o = orders[0]
    assert abs(o["modeled_cost"] - o["notional"] * 0.0015) < 0.01
    assert not (cfg.fast_ledger_path.exists() and
                any(r["type"] == "fast_order"
                    for r in L(cfg.fast_ledger_path).read()))  # arms isolated


def test_movers_min_price_filter(cfg):
    date = datetime.now(ET).date()
    frames = {"PENNY": day_bars(date, "breakout", 2.0)}       # under $5 floor
    b = FastFake(frames, datetime.combine(date, datetime.min.time(), ET)
                 .replace(hour=11, minute=0))
    b.most_actives = lambda n: ["PENNY"]
    result = run_fast_once(cfg, b, now=b.now_et(), arm="movers")
    assert result["positions"] == []
