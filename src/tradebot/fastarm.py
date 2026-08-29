"""The experiment arm: intraday opening-range breakout with SIMULATED fills.

Design choice, stated plainly: this arm does not send orders to the paper
account. It runs a virtual $50 book against live market data and charges
itself an explicit, configurable cost on every fill (half-spread + slippage,
`cost_bps_per_side`). Paper-account fills flatter day trading by ignoring
those costs; here the toll booth is on the books, itemized, every time.
The question under test is whether trade frequency adds net return —
so the costs of frequency must be visible, not hidden.

Entry rule: after the opening range (first `or_minutes`), buy up to `top_k`
symbols trading above their OR high, ranked by breakout strength.
Exit rules: price back below OR low (stop), daily loss stop on the whole
book, and everything flat `flat_minutes_before_close` before the bell.
"""
from __future__ import annotations
import json
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from .config import Config
from .ledger import Ledger

ET = ZoneInfo("America/New_York")
OPEN_T = time(9, 30)
CLOSE_T = time(16, 0)


# ---------- virtual account ----------------------------------------------
def _load_state(cfg: Config) -> dict:
    if cfg.fast_state_path.exists():
        return json.loads(cfg.fast_state_path.read_text())
    return {"cash": cfg.fast.start_cash, "positions": {},
            "high_water_mark": cfg.fast.start_cash, "day": None,
            "day_start_equity": cfg.fast.start_cash, "trades_today": 0,
            "stopped_today": False, "halted": False}


def _save_state(cfg: Config, st: dict) -> None:
    cfg.fast_state_path.write_text(json.dumps(st, indent=2))


def _mark_equity(st: dict, lasts: dict[str, float]) -> float:
    eq = st["cash"]
    for sym, pos in st["positions"].items():
        eq += pos["qty"] * lasts.get(sym, pos["last_px"])
    return round(eq, 4)


def _fill(cfg: Config, st: dict, ledger: Ledger, side: str, sym: str,
          px: float, qty: float, reason: str) -> None:
    """Simulated fill with explicit cost. Buys pay up, sells receive less."""
    bps = cfg.fast.cost_bps_per_side / 1e4
    fill_px = px * (1 + bps) if side == "buy" else px * (1 - bps)
    gross = qty * px
    cost = abs(qty * px * bps)
    if side == "buy":
        st["cash"] -= qty * fill_px
        st["positions"][sym] = {"qty": qty, "entry_px": fill_px,
                                "or_low": st["positions"].get(sym, {}).get("or_low"),
                                "last_px": px}
    else:
        pos = st["positions"].pop(sym, None)
        st["cash"] += qty * fill_px
        if pos:
            pnl = (fill_px - pos["entry_px"]) * qty
            ledger.write("fast_close", symbol=sym, qty=round(qty, 6),
                         entry_px=round(pos["entry_px"], 4),
                         exit_px=round(fill_px, 4), pnl=round(pnl, 4),
                         reason=reason)
    ledger.write("fast_order", side=side, symbol=sym, px=round(fill_px, 4),
                 qty=round(qty, 6), notional=round(gross, 2),
                 modeled_cost=round(cost, 4), reason=reason)


# ---------- opening range -------------------------------------------------
def opening_range(bars, or_end: datetime) -> tuple[float, float] | None:
    """(high, low) of bars strictly before or_end. None if no bars yet."""
    window = bars[bars["t"] < or_end]
    if window.empty:
        return None
    return float(window["h"].max()), float(window["l"].min())


# ---------- the tick ------------------------------------------------------
def run_fast_once(cfg: Config, broker, now: datetime | None = None) -> dict:
    ledger = Ledger(cfg.fast_ledger_path)
    st = _load_state(cfg)
    f = cfg.fast

    now = now or broker.now_et()
    today = now.date().isoformat()
    open_dt = now.replace(hour=OPEN_T.hour, minute=OPEN_T.minute,
                          second=0, microsecond=0)
    close_dt = now.replace(hour=CLOSE_T.hour, minute=CLOSE_T.minute,
                           second=0, microsecond=0)
    or_end = open_dt + timedelta(minutes=f.or_minutes)
    flat_dt = close_dt - timedelta(minutes=f.flat_minutes_before_close)

    if st.get("halted"):
        return {"status": "halted"}
    if now < open_dt or now >= close_dt:
        return {"status": "market_closed"}

    if st.get("day") != today:  # new day: reset counters, carry nothing overnight
        st.update({"day": today, "trades_today": 0, "stopped_today": False})

    bars = broker.intraday_5min(f.universe)  # {sym: df[t,o,h,l,c]}
    lasts = {s: float(df["c"].iloc[-1]) for s, df in bars.items() if len(df)}
    if st.get("day_start_equity") is None or st.get("day") != today or \
       "day_equity_set" not in st:
        st["day_start_equity"] = _mark_equity(st, lasts)
        st["day_equity_set"] = today
    if st["day_equity_set"] != today:
        st["day_start_equity"] = _mark_equity(st, lasts)
        st["day_equity_set"] = today

    equity = _mark_equity(st, lasts)

    # --- account-level kill switch (same rule as the slow arm) ------------
    st["high_water_mark"] = max(st.get("high_water_mark", equity), equity)
    dd = (st["high_water_mark"] - equity) / st["high_water_mark"] * 100 \
        if st["high_water_mark"] > 0 else 0.0
    if dd >= f.max_drawdown_pct:
        for sym in list(st["positions"]):
            _fill(cfg, st, ledger, "sell", sym, lasts.get(
                sym, st["positions"][sym]["last_px"]),
                st["positions"][sym]["qty"], "drawdown_kill")
        st["halted"] = True
        ledger.write("fast_halt", reason=f"drawdown {dd:.1f}%")
        _save_state(cfg, st)
        return {"status": "killed"}

    # --- daily loss stop --------------------------------------------------
    day_pnl_pct = (equity / st["day_start_equity"] - 1) * 100 \
        if st["day_start_equity"] > 0 else 0.0
    if not st["stopped_today"] and day_pnl_pct <= -f.daily_loss_stop_pct:
        for sym in list(st["positions"]):
            _fill(cfg, st, ledger, "sell", sym, lasts[sym],
                  st["positions"][sym]["qty"], "daily_loss_stop")
        st["stopped_today"] = True
        ledger.write("fast_day_stop", day_pnl_pct=round(day_pnl_pct, 3))

    # --- end-of-day flat --------------------------------------------------
    if now >= flat_dt:
        for sym in list(st["positions"]):
            _fill(cfg, st, ledger, "sell", sym, lasts[sym],
                  st["positions"][sym]["qty"], "eod_flat")
    elif not st["stopped_today"] and now >= or_end:
        ors = {}
        for sym, df in bars.items():
            r = opening_range(df, or_end)
            if r:
                ors[sym] = r

        # exits: stop = back below OR low
        for sym in list(st["positions"]):
            if sym in ors and sym in lasts and lasts[sym] < ors[sym][1]:
                _fill(cfg, st, ledger, "sell", sym, lasts[sym],
                      st["positions"][sym]["qty"], "or_low_stop")

        # entries: breakouts above OR high, ranked by strength
        slots = f.top_k - len(st["positions"])
        budget_per_slot = (equity / f.top_k)
        candidates = []
        for sym, (hi, lo) in ors.items():
            if sym in st["positions"] or sym not in lasts:
                continue
            if lasts[sym] > hi > 0:
                candidates.append((sym, lasts[sym] / hi - 1, hi, lo))
        candidates.sort(key=lambda t: t[1], reverse=True)
        entered = []
        for sym, strength, hi, lo in candidates:
            if slots <= 0 or st["trades_today"] >= f.max_trades_per_day:
                break
            notional = min(budget_per_slot, st["cash"])
            if notional < 5:  # not worth a fill under $5
                break
            qty = notional / lasts[sym]
            _fill(cfg, st, ledger, "buy", sym, lasts[sym], qty,
                  f"breakout +{strength:.2%} above OR high {hi:.2f}")
            st["positions"][sym]["or_low"] = lo
            st["trades_today"] += 1
            slots -= 1
            entered.append(sym)
        ledger.write("fast_decision", or_levels={s: [round(h, 2), round(l, 2)]
                                                 for s, (h, l) in ors.items()},
                     candidates=[c[0] for c in candidates], entered=entered,
                     trades_today=st["trades_today"])

    equity = _mark_equity(st, lasts)
    ledger.write("fast_run", equity=equity, day=today,
                 day_pnl_pct=round((equity / st["day_start_equity"] - 1) * 100, 3)
                 if st["day_start_equity"] > 0 else 0.0,
                 positions={s: round(p["qty"] * lasts.get(s, p["last_px"]), 2)
                            for s, p in st["positions"].items()},
                 cash=round(st["cash"], 2))
    for sym, pos in st["positions"].items():
        if sym in lasts:
            pos["last_px"] = lasts[sym]
    _save_state(cfg, st)
    return {"status": "ok", "equity": equity,
            "positions": list(st["positions"])}
