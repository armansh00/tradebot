"""Two-stage provenance. The specification must be in history before the
result exists, and nothing may depend on itself."""
import json
import pathlib
import subprocess

import pytest

from tradebot.provenance import (ProvenanceError, Registry, anchor_message,
                                 strategy_id)

SPEC = {"rule": "reversal", "ks": [1, 2, 3], "cost_bps": 5,
        "universe": ["A", "B"], "regimes": {"volatility": {"threshold": 20}},
        "minimum_observations": {"per_regime": 100},
        "acceptance": "all gates"}


def _repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    return Registry(root=tmp_path)


def _commit(tmp_path, msg):
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", msg], cwd=tmp_path, check=True)


def test_the_id_depends_on_the_spec_and_its_commit_and_nothing_later(tmp_path):
    """No circularity: commit A cannot contain the ID, so the ID can safely
    contain commit A."""
    reg = _repo(tmp_path)
    reg.preregister("reversal-v1", SPEC)
    _commit(tmp_path, "spec: reversal-v1")
    a = reg.spec_commit("reversal-v1")
    sid = strategy_id(SPEC, a)

    # commit B references A, the ID and a log head; none of them changes
    (tmp_path / "result.txt").write_text("REJECT")
    _commit(tmp_path, f"result for {sid} from {a}")
    assert reg.spec_commit("reversal-v1") == a       # A is untouched by B
    assert strategy_id(SPEC, a) == sid               # and so is the ID


def test_an_uncommitted_specification_is_not_a_preregistration(tmp_path):
    reg = _repo(tmp_path)
    (tmp_path / "seed.txt").write_text("x")
    _commit(tmp_path, "init")
    reg.preregister("reversal-v1", SPEC)             # written, not committed
    with pytest.raises(ProvenanceError, match="not committed"):
        reg.spec_commit("reversal-v1")


def test_editing_the_spec_after_the_fact_changes_the_commit_and_the_id(tmp_path):
    reg = _repo(tmp_path)
    reg.preregister("reversal-v1", SPEC)
    _commit(tmp_path, "spec")
    first_commit = reg.spec_commit("reversal-v1")
    first_id = strategy_id(reg.load("reversal-v1"), first_commit)

    reg.preregister("reversal-v1", {**SPEC, "ks": [1, 2, 3, 4]})
    _commit(tmp_path, "quietly widen the grid")
    second_commit = reg.spec_commit("reversal-v1")

    assert second_commit != first_commit
    assert strategy_id(reg.load("reversal-v1"), second_commit) != first_id


def test_a_specification_may_not_contain_results(tmp_path):
    reg = _repo(tmp_path)
    with pytest.raises(ProvenanceError, match="may not contain results"):
        reg.preregister("bad", {**SPEC, "verdict": "ACCEPT"})


def test_an_id_cannot_be_minted_without_a_commit(tmp_path):
    with pytest.raises(ProvenanceError, match="pre-register"):
        strategy_id(SPEC, "")


def test_anchor_message_carries_the_backward_references(tmp_path):
    msg = anchor_message(strategy="reversal-v1", sid="abc123", 
                         spec_commit="def456", log_head="f" * 64,
                         verdict="REJECT")
    for token in ("abc123", "def456", "f" * 64, "REJECT", "pre-registration"):
        assert token in msg


MECHANISMS = {"M01": "short-horizon reversal", "M05": "liquidity / spread"}
HYPOTHESIS = {
    "mechanism_id": "M05",
    "claim": "Liquidity imbalance causes concession then partial recovery.",
    "why_it_might_persist": "Compensation for immediacy and inventory risk.",
    "observable_prediction": "Reversal rises with measured spread stress.",
    "falsifier": "No monotonic relation between stress and reversal after costs.",
}


def test_a_hypothesis_without_a_falsifier_is_refused(tmp_path):
    """The field a system optimising for approval would most like to omit."""
    from tradebot.provenance import validate_hypothesis
    validate_hypothesis(HYPOTHESIS, MECHANISMS)
    for field in ("falsifier", "why_it_might_persist", "observable_prediction",
                  "claim"):
        broken = {**HYPOTHESIS, field: ""}
        with pytest.raises(ProvenanceError, match=field):
            validate_hypothesis(broken, MECHANISMS)


def test_mechanisms_must_come_from_the_preregistered_taxonomy(tmp_path):
    from tradebot.provenance import validate_hypothesis
    with pytest.raises(ProvenanceError, match="not in the preregistered"):
        validate_hypothesis({**HYPOTHESIS, "mechanism_id": "M99"}, MECHANISMS)


def test_the_shipped_template_passes_its_own_validator(tmp_path):
    import json
    from tradebot.provenance import validate_hypothesis
    tpl = json.loads(pathlib.Path("registry/HYPOTHESIS_TEMPLATE.json").read_text())
    validate_hypothesis(tpl, {f"M{i:02d}": "x" for i in range(1, 11)})
