"""The daily tick: fetch -> signal -> decide -> risk-check -> execute -> log.
Designed to run once per trading day; safe to re-run (idempotent per day)."""
from __future__ import annotations
from datetime import datetime, timezone
from .config import Config
from .ledger import Ledger
from .risk import RiskManager
from . import signals, strategy, report


def run_once(cfg: Config, broker, force: bool = False) -> dict:
    ledger = Ledger(cfg.ledger_path)
    risk = RiskManager(cfg, ledger)
    today = datetime.now(timezone.utc).date().isoformat()

    if risk.halted():
        ledger.write("run_skipped", reason="halted",
                     detail=cfg.halt_path.read_text().strip())
        return {"status": "halted"}
    if not force and ledger.last_run_date() == today:
        return {"status": "already_ran_today"}
    if not force and not broker.market_open():
        ledger.write("run_skipped", reason="market_closed")
        return {"status": "market_closed"}

    equity = risk.book_equity(broker.equity())
    positions = broker.positions()

    # drawdown kill switch BEFORE any new orders
    if risk.check_drawdown(equity):
        # Close EVERYTHING, shorts included — a kill switch that leaves a
        # short open has not killed anything.
        # (Adversarial review 2026-08-30, finding 2 — confirmed.)
        flatten = [{"symbol": s, "side": "sell" if mv > 0 else "buy",
                    "notional": abs(mv), "from_notional": mv, "to_notional": 0.0}
                   for s, mv in positions.items() if mv != 0]
        results = [broker.submit(o) for o in flatten]
        for r in results:
            ledger.write("order", **r, context="drawdown_flatten")
        ledger.write("run", equity=equity, positions={}, halted=True)
        return {"status": "killed", "orders": results}

    st = cfg.strategy
    closes = broker.daily_closes(cfg.universe,
                                 days=st.mom_lookback_days + st.mom_skip_days + 10)
    snaps = {sym: signals.snapshot(closes[sym], st.sma_days,
                                   st.mom_lookback_days, st.mom_skip_days)
             for sym in cfg.universe if sym in closes and len(closes[sym]) > 0}

    decision = strategy.decide(cfg, snaps, equity)
    ledger.write("decision", equity=equity, targets=decision["targets"],
                 decisions=decision["decisions"])

    orders = strategy.diff_orders(cfg, decision["targets"], positions)
    approved, rejected = risk.filter_orders(orders)
    for o in rejected:
        ledger.write("order_rejected", **o)
    results = [broker.submit(o) for o in approved]
    for r in results:
        ledger.write("order", **r)

    prev = ledger.equity_series()
    day_change = None
    if len(prev) >= 1 and prev[-1][1] > 0:
        day_change = (equity / prev[-1][1] - 1) * 100
    ledger.write("run", equity=equity,
                 positions=broker.positions() if results else positions,
                 day_change_pct=round(day_change, 3) if day_change is not None else None,
                 n_orders=len(results))

    cfg.reports_dir.mkdir(exist_ok=True)
    (cfg.reports_dir / f"{today}.md").write_text(report.daily_report(cfg, ledger))
    return {"status": "ok", "orders": results, "rejected": rejected,
            "targets": decision["targets"]}
