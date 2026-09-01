"""Deflated Sharpe and PBO. Both must punish searching, and both must be
readable component by component rather than as a verdict."""
import numpy as np
import pytest

from tradebot.stats import (deflated_sharpe, norm_cdf, norm_ppf, pbo, sharpe)


def test_normal_helpers_agree_with_known_values():
    assert norm_cdf(0) == pytest.approx(0.5)
    assert norm_cdf(1.959964) == pytest.approx(0.975, abs=1e-4)
    assert norm_ppf(0.975) == pytest.approx(1.959964, abs=1e-5)
    assert norm_ppf(0.5) == pytest.approx(0.0, abs=1e-9)
    for p in (0.001, 0.02, 0.3, 0.7, 0.98, 0.999):
        assert norm_cdf(norm_ppf(p)) == pytest.approx(p, abs=1e-6)


def test_more_trials_deflate_the_same_sharpe(cfg):
    """The whole point: an identical track record is worth less when it was
    chosen out of many."""
    rng = np.random.default_rng(0)
    r = rng.normal(0.0008, 0.01, 500)
    few = deflated_sharpe(r, [sharpe(r), 0.02, -0.01])
    many = deflated_sharpe(r, list(rng.normal(0, 0.05, 500)) + [sharpe(r)])
    assert few.observed == many.observed
    assert many.expected_max > few.expected_max
    assert many.deflated < few.deflated


def test_a_single_trial_applies_no_haircut(cfg):
    rng = np.random.default_rng(2)
    r = rng.normal(0.001, 0.01, 400)
    d = deflated_sharpe(r, [sharpe(r)])
    assert d.expected_max == 0.0
    assert d.trials == 1


def test_negative_skew_and_fat_tails_reduce_confidence(cfg):
    rng = np.random.default_rng(3)
    base = rng.normal(0.001, 0.01, 600)
    crashed = base.copy()
    crashed[::60] -= 0.05                      # occasional large losses
    trials = [0.0, 0.01, -0.02]
    assert deflated_sharpe(crashed, trials).skew < \
        deflated_sharpe(base, trials).skew
    assert deflated_sharpe(crashed, trials).kurtosis > 3


def test_pbo_is_high_when_configurations_are_pure_noise(cfg):
    """Selecting the in-sample maximum picks the luckiest configuration, and
    luck reverses — so the winner lands BELOW median out of sample and PBO
    runs well above one half. A coin flip would be the answer for a randomly
    chosen configuration; choosing the best is worse than random."""
    rng = np.random.default_rng(5)
    matrix = rng.normal(0, 0.01, (400, 20))
    result = pbo(matrix, partitions=8)
    assert result.configurations == 20
    assert result.combinations == 70           # C(8,4)
    assert result.pbo > 0.6
    assert result.median_oos_rank < 0.5


def test_pbo_is_low_when_one_configuration_is_genuinely_better(cfg):
    rng = np.random.default_rng(6)
    matrix = rng.normal(0, 0.01, (400, 10))
    matrix[:, 3] += 0.004                      # a real, persistent edge
    result = pbo(matrix, partitions=8)
    assert result.pbo < 0.1
    assert result.median_oos_rank > 0.8


def test_components_are_exposed_not_collapsed(cfg):
    rng = np.random.default_rng(7)
    r = rng.normal(0.0005, 0.01, 300)
    text = "\n".join(deflated_sharpe(r, [0.1, 0.2, 0.05]).lines())
    for field in ("observed Sharpe", "trials considered", "expected max",
                  "skew", "kurtosis", "deflated Sharpe"):
        assert field in text
    ptext = "\n".join(pbo(rng.normal(0, 0.01, (200, 6)), partitions=6).lines())
    assert "configurations" in ptext and "PBO" in ptext
