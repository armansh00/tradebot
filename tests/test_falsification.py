"""Falsification contracts. The asymmetry these tests protect: a system that
treats every underpowered result as refutation kills true ideas faster than
false ones."""
import pytest

from tradebot.falsification import (Contract, ContractError, FALSIFIED,
                                    Prediction, SURVIVES, UNSUPPORTED,
                                    adjudicate, evaluate_predicate, render)


def _contract(minimum=200):
    return Contract(
        hypothesis_id="H-0042", mechanism_id="M05",
        predictions=[
            Prediction("P1", "net_long_short_expectancy", ">", 0),
            Prediction("P2", "reversal_by_spread_bucket", "monotonic_increase"),
        ],
        falsifiers=[
            {"prediction": "P1",
             "predicate": {"metric": "net_long_short_expectancy",
                           "operator": "<=", "value": 0}},
            {"prediction": "P2",
             "predicate": {"metric": "reversal_by_spread_bucket",
                           "operator": "not_monotonic"}},
        ],
        minimum_observations=minimum, evaluation_dataset="confirmatory_oos",
        costs="measured", benchmark="factor_adjusted")


def test_a_weak_result_is_unsupported_not_falsified():
    """t = 1.2 in a small sample is compatible with a real effect."""
    out = adjudicate(_contract(), {
        "observations": 40,                       # far below the minimum
        "net_long_short_expectancy": -0.4,
        "reversal_by_spread_bucket": [1, 0, 2],
        "net_expectancy": -1.0})
    assert out[0].status == UNSUPPORTED
    assert "absence of evidence" in out[0].detail


def test_the_same_result_with_precision_is_falsified():
    out = adjudicate(_contract(), {
        "observations": 900,
        "net_long_short_expectancy": -0.4,
        "reversal_by_spread_bucket": [1, 0, 2],
        "net_expectancy": -1.0})
    assert out[0].status == FALSIFIED


def test_a_mechanism_can_survive_while_its_economics_are_falsified():
    """Gross +14 bp, net -3 bp. The signal exists and is not tradeable, and
    those must not collapse into one verdict."""
    out = adjudicate(_contract(), {
        "observations": 900,
        "net_long_short_expectancy": 14.0,
        "reversal_by_spread_bucket": [1.0, 2.0, 3.5],
        "net_expectancy": -3.0})
    mech, econ, strat = out
    assert mech.status == SURVIVES
    assert econ.status == FALSIFIED
    assert strat.status == "REJECT"
    assert "not be tradeable" in econ.detail


def test_everything_surviving_still_does_not_claim_truth():
    out = adjudicate(_contract(), {
        "observations": 900, "net_long_short_expectancy": 9.0,
        "reversal_by_spread_bucket": [1.0, 2.0, 3.0], "net_expectancy": 4.0})
    assert [o.status for o in out] == [SURVIVES, SURVIVES, "ACCEPT"]
    assert "failed to kill it" in out[0].detail
    assert "never means true" in render(out)


def test_a_relationship_predicate_cannot_be_reduced_to_one_threshold():
    """Monotonicity across buckets is the whole point of a predicate tree."""
    rising = {"reversal_by_spread_bucket": [1, 2, 3, 4]}
    lumpy = {"reversal_by_spread_bucket": [1, 5, 2, 4]}
    node = {"metric": "reversal_by_spread_bucket",
            "operator": "monotonic_increase"}
    assert evaluate_predicate(node, rising)
    assert not evaluate_predicate(node, lumpy)


def test_compound_predicates_combine_evidence_and_precision():
    node = {"all": [
        {"metric": "net_long_short_expectancy", "operator": "<=", "value": 0},
        {"metric": "observations", "operator": ">=", "value": 200}]}
    assert evaluate_predicate(node, {"net_long_short_expectancy": -1,
                                     "observations": 400})
    assert not evaluate_predicate(node, {"net_long_short_expectancy": -1,
                                         "observations": 100})


def test_an_unevaluable_contract_raises_rather_than_quietly_passing():
    with pytest.raises(ContractError, match="was not measured"):
        evaluate_predicate({"metric": "never_computed", "operator": ">",
                            "value": 0}, {"observations": 10})
    with pytest.raises(ContractError, match="unknown operator"):
        evaluate_predicate({"metric": "x", "operator": "vibes", "value": 0},
                           {"x": 1})


def test_a_hypothesis_that_predicts_nothing_is_refused():
    with pytest.raises(ContractError, match="cannot be wrong"):
        Contract("H", "M05", [], [{"prediction": "P1", "predicate": {}}],
                 200, "oos", "measured", "factor_adjusted")


def test_a_falsifier_must_reference_a_real_prediction():
    with pytest.raises(ContractError, match="unknown prediction"):
        Contract("H", "M05", [Prediction("P1", "m", ">", 0)],
                 [{"prediction": "P9", "predicate": {}}],
                 200, "oos", "measured", "factor_adjusted")
