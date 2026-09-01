"""Two-stage provenance: specification first, result second.

The naive version is circular. If the strategy ID contains the commit SHA, and
the commit message contains the log head, and the log entry contains the
strategy ID, then:

    commit SHA -> strategy ID -> log head -> commit message -> commit SHA

and there is no fixed point, because editing a commit message changes the SHA
it lives in.

The fix is to split it in two, with each stage referring only backwards:

    COMMIT A  the specification. Rules, parameters, universe, regime
              definitions and thresholds, cost model, minimum sample sizes,
              acceptance criteria. Nothing about results, because none exist.

    strategy ID = SHA256(canonical spec || commit A)
              computable only after A exists, and A does not contain it.

    run, append to the chained log, take the head H_n

    COMMIT B  the result. Log, report, and a reference to A, the strategy ID
              and H_n. Every value in B was fixed before B existed.

    signed tag on B carrying H_n, the strategy ID and A.

Commit A is the pre-registration. It proves the rules existed before the
experiment, which is the entire claim a pre-registration makes. A signed tag
on a protected branch makes rewriting that history awkward and visible rather
than silent; for stronger provenance the head hash belongs somewhere outside
this repository entirely.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path


class ProvenanceError(RuntimeError):
    pass


def _git(root: Path, *args: str) -> str:
    out = subprocess.run(["git", *args], cwd=root, capture_output=True,
                         text=True, timeout=10)
    if out.returncode != 0:
        raise ProvenanceError(out.stderr.strip() or f"git {' '.join(args)} failed")
    return out.stdout.strip()


def canonical(spec: dict) -> str:
    from .vault import _canonical
    return json.dumps(_canonical(spec), sort_keys=True, separators=(",", ":"),
                      default=str)


def strategy_id(spec: dict, spec_commit: str) -> str:
    """SHA256 over the specification and the commit that contains it.

    One-directional by construction: the commit cannot contain this value,
    so nothing here depends on itself.
    """
    if not spec_commit or len(spec_commit) < 7:
        raise ProvenanceError("a specification commit is required — "
                              "pre-register before running")
    payload = canonical(spec) + "|" + spec_commit
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


@dataclass
class Registry:
    root: Path

    @property
    def dir(self) -> Path:
        d = self.root / "registry"
        d.mkdir(exist_ok=True)
        return d

    def preregister(self, name: str, spec: dict) -> Path:
        """Write the specification. Committing it is COMMIT A."""
        if "results" in spec or "verdict" in spec:
            raise ProvenanceError("a specification may not contain results")
        path = self.dir / f"{name}.json"
        path.write_text(json.dumps(spec, indent=2, sort_keys=True,
                                   default=str) + "\n")
        return path

    def spec_commit(self, name: str) -> str:
        """The commit that introduced this specification — commit A.

        Read from history rather than taken on trust, so a spec edited after
        the fact resolves to a different commit and therefore a different
        strategy ID.
        """
        rel = f"registry/{name}.json"
        log = _git(self.root, "log", "-1", "--format=%H", "--", rel)
        if not log:
            raise ProvenanceError(
                f"{rel} is not committed. The specification must exist in "
                "history before the experiment runs — that is what makes it a "
                "pre-registration rather than a note.")
        return log[:12]

    def load(self, name: str) -> dict:
        return json.loads((self.dir / f"{name}.json").read_text())


def anchor_message(*, strategy: str, sid: str, spec_commit: str,
                   log_head: str, verdict: str) -> str:
    """Body for the signed tag on commit B. Every value predates the tag."""
    return "\n".join([
        f"Research anchor: {strategy}",
        "",
        f"Strategy ID:           {sid}",
        f"Specification commit:  {spec_commit}",
        f"Research log head:     {log_head}",
        f"Engine verdict:        {verdict}",
        "",
        "Commit A is the pre-registration; this tag anchors the result.",
        "Neither value depends on this tag, so nothing here is circular.",
    ])


REQUIRED_HYPOTHESIS_FIELDS = ("mechanism_id", "claim", "why_it_might_persist",
                              "observable_prediction", "falsifier",
                              "predictions", "falsifiers",
                              "minimum_observations", "evaluation_dataset",
                              "costs", "benchmark")


def validate_hypothesis(h: dict, mechanisms: dict) -> None:
    """A hypothesis without a falsifier is not a hypothesis.

    The model must state, before any code exists, what observation would kill
    the idea. That single field is what turns a plausible story into
    something the gates can act on — and it is the field a system optimising
    for approval would most like to omit.
    """
    missing = [f for f in REQUIRED_HYPOTHESIS_FIELDS if not h.get(f)]
    if missing:
        raise ProvenanceError(
            "hypothesis is missing " + ", ".join(missing) +
            ". A claim with no stated falsifier cannot be tested, only "
            "believed.")
    # The contract must compile before the specification is committed, so a
    # prose falsifier that cannot be executed is caught at pre-registration
    # rather than discovered when it is time to adjudicate.
    from .falsification import Contract, ContractError, Prediction
    try:
        Contract(hypothesis_id=h.get("hypothesis_id", "?"),
                 mechanism_id=h["mechanism_id"],
                 predictions=[Prediction(p["id"], p["metric"], p["operator"],
                                         p.get("value"))
                              for p in h["predictions"]],
                 falsifiers=h["falsifiers"],
                 minimum_observations=int(h["minimum_observations"]),
                 evaluation_dataset=h["evaluation_dataset"],
                 costs=h["costs"], benchmark=h["benchmark"])
    except (ContractError, KeyError, TypeError, ValueError) as exc:
        raise ProvenanceError(
            f"falsification contract does not compile: {exc}") from exc
    if h["mechanism_id"] not in mechanisms:
        raise ProvenanceError(
            f"mechanism {h['mechanism_id']} is not in the preregistered "
            f"taxonomy {sorted(mechanisms)}. Novelty is declared in advance, "
            "not invented to fit an idea.")
