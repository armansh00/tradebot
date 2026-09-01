"""Count the searching, not just the finding.

A t-stat of 2.4 means one thing as the third hypothesis anyone tried and
something entirely different as the winner of forty-eight thousand variants.
The system is not allowed to forget which it was, so every evaluation appends
here and the counts are reported alongside any result.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def record(path: Path, **event) -> None:
    with path.open("a") as fh:
        fh.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(),
                             **event}) + "\n")


def summary(path: Path) -> dict:
    if not path.exists():
        return {"hypotheses": 0, "parameter_combinations": 0,
                "strategies_distinct": 0, "reached_vault": 0,
                "survived_vault": 0}
    events = []
    for line in path.read_text().splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    evals = [e for e in events if e.get("type") == "evaluation"]
    return {
        "hypotheses": len(evals),
        "parameter_combinations": sum(e.get("variants", 1) for e in evals),
        "strategies_distinct": len({e.get("strategy_hash") for e in evals}),
        "reached_vault": len([e for e in events if e.get("type") == "vault_open"]),
        "survived_vault": len([e for e in events
                               if e.get("type") == "vault_open"
                               and e.get("verdict") == "ACCEPT"]),
    }


def render(path: Path) -> str:
    s = summary(path)
    rows = [("Hypotheses evaluated", s["hypotheses"]),
            ("Parameter combinations", s["parameter_combinations"]),
            ("Distinct strategies", s["strategies_distinct"]),
            ("Strategies reaching vault", s["reached_vault"]),
            ("Strategies surviving vault", s["survived_vault"])]
    out = ["RESEARCH BUDGET", ""]
    out += [f"  {k:<32}{v:>8,}" for k, v in rows]
    out += ["", "  Every result must be read against these counts. The same",
            "  t-stat means different things as hypothesis 3 and as the best",
            "  of 48,000."]
    return "\n".join(out)
