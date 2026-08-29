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


def compare_report(cfg: Config) -> str:
    slow_recs = Ledger(cfg.ledger_path).read()
    fast_recs = Ledger(cfg.fast_ledger_path).read()
    slow = _daily_equity(slow_recs, "run")
    fast = _daily_equity(fast_recs, "fast_run")

    lines = ["# Two-arm comparison — slow vs fast", ""]
    if not slow and not fast:
        return "\n".join(lines + ["No data in either ledger yet."])

    # restrict to the overlapping window so the race is fair
    if slow and fast:
        start = max(slow[0][0], fast[0][0])
        slow = [x for x in slow if x[0] >= start]
        fast = [x for x in fast if x[0] >= start]
        lines.append(f"Common window starts {start}.")
        lines.append("")

    s, f = _stats(slow), _stats(fast)
    fast_orders = [r for r in fast_recs if r["type"] == "fast_order"]
    fast_costs = sum(r.get("modeled_cost", 0.0) for r in fast_orders)
    fast_closes = [r for r in fast_recs if r["type"] == "fast_close"]
    wins = sum(1 for r in fast_closes if r.get("pnl", 0) > 0)
    slow_orders = len([r for r in slow_recs if r["type"] == "order"])

    lines += ["| | Slow arm (dual momentum) | Fast arm (intraday ORB) |",
              "|---|---|---|",
              f"| Days of data | {s['n']} | {f['n']} |",
              f"| Net return | {s['net_pct']:+.2f}% | {f['net_pct']:+.2f}% |",
              f"| Annualized Sharpe | {s['sharpe']:.2f} | {f['sharpe']:.2f} |",
              f"| t-statistic | {s['tstat']:.2f} | {f['tstat']:.2f} |",
              f"| Max drawdown | {s['max_dd']:.2f}% | {f['max_dd']:.2f}% |",
              f"| Orders placed | {slow_orders} | {len(fast_orders)} |",
              f"| Modeled costs paid | ~$0 (4-ish trades/mo) "
              f"| ${fast_costs:.2f} |",
              ""]
    if fast_closes:
        lines.append(f"Fast arm round trips: {len(fast_closes)}, "
                     f"win rate {wins / len(fast_closes):.0%}, "
                     f"total costs ${fast_costs:.2f} "
                     f"({fast_costs / cfg.fast.start_cash * 100:.1f}% of the "
                     f"starting book so far).")
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
    lines += ["", f"**{verdict}**", "",
              "Same caveat as always: at these sample sizes the t-statistics "
              "above are the honest measure of how much this comparison can "
              "prove. Costs, however, are not estimates — they are counted."]
    return "\n".join(lines)
