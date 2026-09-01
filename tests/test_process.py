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


def _d(name, delta, p, commissioning=False, lineages=8):
    return VintageDelta(vintage=name, promoted_metric=delta + 1.0,
                        null_mean=1.0, delta=delta,
                        percentile=(1 - p) * 100, p_value=p,
                        lineages_promoted=lineages, lineages_pool=31,
                        commissioning=commissioning)


def test_commissioning_vintages_are_excluded_from_inference():
    text = process_report([_d("000", 5.0, 0.001, commissioning=True)])
    assert "commissioning" in text
    assert "No confirmatory vintages yet" in text
    assert "Fisher" not in text


def test_report_combines_confirmatory_vintages_only():
    text = process_report([_d("000", 9.9, 0.0001, commissioning=True),
                           _d("001", 3.1, 0.083),
                           _d("002", -1.4, 0.658)])
    assert "Confirmatory vintages" in text
    assert text.split("Confirmatory vintages")[1].split()[0] == "2"
    assert "Fisher combined p" in text and "Stouffer combined p" in text


def test_report_refuses_to_let_advantage_read_as_profit():
    text = process_report([_d("001", 3.1, 0.02)])
    assert "Selector advantage is not profit" in text
    assert "least bad" in text
    assert "independent" in text          # design effect is stated, not hidden
