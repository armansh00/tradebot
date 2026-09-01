"""python -m tradebot <command>"""
from __future__ import annotations
import os
import sys
from .config import load_config
from .ledger import Ledger



def _build_brokers(cfg, ledger=None):
    """One broker per arm, degrading to simulated fills rather than refusing
    to trade.

    Broker fills need a separate paper account per arm; without one the arms
    would pool into a single book and every comparison would be quietly
    wrong. But a missing secret must not silently cancel a trading day
    either. So: if an intraday arm has no credentials of its own, or resolves
    to the same account as another arm, that arm falls back to the simulated
    engine it used before 2026-08-31 and says so in the ledger. The
    experiment continues on the weaker method, visibly, instead of stopping.
    """
    from .broker import AlpacaBroker
    slow = AlpacaBroker(*cfg.creds("slow"))
    brokers = {"slow": slow}
    seen = {slow.account_number(): "slow"}
    for arm in ("fast", "movers"):
        arm_cfg = getattr(cfg, arm)
        try:
            b = AlpacaBroker(*cfg.creds(arm))
            number = b.account_number()
            if number in seen:
                raise RuntimeError(
                    f"shares account {number} with the {seen[number]} arm")
            seen[number] = arm
            brokers[arm] = b
        except Exception as exc:
            arm_cfg.fills_mode = "simulated"
            brokers[arm] = slow          # market data only; no orders sent
            msg = f"{arm}: no separate account ({exc}) -> simulated fills"
            print(f"WARNING {msg}")
            if ledger is not None:
                ledger.write("fills_mode_fallback", arm=arm, reason=str(exc))
    return brokers


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

    if cmd == "sweep":
        try:
            from dotenv import load_dotenv
            load_dotenv(cfg.root / ".env")
        except ImportError:
            pass
        import datetime as _dt
        from .broker import AlpacaBroker
        from .sweep import DayResult, replay_day, report, summarize
        broker = AlpacaBroker(*cfg.creds("fast"))
        f = cfg.fast
        thresholds = cfg.sweep["thresholds_pct"]
        days = broker.trading_days(int(cfg.sweep["lookback_days"]))
        results = []
        for day in days:
            bars = broker.intraday_5min(f.universe, day=day)
            if not bars:
                continue
            for thr in thresholds:
                net, trades, gross = replay_day(
                    bars, or_minutes=f.or_minutes, top_k=f.top_k,
                    threshold_pct=thr, cost_bps_per_side=f.cost_bps_per_side,
                    max_trades=f.max_trades_per_day,
                    daily_loss_stop_pct=f.daily_loss_stop_pct,
                    flat_minutes_before_close=f.flat_minutes_before_close,
                    min_price=f.min_price, start_cash=f.start_cash)
                results.append(DayResult(str(day), thr, net, trades, gross,
                                         round(gross - net, 4)))
        if not results:
            print("no bar data returned for the lookback window")
            return 1
        text = report(summarize(results))
        header = (f"# Threshold sweep — {len(days)} sessions "
                  f"({days[0]} to {days[-1]})\n\n"
                  f"Universe: {', '.join(f.universe)}\n"
                  f"Opening range {f.or_minutes} min, top {f.top_k}, "
                  f"cost {f.cost_bps_per_side} bps/side, "
                  f"max {f.max_trades_per_day} trades/day.\n\n"
                  "Replay of a pre-registered grid, not a live race. "
                  "See DESIGN-threshold-sweep.md.\n\n")
        out_dir = cfg.root / "sweeps"
        out_dir.mkdir(exist_ok=True)
        path = out_dir / f"{_dt.date.today().isoformat()}-thresholds.md"
        path.write_text(header + text + "\n")
        print(header + text)
        print(f"\nwritten to {path.relative_to(cfg.root)}")
        return 0

    if cmd == "rotate":
        try:
            from dotenv import load_dotenv
            load_dotenv(cfg.root / ".env")
        except ImportError:
            pass
        import datetime as _dt
        from .broker import AlpacaBroker
        from .reversal import daily_returns, report, rotate
        broker = AlpacaBroker(*cfg.creds("fast"))
        # Reuse the universes already fixed in config rather than picking new
        # names now — choosing a stock list after seeing history is the
        # cheapest way to manufacture a result.
        symbols = sorted(set(cfg.fast.universe) | set(cfg.universe))
        closes = broker.daily_closes(symbols, 500)
        rets = daily_returns(closes)
        results = [rotate(rets, direction=d, k=k,
                          cost_bps_per_side=cfg.fast.cost_bps_per_side)
                   for d in ("losers", "winners") for k in (1, 2, 3)]
        text = report(results, symbols, cfg.fast.cost_bps_per_side)
        header = (f"# Rotation test — buy yesterday's losers vs winners\n\n"
                  f"{len(rets)} trading days of daily closes.\n"
                  "Hypothesis under test: a stock that fell yesterday is "
                  "likelier to rise today, so rotating between names compounds "
                  "many small edges. Short-term reversal, Jegadeesh (1990) and "
                  "Lehmann (1990).\n\n")
        out = cfg.root / "sweeps"
        out.mkdir(exist_ok=True)
        path = out / f"{_dt.date.today().isoformat()}-rotation.md"
        path.write_text(header + text + "\n")
        print(header + text)
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
        brokers = _build_brokers(cfg)
        for arm in ("slow", "fast", "movers"):
            b = brokers[arm]
            mode = "broker" if arm == "slow" else getattr(cfg, arm).fills_mode
            print(f"{arm:7} account {b.account_number()}  "
                  f"equity ${b.equity():,.2f}  cash ${b.cash():,.2f}  "
                  f"fills={mode}  positions {list(b.positions_detail())}")
        return 0

    if cmd == "session":
        try:
            from dotenv import load_dotenv
            load_dotenv(cfg.root / ".env")
        except ImportError:
            pass
        import subprocess
        from .session import run_session
        brokers = _build_brokers(cfg, Ledger(cfg.ledger_path))
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
          "[run|run-fast|run-movers|session|verify|flatten|sweep|rotate|chat|status|pnl|why SYM|decisions|report|evaluate|compare|kill|resume]")
    return 0 if cmd == "help" else 1


if __name__ == "__main__":
    raise SystemExit(main())
