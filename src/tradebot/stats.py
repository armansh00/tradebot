"""Deflated Sharpe and probability of backtest overfitting — as diagnostics.

Both are reported component by component, never as a bare boolean. A method
that collapses into PASS/FAIL stops being a diagnosis and becomes a rubber
stamp; the engine still issues a verdict, but downstream of numbers a human
can inspect and disagree with.

Deflated Sharpe follows Bailey and Lopez de Prado (2014): correct the observed
Sharpe for the number of trials, the dispersion among them, the length of the
sample, and the non-normality of returns. PBO follows their combinatorially
symmetric cross-validation: split the record into partitions, and ask how often
the configuration that ranks best in-sample lands in the bottom half
out-of-sample.
"""
from __future__ import annotations

import itertools
import math
from dataclasses import dataclass

import numpy as np

EULER = 0.5772156649015329


def _ranks(values: list[float]) -> list[float]:
    """Ranks with ties AVERAGED, which is the actual definition.

    Breaking ties by position instead invents variance that is not in the
    data: a constant series gets ranks 1..n and then correlates perfectly
    with anything. That bug reported rho = -1.0 for a set of strategies whose
    outcomes were all identical.
    """
    order = sorted(range(len(values)), key=lambda i: values[i])
    out = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            out[order[k]] = avg
        i = j + 1
    return out


def spearman_rho(x: list[float], y: list[float]) -> float | None:
    """None when either series is constant — no ranking information exists."""
    n = len(x)
    if n < 2:
        return None
    rx, ry = _ranks(list(x)), _ranks(list(y))
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    return round(num / den, 3) if den else None


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_ppf(p: float) -> float:
    """Inverse normal CDF, Acklam's rational approximation (|error| < 1e-9)."""
    p = min(max(p, 1e-12), 1 - 1e-12)
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    pl, ph = 0.02425, 1 - 0.02425
    if p < pl:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > ph:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
                ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q, r = p - 0.5, (p - 0.5) ** 2
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
           (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


@dataclass
class DeflatedSharpe:
    observed: float
    trials: int
    trial_dispersion: float
    expected_max: float
    skew: float
    kurtosis: float
    periods: int
    deflated: float          # P(true Sharpe > 0) after all corrections

    def lines(self) -> list[str]:
        return [
            f"observed Sharpe        {self.observed:+8.3f}",
            f"trials considered      {self.trials:8d}",
            f"dispersion across      {self.trial_dispersion:8.3f}",
            f"expected max under H0  {self.expected_max:+8.3f}",
            f"skew                   {self.skew:+8.3f}",
            f"kurtosis               {self.kurtosis:8.3f}",
            f"observations           {self.periods:8d}",
            f"deflated Sharpe        {self.deflated:8.3f}  "
            "(probability the true Sharpe exceeds zero)",
        ]


def sharpe(x: np.ndarray) -> float:
    x = np.asarray(x, dtype=float)
    sd = x.std(ddof=1)
    return float(x.mean() / sd) if sd else 0.0


def deflated_sharpe(returns: np.ndarray, trial_sharpes: list[float]
                    ) -> DeflatedSharpe:
    r = np.asarray(returns, dtype=float)
    n, sr = len(r), sharpe(returns)
    trials = max(len(trial_sharpes), 1)
    disp = float(np.std(trial_sharpes, ddof=1)) if trials > 1 else 0.0

    # expected maximum Sharpe from `trials` draws of a zero-Sharpe process
    if trials > 1 and disp > 0:
        emax = disp * ((1 - EULER) * norm_ppf(1 - 1 / trials)
                       + EULER * norm_ppf(1 - 1 / (trials * math.e)))
    else:
        emax = 0.0

    mu, sd = r.mean(), r.std(ddof=1)
    if sd == 0 or n < 3:
        skew = kurt = 0.0
    else:
        z = (r - mu) / sd
        skew = float((z ** 3).mean())
        kurt = float((z ** 4).mean())

    denom = math.sqrt(max(1e-12, 1 - skew * sr + (kurt - 1) / 4 * sr ** 2))
    stat = (sr - emax) * math.sqrt(max(n - 1, 1)) / denom
    return DeflatedSharpe(observed=sr, trials=trials, trial_dispersion=disp,
                          expected_max=emax, skew=skew, kurtosis=kurt,
                          periods=n, deflated=norm_cdf(stat))


@dataclass
class PBO:
    configurations: int
    partitions: int
    combinations: int
    median_oos_rank: float
    pbo: float

    def lines(self) -> list[str]:
        return [
            f"configurations         {self.configurations:8d}",
            f"partitions             {self.partitions:8d}",
            f"combinations tested    {self.combinations:8d}",
            f"median OOS rank of the best-in-sample  "
            f"{self.median_oos_rank:5.2f}  (0 = worst, 1 = best)",
            f"PBO                    {self.pbo:8.3f}  "
            "(probability the in-sample winner is below median out of sample)",
        ]


def pbo(matrix: np.ndarray, partitions: int = 8) -> PBO:
    """Combinatorially symmetric cross-validation.

    `matrix` is periods x configurations. Every balanced split of the record
    into halves is used both ways round, which is what makes it symmetric —
    no split is privileged as "the" training set.
    """
    T, N = matrix.shape
    partitions = max(2, partitions - partitions % 2)
    edges = np.array_split(np.arange(T), partitions)
    logits, ranks = [], []
    for combo in itertools.combinations(range(partitions), partitions // 2):
        is_idx = np.concatenate([edges[i] for i in combo])
        oos_idx = np.concatenate([edges[i] for i in range(partitions)
                                  if i not in combo])
        is_sr = np.array([sharpe(matrix[is_idx, j]) for j in range(N)])
        oos_sr = np.array([sharpe(matrix[oos_idx, j]) for j in range(N)])
        best = int(np.argmax(is_sr))
        # relative rank of that choice out of sample, 0 worst .. 1 best
        rank = float((oos_sr < oos_sr[best]).sum()) / max(N - 1, 1)
        ranks.append(rank)
        w = min(max(rank, 1e-6), 1 - 1e-6)
        logits.append(math.log(w / (1 - w)))
    return PBO(configurations=N, partitions=partitions,
               combinations=len(logits),
               median_oos_rank=float(np.median(ranks)),
               # fraction of splits where the in-sample winner lands below
               # median out of sample — NOT the mean of a list of ones
               pbo=(sum(1 for l in logits if l <= 0) / len(logits))
               if logits else 0.0)


def auc(scores: list[float], labels: list[bool]) -> float | None:
    """P(a survivor is ranked above a failure), ties counted as half.

    The natural statistic when the future outcome is binary. Spearman still
    works with averaged ties, but on a dichotomous outcome the two are
    MONOTONE in each other — they always order a set of selectors the same
    way — so they are one piece of evidence in two dresses, not two
    confirmations. (The exact identity 2*AUC - 1 is the RANK-BISERIAL
    correlation; Spearman is the Pearson correlation of ranks and differs
    from it by a factor that depends on class balance. Checked numerically
    rather than assumed, after asserting the wrong identity once.)

    AUC is the one to report, because it has a direct reading: the
    probability that a survivor was ranked above a failure.

    None when every strategy survived or none did — no discrimination exists
    to measure, exactly as the corrected rank correlation returns None.
    """
    pos = [s for s, y in zip(scores, labels) if y]
    neg = [s for s, y in zip(scores, labels) if not y]
    if not pos or not neg:
        return None
    wins = sum(1.0 if p > n else 0.5 if p == n else 0.0
               for p in pos for n in neg)
    return round(wins / (len(pos) * len(neg)), 4)


def two_proportion_power_n(p_treat: float, p_control: float,
                           alpha: float = 0.05, power: float = 0.80) -> int:
    """Strategies needed PER ARM to detect this difference in survival rates.

    Included because the process-level question is itself a low-power
    experiment, and it is better to know that before spending two years
    collecting an answer that cannot reach significance. A survival rate of
    3% against a background of 1% is a threefold lift and a two-point
    difference; the second number is what determines the sample size.
    """
    if p_treat == p_control:
        return 10 ** 9
    za, zb = norm_ppf(1 - alpha / 2), norm_ppf(power)
    var = p_treat * (1 - p_treat) + p_control * (1 - p_control)
    return int(math.ceil((za + zb) ** 2 * var / (p_treat - p_control) ** 2))


def two_sample_power_n(effect_sd: float, alpha: float = 0.05,
                       power: float = 0.80) -> int:
    """Per-arm n for a continuous outcome, effect expressed in SDs.

    A continuous out-of-sample metric — net expectancy, net Sharpe — needs
    far fewer strategies than a binary survived/failed flag, because
    dichotomising throws away most of the information. That is the practical
    argument for making the continuous comparison primary.
    """
    if effect_sd <= 0:
        return 10 ** 9
    za, zb = norm_ppf(1 - alpha / 2), norm_ppf(power)
    return int(math.ceil(2 * ((za + zb) / effect_sd) ** 2))


def risk_comparison(hits_t: int, n_t: int, hits_c: int, n_c: int) -> dict:
    """Difference AND ratio, with a Wald interval on the difference.

    A ratio alone flatters small numbers: 1% to 3% is 'three times better'
    and two percentage points. Both belong in the report.
    """
    if not n_t or not n_c:
        return {"error": "an arm has no judged members"}
    pt, pc = hits_t / n_t, hits_c / n_c
    se = math.sqrt(pt * (1 - pt) / n_t + pc * (1 - pc) / n_c)
    lo, hi = (pt - pc) - 1.96 * se, (pt - pc) + 1.96 * se
    return {"p_treat": round(pt, 4), "p_control": round(pc, 4),
            "risk_difference": round(pt - pc, 4),
            "ci95": (round(lo, 4), round(hi, 4)),
            "risk_ratio": round(pt / pc, 3) if pc else None,
            "crosses_zero": lo <= 0 <= hi}
