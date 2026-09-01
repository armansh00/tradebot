"""Machine-evaluated falsification contracts, with three states.

Why not "t < 2 means dead". A t-statistic of 1.2 in a small sample is
compatible with a real effect; treating it as refutation converts every
underpowered test into a mechanism failure and quietly builds a system that
kills true ideas faster than false ones. Falsification requires BOTH that the
preregistered predicate is satisfied AND that there was enough information to
evaluate it.

    FALSIFIED    the predicate holds, with adequate precision
    UNSUPPORTED  not enough evidence either way
    SURVIVES     the prediction held under the preregistered test

SURVIVES never means true. It means this test failed to kill it.

Three verdicts are reported, not one, because they answer different
questions. A signal that exists gross and dies net leaves the MECHANISM
standing and falsifies the ECONOMIC IMPLEMENTATION — and that is worth
knowing, since a non-tradeable phenomenon can still matter for execution, for
filtering another strategy, or for understanding the market.

Predicates are trees, not single comparisons, because a mechanism usually
predicts a RELATIONSHIP: reversal rising with liquidity stress is not
expressible as one number against one threshold.
"""
from __future__ import annotations

from dataclasses import dataclass, field

FALSIFIED = "FALSIFIED"
UNSUPPORTED = "UNSUPPORTED"
SURVIVES = "SURVIVES"


class ContractError(RuntimeError):
    pass


def _monotonic(values: list[float], increasing: bool) -> bool:
    if len(values) < 3:
        raise ContractError("monotonicity needs at least three buckets")
    pairs = zip(values, values[1:])
    return all(b >= a for a, b in pairs) if increasing else \
        all(b <= a for a, b in zip(values, values[1:]))


OPERATORS = {
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "==": lambda a, b: a == b,
    "monotonic_increase": lambda a, _: _monotonic(list(a), True),
    "monotonic_decrease": lambda a, _: _monotonic(list(a), False),
    "not_monotonic": lambda a, _: not (_monotonic(list(a), True)
                                       or _monotonic(list(a), False)),
}


def evaluate_predicate(node: dict, metrics: dict) -> bool:
    """Recursive predicate over measured metrics. Missing metric is an error,
    never a silent False — an unevaluable contract must not read as passing."""
    if "all" in node:
        return all(evaluate_predicate(c, metrics) for c in node["all"])
    if "any" in node:
        return any(evaluate_predicate(c, metrics) for c in node["any"])
    if "not" in node:
        return not evaluate_predicate(node["not"], metrics)
    metric, op = node.get("metric"), node.get("operator")
    if op not in OPERATORS:
        raise ContractError(f"unknown operator {op!r}")
    if metric not in metrics:
        raise ContractError(f"metric {metric!r} was not measured — a contract "
                            "that cannot be evaluated is not a contract")
    return OPERATORS[op](metrics[metric], node.get("value"))


@dataclass
class Prediction:
    id: str
    metric: str
    operator: str
    value: float | None = None

    def holds(self, metrics: dict) -> bool:
        return evaluate_predicate(
            {"metric": self.metric, "operator": self.operator,
             "value": self.value}, metrics)


@dataclass
class Contract:
    hypothesis_id: str
    mechanism_id: str
    predictions: list[Prediction]
    falsifiers: list[dict]          # {"prediction": id, "predicate": {...}}
    minimum_observations: int
    evaluation_dataset: str
    costs: str
    benchmark: str

    def __post_init__(self):
        if not self.predictions:
            raise ContractError("a mechanism must generate at least one "
                                "observable prediction — a story that predicts "
                                "nothing cannot be wrong")
        if not self.falsifiers:
            raise ContractError("at least one executable falsification "
                                "predicate is required")
        ids = {p.id for p in self.predictions}
        for f in self.falsifiers:
            if f.get("prediction") not in ids:
                raise ContractError(
                    f"falsifier references unknown prediction "
                    f"{f.get('prediction')!r}")
            if "predicate" not in f:
                raise ContractError("falsifier has no predicate")


@dataclass
class Outcome:
    layer: str
    status: str
    detail: str
    checks: list[str] = field(default_factory=list)


def _precision_ok(metrics: dict, minimum: int) -> tuple[bool, str]:
    n = metrics.get("observations")
    if n is None:
        return False, "observation count not reported"
    if n < minimum:
        return False, (f"{n} observations against a preregistered minimum of "
                       f"{minimum} — too little information to refute anything")
    return True, f"{n} observations, minimum {minimum}"


def adjudicate(contract: Contract, metrics: dict) -> list[Outcome]:
    """Layered verdict. Statistical rejection and economic falsification are
    kept apart on purpose."""
    ok, why = _precision_ok(metrics, contract.minimum_observations)
    checks = [why]

    triggered = []
    for f in contract.falsifiers:
        if evaluate_predicate(f["predicate"], metrics):
            triggered.append(f["prediction"])
    held = [p.id for p in contract.predictions if p.holds(metrics)]
    checks.append(f"predictions holding: {held or 'none'}")
    checks.append(f"falsifiers triggered: {triggered or 'none'}")

    if triggered and not ok:
        mech = Outcome("MECHANISM", UNSUPPORTED,
                       "falsifier fired but precision was inadequate — "
                       "absence of evidence, not evidence of absence", checks)
    elif triggered:
        mech = Outcome("MECHANISM", FALSIFIED,
                       f"preregistered falsifier(s) {triggered} satisfied with "
                       "adequate precision", checks)
    elif len(held) == len(contract.predictions) and ok:
        mech = Outcome("MECHANISM", SURVIVES,
                       "every preregistered prediction held. This test failed "
                       "to kill it; that is not the same as true", checks)
    else:
        mech = Outcome("MECHANISM", UNSUPPORTED,
                       "predictions not established and falsifiers not "
                       "triggered", checks)

    net = metrics.get("net_expectancy")
    if net is None:
        econ = Outcome("ECONOMIC IMPLEMENTATION", UNSUPPORTED,
                       "net expectancy not measured")
    elif net > 0 and ok:
        econ = Outcome("ECONOMIC IMPLEMENTATION", SURVIVES,
                       f"net expectancy {net:+.2f} bp after costs")
    elif net <= 0 and ok:
        econ = Outcome("ECONOMIC IMPLEMENTATION", FALSIFIED,
                       f"net expectancy {net:+.2f} bp after costs — the "
                       "phenomenon may exist and still not be tradeable")
    else:
        econ = Outcome("ECONOMIC IMPLEMENTATION", UNSUPPORTED, why)

    tradeable = (mech.status == SURVIVES and econ.status == SURVIVES)
    strat = Outcome("STRATEGY", "ACCEPT" if tradeable else "REJECT",
                    "both the mechanism and its economics survived"
                    if tradeable else
                    f"mechanism {mech.status}, economics {econ.status}")
    return [mech, econ, strat]


def render(outcomes: list[Outcome]) -> str:
    out = []
    for o in outcomes:
        out.append(f"{o.layer}")
        out.append(f"  {o.status:<12} {o.detail}")
        out += [f"    - {c}" for c in o.checks]
        out.append("")
    out.append("SURVIVES means this test failed to kill it. It never means true.")
    return "\n".join(out)
