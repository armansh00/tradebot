from tradebot.ledger import Ledger
from tradebot.risk import RiskManager
from tradebot.run import run_once
from conftest import FakeBroker, trending_series, falling_series


def test_order_cap_rejects(cfg):
    risk = RiskManager(cfg, Ledger(cfg.ledger_path))
    approved, rejected = risk.filter_orders(
        [{"symbol": "SPY", "side": "buy", "notional": 999.0, "to_notional": 999.0}])
    assert not approved and rejected[0]["rejected_reason"].startswith("notional")


def test_drawdown_kill_switch_flattens_and_halts(cfg):
    closes = {s: trending_series(seed=i) for i, s in enumerate(cfg.universe)}
    b = FakeBroker(closes, equity=50.0, positions={"SPY": 24.0})
    assert run_once(cfg, b, force=True)["status"] == "ok"       # sets HWM ~50
    b._equity = 40.0                                            # -20% drawdown
    result = run_once(cfg, b, force=True)
    assert result["status"] == "killed"
    assert cfg.halt_path.exists()
    assert any(o["side"] == "sell" for o in b.submitted)
    assert run_once(cfg, b, force=True)["status"] == "halted"   # stays halted


def test_full_run_buys_uptrends_and_logs(cfg):
    closes = {s: (trending_series(seed=i) if s in {"SPY", "QQQ"}
                  else falling_series(seed=i)) for i, s in enumerate(cfg.universe)}
    b = FakeBroker(closes, equity=50.0)
    result = run_once(cfg, b, force=True)
    assert result["status"] == "ok"
    bought = {o["symbol"] for o in b.submitted}
    assert bought == {"SPY", "QQQ"}
    types = [r["type"] for r in Ledger(cfg.ledger_path).read()]
    assert {"decision", "order", "run"} <= set(types)
    assert (cfg.reports_dir).exists()


def test_idempotent_per_day(cfg):
    closes = {s: trending_series(seed=i) for i, s in enumerate(cfg.universe)}
    b = FakeBroker(closes, equity=50.0)
    assert run_once(cfg, b)["status"] == "ok"
    assert run_once(cfg, b)["status"] == "already_ran_today"


def test_chat_answers_why_from_ledger(cfg, monkeypatch):
    closes = {s: (trending_series(seed=i) if s == "SPY" else falling_series(seed=i))
              for i, s in enumerate(cfg.universe)}
    run_once(cfg, FakeBroker(closes, equity=50.0), force=True)
    monkeypatch.chdir(cfg.root)
    from tradebot.chat import answer
    why = answer(cfg, "why SPY")
    assert "HOLDING SPY" in why and "momentum" in why
    why_gld = answer(cfg, "why GLD")
    assert "NOT holding" in why_gld


def test_book_cap_trades_fifty_inside_hundred_k(cfg):
    from conftest import FakeBroker, trending_series
    closes = {s: trending_series(seed=i) for i, s in enumerate(cfg.universe)}
    b = FakeBroker(closes, equity=100_000.0)
    result = run_once(cfg, b, force=True)
    assert result["status"] == "ok"
    total = sum(o["notional"] for o in b.submitted)
    assert 40 < total <= 50.0                    # sized to the $50 book
    assert all(o["notional"] <= cfg.risk.max_order_notional for o in b.submitted)


def test_book_cap_rebaselines_after_dashboard_reset(cfg):
    from tradebot.risk import RiskManager
    risk = RiskManager(cfg, Ledger(cfg.ledger_path))
    assert risk.book_equity(100_000.0) == 50.0   # baseline anchors at 100k
    assert risk.book_equity(100_010.0) == 60.0   # bot P&L flows through
    assert risk.book_equity(50.0) == 50.0        # account reset detected, re-anchored


def test_trading_loss_is_never_rebaselined_away(cfg):
    # Adversarial review finding 1: a 60% TRADING loss must flow into the
    # book and trip the kill switch, not be mistaken for an account reset.
    from tradebot.risk import RiskManager
    risk = RiskManager(cfg, Ledger(cfg.ledger_path))
    assert risk.book_equity(1000.0) == 50.0      # baseline 1000
    assert not risk.check_drawdown(50.0)          # normal day records HWM
    book_after_loss = risk.book_equity(400.0)     # -60% raw, NOT a reset
    assert book_after_loss < 0                    # loss hits the book
    assert risk.check_drawdown(book_after_loss)   # and the switch fires
    # while a true dashboard reset (collapse to ~cap) still re-anchors:
    risk2 = RiskManager(cfg, Ledger(cfg.ledger_path))
    cfg.state_path.unlink()
    assert risk2.book_equity(100_000.0) == 50.0
    assert risk2.book_equity(50.0) == 50.0


def test_kill_switch_flattens_shorts_too(cfg):
    from conftest import FakeBroker, trending_series
    closes = {s: trending_series(seed=i) for i, s in enumerate(cfg.universe)}
    b = FakeBroker(closes, equity=50.0, positions={"SPY": -30.0})
    run_once(cfg, b, force=True)
    b._equity = 40.0
    result = run_once(cfg, b, force=True)
    assert result["status"] == "killed"
    assert any(o["side"] == "buy" and o["symbol"] == "SPY"
               for o in b.submitted)              # short covered, not ignored
