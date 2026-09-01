"""Vintages and the process ledger. The object under evaluation here is the
research engine, not any strategy it produced."""
from datetime import date

import pytest

from tradebot.vintage import (Member, Vintage, VintageError, process_ledger,
                              rank_correlation)


def _v(tmp_path, name="001"):
    return Vintage(path=tmp_path / f"vintage-{name}.json", name=name,
                   information_cutoff=date(2026, 8, 31),
                   window_start=date(2026, 9, 1), window_end=date(2026, 10, 31))


def _populate(v, promoted=4, rejected=8, survive_promoted=2, survive_rejected=1):
    for i in range(promoted):
        v.add(Member(f"p{i}", lineage=f"lin{i}", cohort="promoted",
                     research_rank=i + 1, survived=i < survive_promoted))
    for i in range(rejected):
        v.add(Member(f"r{i}", lineage=f"lin{i}", cohort="rejected",
                     research_rank=promoted + i + 1,
                     survived=i < survive_rejected))
    return v


def test_a_cohort_shares_one_future_because_none_of_them_saw_it(tmp_path):
    v = _populate(_v(tmp_path))
    v.freeze()
    v.open_window()
    assert len(v.members) == 12
    assert v.window_start > v.information_cutoff


def test_nothing_may_join_after_the_freeze(tmp_path):
    """A strategy created later could have been informed by this cohort's
    results, so it belongs to the next vintage."""
    v = _populate(_v(tmp_path))
    v.freeze()
    with pytest.raises(VintageError, match="belongs to the next vintage"):
        v.add(Member("late", lineage="linX", cohort="promoted"))


def test_a_cohort_without_controls_cannot_be_frozen(tmp_path):
    v = _v(tmp_path)
    v.add(Member("p0", lineage="a", cohort="promoted"))
    with pytest.raises(VintageError, match="controls are required"):
        v.freeze()


def test_a_cohort_without_promotions_cannot_be_frozen(tmp_path):
    v = _v(tmp_path)
    v.add(Member("r0", lineage="a", cohort="rejected"))
    with pytest.raises(VintageError, match="no promoted strategies"):
        v.freeze()


def test_the_window_opens_once_and_records_when(tmp_path):
    v = _populate(_v(tmp_path))
    v.freeze()
    v.open_window()
    first = v.opened_at
    v.open_window()
    assert v.opened_at == first


def test_ledger_reports_selection_lift_against_controls(tmp_path):
    v = _populate(_v(tmp_path), promoted=4, rejected=8,
                  survive_promoted=2, survive_rejected=1)
    v.freeze()
    text = process_ledger([v])
    assert "promoted" in text and "rejected" in text
    assert "50.0%" in text          # 2 of 4
    assert "12.5%" in text          # 1 of 8
    assert "4.0x" in text           # the lift


def test_ledger_reports_by_lineage_so_variants_do_not_inflate_it(tmp_path):
    """Twenty descendants of one idea are not twenty discoveries."""
    v = _v(tmp_path)
    for i in range(20):
        v.add(Member(f"p{i}", lineage="reversal", cohort="promoted",
                     research_rank=i + 1, survived=True))
    v.add(Member("c0", lineage="control", cohort="random",
                 research_rank=21, survived=False))
    v.freeze()
    text = process_ledger([v])
    strategy_block = text.split("by strategy:")[1].split("by lineage:")[0]
    lineage_block = text.split("by lineage:")[1]
    assert "20 / 20" in strategy_block.replace("  ", " ")
    assert "1 / 1" in lineage_block.replace("  ", " ")


def test_lift_is_refused_rather_than_reported_as_infinite(tmp_path):
    v = _v(tmp_path)
    v.add(Member("p0", lineage="a", cohort="promoted", survived=True))
    v.add(Member("r0", lineage="b", cohort="rejected", survived=False))
    v.freeze()
    text = process_ledger([v])
    assert "not computable" in text
    assert "x" not in text.split("Selection lift")[1][:20]


def _members(f, n=6):
    return [Member(f"s{i}", "l", "promoted", research_rank=i + 1, survived=f(i))
            for i in range(n)]


def test_rank_correlation_reads_the_obvious_way(tmp_path):
    """Positive means the system's ranking predicted survival."""
    assert rank_correlation(_members(lambda i: i < 3)) > 0.8      # top picks won
    assert rank_correlation(_members(lambda i: i >= 3)) < -0.8    # backwards


def test_identical_outcomes_yield_no_correlation_not_a_perfect_one(tmp_path):
    """The tie-handling bug: ranking equal values by position invents variance
    that is not in the data, and reported rho = -1.0 for a set of strategies
    whose outcomes were all the same."""
    assert rank_correlation(_members(lambda i: False)) is None
    assert rank_correlation(_members(lambda i: True)) is None
