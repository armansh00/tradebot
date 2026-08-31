"""Load and validate config.yaml. One source of truth for all parameters."""
from __future__ import annotations
import os
from dataclasses import dataclass, field
from pathlib import Path
import yaml


@dataclass
class StrategyCfg:
    name: str
    top_n: int
    sma_days: int
    mom_lookback_days: int
    mom_skip_days: int
    cash_buffer_pct: float
    min_order_notional: float


@dataclass
class RiskCfg:
    max_order_notional: float
    book_cap: float
    max_positions: int
    max_drawdown_pct: float
    halt_file: str


@dataclass
class FastCfg:
    start_cash: float
    or_minutes: int
    top_k: int
    cost_bps_per_side: float
    max_trades_per_day: int
    daily_loss_stop_pct: float
    flat_minutes_before_close: int
    max_drawdown_pct: float
    universe: list[str] = field(default_factory=list)
    universe_mode: str = "static"      # "static" | "most_active"
    universe_size: int = 10
    min_price: float = 5.0
    tick_minutes: int = 30             # in-process tick cadence (see session.py)


@dataclass
class MoversEvalCfg:
    min_weeks: int
    require_net_positive: bool
    must_beat_both_arms: bool


@dataclass
class FastEvalCfg:
    min_weeks: int
    must_beat_slow_arm: bool
    require_net_positive: bool


@dataclass
class EvalCfg:
    min_weeks: int
    require_net_positive: bool
    max_drawdown_pct: float
    max_manual_overrides: int


@dataclass
class Config:
    universe: list[str]
    strategy: StrategyCfg
    risk: RiskCfg
    evaluation: EvalCfg
    fast: FastCfg
    movers: FastCfg
    fast_evaluation: FastEvalCfg
    movers_evaluation: MoversEvalCfg
    root: Path = field(default_factory=Path.cwd)

    @property
    def ledger_path(self) -> Path:
        return self.root / "ledger.jsonl"

    @property
    def state_path(self) -> Path:
        return self.root / "state.json"

    @property
    def fast_ledger_path(self) -> Path:
        return self.root / "ledger_fast.jsonl"

    @property
    def fast_state_path(self) -> Path:
        return self.root / "state_fast.json"

    @property
    def movers_ledger_path(self) -> Path:
        return self.root / "ledger_movers.jsonl"

    @property
    def movers_state_path(self) -> Path:
        return self.root / "state_movers.json"

    @property
    def reports_dir(self) -> Path:
        return self.root / "reports"

    @property
    def halt_path(self) -> Path:
        return self.root / self.risk.halt_file


def load_config(root: str | os.PathLike | None = None) -> Config:
    root_path = Path(root) if root else Path.cwd()
    raw = yaml.safe_load((root_path / "config.yaml").read_text())
    return Config(
        universe=[s.upper() for s in raw["universe"]],
        strategy=StrategyCfg(**raw["strategy"]),
        risk=RiskCfg(**{"book_cap": 0.0, **raw["risk"]}),
        evaluation=EvalCfg(**raw["evaluation"]),
        fast=FastCfg(**{**raw["fast"],
                        "universe": [x.upper() for x in raw["fast"]["universe"]]}),
        movers=FastCfg(**raw["movers"]),
        fast_evaluation=FastEvalCfg(**raw["fast_evaluation"]),
        movers_evaluation=MoversEvalCfg(**raw["movers_evaluation"]),
        root=root_path,
    )
