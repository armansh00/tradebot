"""The vault and the research budget. Both exist to stop the system from
lying to itself later."""
import numpy as np
import pandas as pd
import pytest

from tradebot.research_log import record, render, summary, verify_chain
from tradebot.vault import Vault, VaultError, strategy_hash

DATES = pd.date_range("2025-01-01", periods=400, freq="B").date
SPEC = {"rule": "reversal", "k": 1, "cost_bps": 5, "universe": ["A", "B"]}


def _frame():
    rng = np.random.default_rng(0)
    return pd.DataFrame({c: rng.normal(0, 0.01, len(DATES)) for c in "AB"})


def _vault(tmp_path):
    return Vault(path=tmp_path / "vault.json",
                 research_end=pd.Timestamp("2025-09-30").date(),
                 vault_start=pd.Timestamp("2025-10-01").date())


def test_research_slice_never_returns_vault_period(tmp_path):
    v, df = _vault(tmp_path), _frame()
    research = v.research_slice(df, DATES)
    assert len(research) < len(df)
    # the excluded rows are not filtered later — they are never handed over
    assert len(research) == sum(1 for d in DATES if d <= v.research_end)


def test_seeing_the_data_consumes_the_vault_even_if_evaluation_dies(tmp_path):
    """Opened and passed are different events. A crash after the peek must
    not hand the holdout back."""
    v, df = _vault(tmp_path), _frame()
    v.open(df, DATES, spec=SPEC, name="reversal-v1")
    assert v.status == "CONSUMED"              # before any verdict exists
    with pytest.raises(VaultError):
        v.open(df, DATES, spec={**SPEC, "k": 9}, name="reversal-v2")


def test_code_changes_alter_the_hash_even_with_identical_parameters(tmp_path):
    a = strategy_hash({**SPEC, "code": "aaaaaaaaaaaa"})
    b = strategy_hash({**SPEC, "code": "bbbbbbbbbbbb"})
    assert a != b


def test_float_noise_does_not_fork_a_strategy(tmp_path):
    assert strategy_hash({**SPEC, "cost_bps": 0.05, "code": "x"}) == \
        strategy_hash({**SPEC, "cost_bps": 0.05000000000000001, "code": "x"})


def test_vault_opens_once_and_records_who_spent_it(tmp_path):
    v, df = _vault(tmp_path), _frame()
    assert v.status == "LOCKED"
    held = v.open(df, DATES, spec=SPEC, name="reversal-v1")
    assert v.status == "CONSUMED"
    assert len(held) == sum(1 for d in DATES if d >= v.vault_start)
    # same strategy may re-read its own result
    again = v.open(df, DATES, spec=SPEC, name="reversal-v1")
    assert len(again) == len(held)


def test_a_tweaked_strategy_cannot_reuse_the_holdout(tmp_path):
    """The loophole: open, peek, adjust, call it out-of-sample again."""
    v, df = _vault(tmp_path), _frame()
    v.open(df, DATES, spec=SPEC, name="reversal-v1")
    tweaked = {**SPEC, "k": 2}
    with pytest.raises(VaultError, match="already consumed"):
        v.open(df, DATES, spec=tweaked, name="reversal-v2")


def test_hash_covers_every_defining_choice(tmp_path):
    base = strategy_hash(SPEC)
    for field, value in [("k", 2), ("cost_bps", 10), ("universe", ["A"]),
                         ("rule", "momentum")]:
        assert strategy_hash({**SPEC, field: value}) != base
    assert strategy_hash(dict(reversed(list(SPEC.items())))) == base  # order-free


def test_budget_counts_searching_not_just_finding(tmp_path):
    log = tmp_path / "research_log.jsonl"
    for i in range(5):
        record(log, type="evaluation", strategy_hash=f"h{i % 2}", variants=3,
               verdict="REJECT")
    record(log, type="vault_open", strategy_hash="h0", verdict="REJECT")
    s = summary(log)
    assert s["tests_executed"] == 5
    assert s["parameter_sets_evaluated"] == 15
    assert s["vault_tests"] == 1
    assert s["vault_survivors"] == 0
    assert s["chain_intact"]
    assert "Log chain verified" in render(log)


def test_an_idea_counts_even_when_never_tested(tmp_path):
    """Once the model has seen prior results and proposed a change,
    information has been consumed whether or not a backtest followed."""
    log = tmp_path / "research_log.jsonl"
    record(log, type="idea", note="add a volume filter")
    record(log, type="idea", note="try a 2% threshold")
    record(log, type="evaluation", strategy_hash="h0", variants=3)
    s = summary(log)
    assert s["tests_executed"] == 1
    assert s["ideas_generated"] == 3          # two proposed, one executed


def test_editing_an_old_row_breaks_the_chain(tmp_path):
    log = tmp_path / "research_log.jsonl"
    for i in range(4):
        record(log, type="evaluation", strategy_hash=f"h{i}", variants=1,
               verdict="REJECT")
    assert verify_chain(log) == (True, None)

    lines = log.read_text().splitlines()
    lines[1] = lines[1].replace('"verdict": "REJECT"', '"verdict": "ACCEPT"')
    log.write_text("\n".join(lines) + "\n")

    intact, at = verify_chain(log)
    assert not intact and at == 1
    assert "LOG CHAIN BROKEN" in render(log)


def test_deleting_a_row_breaks_the_chain(tmp_path):
    log = tmp_path / "research_log.jsonl"
    for i in range(4):
        record(log, type="evaluation", strategy_hash=f"h{i}", variants=1)
    lines = log.read_text().splitlines()
    del lines[2]                               # quietly drop one experiment
    log.write_text("\n".join(lines) + "\n")
    assert verify_chain(log)[0] is False


def test_lineage_stops_twenty_edits_looking_like_twenty_hypotheses(tmp_path):
    log = tmp_path / "research_log.jsonl"
    for i in range(20):
        record(log, type="evaluation", strategy_hash=f"v{i}",
               lineage_root="reversal-v1", variants=1)
    record(log, type="evaluation", strategy_hash="other",
           lineage_root="momentum-v1", variants=1)
    s = summary(log)
    assert s["tests_executed"] == 21
    assert s["distinct_lineages"] == 2         # not 21
