"""A holdout that can only be spent once, and only on the strategy that
claimed it.

The loophole this closes: open the vault, see the result, tweak the strategy,
open it again and call the second look "out-of-sample". After enough rounds
the vault is training data and nobody noticed the moment it stopped being
honest.

So the vault is bound to a hash of everything that defines the strategy — its
rules, parameters, universe, cost model and acceptance criteria. Change any of
them and it is a different strategy, which does not inherit the right to
unseen data. The vault records who consumed it and when, and refuses everyone
else.

This is pre-registration with the paperwork enforced by code rather than
by good intentions at 1am.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd


class VaultError(RuntimeError):
    pass


def strategy_hash(spec: dict) -> str:
    """Stable fingerprint of everything that makes this strategy this one."""
    canonical = json.dumps(spec, sort_keys=True, separators=(",", ":"),
                           default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


@dataclass
class Vault:
    path: Path
    research_end: date
    vault_start: date

    def _state(self) -> dict:
        if self.path.exists():
            return json.loads(self.path.read_text())
        return {"status": "LOCKED", "opened_at": None, "strategy_hash": None,
                "strategy_name": None}

    def _write(self, state: dict) -> None:
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=2))
        tmp.replace(self.path)

    @property
    def status(self) -> str:
        return self._state()["status"]

    def research_slice(self, df: pd.DataFrame, dates) -> pd.DataFrame:
        """Everything up to research_end. The vault period is not returned —
        not filtered later, not returned and ignored. Never loaded."""
        dates = pd.to_datetime(pd.Series(list(dates))).dt.date
        keep = [i for i, d in enumerate(dates) if d <= self.research_end]
        return df.iloc[keep].reset_index(drop=True)

    def open(self, df: pd.DataFrame, dates, *, spec: dict,
             name: str) -> pd.DataFrame:
        """Spend the vault. Succeeds once, for one strategy, forever."""
        h = strategy_hash(spec)
        state = self._state()
        if state["status"] == "CONSUMED":
            if state["strategy_hash"] != h:
                raise VaultError(
                    f"vault already consumed by {state['strategy_name']} "
                    f"({state['strategy_hash']}) on {state['opened_at']}. "
                    f"Strategy {name} ({h}) differs, so this would not be "
                    "out-of-sample. Extend the dataset or accept that this "
                    "strategy has no untouched holdout.")
        else:
            self._write({"status": "CONSUMED",
                         "opened_at": datetime.now(timezone.utc).isoformat(),
                         "strategy_hash": h, "strategy_name": name,
                         "spec": spec})
        dates = pd.to_datetime(pd.Series(list(dates))).dt.date
        keep = [i for i, d in enumerate(dates) if d >= self.vault_start]
        return df.iloc[keep].reset_index(drop=True)
