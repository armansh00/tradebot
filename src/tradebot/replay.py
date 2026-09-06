"""Run the intraday arms over past sessions, through the production code.

Six days after the arms were built, the account held two fills. The slow arm
is meant to sit still; the intraday arms — the only ones that generate
observations at a useful rate — had been stood down for one reason after
another. One session per weekday for eight weeks is a slow way to learn
whether a rule does anything at all, and it is not the only way.

This module replays past sessions tick by tick through `run_fast_once` — the
same function, the same halt, the same calendar-aware flatten, the same
missing-bar exit path the live arm uses — against historical 5-minute bars,
with the modeled costs that were pre-registered. It is not live evidence: no
real fills, no queue, no settlement. It answers a narrower question, which is
whether the rule as written produces anything at all when it is allowed to
run, and it answers it in an hour rather than in November.

Two honesties this file insists on:

- **The vault boundary is respected in the output.** Sessions before the
  research cutoff are exploratory. Sessions after it are a one-shot
  confirmatory read of the strategy as registered, and no parameter may be
  changed in response to them. The report keeps the two apart.
- **The movers universe is a proxy.** The live arm screens Alpaca's
  most-actives list each morning; there is no historical screener. The
  replay ranks a fixed candidate list by prior-day dollar volume instead.
  Same rules, approximate universe, labeled as such everywhere it appears.
"""
from __future__ import annotations

import csv
import json
import shutil
import statistics
from dataclasses import dataclass, asdict
from datetime import date as _date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from .config import Config, load_config
from .fastarm import ET, run_fast_once
from .ledger import Ledger


# ------------------------------------------------------------------ the shim

class DayShim:
    """A broker that knows exactly one past day and nothing after `now`.

    Simulated fills only. Bars are cut at the current tick so the arm never
    sees a close that had not printed yet — the same look-ahead guard the
    fast-arm tests use.
    """

    def __init__(self, day, bars: dict, open_et: datetime, close_et: datetime,
                 universe: list[str]):
        self.day = day
        self._bars = bars
        self._open, self._close = open_et, close_et
        self._universe = list(universe)
        self._now = open_et

    def at(self, now: datetime):
        self._now = now
        return now

    def now_et(self):
        return self._now

    def session_today(self):
        return (self._open.astimezone(timezone.utc),
                self._close.astimezone(timezone.utc))

    def most_actives(self, n: int):
        return self._universe[:n]

    def intraday_5min(self, symbols, day=None):
        return {s: df[df["t"] <= self._now].reset_index(drop=True)
                for s, df in self._bars.items()
                if s in symbols and len(df[df["t"] <= self._now])}

    def quote_snapshot(self, symbol):
        df = self._bars.get(symbol)
        if df is None:
            return {"quote": None, "quote_error": "no bars"}
        sub = df[df["t"] <= self._now]
        if sub.empty:
            return {"quote": None, "quote_error": "no bars yet"}
        px = float(sub["c"].iloc[-1])
        return {"bid": px, "ask": px, "mid": px, "spread_bps": 0.0,
                "requested_feed": "replay", "bid_exchange": "", "ask_exchange": ""}


# --------------------------------------------------------------- one session

@dataclass
class SessionResult:
    day: str
    arm: str
    period: str                 # pre_vault | vault
    universe: str
    ticks: int
    trades: int
    gross_pnl: float
    modeled_cost: float
    net_pnl: float
    net_pct: float
    equity_start: float
    equity_end: float
    halted: bool
    day_stop: bool
    session_close: str


def tick_times(open_et: datetime, close_et: datetime, f) -> list[datetime]:
    """Same cadence the live scheduler builds: first tick once the opening
    range has closed, every `tick_minutes`, and a last one two minutes before
    the bell so the flatten always gets a turn."""
    t = open_et + timedelta(minutes=int(f.or_minutes) + 5)
    last = close_et - timedelta(minutes=2)
    out = []
    while t < last:
        out.append(t)
        t += timedelta(minutes=int(f.tick_minutes))
    out.append(last)
    return out


def replay_session(cfg: Config, arm: str, shim: DayShim, period: str) -> SessionResult:
    f = cfg.movers if arm == "movers" else cfg.fast
    ledger_path = cfg.movers_ledger_path if arm == "movers" else cfg.fast_ledger_path
    state_path = cfg.movers_state_path if arm == "movers" else cfg.fast_state_path

    before = len(Ledger(ledger_path).read()) if ledger_path.exists() else 0
    st0 = json.loads(state_path.read_text()) if state_path.exists() else {}
    eq_start = None
    ticks = 0
    for t in tick_times(shim._open, shim._close, f):
        shim.at(t)
        r = run_fast_once(cfg, shim, now=t, arm=arm)
        ticks += 1
        if eq_start is None and r.get("status") == "ok":
            st = json.loads(state_path.read_text())
            eq_start = float(st.get("day_start_equity") or r.get("equity") or f.start_cash)

    events = Ledger(ledger_path).read()[before:]
    orders = [e for e in events if e["type"] == "fast_order"]
    closes = [e for e in events if e["type"] == "fast_close"]
    st = json.loads(state_path.read_text()) if state_path.exists() else {}
    runs = [e for e in events if e["type"] == "fast_run"]
    eq_end = float(runs[-1]["equity"]) if runs else float(st.get("cash", f.start_cash))
    if eq_start is None:
        eq_start = eq_end
    gross = round(sum(float(c.get("pnl", 0.0)) for c in closes), 4)
    cost = round(sum(float(o.get("modeled_cost", 0.0)) for o in orders), 4)
    halted = bool(st.get("halted")) and not bool(st0.get("halted"))
    if st.get("halted"):
        # Record it, then release it: a replay that stops in week two yields
        # nothing, and what we want to know is how often the kill fires. The
        # count is of sessions where it fired, not sessions it stayed down.
        st["halted"] = False
        st["high_water_mark"] = eq_end
        state_path.write_text(json.dumps(st))
    return SessionResult(
        day=str(shim.day), arm=arm, period=period,
        universe=",".join(shim._universe),
        ticks=ticks, trades=len([o for o in orders if o.get("side") == "buy"]),
        gross_pnl=gross, modeled_cost=cost, net_pnl=round(gross - cost, 4),
        net_pct=round((eq_end / eq_start - 1) * 100, 4) if eq_start else 0.0,
        equity_start=round(eq_start, 4), equity_end=round(eq_end, 4),
        halted=halted, day_stop=bool(st.get("stopped_today")),
        session_close=shim._close.strftime("%H:%M"))


# ------------------------------------------------------------------ universe

def movers_proxy(daily: dict, day, candidates: list[str], n: int,
                 min_price: float) -> list[str]:
    """Top-n by prior-session dollar volume from a fixed candidate list.

    The live arm asks Alpaca's screener; there is no historical screener.
    This is the declared stand-in, and it cannot see the name that became
    the most active stock in America overnight unless it was already on the
    list. That is a real limitation and it is why every movers row in the
    output says `proxy`.
    """
    rows = []
    for sym in candidates:
        df = daily.get(sym)
        if df is None or df.empty:
            continue
        prev = df[df["d"] < day]
        if prev.empty:
            continue
        last = prev.iloc[-1]
        if float(last["c"]) < min_price:
            continue
        rows.append((float(last["c"]) * float(last.get("v", 0) or 0), sym))
    rows.sort(reverse=True)
    return [s for _, s in rows[:n]]


# ------------------------------------------------------------------ the run

def _period(day, cfg: Config) -> str:
    cut = datetime.fromisoformat(str((cfg.vault_dates or {}).get(
        "research_end", "2026-01-31"))).date()
    return "pre_vault" if day <= cut else "vault"


def replay(cfg: Config, broker, arm: str, months: int, out_dir: Path,
           log=print) -> dict:
    """Replay `arm` over the last `months` of sessions. Writes a CSV of one row
    per session and a markdown summary. Returns the summary."""
    today = datetime.now(ET).date()
    start = today - timedelta(days=int(months * 30.5))
    sessions = [s for s in broker.calendar(start, today) if s[0] < today]
    if not sessions:
        raise RuntimeError("no sessions in range")

    # An isolated config root so the replay's ledgers and state never touch
    # the live ones. Same config.yaml, different directory.
    work = out_dir / f".work-{arm}"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    shutil.copy(cfg.root / "config.yaml", work / "config.yaml")
    rcfg = load_config(work)
    for a in (rcfg.fast, rcfg.movers):
        a.fills_mode = "simulated"

    f = rcfg.movers if arm == "movers" else rcfg.fast
    daily = {}
    candidates = []
    if arm == "movers":
        candidates = list((getattr(cfg, "replay", None) or {}).get(
            "movers_candidates", []))
        if not candidates:
            raise RuntimeError("config.replay.movers_candidates is empty")
        daily = broker.daily_bars(candidates, len(sessions) + 30)

    results: list[SessionResult] = []
    for day, open_et, close_et in sessions:
        if arm == "movers":
            universe = movers_proxy(daily, day, candidates, f.universe_size, f.min_price)
        else:
            universe = list(f.universe)
        if not universe:
            continue
        bars = broker.intraday_5min(universe, day=day)
        if not bars:
            log(f"{day}: no bars, skipped")
            continue
        shim = DayShim(day, bars, open_et, close_et, universe)
        r = replay_session(rcfg, arm, shim, _period(day, cfg))
        results.append(r)
        log(f"{day} {arm:6} {r.period:9} trades={r.trades} net={r.net_pct:+.3f}% "
            f"eq={r.equity_end:.2f}{' HALT' if r.halted else ''}")

    out_dir.mkdir(parents=True, exist_ok=True)
    tag = f"{arm}-{sessions[0][0]}-to-{sessions[-1][0]}"
    csv_path = out_dir / f"{tag}.csv"
    with open(csv_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(asdict(results[0]).keys()))
        w.writeheader()
        for r in results:
            w.writerow(asdict(r))
    summary = summarize(results, arm)
    (out_dir / f"{tag}.md").write_text(render(summary, arm, sessions[0][0],
                                               sessions[-1][0], csv_path.name))
    shutil.rmtree(work, ignore_errors=True)
    return summary


# ------------------------------------------------------------------ summary

def _stats(rows: list[SessionResult]) -> dict:
    if not rows:
        return {"sessions": 0}
    pcts = [r.net_pct for r in rows]
    eq = 1.0
    peak, mdd = 1.0, 0.0
    for p in pcts:
        eq *= 1 + p / 100
        peak = max(peak, eq)
        mdd = max(mdd, (peak - eq) / peak)
    sd = statistics.pstdev(pcts) if len(pcts) > 1 else 0.0
    return {
        "sessions": len(rows),
        "sessions_with_trades": sum(1 for r in rows if r.trades),
        "trades": sum(r.trades for r in rows),
        "mean_net_pct": round(statistics.mean(pcts), 4),
        "median_net_pct": round(statistics.median(pcts), 4),
        "sd_net_pct": round(sd, 4),
        "win_rate": round(sum(1 for p in pcts if p > 0) / len(pcts), 4),
        "loss_rate": round(sum(1 for p in pcts if p < 0) / len(pcts), 4),
        "gross_pnl": round(sum(r.gross_pnl for r in rows), 4),
        "modeled_cost": round(sum(r.modeled_cost for r in rows), 4),
        "net_pnl": round(sum(r.net_pnl for r in rows), 4),
        "compounded_pct": round((eq - 1) * 100, 4),
        "max_drawdown_pct": round(mdd * 100, 4),
        "naive_annualized_sharpe": round(
            statistics.mean(pcts) / sd * (252 ** 0.5), 3) if sd > 0 else None,
        "halts": sum(1 for r in rows if r.halted),
        "day_stops": sum(1 for r in rows if r.day_stop),
    }


def summarize(results: list[SessionResult], arm: str) -> dict:
    return {
        "arm": arm,
        "universe": "proxy (prior-day dollar volume over a fixed list)"
                    if arm == "movers" else "as registered",
        "all": _stats(results),
        "pre_vault": _stats([r for r in results if r.period == "pre_vault"]),
        "vault": _stats([r for r in results if r.period == "vault"]),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def render(summary: dict, arm: str, first, last, csv_name: str) -> str:
    def block(name, s):
        if not s.get("sessions"):
            return f"### {name}\n\nno sessions\n"
        return (f"### {name} — {s['sessions']} sessions\n\n"
                f"| | |\n|---|---:|\n"
                f"| sessions with a trade | {s['sessions_with_trades']} |\n"
                f"| trades | {s['trades']} |\n"
                f"| mean net / session | {s['mean_net_pct']:+.3f}% |\n"
                f"| median net / session | {s['median_net_pct']:+.3f}% |\n"
                f"| sd | {s['sd_net_pct']:.3f}% |\n"
                f"| sessions up / down | {s['win_rate']:.0%} / {s['loss_rate']:.0%} |\n"
                f"| gross P&L | ${s['gross_pnl']:+.2f} |\n"
                f"| modeled cost | ${s['modeled_cost']:.2f} |\n"
                f"| net P&L | ${s['net_pnl']:+.2f} |\n"
                f"| compounded | {s['compounded_pct']:+.2f}% |\n"
                f"| max drawdown | {s['max_drawdown_pct']:.2f}% |\n"
                f"| naive annualized Sharpe | {s['naive_annualized_sharpe']} |\n"
                f"| kill-switch halts / day stops | {s['halts']} / {s['day_stops']} |\n")

    caveat = ("The universe is a **proxy**: the live arm screens Alpaca's "
              "most-actives list each morning and there is no historical "
              "screener, so the replay ranks a fixed candidate list by "
              "prior-day dollar volume. Same rules, approximate universe. "
              "It cannot see a name that became the most active stock in "
              "America overnight unless it was already on the list.\n\n"
              if arm == "movers" else "")
    return (f"# Replay — {arm} — {first} to {last}\n\n"
            "Past sessions run tick by tick through `run_fast_once`, the same "
            "function the live arm uses, with simulated fills at the "
            "pre-registered modeled cost. **Not live evidence**: no real fills, "
            "no queue, no settlement. It answers whether the rule as written "
            "does anything when it is allowed to run.\n\n"
            + caveat +
            "`pre_vault` sessions are exploratory. `vault` sessions are a "
            "one-shot confirmatory read of the strategy as registered — no "
            "parameter may be changed in response to them, and this replay "
            "is not to be rerun with different settings over the same "
            "window.\n\n"
            "A kill-switch halt is recorded and then released so the sample "
            "continues; the count is what matters.\n\n"
            + block("All", summary["all"]) + "\n"
            + block("Pre-vault (exploratory)", summary["pre_vault"]) + "\n"
            + block("Vault (confirmatory, one shot)", summary["vault"]) + "\n"
            f"Per-session rows: `{csv_name}`.\n")
