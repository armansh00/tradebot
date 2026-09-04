"""The experiment arms: intraday opening-range breakout.

Fills (revised 2026-08-31). This arm used to keep a virtual book and never
touch the account, because all three arms shared one paper account and their
positions would have pooled into an unattributable pile. Alpaca now allows
additional paper accounts with a chosen starting balance, so each arm has its
own $50 account and `fills_mode: broker` sends real orders. The account is the
single record of what is held; this module keeps only its own metadata.

The modeled cost survives the change and is the reason both modes exist.
Alpaca's paper engine fills at the best available price and explicitly models
no slippage, no spread, no market impact, no queue position — free perfect
execution, which flatters frequent trading in exactly the names these arms
trade. So the cost is still charged, as a separate accrued line rather than
inside the fill: the account reports gross, `cost_accrued` converts it to net,
and the pre-registered criterion reads the net number. `fills_mode: simulated`
keeps the older self-contained engine for testing and comparison.

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


def intent_id(arm: str, day: str, tick: str, side: str, sym: str) -> str:
    """Deterministic id for one intended order.

    Regenerated identically by a retry, which is the point: it goes to the
    broker as client_order_id, and the broker refuses the second one. The
    dangerous failure is not slippage, it is submitting twice because a leg
    restarted after the network dropped mid-submission.
    """
    import hashlib
    key = f"{arm}|{day}|{tick}|{side}|{sym}"
    return "tb-" + hashlib.sha256(key.encode()).hexdigest()[:20]

ET = ZoneInfo("America/New_York")
OPEN_T = time(9, 30)
CLOSE_T = time(16, 0)


# ---------- virtual account ----------------------------------------------
def _arm(cfg: Config, arm: str):
    """(arm_cfg, ledger_path, state_path) for the named simulated arm."""
    if arm == "movers":
        return cfg.movers, cfg.movers_ledger_path, cfg.movers_state_path
    return cfg.fast, cfg.fast_ledger_path, cfg.fast_state_path


def _load_state(state_path, start_cash: float) -> dict:
    if state_path.exists():
        return json.loads(state_path.read_text())
    return {"cash": start_cash, "positions": {}, "or_low": {},
            "cost_accrued": 0.0,
            "high_water_mark": start_cash, "day": None,
            "day_start_equity": start_cash, "trades_today": 0,
            "stopped_today": False, "halted": False}


def _save_state(state_path, st: dict) -> None:
    import os
    tmp = state_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(st, indent=2))
    os.replace(tmp, state_path)


def _live(f) -> bool:
    return getattr(f, "fills_mode", "broker") == "broker"


def _positions(f, broker, st) -> dict[str, dict]:
    """What the arm holds. In broker mode the account is the only record."""
    if _live(f):
        return broker.positions_detail()
    return st["positions"]


def _mark_equity(f, broker, st: dict, lasts: dict[str, float]) -> float:
    """Net equity: what the account says, less the costs a real venue would
    have taken and this paper venue does not."""
    if _live(f):
        return round(broker.equity() - st.get("cost_accrued", 0.0), 4)
    eq = st["cash"]
    for sym, pos in st["positions"].items():
        eq += pos["qty"] * lasts.get(sym, pos["last_px"])
    return round(eq, 4)


def _fill(f, st: dict, ledger: Ledger, side: str, sym: str,
          px: float, qty: float, reason: str, broker=None,
          arm: str = "fast", day: str = "", tick: str = "") -> bool:
    """Execute one side. Returns False if the venue refused the order.

    A rejection is a result, not an error: a $50 cash account genuinely
    cannot recycle unsettled proceeds all day, and if that is what stops the
    fast arm then that is the finding. It goes in the ledger and the arm
    carries on."""
    bps = f.cost_bps_per_side / 1e4
    cost = abs(qty * px * bps)

    if _live(f):
        # Snapshot the market BEFORE the order goes out, so the recorded
        # midpoint is the one the order crossed rather than the one it moved.
        snap = broker.quote_snapshot(sym) if hasattr(broker, "quote_snapshot") else {}
        iid = intent_id(arm, day, tick, side, sym)
        decision_ts = datetime.now(ET).isoformat()
        if side == "buy":
            result = broker.submit({"symbol": sym, "side": "buy",
                                    "notional": round(qty * px, 2),
                                    "client_order_id": iid})
        else:
            result = broker.close(sym)
        status = str(result.get("status", "")).lower()
        if status == "duplicate_suppressed":
            # Already sent. Not an error and not a new position.
            ledger.write("fast_duplicate_suppressed", intent_id=iid, side=side,
                         symbol=sym, detail=result.get("detail"))
            return False
        if "reject" in status or "cancel" in status:
            ledger.write("fast_rejected", intent_id=iid, side=side, symbol=sym,
                         status=result.get("status"), reason=reason, **snap)
            return False
        st["cost_accrued"] = round(st.get("cost_accrued", 0.0) + cost, 6)
        if side == "buy":
            st["or_low"][sym] = st["or_low"].get(sym)
        else:
            st["or_low"].pop(sym, None)
        ledger.write("fast_order", intent_id=iid, arm=arm, side=side,
                     symbol=sym, px=round(px, 4), qty=round(qty, 6),
                     notional=round(qty * px, 2),
                     modeled_cost=round(cost, 4), reason=reason,
                     status=result.get("status"),
                     broker_order_id=result.get("broker_order_id"),
                     decision_ts=decision_ts,
                     submitted_at=result.get("submitted_at"),
                     filled_qty=result.get("filled_qty"),
                     filled_avg_price=result.get("filled_avg_price"),
                     scheduled_tick=tick, **snap)
        return True

    # simulated engine: cost is taken inside the fill price
    fill_px = px * (1 + bps) if side == "buy" else px * (1 - bps)
    gross = qty * px
    if side == "buy":
        st["cash"] -= qty * fill_px
        st["positions"][sym] = {"qty": qty, "entry_px": fill_px,
                                "last_px": px}
    else:
        pos = st["positions"].pop(sym, None)
        st["or_low"].pop(sym, None)
        st["cash"] += qty * fill_px
        if pos:
            pnl = (fill_px - pos["entry_px"]) * qty
            ledger.write("fast_close", symbol=sym, qty=round(qty, 6),
                         entry_px=round(pos["entry_px"], 4),
                         exit_px=round(fill_px, 4), pnl=round(pnl, 4),
                         reason=reason)
    st["cost_accrued"] = round(st.get("cost_accrued", 0.0) + cost, 6)
    ledger.write("fast_order", side=side, symbol=sym, px=round(fill_px, 4),
                 qty=round(qty, 6), notional=round(gross, 2),
                 modeled_cost=round(cost, 4), reason=reason)
    return True


# ---------- opening range -------------------------------------------------
def opening_range(bars, or_end: datetime) -> tuple[float, float] | None:
    """(high, low) of bars strictly before or_end. None if no bars yet."""
    window = bars[bars["t"] < or_end]
    if window.empty:
        return None
    return float(window["h"].max()), float(window["l"].min())


# ---------- the tick ------------------------------------------------------
def session_bounds(broker, now: datetime) -> tuple[datetime, datetime, str]:
    """Today's open and close in exchange time, from the exchange calendar.

    Not from constants. `OPEN_T`/`CLOSE_T` were hardcoded 9:30 and 16:00, and
    on a 13:00 half day that produced a flatten time of 15:30 — two and a half
    hours after the bell. The arm would never reach its own flatten branch,
    would go on believing the session was open until 16:00, and would carry
    positions overnight in exact violation of the invariant it advertises.

    A date list of early closes would fix this year and rot. The calendar is
    the mechanism: half days, holidays and any future change to exchange hours
    are one lookup, not a table someone has to remember to update.

    Falls back to the constants only where no calendar is reachable, and says
    which was used so the choice is never invisible in the record.
    """
    if hasattr(broker, "session_today"):
        try:
            window = broker.session_today()
        except Exception:                             # noqa: BLE001
            window = None
        if window:
            open_utc, close_utc = window
            return (open_utc.astimezone(ET), close_utc.astimezone(ET),
                    "exchange_calendar")
    return (now.replace(hour=OPEN_T.hour, minute=OPEN_T.minute,
                        second=0, microsecond=0),
            now.replace(hour=CLOSE_T.hour, minute=CLOSE_T.minute,
                        second=0, microsecond=0),
            "assumed_regular_hours")


def _exit_px(pos: dict, lasts: dict) -> float:
    """Best price we have for a position we are closing.

    In broker mode the price is bookkeeping — the exit goes through
    `close_position`, which needs no price at all — so a missing quote must
    never be the reason a liquidation does not happen.
    """
    return pos.get("last_px") or pos.get("entry_px") or 0.0


def _stand_down(cfg, f, st: dict, ledger: Ledger, broker, arm: str,
                reason: str, state_path, now) -> dict:
    """Flatten what is held, then refuse to trade until the halt is lifted.

    Runs on every halted tick, not only the first. A liquidation the venue
    refused is retried next tick rather than assumed done — the point of a
    kill switch is that it keeps being true.
    """
    today = now.date().isoformat()
    tick_key = now.strftime("%H:%M")
    closed, remaining = [], []
    for sym, pos in list(_positions(f, broker, st).items()):
        px = _exit_px(pos, {})
        if _fill(f, st, ledger, "sell", sym, px, pos["qty"],
                 f"{reason}_flatten", broker, arm, today, tick_key):
            closed.append(sym)
        else:
            remaining.append(sym)
    if reason == "halt_file":
        st["halted_by_file"] = True
    ledger.write("fast_halted", arm=arm, reason=reason, day=today,
                 flattened=closed, still_held=remaining)
    _save_state(state_path, st)
    return {"status": "halted", "reason": reason,
            "flattened": closed, "still_held": remaining}


def run_fast_once(cfg: Config, broker, now: datetime | None = None,
                  arm: str = "fast") -> dict:
    f, ledger_path, state_path = _arm(cfg, arm)
    ledger = Ledger(ledger_path)
    st = _load_state(state_path, f.start_cash)

    now = now or broker.now_et()
    today = now.date().isoformat()
    open_dt, close_dt, cal = session_bounds(broker, now)
    or_end = open_dt + timedelta(minutes=f.or_minutes)
    flat_dt = close_dt - timedelta(minutes=f.flat_minutes_before_close)

    # --- halt: stop entering, never stop exiting -------------------------
    # `.halt` is the emergency switch. Until 2026-09-04 the intraday arms did
    # not look at it at all — the slow arm stopped and these two carried on
    # trading, which is the worst possible reading of a kill switch. The
    # obvious repair is worse still: `if halted: return` at the top of this
    # function would also cancel the stop-loss, the daily loss stop and the
    # end-of-day flatten, so pulling the switch would freeze the arm holding
    # whatever it happened to hold. A halt blocks new risk; it does not
    # abandon open risk.
    #
    # So a halted tick liquidates and then stands down. Two halts, deliberately
    # not merged: the file is the human switch and lifts when the file goes;
    # the drawdown kill is the arm's own and stays until its state is reset.
    halt = ("halt_file" if cfg.halt_path.exists()
            else "drawdown_kill" if st.get("halted") else None)
    if halt:
        return _stand_down(cfg, f, st, ledger, broker, arm, halt,
                           state_path, now)

    if now < open_dt or now >= close_dt:
        return {"status": "market_closed"}

    new_day = st.get("day") != today
    # Identifies the tick within the day, so a retry of the SAME tick
    # regenerates the same intent ids while the next tick gets fresh ones.
    tick_key = now.strftime("%H:%M")

    if f.universe_mode == "most_active":
        universe = broker.most_actives(f.universe_size)
        ledger.write("universe", arm=arm, day=today, symbols=universe)
    else:
        universe = f.universe
    bars = broker.intraday_5min(universe)  # {sym: df[t,o,h,l,c]}
    lasts = {s: float(df["c"].iloc[-1]) for s, df in bars.items() if len(df)}

    if new_day:
        # If a scheduler miss ever left positions overnight, close them at
        # the first tick of the new day — the stated invariant is flat
        # overnight, and yesterday's OR stops are meaningless today.
        # (Adversarial review 2026-08-30, finding 5 — confirmed.)
        for sym, pos in list(_positions(f, broker, st).items()):
            px = lasts.get(sym, pos.get("last_px", pos["entry_px"]))
            _fill(f, st, ledger, "sell", sym, px, pos["qty"],
                  "overnight_flatten", broker, arm, today, "new_day")
        st.update({"day": today, "trades_today": 0, "stopped_today": False})
    if st.get("day_start_equity") is None or st.get("day") != today or \
       "day_equity_set" not in st:
        st["day_start_equity"] = _mark_equity(f, broker, st, lasts)
        st["day_equity_set"] = today
    if st["day_equity_set"] != today:
        st["day_start_equity"] = _mark_equity(f, broker, st, lasts)
        st["day_equity_set"] = today

    equity = _mark_equity(f, broker, st, lasts)

    # --- account-level kill switch (same rule as the slow arm) ------------
    st["high_water_mark"] = max(st.get("high_water_mark", equity), equity)
    dd = (st["high_water_mark"] - equity) / st["high_water_mark"] * 100 \
        if st["high_water_mark"] > 0 else 0.0
    if dd >= f.max_drawdown_pct:
        for sym, pos in list(_positions(f, broker, st).items()):
            _fill(f, st, ledger, "sell", sym,
                  lasts.get(sym, pos.get("last_px", pos["entry_px"])),
                  pos["qty"], "drawdown_kill", broker, arm, today, tick_key)
        st["halted"] = True
        ledger.write("fast_halt", arm=arm, reason=f"drawdown {dd:.1f}%")
        _save_state(state_path, st)
        return {"status": "killed"}

    # --- daily loss stop --------------------------------------------------
    day_pnl_pct = (equity / st["day_start_equity"] - 1) * 100 \
        if st["day_start_equity"] > 0 else 0.0
    if not st["stopped_today"] and day_pnl_pct <= -f.daily_loss_stop_pct:
        for sym, pos in list(_positions(f, broker, st).items()):
            _fill(f, st, ledger, "sell", sym, lasts[sym], pos["qty"],
                  "daily_loss_stop", broker, arm, today, tick_key)
        st["stopped_today"] = True
        ledger.write("fast_day_stop", arm=arm, day_pnl_pct=round(day_pnl_pct, 3))

    # --- end-of-day flat --------------------------------------------------
    if now >= flat_dt:
        for sym, pos in list(_positions(f, broker, st).items()):
            _fill(f, st, ledger, "sell", sym, lasts[sym], pos["qty"],
                  "eod_flat", broker, arm, today, tick_key)
    elif not st["stopped_today"] and now >= or_end:
        ors = {}
        for sym, df in bars.items():
            r = opening_range(df, or_end)
            if r:
                ors[sym] = r

        # exits: stop = back below the OR low recorded when we entered
        held = _positions(f, broker, st)
        for sym, pos in list(held.items()):
            floor = st["or_low"].get(sym, ors.get(sym, (0, 0))[1])
            if floor and sym in lasts and lasts[sym] < floor:
                _fill(f, st, ledger, "sell", sym, lasts[sym], pos["qty"],
                      "or_low_stop", broker, arm, today, tick_key)

        # entries: breakouts above OR high, ranked by strength
        held = _positions(f, broker, st)
        slots = f.top_k - len(held)
        budget_per_slot = (equity / f.top_k)
        candidates = []
        for sym, (hi, lo) in ors.items():
            if sym in held or sym not in lasts:
                continue
            if lasts[sym] < f.min_price:
                continue
            if lasts[sym] > hi > 0:
                candidates.append((sym, lasts[sym] / hi - 1, hi, lo))
        candidates.sort(key=lambda t: t[1], reverse=True)
        entered = []
        for sym, strength, hi, lo in candidates:
            if slots <= 0 or st["trades_today"] >= f.max_trades_per_day:
                break
            cash = broker.cash() if _live(f) else st["cash"]
            notional = min(budget_per_slot, cash)
            if notional < 5:  # not worth a fill under $5
                break
            qty = notional / lasts[sym]
            if not _fill(f, st, ledger, "buy", sym, lasts[sym], qty,
                         f"breakout +{strength:.2%} above OR high {hi:.2f}",
                         broker, arm, today, tick_key):
                continue
            st["or_low"][sym] = lo
            st["trades_today"] += 1
            slots -= 1
            entered.append(sym)
        ledger.write("fast_decision", arm=arm, or_levels={s: [round(h, 2), round(l, 2)]
                                                 for s, (h, l) in ors.items()},
                     candidates=[c[0] for c in candidates], entered=entered,
                     trades_today=st["trades_today"])

    equity = _mark_equity(f, broker, st, lasts)
    held = _positions(f, broker, st)
    ledger.write("fast_run", arm=arm, equity=equity, day=today,
                 session_source=cal,
                 session_close=close_dt.isoformat(),
                 cost_accrued=round(st.get("cost_accrued", 0.0), 4),
                 day_pnl_pct=round((equity / st["day_start_equity"] - 1) * 100, 3)
                 if st["day_start_equity"] > 0 else 0.0,
                 positions={s: round(p.get("market_value",
                                            p["qty"] * lasts.get(s, p.get("last_px", 0))), 2)
                            for s, p in held.items()},
                 cash=round(broker.cash() if _live(f) else st["cash"], 2))
    if not _live(f):
        for sym, pos in st["positions"].items():
            if sym in lasts:
                pos["last_px"] = lasts[sym]
    _save_state(state_path, st)
    return {"status": "ok", "equity": equity, "positions": list(held)}
