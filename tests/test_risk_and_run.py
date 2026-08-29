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
