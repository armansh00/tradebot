"""Hard brakes. Checked before every order, every run. Errors fail closed."""
from __future__ import annotations
import json
from .config import Config
from .ledger import Ledger


class RiskManager:
    def __init__(self, cfg: Config, ledger: Ledger):
        self.cfg = cfg
        self.ledger = ledger

    # ---- halt switch -------------------------------------------------
    def halted(self) -> bool:
        return self.cfg.halt_path.exists()

    def halt(self, reason: str) -> None:
        self.cfg.halt_path.write_text(reason + "\n")
        self.ledger.write("halt", reason=reason)

    def clear_halt(self) -> None:
        if self.cfg.halt_path.exists():
            self.cfg.halt_path.unlink()
        self.ledger.write("halt_cleared")

    # ---- drawdown kill switch ---------------------------------------
    def _state(self) -> dict:
        if self.cfg.state_path.exists():
            return json.loads(self.cfg.state_path.read_text())
        return {}

    def _save_state(self, state: dict) -> None:
        self.cfg.state_path.write_text(json.dumps(state, indent=2))

    def check_drawdown(self, equity: float) -> bool:
        """Update high-water mark; return True if drawdown limit breached."""
        state = self._state()
        hwm = max(float(state.get("high_water_mark", 0.0)), equity)
        state["high_water_mark"] = hwm
        self._save_state(state)
        if hwm <= 0:
            return False
        dd_pct = (hwm - equity) / hwm * 100.0
        if dd_pct >= self.cfg.risk.max_drawdown_pct:
            self.halt(f"drawdown {dd_pct:.1f}% >= limit "
                      f"{self.cfg.risk.max_drawdown_pct}% (equity {equity:.2f}, "
                      f"high-water {hwm:.2f})")
            return True
        return False

    # ---- order-level checks -----------------------------------------
    def filter_orders(self, orders: list[dict]) -> tuple[list[dict], list[dict]]:
        """Split into (approved, rejected). Rejections are logged with reasons."""
        approved, rejected = [], []
        for o in orders:
            if o["notional"] > self.cfg.risk.max_order_notional:
                o["rejected_reason"] = (f"notional {o['notional']:.2f} > cap "
                                        f"{self.cfg.risk.max_order_notional:.2f}")
                rejected.append(o)
            else:
                approved.append(o)
        buys = [o for o in approved if o["side"] == "buy" and o["to_notional"] > 0]
        if len(buys) > self.cfg.risk.max_positions:
            for o in buys[self.cfg.risk.max_positions:]:
                o["rejected_reason"] = "exceeds max_positions"
                approved.remove(o)
                rejected.append(o)
        return approved, rejected
