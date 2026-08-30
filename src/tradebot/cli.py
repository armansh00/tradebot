"""python -m tradebot <command>"""
from __future__ import annotations
import sys
from .config import load_config
from .ledger import Ledger


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    cmd = args[0] if args else "help"
    cfg = load_config()

    if cmd == "run":
        try:
            from dotenv import load_dotenv
            load_dotenv(cfg.root / ".env")
        except ImportError:
            pass
        from .broker import AlpacaBroker
        from .run import run_once
        result = run_once(cfg, AlpacaBroker(), force="--force" in args)
        print(f"run: {result['status']}")
        for o in result.get("orders", []):
            print(f"  {o['side'].upper()} ${o['notional']:.2f} {o['symbol']} "
                  f"[{o.get('status')}]")
        for o in result.get("rejected", []):
            print(f"  REJECTED {o['symbol']}: {o['rejected_reason']}")
        return 0

    if cmd in {"run-fast", "run-movers"}:
        try:
            from dotenv import load_dotenv
            load_dotenv(cfg.root / ".env")
        except ImportError:
            pass
        from .broker import AlpacaBroker
        from .fastarm import run_fast_once
        arm = "movers" if cmd == "run-movers" else "fast"
        result = run_fast_once(cfg, AlpacaBroker(), arm=arm)
        print(f"{arm} run: {result['status']}"
              + (f" equity ${result['equity']:.2f} "
                 f"positions {result['positions']}"
                 if result["status"] == "ok" else ""))
        return 0

    if cmd == "compare":
        from .compare import compare_report
        print(compare_report(cfg))
        return 0

    if cmd == "chat":
        from .chat import repl
        repl(cfg)
        return 0

    if cmd in {"status", "pnl", "decisions", "report", "evaluate"} or cmd == "why":
        from .chat import answer
        print(answer(cfg, " ".join(args)))
        return 0

    if cmd == "kill":
        from .risk import RiskManager
        RiskManager(cfg, Ledger(cfg.ledger_path)).halt("manual kill via CLI")
        print("Halted. No further trading until: python -m tradebot resume")
        return 0

    if cmd == "resume":
        from .risk import RiskManager
        RiskManager(cfg, Ledger(cfg.ledger_path)).clear_halt()
        print("Halt cleared.")
        return 0

    print("Usage: python -m tradebot "
          "[run|run-fast|run-movers|chat|status|pnl|why SYM|decisions|report|evaluate|compare|kill|resume]")
    return 0 if cmd == "help" else 1


if __name__ == "__main__":
    raise SystemExit(main())
