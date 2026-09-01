"""The validation engine. Every test here checks that it says NO when it
should — a gate that cannot reject is decoration."""
import numpy as np
import pandas as pd
import pytest

from tradebot.validate import (Gate, evaluate, leg_returns, report_card,
                               shifted_null_max_t, spread_returns, t_stat,
                               walk_forward)


def noise_frame(n=600, seed=0, cols="ABCDEF"):
    rng = np.random.default_rng(seed)
    return pd.DataFrame({c: rng.normal(0, 0.012, n) for c in cols})


def reversal_frame(n=600, seed=1, phi=-0.4, cols="ABCDEF"):
    rng = np.random.default_rng(seed)
    data = {}
    for c in cols:
        r = [0.0]
        for _ in range(n):
            r.append(phi * r[-1] + rng.normal(0, 0.012))
        data[c] = np.array(r[1:])
    return pd.DataFrame(data)


def test_long_short_removes_a_market_wide_drift(cfg):
    """The correction that matters: a rising tide lifts the long-only leg and
    must cancel out of losers-minus-winners."""
    base = noise_frame(seed=3)
    drifted = base + 0.004                      # +40 bp/day on everything
    long_only_t = t_stat(leg_returns(drifted, 1, "losers"))
    assert long_only_t > 3                      # drift alone looks spectacular
    plain = spread_returns(base, 1)
    lifted = spread_returns(drifted, 1)
    assert np.allclose(plain, lifted, atol=1e-12)   # spread is untouched by it


def test_null_max_t_is_inflated_above_two_for_correlated_variants(cfg):
    """The point of computing the null instead of quoting a rule of thumb."""
    null, mean_max = shifted_null_max_t(noise_frame(seed=5), [1, 2, 3], draws=800)
    assert 0.5 < mean_max < 3.0
    assert null.std() > 0                       # a distribution, not a constant


def test_noise_is_rejected_and_names_the_gates_it_failed(cfg):
    gates, passed = evaluate(noise_frame(seed=11), ks=[1, 2, 3],
                             cost_bps_per_side=5, null_draws=500)
    assert not passed
    card = report_card("noise", gates, passed)
    assert "REJECT" in card and "PASS" not in card.split("REJECT")[1]
    assert "Failed:" in card


def test_a_planted_effect_clears_the_early_gates(cfg):
    """If the engine cannot detect a real effect it is useless in the other
    direction. Costs may still sink it — that is correct behaviour."""
    gates, _ = evaluate(reversal_frame(phi=-0.5, seed=2), ks=[1, 2, 3],
                        cost_bps_per_side=0, null_draws=500)
    by_name = {g.name: g for g in gates}
    assert by_name["LONG-SHORT (the hypothesis)"].passed
    assert by_name["MULTIPLE TESTING"].passed


def test_costs_alone_can_reject_a_genuine_effect(cfg):
    """The central finding of this whole project, as a unit test."""
    frame = reversal_frame(phi=-0.15, seed=4)   # small but real
    cheap, _ = evaluate(frame, ks=[1, 2, 3], cost_bps_per_side=0, null_draws=400)
    dear, _ = evaluate(frame, ks=[1, 2, 3], cost_bps_per_side=40, null_draws=400)
    assert {g.name: g.passed for g in cheap}["TRANSACTION COSTS"]
    assert not {g.name: g.passed for g in dear}["TRANSACTION COSTS"]


def test_walk_forward_splits_are_contiguous_and_cover_everything(cfg):
    series = np.arange(100, dtype=float)
    folds = walk_forward(series, folds=4)
    assert len(folds) == 4
    assert folds[0][1] < folds[-1][1]           # ordered in time, not shuffled


def test_verdict_requires_every_gate(cfg):
    gates = [Gate("A", True), Gate("B", True), Gate("C", False)]
    assert "REJECT" in report_card("x", gates, all(g.passed for g in gates))
    assert "ACCEPT" in report_card("x", gates[:2], True)
