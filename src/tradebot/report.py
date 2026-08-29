"""Daily markdown report + evaluation with honest statistics."""
from __future__ import annotations
import math
from .config import Config
from .ledger import Ledger


def daily_report(cfg: Config, ledger: Ledger) -> str:
    run = ledger.last("run")
    if not run:
        return "No runs recorded yet."
    date = run["ts"][:10]
    lines = [f"# tradebot daily report — {date}", ""]
    lines.append(f"**Equity:** ${run['equity']:,.2f}"
                 + (f"  ({run['day_change_pct']:+.2f}% vs previous run)"
                    if run.get("day_change_pct") is not None else ""))
    lines.append("")
    pos = run.get("positions", {})
    lines.append("## Positions")
    if pos:
        lines.append("")
        lines.append("| Symbol | Market value |")
        lines.append("|---|---|")
        for sym, mv in sorted(pos.items()):
            lines.append(f"| {sym} | ${mv:,.2f} |")
    else:
        lines.append("\nAll cash.")
    lines.append("")
    decision = ledger.last("decision")
    if decision and decision["ts"][:10] == date:
        lines.append("## Today's decisions\n")
        for sym, d in sorted(decision["decisions"].items()):
            mark = "HOLD" if d.get("selected") else "skip"
            lines.append(f"- **{sym}** [{mark}] — {d.get('rule', '')}")
        lines.append("")
    orders = [r for r in ledger.read()
              if r["type"] == "order" and r["ts"][:10] == date]
    lines.append("## Orders")
    if orders:
        lines.append("")
        for o in orders:
            lines.append(f"- {o['side'].upper()} ${o['notional']:.2f} "
                         f"{o['symbol']} — {o.get('status', '?')}")
    else:
        lines.append("\nNo trades today (target portfolio unchanged).")
    lines.append("")
    return "\n".join(lines)


def evaluate(cfg: Config, ledger: Ledger) -> str:
    """Compare paper performance against the PRE-REGISTERED criteria in
    config.yaml, and say plainly what the sample size can and cannot support."""
    ev = cfg.evaluation
    series = ledger.equity_series()
    lines = ["# Paper-phase evaluation", ""]
    if len(series) < 2:
        return "\n".join(lines + ["Not enough runs to evaluate. Keep it running."])

    dates = [d for d, _ in series]
    equities = [e for _, e in series]
    weeks = max((len(series) - 1) / 5.0, 0.0)  # trading days -> weeks
    rets = [(equities[i] / equities[i - 1] - 1) for i in range(1, len(equities))
            if equities[i - 1] > 0]
    net_pct = (equities[-1] / equities[0] - 1) * 100 if equities[0] > 0 else 0.0

    hwm, max_dd = equities[0], 0.0
    for e in equities:
        hwm = max(hwm, e)
        if hwm > 0:
            max_dd = max(max_dd, (hwm - e) / hwm * 100)

    mean = sum(rets) / len(rets) if rets else 0.0
    var = (sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
           if len(rets) > 1 else 0.0)
    sd = math.sqrt(var)
    sharpe = (mean / sd * math.sqrt(252)) if sd > 0 else 0.0
    years = len(rets) / 252.0
    tstat = sharpe * math.sqrt(years) if years > 0 else 0.0

    overrides = len([r for r in ledger.read() if r["type"] == "manual_override"])

    lines += [
        f"- Period: {dates[0]} to {dates[-1]} ({len(series)} runs, ~{weeks:.1f} weeks)",
        f"- Net P&L: {net_pct:+.2f}%",
        f"- Max drawdown: {max_dd:.2f}%",
        f"- Annualized Sharpe (point estimate): {sharpe:.2f}",
        f"- t-statistic on that Sharpe: {tstat:.2f} "
        f"(needs ~2.0 for conventional significance)",
        f"- Manual overrides: {overrides}",
        "",
    ]

    checks = {
        f"min {ev.min_weeks} weeks of data": weeks >= ev.min_weeks,
        "net positive": (net_pct > 0) if ev.require_net_positive else True,
        f"max drawdown < {ev.max_drawdown_pct}%": max_dd < ev.max_drawdown_pct,
        f"manual overrides <= {ev.max_manual_overrides}":
            overrides <= ev.max_manual_overrides,
    }
    for name, ok in checks.items():
        lines.append(f"- [{'PASS' if ok else 'FAIL'}] {name}")
    lines.append("")

    if sharpe > 0 and years > 0:
        need_years = (2.0 / sharpe) ** 2 if sharpe > 0 else float("inf")
        lines.append(
            f"Honest caveat: at this Sharpe, distinguishing skill from luck at "
            f"t=2 needs ~{need_years:.1f} years of data; you have {years:.2f}. "
            f"A PASS below clears the pre-registered operational bar — it is "
            f"not statistical proof of edge, and no short test can be.")
        lines.append("")
    verdict = "PASS" if all(checks.values()) else "NOT YET"
    lines.append(f"**Pre-registered verdict: {verdict}**")
    if verdict == "PASS":
        lines.append("\nThe $50 may now go live — as an execution-quality test "
                     "(fills, slippage vs paper), not a performance claim.")
    return "\n".join(lines)
