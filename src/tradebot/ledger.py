"""Append-only JSONL ledger. Every run, decision, order, and halt is a line.
The chat interface answers only from what is written here — it cannot invent."""
from __future__ import annotations
import json
import os
from datetime import datetime, timezone
from pathlib import Path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Ledger:
    def __init__(self, path: Path):
        self.path = path

    def write(self, event_type: str, **fields) -> dict:
        """Append one event and make it real before returning.

        The flush and fsync are not ceremony. On 2026-09-01 a session hit its
        deadline, wrote its handoff record, and the runner was destroyed; the
        record never reached the repository and the day showed a 5h45m hole
        that nothing admitted to. "Written" has to mean written to disk, so
        that the only remaining question is whether it was pushed.
        """
        record = {"ts": _now(), "type": event_type, **fields}
        with open(self.path, "a") as f:
            f.write(json.dumps(record) + "\n")
            f.flush()
            os.fsync(f.fileno())
        return record

    def read(self) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text().splitlines():
            line = line.strip()
            if line:
                out.append(json.loads(line))
        return out

    def last(self, event_type: str) -> dict | None:
        for rec in reversed(self.read()):
            if rec["type"] == event_type:
                return rec
        return None

    def last_run_date(self) -> str | None:
        rec = self.last("run")
        return rec["ts"][:10] if rec else None

    def equity_series(self) -> list[tuple[str, float]]:
        return [(r["ts"][:10], r["equity"]) for r in self.read()
                if r["type"] == "run" and "equity" in r]
