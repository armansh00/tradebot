"""The scorer is arithmetic, and these tests pin the arithmetic.

The point of a separate scorer is that the verdict does not depend on who is
writing it up. That only holds if the arithmetic is fixed in advance, so it is
fixed here — including the cases the scorer is supposed to refuse.
"""
import pytest

from tradebot.forecast import (UNADJUDICABLE, Market, aggregate,
                               climatology_from_closes, score)

FORECAST = {
    "forecast_for": "2026-09-04",
    "spy_expected_range_low_pct": 0.90,
    "spy_expected_range_high_pct": 1.35,
    "spy_p_down": 0.52,
    "relative_call": "semiconductors_over_energy",
    "relative_call_instruments": ["SMH", "XLE"],
    "invalidation": {"instrument": "WTI", "condition": ">=", "level": 93.0,
                     "before": "09:30 ET"},
}


def mkt(prior=773.17, high=780.0, low=770.0, close=775.0, smh=0.5, xle=0.1):
    return Market(prior_close=prior, high=high, low=low, close=close,
                  sector={"SMH": smh, "XLE": xle}, source="test")


# ---------------------------------------------------------------- range

def test_range_is_measured_against_the_prior_close():
    r = score(FORECAST, mkt(prior=767.45, high=774.03, low=767.45))["range"]
    assert r["realized_pct"] == pytest.approx(0.8574, abs=1e-4)


def test_a_realized_range_inside_the_interval_hits_with_no_penalty():
    r = score(FORECAST, mkt(prior=100.0, high=101.0, low=100.0))["range"]
    assert r["inside"] and r["miss_pct"] == 0.0


def test_a_miss_records_how_far_outside_it_landed():
    """A 0.01pp miss and a rout are not the same result, and a binary grade
    says they are."""
    near = score(FORECAST, mkt(prior=100.0, high=100.89, low=100.0))["range"]
    far = score(FORECAST, mkt(prior=100.0, high=103.0, low=100.0))["range"]
    assert not near["inside"] and not far["inside"]
    assert near["miss_pct"] == pytest.approx(0.01, abs=1e-6)
    assert far["miss_pct"] == pytest.approx(1.65, abs=1e-6)
    assert far["miss_pct"] > near["miss_pct"]


def test_no_interval_score_is_invented():
    """Winkler needs a declared coverage level. The forecasts state none, so
    the field stays empty and says why rather than assuming 80% or 90%."""
    r = score(FORECAST, mkt())["range"]
    assert r["interval_score"] is None and "coverage" in r["interval_score_note"]


# ------------------------------------------------------------- direction

def test_brier_on_a_day_that_closed_up():
    """The 2026-09-03 case worked by hand: p=0.54 on a down day that did not
    happen gives 0.2916."""
    f = {**FORECAST, "spy_p_down": 0.54}
    d = score(f, mkt(prior=765.0, close=773.17))["direction"]
    assert d["outcome_down"] is False
    assert d["brier"] == pytest.approx(0.2916, abs=1e-9)


def test_brier_on_a_day_that_closed_down():
    f = {**FORECAST, "spy_p_down": 0.54}
    d = score(f, mkt(prior=780.0, close=770.0))["direction"]
    assert d["outcome_down"] is True
    assert d["brier"] == pytest.approx(0.2116, abs=1e-9)


def test_an_unchanged_close_is_not_a_down_day():
    d = score(FORECAST, mkt(prior=770.0, close=770.0))["direction"]
    assert d["outcome_down"] is False


def test_no_skill_score_is_reported_for_a_single_session():
    """With n=1 the reference Brier can sit arbitrarily near zero and the
    ratio is noise wearing a decimal point."""
    d = score(FORECAST, mkt(), climatology={"p_down": 0.46})["direction"]
    assert d["climatology_brier"] == pytest.approx(0.2116, abs=1e-9)
    assert "brier_skill_score" not in d
    assert "n=1" in d["skill_note"]


# -------------------------------------------------------------- relative

def test_the_predicted_winner_comes_from_the_instrument_order():
    """Not from parsing English. 'semiconductors_over_energy' reads fine until
    someone writes 'energy_under_semis' and the scorer inverts a verdict."""
    hit = score(FORECAST, mkt(smh=0.31, xle=0.11))["relative"]
    assert hit["predicted_winner"] == "SMH" and hit["hit"] is True
    assert hit["spread_pp"] == pytest.approx(0.20, abs=1e-9)

    miss = score(FORECAST, mkt(smh=-0.40, xle=0.60))["relative"]
    assert miss["hit"] is False


def test_a_missing_sector_return_is_refused_not_guessed():
    m = Market(773.0, 780.0, 770.0, 775.0, {"SMH": 0.3}, "test")
    assert score(FORECAST, m)["relative"]["status"] == UNADJUDICABLE


# ---------------------------------------------------------- invalidation

def test_the_wti_condition_is_always_unadjudicable():
    """This repository cannot query crude futures. Saying so every day is the
    honest way to keep saying it — a commitment nobody can settle is not a
    commitment."""
    inv = score(FORECAST, mkt())["invalidation"]
    assert inv["status"] == UNADJUDICABLE
    assert inv["instrument"] == "WTI" and inv["level"] == 93.0


# ------------------------------------------------------------- aggregate

def test_skill_appears_once_there_is_a_sample():
    clim = {"p_down": 0.46}
    up = score({**FORECAST, "spy_p_down": 0.52}, mkt(prior=770.0, close=775.0), clim)
    down = score({**FORECAST, "spy_p_down": 0.52}, mkt(prior=780.0, close=770.0), clim)
    agg = aggregate([up, down], clim)

    assert agg["n"] == 2 and agg["direction"]["n"] == 2
    assert agg["direction"]["down_days"] == 1
    assert agg["direction"]["mean_brier"] == pytest.approx((0.52**2 + 0.48**2) / 2)
    assert agg["direction"]["brier_skill_score"] is not None


def test_aggregate_counts_range_and_relative_too():
    a = score(FORECAST, mkt(prior=100.0, high=101.0, low=100.0, smh=1.0, xle=0.0))
    b = score(FORECAST, mkt(prior=100.0, high=105.0, low=100.0, smh=0.0, xle=1.0))
    agg = aggregate([a, b])
    assert agg["range"]["hit_rate"] == 0.5
    assert agg["relative"]["hit_rate"] == 0.5
    assert agg["range"]["mean_miss_pct"] > 0


# ----------------------------------------------------------- climatology

def test_the_base_rate_counts_down_days():
    closes = [100, 99, 101, 100, 100.5, 99.5]        # down, up, down, up, down
    c = climatology_from_closes(closes, through="2026-01-31")
    assert c["sessions"] == 5 and c["down_days"] == 3
    assert c["p_down"] == pytest.approx(0.6)
    assert c["through"] == "2026-01-31"


def test_an_empty_history_yields_no_base_rate_rather_than_a_guess():
    assert climatology_from_closes([], through="2026-01-31")["p_down"] is None


def test_the_frozen_file_on_disk_is_scoreable():
    """The schema and the scorer have to agree about the real file, not just
    about a fixture. This is the one forecast that existed in the repository
    before its session, so it is the one that counts."""
    import json
    from pathlib import Path
    path = Path(__file__).resolve().parent.parent / "forecasts" / "2026-09-04.json"
    frozen = json.loads(path.read_text())

    out = score(frozen, mkt(prior=773.17, high=780.0, low=770.0, close=775.0,
                            smh=0.5, xle=0.1), {"p_down": 0.46})

    assert out["forecast_for"] == "2026-09-04"
    assert out["range"]["predicted_low_pct"] == 0.90
    assert out["range"]["predicted_high_pct"] == 1.35
    assert out["direction"]["p_down"] == 0.52
    assert out["relative"]["predicted_winner"] == "SMH"
    assert out["invalidation"]["status"] == UNADJUDICABLE
