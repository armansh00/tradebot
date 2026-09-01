"""Research vintages: one future per information state, not one per strategy.

A separate untouched window for every strategy would make calendar time the
binding constraint almost immediately. It is also more than the logic
requires. What must be unseen is the DATA, and every strategy frozen before a
window opens is equally blind to it — so a whole cohort can share one future.

    VINTAGE 001   information cutoff 2026-08-31
                  30 candidates, 30 tested, 4 promoted, decisions FROZEN
                  future window 2026-09-01 -> 2026-10-31

All four promoted strategies may be judged on that window. Once it is opened
and inspected it is spent for anything created afterwards, because anything
created afterwards could have been informed by it.

The point of the cohort is not efficiency. It is that it makes the SELECTOR
measurable. Freeze the rejected strategies and some random and null ones
alongside the promoted, judge them all on the same window, and the question
stops being "did strategy 17 work" and becomes "does this process pick future
survivors better than chance". A single strategy is a sample of size one; a
cohort with controls is an experiment about the research engine.

Everything is reported twice — by strategy and by lineage — because twenty
descendants of one idea are not twenty discoveries.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

# Outcome of putting a candidate through the research process. Only one of
# these is a control: a strategy that crashed because an API returned no data
# is not evidence against a hypothesis, and counting it as one makes the gates
# look good for reasons that have nothing to do with the gates.
INVALID = "INVALID"                    # code, data or spec cannot be evaluated
INELIGIBLE = "INELIGIBLE"              # violates account, liquidity, or
                                       # minimum-observation requirements
EVIDENCE_REJECTED = "EVIDENCE_REJECTED"  # ran, tested, failed a research gate
PROMOTED = "PROMOTED"                  # ran, tested, passed every gate

STATES = (INVALID, INELIGIBLE, EVIDENCE_REJECTED, PROMOTED)
CONTROL_STATES = (EVIDENCE_REJECTED,)  # and nothing else
TECHNICAL_STATES = (INVALID, INELIGIBLE)

ARMS = ("promoted", "rejected", "random", "null")


class VintageError(RuntimeError):
    pass


@dataclass
class Member:
    strategy_id: str
    lineage: str
    cohort: str                 # one of ARMS
    state: str = PROMOTED       # one of STATES
    mechanism: str | None = None   # preregistered id, e.g. "M01"
    research_rank: int | None = None   # the system's own ordering, 1 = best
    survived: bool | None = None       # filled after the window opens
    oos_metric: float | None = None    # continuous outcome, primary

    @property
    def is_control(self) -> bool:
        return self.state in CONTROL_STATES

    @property
    def is_technical_failure(self) -> bool:
        return self.state in TECHNICAL_STATES


@dataclass
class Vintage:
    path: Path
    name: str
    information_cutoff: date
    window_start: date
    window_end: date
    members: list[Member] = field(default_factory=list)
    frozen_at: str | None = None
    opened_at: str | None = None
    # Vintage 000 was observed while the measuring instrument was still being
    # built. Preserve everything it produces about scheduling, execution,
    # fills and failure modes — and keep it out of confirmatory inference,
    # because its process-level metrics were not frozen before outcomes
    # became visible.
    commissioning: bool = False

    # ---- lifecycle -------------------------------------------------------
    def add(self, m: Member) -> None:
        if self.frozen_at:
            raise VintageError(
                f"{self.name} was frozen at {self.frozen_at}. A strategy added "
                "now could have been informed by results this cohort produced, "
                "so it belongs to the next vintage.")
        if m.cohort not in ARMS:
            raise VintageError(f"cohort must be one of {ARMS}")
        self.members.append(m)

    def executable_pool(self) -> list[Member]:
        """Everything the gates actually got to judge.

        The selector null draws from exactly this set — not from the whole
        list, which would include candidates that never ran, and not from a
        freshly generated one, which the gates never saw.
        """
        return [m for m in self.members if not m.is_technical_failure]

    def freeze(self) -> None:
        if any(m.is_technical_failure and m.cohort == "rejected"
               for m in self.members):
            raise VintageError(
                "a technical failure cannot be a research control — "
                "something that crashed on missing data is not evidence "
                "against the hypothesis")
        if not any(m.cohort == "promoted" for m in self.members):
            raise VintageError("a vintage with no promoted strategies cannot "
                               "measure selection")
        if not any(m.cohort != "promoted" for m in self.members):
            raise VintageError("controls are required — without rejected, "
                               "random or null members there is nothing to "
                               "compare the selector against")
        self.frozen_at = datetime.now(timezone.utc).isoformat()
        self.save()

    def open_window(self) -> None:
        if not self.frozen_at:
            raise VintageError("freeze the cohort before opening its window")
        if not self.opened_at:
            self.opened_at = datetime.now(timezone.utc).isoformat()
            self.save()

    # ---- persistence -----------------------------------------------------
    def save(self) -> None:
        self.path.write_text(json.dumps({
            "name": self.name,
            "information_cutoff": str(self.information_cutoff),
            "window_start": str(self.window_start),
            "window_end": str(self.window_end),
            "frozen_at": self.frozen_at, "opened_at": self.opened_at,
            "commissioning": self.commissioning,
            "members": [m.__dict__ for m in self.members],
        }, indent=2) + "\n")

    @classmethod
    def load(cls, path: Path) -> "Vintage":
        d = json.loads(path.read_text())
        v = cls(path=path, name=d["name"],
                information_cutoff=date.fromisoformat(d["information_cutoff"]),
                window_start=date.fromisoformat(d["window_start"]),
                window_end=date.fromisoformat(d["window_end"]),
                frozen_at=d.get("frozen_at"), opened_at=d.get("opened_at"),
                commissioning=bool(d.get("commissioning", False)))
        v.members = [Member(**m) for m in d["members"]]
        return v


def _rate(members: list[Member]) -> tuple[int, int]:
    judged = [m for m in members if m.survived is not None]
    return sum(1 for m in judged if m.survived), len(judged)


def rank_correlation(members: list[Member]) -> float | None:
    """Spearman rho between the system's research ranking and survival.

    If the engine ranks ten strategies and that ordering has no relationship
    to what survives, then even a winner among them is not evidence that the
    selection mechanism carries information.
    """
    # Rank 1 is the system's best pick, so the raw correlation between rank
    # and survival is negative when the selector works. Sign is flipped here
    # so the reported number reads the obvious way: positive means the
    # ranking predicts survival, zero means it carries no information.
    pairs = [(-m.research_rank, 1.0 if m.survived else 0.0) for m in members
             if m.research_rank is not None and m.survived is not None]
    if len(pairs) < 3:
        return None
    from .stats import spearman_rho
    return spearman_rho([p[0] for p in pairs], [p[1] for p in pairs])


def process_ledger(vintages: list[Vintage]) -> str:
    """What the research engine did, not what any one strategy did."""
    by_cohort = {c: [] for c in ARMS}
    lineages: dict[str, dict] = {}
    for v in vintages:
        for m in v.members:
            by_cohort[m.cohort].append(m)
            L = lineages.setdefault(m.lineage, {"cohort": m.cohort,
                                                "survived": False, "judged": False})
            if m.survived is not None:
                L["judged"] = True
                L["survived"] = L["survived"] or bool(m.survived)

    lines = ["PROCESS LEDGER", "",
             f"  {'Research vintages':<32}{len(vintages):>8,}",
             f"  {'Strategies frozen':<32}"
             f"{sum(len(v.members) for v in vintages):>8,}",
             f"  {'Distinct lineages':<32}{len(lineages):>8,}", "",
             "  Out-of-sample survival, by strategy:"]
    rates = {}
    for c in ARMS:
        hit, n = _rate(by_cohort[c])
        rates[c] = (hit / n) if n else None
        pct = f"{hit / n:6.1%}" if n else "     --"
        lines.append(f"    {c:<28}{hit:>4} / {n:<6}{pct}")

    lines += ["", "  Out-of-sample survival, by lineage:"]
    for c in ARMS:
        group = [L for L in lineages.values() if L["cohort"] == c and L["judged"]]
        hit, n = sum(1 for L in group if L["survived"]), len(group)
        pct = f"{hit / n:6.1%}" if n else "     --"
        lines.append(f"    {c:<28}{hit:>4} / {n:<6}{pct}")

    base = next((rates[c] for c in ("rejected", "random", "null")
                 if rates.get(c)), None)
    if rates.get("promoted") is not None and base:
        lines += ["", f"  {'Selection lift vs controls':<32}"
                      f"{rates['promoted'] / base:>7.1f}x"]
    elif rates.get("promoted") is not None:
        lines += ["", "  Selection lift               not computable — controls",
                  "  have zero survivors, so the ratio is undefined rather than",
                  "  infinite. Report the counts, not a lift."]

    rho = rank_correlation([m for v in vintages for m in v.members])
    if rho is not None:
        lines += ["", f"  {'Research rank vs survival (rho)':<32}{rho:>8.3f}",
                  "  A ranking unrelated to what survives means the selector is",
                  "  not demonstrating information, whatever any single result did."]
    lines += ["", "  Lineage rows exist so that twenty descendants of one idea",
              "  cannot make the process look like twenty discoveries."]
    return "\n".join(lines)


def selector_null(vintage: Vintage, *, metric: str = "oos_metric",
                  draws: int = 20_000, seed: int = 0) -> dict:
    """Did the gates beat drawing the same number of candidates at random
    from the pool they were actually given?

    Not a coin flip, and not a freshly generated strategy the gates never
    considered. The comparator is a random subset of the exact frozen
    executable pool, the same size as the promoted set, with the lineage
    composition preserved.

    Two things the report must not overstate. If every admissible subset is
    enumerated this is an exact conditional permutation test; if 20,000 are
    sampled from a far larger space it is a Monte Carlo permutation test, and
    the p-value is (b + 1) / (B + 1) so that a finite run cannot claim the
    impossible certainty of p = 0.

    Controls are deliberately NOT matched on the characteristics the gates
    select for — volatility, turnover, beta. Matching those away would
    match away the thing under test. Whether selecting on them helped is
    precisely the question; whether it helped for reasons other than market
    exposure is answered by running this on a factor-adjusted metric too.
    """
    import itertools
    import random

    pool = [m for m in vintage.executable_pool()
            if getattr(m, metric) is not None]
    promoted = [m for m in pool if m.state == PROMOTED]
    if len(promoted) < 1 or len(pool) <= len(promoted):
        return {"error": "need promoted members and a larger pool"}

    score = lambda group: sum(getattr(m, metric) for m in group) / len(group)
    observed = score(promoted)

    want: dict[str, int] = {}
    for m in promoted:
        want[m.lineage] = want.get(m.lineage, 0) + 1
    by_lineage: dict[str, list] = {}
    for m in pool:
        by_lineage.setdefault(m.lineage, []).append(m)

    matched = all(len(by_lineage.get(lin, [])) >= k for lin, k in want.items())
    if matched:
        groups = [by_lineage[lin] for lin in want]
        counts = [want[lin] for lin in want]
        total = 1
        for g, k in zip(groups, counts):
            total *= math.comb(len(g), k)
        note = "lineage-matched"
    else:
        groups, counts = [pool], [len(promoted)]
        total = math.comb(len(pool), len(promoted))
        note = "lineage matching not possible — unmatched draw used"

    scores: list[float] = []
    if total <= draws:
        method = "exhaustive"
        per_group = [list(itertools.combinations(g, k))
                     for g, k in zip(groups, counts)]
        for combo in itertools.product(*per_group):
            scores.append(score([m for part in combo for m in part]))
    else:
        method = "monte_carlo"
        rng = random.Random(seed)
        for _ in range(draws):
            pick = []
            for g, k in zip(groups, counts):
                pick += rng.sample(g, k)
            scores.append(score(pick))

    arr = sorted(scores)
    b = sum(1 for x in scores if x >= observed)
    B = len(scores)
    mean = sum(scores) / B
    sd = (sum((x - mean) ** 2 for x in scores) / max(B - 1, 1)) ** 0.5
    p = (b / B) if method == "exhaustive" else ((b + 1) / (B + 1))
    return {"observed": round(observed, 6), "pool": len(pool),
            "promoted": len(promoted), "draws": B, "extreme": b,
            "null_mean": round(mean, 6), "null_sd": round(sd, 6),
            "z": round((observed - mean) / sd, 4) if sd else None,
            "p_value": round(p, 6), "null_method": method, "note": note,
            "p_formula": "b/B" if method == "exhaustive" else "(b+1)/(B+1)",
            "null_scores": arr}
