"""python -m tradebot <command>"""
from __future__ import annotations
import os
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
        result = run_once(cfg, AlpacaBroker(*cfg.creds("slow")),
                          force="--force" in args)
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
        result = run_fast_once(cfg, AlpacaBroker(*cfg.creds(arm)), arm=arm)
        print(f"{arm} run: {result['status']}"
              + (f" equity ${result['equity']:.2f} "
                 f"positions {result['positions']}"
                 if result["status"] == "ok" else ""))
        return 0

    if cmd == "flatten":
        from .broker import AlpacaBroker
        try:
            from dotenv import load_dotenv
            load_dotenv(cfg.root / ".env")
        except ImportError:
            pass
        for arm in ("slow", "fast", "movers"):
            b = AlpacaBroker(*cfg.creds(arm))
            held = list(b.positions_detail())
            for sym in held:
                print(f"{arm}: closing {sym} -> {b.close(sym).get('status')}")
            if not held:
                print(f"{arm}: already flat")
        return 0

    if cmd == "verify":
        from .broker import AlpacaBroker, assert_distinct_accounts
        try:
            from dotenv import load_dotenv
            load_dotenv(cfg.root / ".env")
        except ImportError:
            pass
        brokers = {a: AlpacaBroker(*cfg.creds(a))
                   for a in ("slow", "fast", "movers")}
        numbers = assert_distinct_accounts(brokers)
        for arm, b in brokers.items():
            print(f"{arm:7} account {numbers[arm]}  equity ${b.equity():,.2f}  "
                  f"cash ${b.cash():,.2f}  positions {list(b.positions_detail())}")
        return 0

    if cmd == "session":
        try:
            from dotenv import load_dotenv
            load_dotenv(cfg.root / ".env")
        except ImportError:
            pass
        import subprocess
        from .broker import AlpacaBroker, assert_distinct_accounts
        from .session import run_session
        brokers = {a: AlpacaBroker(*cfg.creds(a))
                   for a in ("slow", "fast", "movers")}
        assert_distinct_accounts(brokers)
        hook = None
        if os.environ.get("TRADEBOT_AUTOCOMMIT") == "1":
            script = cfg.root / "scripts" / "commit_state.sh"
            hook = lambda: subprocess.run(["bash", str(script)], cwd=cfg.root)
        result = run_session(cfg, brokers, on_tick_done=hook)
        print(f"session: {result['status']} "
              f"(ran {result['ran']}, missed {result['missed']})")
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
          "[run|run-fast|run-movers|session|verify|flatten|chat|status|pnl|why SYM|decisions|report|evaluate|compare|kill|resume]")
    return 0 if cmd == "help" else 1


if __name__ == "__main__":
    raise SystemExit(main())
