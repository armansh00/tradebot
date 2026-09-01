"""An append-only research record, chained so that edits are detectable.

Two jobs. First, count the searching: a t-stat of 2.4 means one thing as the
third idea anyone tried and something else as the winner of forty-eight
thousand. Second, make the count hard to quietly revise later.

Each row carries the hash of the row before it:

    H_n = SHA256(H_{n-1} || record_n)

Delete an embarrassing experiment, or edit one after the fact, and every
subsequent link fails to verify. This does not make tampering impossible —
the chain can be rebuilt by anyone with write access — but it makes it
deliberate rather than casual, and it makes an accidental loss visible.

Ideas are counted even when they are never executed. Once the model has seen
prior results and proposed a modification, information has been consumed
whether or not a backtest followed.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

GENESIS = "0" * 64


def _digest(prev: str, body: str) -> str:
    return hashlib.sha256((prev + body).encode()).hexdigest()


def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            out.append({"_unparseable": line})
    return out


def last_hash(path: Path) -> str:
    rows = _rows(path)
    return rows[-1].get("hash", GENESIS) if rows else GENESIS


def record(path: Path, **event) -> str:
    """Append one chained row. Returns its hash."""
    prev = last_hash(path)
    body = dict(event)
    body["ts"] = datetime.now(timezone.utc).isoformat()
    body["prev"] = prev
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"),
                           default=str)
    h = _digest(prev, canonical)
    with path.open("a") as fh:
        fh.write(json.dumps({**body, "hash": h}, sort_keys=True,
                            default=str) + "\n")
    return h


def verify_chain(path: Path) -> tuple[bool, int | None]:
    """(intact, index of first broken link). Index is 0-based."""
    prev = GENESIS
    for i, row in enumerate(_rows(path)):
        if "_unparseable" in row or "hash" not in row:
            return False, i
        body = {k: v for k, v in row.items() if k != "hash"}
        canonical = json.dumps(body, sort_keys=True, separators=(",", ":"),
                               default=str)
        if row.get("prev") != prev or _digest(prev, canonical) != row["hash"]:
            return False, i
        prev = row["hash"]
    return True, None


def summary(path: Path) -> dict:
    rows = [r for r in _rows(path) if "_unparseable" not in r]
    kind = lambda t: [r for r in rows if r.get("type") == t]
    evals = kind("evaluation")
    vault = kind("vault_open")
    intact, broken_at = verify_chain(path)
    return {
        # counted even when no backtest followed — the model saw prior results
        "ideas_generated": len(kind("idea")) + len(evals),
        "tests_executed": len(evals),
        "parameter_sets_evaluated": sum(r.get("variants", 1) for r in evals),
        "strategies_promoted": len({r.get("strategy_hash") for r in evals
                                    if r.get("verdict") == "ACCEPT"}),
        "vault_tests": len(vault),
        "vault_survivors": len([r for r in vault if r.get("verdict") == "ACCEPT"]),
        "distinct_lineages": len({r.get("lineage_root") or r.get("strategy_hash")
                                  for r in evals}),
        "chain_intact": intact,
        "chain_broken_at": broken_at,
    }


def render(path: Path) -> str:
    s = summary(path)
    rows = [("Ideas generated", s["ideas_generated"]),
            ("Tests executed", s["tests_executed"]),
            ("Parameter sets evaluated", s["parameter_sets_evaluated"]),
            ("Distinct lineages", s["distinct_lineages"]),
            ("Strategies promoted", s["strategies_promoted"]),
            ("Vault tests", s["vault_tests"]),
            ("Vault survivors", s["vault_survivors"])]
    out = ["RESEARCH BUDGET", ""]
    out += [f"  {k:<32}{v:>8,}" for k, v in rows]
    out.append("")
    if s["chain_intact"]:
        out.append("  Log chain verified — no row altered or removed.")
    else:
        out.append(f"  *** LOG CHAIN BROKEN at row {s['chain_broken_at']}. "
                   "The counts below it cannot be trusted. ***")
    out += ["", "  Read every result against these counts. Twenty edits of one",
            "  failed idea share a lineage; they are not twenty hypotheses."]
    return "\n".join(out)
