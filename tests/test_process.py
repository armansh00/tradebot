"""Process-level inference. The unit is the vintage, and the design is paired
because every strategy inside a vintage shares one future."""
import pytest

from tradebot.process import (VintageDelta, chi2_sf, design_effect,
                              fisher_combine, process_report, stouffer_combine)


def test_clustering_collapses_twenty_lineages_to_about_four():
    """The arithmetic that killed 'promote twenty instead of four'."""
    assert design_effect(20, 0.20) == pytest.approx(4.17, abs=0.01)
    assert design_effect(20, 0.0) == 20          # independent, no penalty
    assert design_effect(20, 1.0) == 1.0         # perfectly correlated
    assert design_effect(1, 0.5) == 1.0


def test_chi2_tail_matches_known_values():
    assert chi2_sf(0, 4) == 1.0
    assert chi2_sf(9.488, 4) == pytest.approx(0.05, abs=0.002)
    assert chi2_sf(12.592, 6) == pytest.approx(0.05, abs=0.002)
    assert chi2_sf(1000, 4) < 1e-9


def test_fisher_has_power_at_three_vintages_where_a_t_test_has_none(cfg):
    """Three exact p-values of 0.08 combine to something meaningful; three
    numbers do not support a t-test."""
    stat, df = fisher_combine([0.08, 0.09, 0.07])
    assert df == 6
    assert chi2_sf(stat, df) < 0.02
    weak = fisher_combine([0.5, 0.6, 0.4])
    assert chi2_sf(*weak) > 0.4


def test_stouffer_weights_bigger_vintages_more():
    small_strong = stouffer_combine([0.01, 0.9], [1.0, 10.0])
    big_strong = stouffer_combine([0.9, 0.01], [1.0, 10.0])
    assert big_strong < small_strong


def _d(name, delta, p, commissioning=False, lineages=8,
       window=("2026-11-01", "2026-12-31"), null=None):
    import random
    rng = random.Random(hash(name) % 1000)
    scores = null if null is not None else [rng.gauss(1.0, 1.0) for _ in range(400)]
    mean = sum(scores) / len(scores)
    sd = (sum((x - mean) ** 2 for x in scores) / (len(scores) - 1)) ** 0.5
    return VintageDelta(vintage=name, promoted_metric=delta + 1.0,
                        null_mean=1.0, delta=delta,
                        percentile=(1 - p) * 100, p_value=p,
                        lineages_promoted=lineages, lineages_pool=31,
                        commissioning=commissioning, null_sd=sd,
                        null_scores=scores, draws=len(scores),
                        window_start=window[0], window_end=window[1])


def test_commissioning_vintages_are_excluded_from_inference():
    text = process_report([_d("000", 5.0, 0.001, commissioning=True)])
    assert "commissioning" in text
    assert "No confirmatory vintages yet" in text
    assert "Fisher" not in text


def test_report_combines_confirmatory_vintages_only():
    text = process_report([
        _d("000", 9.9, 0.0001, commissioning=True,
           window=("2026-09-01", "2026-10-26")),
        _d("001", 3.1, 0.083, window=("2026-11-01", "2026-12-31")),
        _d("002", -1.4, 0.658, window=("2027-01-01", "2027-02-28"))])
    assert "Confirmatory vintages" in text
    assert text.split("Confirmatory vintages")[1].split()[0] == "2"
    assert "Fisher combined p" in text and "Stouffer combined p" in text
    assert "PRIMARY" in text


def test_report_refuses_to_let_advantage_read_as_profit():
    text = process_report([_d("001", 3.1, 0.02)])
    assert "Selector advantage is not profit" in text
    assert "least bad" in text
    assert "independent" in text          # design effect is stated, not hidden


def test_weights_are_root_effective_n_not_head_count():
    """Forty lineages do not carry forty-sixths the information of six."""
    small, big = _d("a", 1.0, 0.2, lineages=6), _d("b", 1.0, 0.2, lineages=40)
    assert big.weight > small.weight
    assert big.weight / small.weight < 40 / 6      # nothing like head count
    assert big.n_effective < 6                     # clustering bites hard


def test_overlapping_windows_suppress_generic_combination():
    """Vintages sharing calendar time are dependent; Fisher is miscalibrated."""
    a = _d("001", 3.0, 0.05, window=("2026-11-01", "2026-12-31"))
    b = _d("002", 3.0, 0.05, window=("2026-12-01", "2027-01-31"))
    text = process_report([a, b])
    assert "SUPPRESSED" in text and "Brown" in text
    assert "Fisher combined p" not in text


def test_disjoint_windows_allow_generic_combination():
    a = _d("001", 3.0, 0.05, window=("2026-11-01", "2026-12-31"))
    b = _d("002", 3.0, 0.05, window=("2027-01-01", "2027-02-28"))
    text = process_report([a, b])
    assert "SUPPRESSED" not in text
    assert "Fisher combined p" in text


def test_combined_permutation_is_the_primary_result():
    from tradebot.process import combined_permutation
    strong = [_d("001", 4.0, 0.01, window=("2026-11-01", "2026-12-31")),
              _d("002", 3.5, 0.02, window=("2027-01-01", "2027-02-28"))]
    weak = [_d("001", 0.0, 0.5, window=("2026-11-01", "2026-12-31")),
            _d("002", 0.0, 0.5, window=("2027-01-01", "2027-02-28"))]
    assert combined_permutation(strong, draws=4000)["p_value"] < \
        combined_permutation(weak, draws=4000)["p_value"]
    assert combined_permutation(weak, draws=4000)["p_value"] > 0
    text = process_report(strong)
    assert text.index("PRIMARY") < text.index("SECONDARY")


def test_a_finite_combined_run_never_reports_certainty():
    from tradebot.process import combined_permutation
    huge = [_d("001", 500.0, 0.0001, window=("2026-11-01", "2026-12-31"))]
    out = combined_permutation(huge, draws=1000)
    assert out["p_value"] == pytest.approx(1 / 1001, abs=1e-6)
    assert out["p_formula"] == "(b+1)/(B+1)"
