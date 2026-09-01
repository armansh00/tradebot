"""The vault and the research budget. Both exist to stop the system from
lying to itself later."""
import numpy as np
import pandas as pd
import pytest

from tradebot.research_log import record, render, summary
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
    assert s["hypotheses"] == 5
    assert s["parameter_combinations"] == 15
    assert s["strategies_distinct"] == 2
    assert s["reached_vault"] == 1
    assert s["survived_vault"] == 0
    assert "48,000" in render(log)


def test_budget_survives_a_corrupt_line(tmp_path):
    log = tmp_path / "research_log.jsonl"
    record(log, type="evaluation", variants=1)
    log.write_text(log.read_text() + "{not json\n")
    record(log, type="evaluation", variants=1)
    assert summary(log)["hypotheses"] == 2
