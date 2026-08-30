"""Chat interface. Answers ONLY from the ledger — it cannot invent a reason
that was not recorded at decision time. Deterministic by design: an
explanation you can audit beats a fluent one you cannot."""
from __future__ import annotations
from .config import Config
from .ledger import Ledger
from . import report

HELP = """Commands I understand:
  status            equity, positions, last run
  pnl               equity history summary
  why <SYMBOL>      the recorded reason for the latest decision on that symbol
  decisions         today's full decision table
  orders [n]        last n orders (default 10)
  evaluate          score the slow arm against pre-registered criteria
  compare           fast arm vs slow arm vs the toll booth
  fast              fast-arm status: equity, positions, today's trades
  movers            movers-arm status (most-active universe)
  report            print today's report
  help / quit
"""


def answer(cfg: Config, query: str) -> str:
    ledger = Ledger(cfg.ledger_path)
    q = query.strip().lower()

    if q in {"help", "?", ""}:
        return HELP

    if q == "status":
        run = ledger.last("run")
        if not run:
            return "No runs yet. Start with: python -m tradebot run"
        pos = run.get("positions", {})
        pos_txt = (", ".join(f"{s} ${v:,.2f}" for s, v in sorted(pos.items()))
                   or "all cash")
        halted = cfg.halt_path.exists()
        return (f"As of {run['ts'][:16]} UTC — equity ${run['equity']:,.2f}, "
                f"positions: {pos_txt}."
                + (" TRADING IS HALTED (.halt present)." if halted else ""))

    if q == "pnl":
        series = ledger.equity_series()
        if len(series) < 2:
            return "Fewer than two runs recorded; nothing to compare yet."
        first_d, first_e = series[0]
        last_d, last_e = series[-1]
        chg = (last_e / first_e - 1) * 100 if first_e > 0 else 0.0
        return (f"Equity {first_d}: ${first_e:,.2f} -> {last_d}: ${last_e:,.2f} "
                f"({chg:+.2f}%) over {len(series)} runs.")

    if q.startswith("why"):
        parts = query.split()
        if len(parts) < 2:
            return "why <SYMBOL>, e.g.: why SPY"
        sym = parts[1].upper()
        dec = ledger.last("decision")
        if not dec:
            return "No decisions recorded yet."
        d = dec["decisions"].get(sym)
        if not d:
            return f"{sym} is not in the configured universe."
        held = d.get("selected")
        verdict = (f"HOLDING {sym} (rank {d.get('rank')}, target "
                   f"${d.get('target_notional', 0):,.2f})" if held
                   else f"NOT holding {sym}")
        snap = d.get("snapshot", {})
        return (f"{verdict}. Recorded rule at decision time ({dec['ts'][:16]} UTC): "
                f"{d.get('rule')}. Signals then: price {snap.get('price')}, "
                f"200d SMA {snap.get('sma')}, 12-1 momentum {snap.get('momentum')}.")

    if q == "decisions":
        dec = ledger.last("decision")
        if not dec:
            return "No decisions recorded yet."
        lines = [f"Decision set from {dec['ts'][:16]} UTC:"]
        for sym, d in sorted(dec["decisions"].items()):
            mark = "HOLD" if d.get("selected") else "----"
            lines.append(f"  {mark} {sym}: {d.get('rule')}")
        return "\n".join(lines)

    if q.startswith("orders"):
        parts = q.split()
        n = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 10
        orders = [r for r in ledger.read() if r["type"] == "order"][-n:]
        if not orders:
            return "No orders yet."
        return "\n".join(f"{o['ts'][:16]} {o['side'].upper():4} "
                         f"${o['notional']:>8.2f} {o['symbol']} "
                         f"[{o.get('status', '?')}]" for o in orders)

    if q == "compare":
        from .compare import compare_report
        return compare_report(cfg)

    if q == "fast":
        fl = Ledger(cfg.fast_ledger_path)
        run = fl.last("fast_run")
        if not run:
            return "Fast arm has no runs yet. Start with: python -m tradebot run-fast"
        pos = run.get("positions", {})
        return (f"Fast arm as of {run['ts'][:16]} UTC — equity "
                f"${run['equity']:,.2f} (day {run.get('day_pnl_pct', 0):+.2f}%), "
                f"cash ${run.get('cash', 0):,.2f}, positions: "
                + (", ".join(f"{s} ${v:,.2f}" for s, v in sorted(pos.items()))
                   or "flat") + ".")

    if q == "movers":
        ml = Ledger(cfg.movers_ledger_path)
        run = ml.last("fast_run")
        if not run:
            return "Movers arm has no runs yet. Start with: python -m tradebot run-movers"
        uni = ml.last("universe")
        pos = run.get("positions", {})
        return (f"Movers arm as of {run['ts'][:16]} UTC — equity "
                f"${run['equity']:,.2f} (day {run.get('day_pnl_pct', 0):+.2f}%), "
                f"positions: "
                + (", ".join(f"{s} ${v:,.2f}" for s, v in sorted(pos.items()))
                   or "flat")
                + (f". Today's universe: {', '.join(uni['symbols'])}" if uni else ""))

    if q == "evaluate":
        return report.evaluate(cfg, Ledger(cfg.ledger_path))

    if q == "report":
        return report.daily_report(cfg, ledger)

    return "Didn't catch that. " + HELP


def repl(cfg: Config) -> None:
    print("tradebot chat — answers come from the ledger, nowhere else. "
          "'help' for commands, 'quit' to exit.")
    while True:
        try:
            query = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if query.lower() in {"quit", "exit", "q"}:
            break
        print(answer(cfg, query))
