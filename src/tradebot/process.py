"""Process-level inference: is the SELECTOR informative across futures?

The unit of inference is the vintage, not the strategy. Twenty lineages
evaluated in one eight-week window share a market trend, a volatility level, a
liquidity regime, a macro calendar. Even with genuinely different mechanisms
their outcomes stay correlated, so the effective sample is far below the head
count:

    n_eff = m / (1 + (m - 1) * rho)

Twenty lineages at rho = 0.20 carry about 4.2 independent observations. Which
means "promote twenty instead of four" does not divide the required number of
vintages by five, and I claimed it did.

The fix is to pair inside each vintage. Both the promoted set and the random
lineage-matched set live through the same future, so market-state variance
largely cancels and what remains is the selector's contribution:

    delta_v = M(promoted) - E[M(lineage-matched random draw)]

The process-level object is then the sequence delta_1 ... delta_V, and the
hypothesis is E[delta_v] > 0: across futures, does the selector consistently
beat random selection from the pool it was given?

One reading rule that has to travel with the number: delta measures the
SELECTOR, not profitability. A positive delta with both arms losing money
means the gates pick the least bad, which is real information about the
selector and none at all about whether anything makes money. Absolute levels
are therefore reported beside it, never replaced by it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .stats import norm_ppf


def design_effect(m: int, rho: float) -> float:
    """Effective independent observations from m clustered ones."""
    if m <= 1:
        return float(m)
    return m / (1 + (m - 1) * max(rho, 0.0))


@dataclass
class VintageDelta:
    vintage: str
    promoted_metric: float
    null_mean: float
    delta: float
    percentile: float          # where promoted sits in the null distribution
    p_value: float             # one-sided, from the same draws
    lineages_promoted: int
    lineages_pool: int
    commissioning: bool = False   # excluded from confirmatory inference

    def lines(self) -> list[str]:
        return [
            f"promoted OOS metric   {self.promoted_metric:+10.4f}",
            f"random-selector mean  {self.null_mean:+10.4f}",
            f"selector advantage    {self.delta:+10.4f}",
            f"percentile            {self.percentile:9.1f}%",
            f"permutation p         {self.p_value:10.4f}",
            f"lineages              {self.lineages_promoted} promoted "
            f"of {self.lineages_pool} in pool",
        ]


def fisher_combine(p_values: list[float]) -> tuple[float, float]:
    """Fisher's method: (chi-square statistic, degrees of freedom).

    Why this and not a t-test on the deltas. Each vintage yields an EXACT
    within-vintage permutation p-value, available at V = 1. A one-sample t
    across vintages needs many vintages before it can say anything, and
    vintages arrive at the speed of the calendar. Combining exact p-values
    has power at V = 3 where a t-test has essentially none.
    """
    ps = [min(max(p, 1e-12), 1.0) for p in p_values]
    return -2 * sum(math.log(p) for p in ps), 2 * len(ps)


def chi2_sf(x: float, df: int) -> float:
    """Upper tail of a chi-square with even df — exact, no scipy."""
    if x <= 0:
        return 1.0
    k = df // 2
    term, total = 1.0, 1.0
    for i in range(1, k):
        term *= (x / 2) / i
        total += term
    return min(1.0, math.exp(-x / 2) * total)


def stouffer_combine(p_values: list[float], weights: list[float] | None = None
                     ) -> float:
    """Weighted combination, so a vintage with 40 lineages counts for more
    than one with 6."""
    if not p_values:
        return 1.0
    w = weights or [1.0] * len(p_values)
    zs = [norm_ppf(1 - min(max(p, 1e-12), 1 - 1e-12)) for p in p_values]
    num = sum(wi * z for wi, z in zip(w, zs))
    den = math.sqrt(sum(wi ** 2 for wi in w))
    from .stats import norm_cdf
    return round(1 - norm_cdf(num / den), 5) if den else 1.0


def process_report(deltas: list[VintageDelta], rho_estimate: float = 0.2
                   ) -> str:
    confirmatory = [d for d in deltas if not d.commissioning]
    out = ["PROCESS-LEVEL OUTCOME", "",
           "  Primary: paired selector advantage per vintage, promoted set",
           "  against a lineage-matched random draw from the same frozen pool,",
           "  both living through the same future.", ""]
    for d in deltas:
        tag = "  (commissioning — excluded from inference)" if d.commissioning else ""
        out.append(f"  VINTAGE {d.vintage}{tag}")
        out += [f"    {l}" for l in d.lines()]
        out.append("")

    if not confirmatory:
        out.append("  No confirmatory vintages yet. Nothing here is evidence "
                   "about the selector.")
        return "\n".join(out)

    ps = [d.p_value for d in confirmatory]
    stat, df = fisher_combine(ps)
    fisher_p = chi2_sf(stat, df)
    stouffer_p = stouffer_combine(
        ps, [float(d.lineages_promoted) for d in confirmatory])
    mean_delta = sum(d.delta for d in confirmatory) / len(confirmatory)
    positive = sum(1 for d in confirmatory if d.delta > 0)

    m = sum(d.lineages_promoted for d in confirmatory) / len(confirmatory)
    out += [
        f"  {'Confirmatory vintages':<32}{len(confirmatory):>10}",
        f"  {'Mean selector advantage':<32}{mean_delta:>+10.4f}",
        f"  {'Vintages with advantage > 0':<32}"
        f"{positive:>4} of {len(confirmatory)}",
        f"  {'Fisher combined p':<32}{fisher_p:>10.4f}",
        f"  {'Stouffer combined p':<32}{stouffer_p:>10.4f}",
        "",
        f"  Mean lineages per vintage {m:.1f}; at an assumed within-vintage",
        f"  outcome correlation of {rho_estimate:.2f} that is about "
        f"{design_effect(int(round(m)), rho_estimate):.1f} independent",
        "  observations, not the head count. Once several vintages exist,",
        "  estimate that correlation from the data rather than assuming it.",
        "",
        "  Selector advantage is not profit. A positive advantage with both",
        "  sets losing money means the gates pick the least bad — informative",
        "  about the selector, silent about whether anything makes money.",
    ]
    return "\n".join(out)
