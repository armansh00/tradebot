"""Two-arm comparison: slow (dual momentum, paper account) vs fast (intraday
ORB, simulated fills with explicit costs). Pre-registered verdict."""
from __future__ import annotations
import math
from .config import Config
from .ledger import Ledger


def _daily_equity(records: list[dict], run_type: str) -> list[tuple[str, float]]:
    """Last equity snapshot per calendar day."""
    by_day: dict[str, float] = {}
    for r in records:
        if r["type"] == run_type and "equity" in r:
            by_day[r["ts"][:10] if run_type == "run" else r.get("day", r["ts"][:10])] \
                = r["equity"]
    return sorted(by_day.items())


def _stats(series: list[tuple[str, float]]) -> dict:
    eq = [e for _, e in series]
    if len(eq) < 2 or eq[0] <= 0:
        return {"n": len(eq), "net_pct": 0.0, "sharpe": 0.0, "tstat": 0.0,
                "max_dd": 0.0}
    rets = [eq[i] / eq[i - 1] - 1 for i in range(1, len(eq)) if eq[i - 1] > 0]
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1) if len(rets) > 1 else 0
    sd = math.sqrt(var)
    sharpe = mean / sd * math.sqrt(252) if sd > 0 else 0.0
    years = len(rets) / 252
    hwm, mdd = eq[0], 0.0
    for e in eq:
        hwm = max(hwm, e)
        mdd = max(mdd, (hwm - e) / hwm * 100 if hwm > 0 else 0)
    return {"n": len(eq), "net_pct": (eq[-1] / eq[0] - 1) * 100,
            "sharpe": sharpe, "tstat": sharpe * math.sqrt(years) if years > 0 else 0,
            "max_dd": mdd}


def _arm_row(recs: list[dict]) -> dict:
    orders = [r for r in recs if r["type"] == "fast_order"]
    closes = [r for r in recs if r["type"] == "fast_close"]
    return {"orders": len(orders),
            "costs": sum(r.get("modeled_cost", 0.0) for r in orders),
            "closes": len(closes),
            "wins": sum(1 for r in closes if r.get("pnl", 0) > 0)}


def compare_report(cfg: Config) -> str:
    slow_recs = Ledger(cfg.ledger_path).read()
    fast_recs = Ledger(cfg.fast_ledger_path).read()
    movers_recs = Ledger(cfg.movers_ledger_path).read()
    slow = _daily_equity(slow_recs, "run")
    fast = _daily_equity(fast_recs, "fast_run")
    movers = _daily_equity(movers_recs, "fast_run")

    lines = ["# Arm comparison — patience vs frequency vs heat", ""]
    if not slow and not fast and not movers:
        return "\n".join(lines + ["No data in any ledger yet."])

    # restrict to the overlapping window of the arms that have data
    starts = [series[0][0] for series in (slow, fast, movers) if series]
    if len(starts) > 1:
        start = max(starts)
        slow = [x for x in slow if x[0] >= start]
        fast = [x for x in fast if x[0] >= start]
        movers = [x for x in movers if x[0] >= start]
        lines.append(f"Common window starts {start}.")
        lines.append("")

    s, f, m = _stats(slow), _stats(fast), _stats(movers)
    fa, ma = _arm_row(fast_recs), _arm_row(movers_recs)
    slow_orders = len([r for r in slow_recs if r["type"] == "order"])

    lines += ["| Arm | Days | Net return | Sharpe | t-stat | Max DD | Orders | Costs paid |",
              "|---|---|---|---|---|---|---|---|",
              f"| Slow (dual momentum) | {s['n']} | {s['net_pct']:+.2f}% | "
              f"{s['sharpe']:.2f} | {s['tstat']:.2f} | {s['max_dd']:.2f}% | "
              f"{slow_orders} | ~$0 |",
              f"| Fast (ORB, fixed universe) | {f['n']} | {f['net_pct']:+.2f}% | "
              f"{f['sharpe']:.2f} | {f['tstat']:.2f} | {f['max_dd']:.2f}% | "
              f"{fa['orders']} | ${fa['costs']:.2f} |",
              f"| Movers (ORB, most-active) | {m['n']} | {m['net_pct']:+.2f}% | "
              f"{m['sharpe']:.2f} | {m['tstat']:.2f} | {m['max_dd']:.2f}% | "
              f"{ma['orders']} | ${ma['costs']:.2f} |",
              ""]
    for label, arm in (("Fast", fa), ("Movers", ma)):
        if arm["closes"]:
            lines.append(f"{label} arm round trips: {arm['closes']}, win rate "
                         f"{arm['wins'] / arm['closes']:.0%}, costs "
                         f"${arm['costs']:.2f} "
                         f"({arm['costs'] / 50.0 * 100:.1f}% of starting book).")
    lines.append("")

    fe = cfg.fast_evaluation
    weeks = min(s["n"], f["n"]) / 5.0 if (slow and fast) else 0.0
    checks = {
        f"min {fe.min_weeks} weeks of overlap": weeks >= fe.min_weeks,
        "fast arm net positive": (f["net_pct"] > 0)
            if fe.require_net_positive else True,
        "fast beats slow net of costs": (f["net_pct"] > s["net_pct"])
            if fe.must_beat_slow_arm else True,
    }
    lines.append("## Pre-registered verdict (fast arm)\n")
    for name, ok in checks.items():
        lines.append(f"- [{'PASS' if ok else 'FAIL'}] {name}")
    verdict = "FAST ARM WINS" if all(checks.values()) else \
        ("INSUFFICIENT DATA" if weeks < fe.min_weeks else "SLOW ARM WINS")
    lines += ["", f"**Fast-vs-slow verdict: {verdict}**", ""]

    if movers:
        me = cfg.movers_evaluation
        mweeks = min(x for x in (s["n"], f["n"], m["n"]) if x) / 5.0 \
            if (slow and fast and movers) else 0.0
        mchecks = {
            f"min {me.min_weeks} weeks of overlap": mweeks >= me.min_weeks,
            "movers arm net positive": (m["net_pct"] > 0)
                if me.require_net_positive else True,
            "movers beats BOTH other arms net of costs":
                (m["net_pct"] > s["net_pct"] and m["net_pct"] > f["net_pct"])
                if me.must_beat_both_arms else True,
        }
        lines.append("## Pre-registered verdict (movers arm)\n")
        for name, ok in mchecks.items():
            lines.append(f"- [{'PASS' if ok else 'FAIL'}] {name}")
        mv = "MOVERS ARM WINS" if all(mchecks.values()) else \
            ("INSUFFICIENT DATA" if mweeks < me.min_weeks
             else "ATTENTION TAX CONFIRMED")
        lines += ["", f"**Movers verdict: {mv}**", ""]

    lines += ["",
              "Same caveat as always: at these sample sizes the t-statistics "
              "above are the honest measure of how much this comparison can "
              "prove. Costs, however, are not estimates — they are counted."]
    return "\n".join(lines)
