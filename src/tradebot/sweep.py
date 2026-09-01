"""Replay the opening-range rule at many entry thresholds over past days.

Why this is a replay and not eight more live arms: eight weeks gives a
standard error on the Sharpe ratio of roughly 2.5, so the best of k worthless
arms still looks excellent — about 3.6 at k = 8. Racing thresholds live and
keeping the winner is a machine for manufacturing false positives, and it
costs eight weeks to do it. The same question can be asked against recorded
bars in seconds, as many times as you like, without touching the running
experiment. See DESIGN-threshold-sweep.md.

The reported statistic is deliberately not "which threshold won". It is
whether net return moves *monotonically* with the threshold — one hypothesis
across the whole grid, tested by an exact permutation test on Spearman's rho.
A real effect ("bigger breakouts pay better") shows up as an ordering. Noise
does not have an ordering.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field

import pandas as pd


@dataclass
class DayResult:
    date: str
    threshold_pct: float
    net_return_pct: float
    trades: int
    gross_return_pct: float
    cost_pct: float


@dataclass
class ThresholdSummary:
    threshold_pct: float
    days: int
    trades: int
    mean_daily_net_pct: float
    mean_daily_gross_pct: float
    total_net_pct: float
    win_rate: float
    daily: list[float] = field(default_factory=list)


def replay_day(bars: dict[str, pd.DataFrame], *, or_minutes: int, top_k: int,
               threshold_pct: float, cost_bps_per_side: float,
               max_trades: int, daily_loss_stop_pct: float,
               flat_minutes_before_close: int, min_price: float,
               start_cash: float = 50.0,
               log: list | None = None) -> tuple[float, int, float]:
    """One day, one threshold. Returns (net return %, trades, gross return %).

    Same rules as the live arm: buy above the opening-range high by at least
    `threshold_pct`, ranked by breakout strength; stop out below the opening
    range low; flat before the bell; per-day trade cap and loss stop.

    Pass `log` to collect ("buy"|"sell", symbol) in order — the equivalence
    test against the live engine reads it.
    """
    if not bars:
        return 0.0, 0, 0.0
    times = sorted({t for df in bars.values() for t in df["t"]})
    if not times:
        return 0.0, 0, 0.0
    open_t, close_t = times[0], times[-1]
    or_end = open_t + pd.Timedelta(minutes=or_minutes)
    flat_at = close_t - pd.Timedelta(minutes=flat_minutes_before_close)

    ors: dict[str, tuple[float, float]] = {}
    for sym, df in bars.items():
        w = df[df["t"] < or_end]
        if not w.empty:
            ors[sym] = (float(w["h"].max()), float(w["l"].min()))

    cash, positions, trades, gross_cost = start_cash, {}, 0, 0.0
    bps = cost_bps_per_side / 1e4
    stopped = False

    def price(sym, t):
        df = bars[sym]
        row = df[df["t"] == t]
        return float(row["c"].iloc[0]) if len(row) else None

    def equity(t):
        eq = cash
        for sym, p in positions.items():
            px = price(sym, t) or p["entry"]
            eq += p["qty"] * px
        return eq

    def sell(sym, t):
        nonlocal cash, gross_cost
        px = price(sym, t)
        if px is None:
            return
        p = positions.pop(sym)
        cash += p["qty"] * px * (1 - bps)
        gross_cost += p["qty"] * px * bps
        if log is not None:
            log.append(("sell", sym))

    for t in times:
        if t < or_end:
            continue
        day_pnl = (equity(t) / start_cash - 1) * 100
        if not stopped and day_pnl <= -daily_loss_stop_pct:
            for sym in list(positions):
                sell(sym, t)
            stopped = True
        if t >= flat_at:
            for sym in list(positions):
                sell(sym, t)
            continue
        if stopped:
            continue

        for sym in list(positions):
            px = price(sym, t)
            if px is not None and sym in ors and px < ors[sym][1]:
                sell(sym, t)

        slots = top_k - len(positions)
        if slots <= 0 or trades >= max_trades:
            continue
        budget = equity(t) / top_k
        candidates = []
        for sym, (hi, lo) in ors.items():
            if sym in positions or hi <= 0:
                continue
            px = price(sym, t)
            if px is None or px < min_price:
                continue
            strength = px / hi - 1
            if strength >= threshold_pct / 100:
                candidates.append((strength, sym, px))
        candidates.sort(reverse=True)
        for strength, sym, px in candidates:
            if slots <= 0 or trades >= max_trades:
                break
            notional = min(budget, cash)
            if notional < 5:
                break
            qty = notional / (px * (1 + bps))
            cash -= notional
            gross_cost += qty * px * bps
            positions[sym] = {"qty": qty, "entry": px * (1 + bps)}
            if log is not None:
                log.append(("buy", sym))
            trades += 1
            slots -= 1

    final = equity(close_t)
    net = (final / start_cash - 1) * 100
    return round(net, 4), trades, round(net + gross_cost / start_cash * 100, 4)


def spearman_rho(x: list[float], y: list[float]) -> float:
    def rank(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        for pos, i in enumerate(order):
            r[i] = pos + 1
        return r
    rx, ry = rank(x), rank(y)
    n = len(x)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    return num / den if den else 0.0


def exact_permutation_p(x: list[float], y: list[float]) -> float:
    """Two-sided exact p-value for Spearman's rho.

    The grid is small enough (7! = 5040) to enumerate every ordering rather
    than lean on an asymptotic approximation that does not hold at n = 7.
    """
    observed = abs(spearman_rho(x, y))
    perms = list(itertools.permutations(y))
    hits = sum(1 for p in perms if abs(spearman_rho(x, list(p))) >= observed - 1e-12)
    return hits / len(perms)


def summarize(results: list[DayResult]) -> list[ThresholdSummary]:
    out = []
    for thr in sorted({r.threshold_pct for r in results}):
        rows = [r for r in results if r.threshold_pct == thr]
        daily = [r.net_return_pct for r in rows]
        gross = [r.gross_return_pct for r in rows]
        traded = [r for r in rows if r.trades]
        total = 1.0
        for d in daily:
            total *= (1 + d / 100)
        out.append(ThresholdSummary(
            threshold_pct=thr, days=len(rows),
            trades=sum(r.trades for r in rows),
            mean_daily_net_pct=round(sum(daily) / len(daily), 4) if daily else 0.0,
            mean_daily_gross_pct=round(sum(gross) / len(gross), 4) if gross else 0.0,
            total_net_pct=round((total - 1) * 100, 3),
            win_rate=round(sum(1 for r in traded if r.net_return_pct > 0)
                           / len(traded), 3) if traded else 0.0,
            daily=daily))
    return out


def report(summaries: list[ThresholdSummary]) -> str:
    thresholds = [s.threshold_pct for s in summaries]
    means = [s.mean_daily_net_pct for s in summaries]
    rho = spearman_rho(thresholds, means)
    p = exact_permutation_p(thresholds, means)

    gross_means = [s.mean_daily_gross_pct for s in summaries]
    rho_gross = spearman_rho(thresholds, gross_means)
    p_gross = exact_permutation_p(thresholds, gross_means)

    lines = ["| threshold | days | trades | mean daily gross % | mean daily net % "
             "| total net % | win rate |",
             "|---:|---:|---:|---:|---:|---:|---:|"]
    for s in summaries:
        lines.append(f"| {s.threshold_pct:.2f}% | {s.days} | {s.trades} | "
                     f"{s.mean_daily_gross_pct:+.4f} | "
                     f"{s.mean_daily_net_pct:+.4f} | {s.total_net_pct:+.3f} | "
                     f"{s.win_rate:.2f} |")
    lines += [
        "",
        f"Spearman rho (threshold vs mean daily net) = {rho:+.3f}, "
        f"exact permutation p = {p:.4f}",
        f"Same test on GROSS return (costs removed) = {rho_gross:+.3f}, "
        f"p = {p_gross:.4f}",
        "",
        "Read the two together. Trading less always loses less to costs, so a "
        "positive ordering on net that vanishes on gross is the cost gradient "
        "wearing a signal's clothes, not evidence that bigger breakouts pay. "
        "Note also that the grid is nested — a 2% breakout is also a 1% "
        "breakout — so the arms are heavily correlated and the permutation "
        "test's independence assumption is generous. Treat the p-value as an "
        "optimistic bound.",
        "",
    ]
    if p < 0.05 and rho > 0:
        lines.append("Ordering is monotone increasing and would not arise this "
                     "often by chance. Worth a pre-registered live test of ONE "
                     "threshold — not of the whole grid.")
    elif p < 0.05 and rho < 0:
        lines.append("Ordering is monotone decreasing: tighter thresholds did "
                     "better. Note that this is the direction transaction costs "
                     "alone would produce, so check the gross column before "
                     "reading it as signal.")
    else:
        lines.append("No ordering. The best row in this table is the best row "
                     "in a table of noise; do not promote it, and do not report "
                     "its return as an estimate of anything.")
    return "\n".join(lines)
