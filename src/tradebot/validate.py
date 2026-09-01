"""A validation engine whose job is to reject things.

Design principle, stated so it cannot quietly erode: REJECT is the default
verdict and every gate must be passed to escape it. Nothing here is built to
find edges. Everything here is built to kill apparent ones.

The gates, in order, and what each one is protecting against:

  DISCOVERY          is there anything at all
  LONG-SHORT         is it the hypothesis, or just owning risky stocks
  MULTIPLE TESTING   is it the best of many looks
  BENCHMARK          is it better than doing nothing, or than the universe
  COSTS              does it survive the spread
  WALK-FORWARD       does it hold in periods it was not chosen on
  PARAMETER STABILITY is it a region or a lucky point

Two design notes worth defending.

Multiple-testing correction is COMPUTED, not asserted. A rule of thumb like
"six tries means the expected max t is about 1.7" assumes independence, and
six variants of one trade are anything but independent. Instead the null is
built empirically: circularly shift yesterday's ranking against today's
returns, which destroys the predictive link while preserving both the
cross-sectional structure and the time-series autocorrelation, then read off
where the observed max sits in that distribution.

Parameter stability asks for a REGION. A single threshold that works while
its neighbours do not is an optimised parameter, not a phenomenon.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class Gate:
    name: str
    passed: bool
    lines: list[str] = field(default_factory=list)


def t_stat(xs) -> float:
    xs = np.asarray(xs, dtype=float)
    if len(xs) < 2:
        return 0.0
    sd = xs.std(ddof=1)
    return float(xs.mean() / (sd / len(xs) ** 0.5)) if sd else 0.0


def _legs(signal: np.ndarray, outcome: np.ndarray, k: int
          ) -> tuple[np.ndarray, np.ndarray]:
    """(loser leg, winner leg) next-day returns, vectorised.

    Rank row i-1 of `signal`, read row i of `outcome`. Keeping the two frames
    separate is what lets the null hypothesis shift one against the other.
    """
    order = np.argsort(signal[:-1], axis=1)
    nxt = outcome[1:]
    rows = np.arange(nxt.shape[0])[:, None]
    lo = nxt[rows, order[:, :k]].mean(axis=1)
    hi = nxt[rows, order[:, -k:]].mean(axis=1)
    return lo, hi


def leg_returns(rets: pd.DataFrame, k: int, side: str) -> np.ndarray:
    """Next-day return of the k extreme names ranked by yesterday's move."""
    v = rets.to_numpy(dtype=float)
    lo, hi = _legs(v, v, k)
    return lo if side == "losers" else hi


def spread_returns(rets: pd.DataFrame, k: int) -> np.ndarray:
    """Losers minus winners, same day, same universe.

    This is the hypothesis. E[R|loser] > 0 can be satisfied by owning volatile
    stocks in a rising market; E[R|loser] - E[R|winner] cannot, because both
    legs carry the market.
    """
    v = rets.to_numpy(dtype=float)
    lo, hi = _legs(v, v, k)
    return lo - hi


def equal_weight(rets: pd.DataFrame) -> np.ndarray:
    return rets.iloc[1:].mean(axis=1).dropna().to_numpy(dtype=float)


# Declared in advance and capped, so the system cannot keep buying
# permutations until an unstable p-value lands on the side it prefers.
# One escalation, one ceiling, both fixed before any result is seen.
ESCALATION = {"screen": 400, "boundary": 20_000, "tail": 5_000}
BOUNDARY = (0.01, 0.15)


def resolution_for(observed: float, null: np.ndarray) -> int:
    """How many more draws this result deserves — from a fixed ladder.

    400 is plenty to establish that p is nowhere near 0.05. It is not enough
    to resolve the tail if the statistic sits near the boundary, where the
    answer depends on the third decimal. The escalation happens at most once;
    an unstable p-value is a finding, not an invitation to keep drawing.
    """
    p = float((null >= observed).mean())
    if BOUNDARY[0] <= p <= BOUNDARY[1]:
        return ESCALATION["boundary"]
    if p < BOUNDARY[0]:
        return ESCALATION["tail"]
    return 0


def shifted_null_max_t(rets: pd.DataFrame, ks: list[int], draws: int = 400,
                       seed: int = 0) -> tuple[np.ndarray, float]:
    """Empirical distribution of max |t| when the signal cannot work.

    Rotating the ranking against the outcome by a random lag severs the
    predictive relationship while leaving every other feature of the data
    intact. Whatever max |t| this produces is what the search procedure
    manufactures from nothing.
    """
    rng = np.random.default_rng(seed)
    v = rets.to_numpy(dtype=float)
    n = len(v)
    maxima = []
    for _ in range(draws):
        shifted = np.roll(v, int(rng.integers(2, n - 2)), axis=0)
        ts = []
        for k in ks:
            lo, hi = _legs(shifted, v, k)      # signal shifted, outcome real
            ts.append(abs(t_stat(lo - hi)))
        maxima.append(max(ts))
    return np.asarray(maxima), float(np.mean(maxima))


def walk_forward(series: np.ndarray, folds: int = 4) -> list[tuple[int, float, float]]:
    """(fold, mean bps, t) over contiguous blocks — no shuffling, no leakage."""
    out = []
    size = len(series) // folds
    for i in range(folds):
        chunk = series[i * size:(i + 1) * size] if i < folds - 1 \
            else series[i * size:]
        if len(chunk) > 2:
            out.append((i + 1, float(chunk.mean()) * 1e4, t_stat(chunk)))
    return out


def evaluate(rets: pd.DataFrame, *, ks: list[int], cost_bps_per_side: float,
             benchmark: str = "SPY", name: str = "daily reversal",
             null_draws: int = 400) -> tuple[list[Gate], bool]:
    gates: list[Gate] = []
    round_trip = 2 * cost_bps_per_side / 1e4

    # --- 1. discovery ----------------------------------------------------
    best_k = max(ks, key=lambda k: t_stat(spread_returns(rets, k)))
    long_only = leg_returns(rets, best_k, "losers")
    t_long = t_stat(long_only)
    gates.append(Gate("DISCOVERY", abs(t_long) >= 2.0, [
        f"long-only expectancy   {long_only.mean() * 1e4:+8.2f} bp/day",
        f"t-stat                 {t_long:+8.2f}",
        f"observations           {len(long_only):8d}",
    ]))

    # --- 2. long-short: the actual hypothesis ----------------------------
    spread = spread_returns(rets, best_k)
    t_spread = t_stat(spread)
    gates.append(Gate("LONG-SHORT (the hypothesis)", t_spread >= 2.0, [
        f"losers minus winners   {spread.mean() * 1e4:+8.2f} bp/day",
        f"t-stat                 {t_spread:+8.2f}",
        "asks E[R|loser] > E[R|winner], not E[R|loser] > 0 — both legs carry",
        "the market, so this is the reversal effect with beta removed",
    ]))

    # --- 3. multiple testing, computed not assumed -----------------------
    null, null_mean = shifted_null_max_t(rets, ks, draws=null_draws)
    observed_max = max(abs(t_stat(spread_returns(rets, k))) for k in ks)
    extra = resolution_for(observed_max, null)
    if extra:
        # near the boundary the third decimal decides — buy the resolution
        null, null_mean = shifted_null_max_t(rets, ks, draws=extra, seed=1)
    p_adj = float((null >= observed_max).mean())
    gates.append(Gate("MULTIPLE TESTING", p_adj < 0.05, [
        f"variants examined      {len(ks):8d}",
        f"observed max |t|       {observed_max:+8.2f}",
        f"null mean max |t|      {null_mean:+8.2f}  ({len(null)} shifted draws)",
        "draw count is adaptive: a screening result gets 400, one anywhere",
        "near the boundary gets 20,000, because the tail is where it matters",
        f"adjusted p-value       {p_adj:8.3f}",
        "null preserves cross-section and autocorrelation; only the link",
        "between yesterday's ranking and today's return is severed",
    ]))

    # --- 4. benchmark ladder ---------------------------------------------
    ew = equal_weight(rets)
    n = min(len(long_only), len(ew))
    vs_ew = long_only[:n] - ew[:n]
    bench_lines = [f"vs cash                {long_only.mean() * 1e4:+8.2f} bp/day"]
    if benchmark in rets.columns:
        b = rets[benchmark].iloc[1:].dropna().to_numpy(dtype=float)
        m = min(len(long_only), len(b))
        vs_b = long_only[:m] - b[:m]
        bench_lines.append(f"vs {benchmark:<19}{vs_b.mean() * 1e4:+8.2f} bp/day"
                           f"   t {t_stat(vs_b):+.2f}")
    bench_lines.append(f"vs equal-weight        {vs_ew.mean() * 1e4:+8.2f} bp/day"
                       f"   t {t_stat(vs_ew):+.2f}")
    bench_lines.append("no factor model here — this is a beta-and-universe check,")
    bench_lines.append("not a Fama-French adjustment; stated so it is not overread")
    gates.append(Gate("BENCHMARK", t_stat(vs_ew) >= 2.0, bench_lines))

    # --- 5. costs ---------------------------------------------------------
    gross = spread.mean()
    net = gross - round_trip
    gates.append(Gate("TRANSACTION COSTS", net > 0, [
        f"gross expectancy       {gross * 1e4:+8.2f} bp/day",
        f"modeled spread         {-round_trip * 1e4:+8.2f} bp/day  "
        f"({cost_bps_per_side:g} bps x 2 sides)",
        f"net expectancy         {net * 1e4:+8.2f} bp/day",
        "slippage and impact not yet separated — one lumped figure, and it is",
        "an assumption, not a measurement (see Amendment 2026-09-01)",
    ]))

    # --- 6. walk-forward --------------------------------------------------
    folds = walk_forward(spread - round_trip)
    positive = sum(1 for _, m, _ in folds if m > 0)
    gates.append(Gate("WALK-FORWARD", positive == len(folds), [
        *[f"fold {i}                 {m:+8.2f} bp/day   t {t:+.2f}"
          for i, m, t in folds],
        f"positive in {positive} of {len(folds)} contiguous periods",
    ]))

    # --- 7. parameter stability -------------------------------------------
    by_k = {k: spread_returns(rets, k).mean() * 1e4 - round_trip * 1e4 for k in ks}
    # Sign agreement alone is not stability: three consistently NEGATIVE values
    # agree perfectly and mean the strategy consistently loses. The gate has to
    # require a positive region, or "reliably bad" passes it. (Caught on the
    # first live run of this engine, 2026-09-01 — it had returned PASS for
    # k = -2.46, -7.35, -2.57 bp/day.)
    consistent = len({np.sign(v) for v in by_k.values()}) == 1
    viable = all(v > 0 for v in by_k.values())
    gates.append(Gate("PARAMETER STABILITY", consistent and viable, [
        *[f"k = {k}                  {v:+8.2f} bp/day net" for k, v in by_k.items()],
        f"sign consistency       {'PASS' if consistent else 'FAIL':>8}"
        "   (neighbours agree — a region, not a lucky point)",
        f"economic viability     {'PASS' if viable else 'FAIL':>8}"
        "   (the region is above zero)",
        "reported separately because a process can be perfectly stable at",
        "losing money; consistency is not profitability",
    ]))

    return gates, all(g.passed for g in gates)


def report_card(name: str, gates: list[Gate], passed: bool) -> str:
    out = [f"STRATEGY: {name}", ""]
    for g in gates:
        out.append(g.name)
        out += [f"  {l}" for l in g.lines]
        out.append("  PASS" if g.passed else "  FAIL")
        out.append("")
    bar = "=" * 40
    out += [bar, ("   ACCEPT — candidate edge" if passed else "   REJECT").center(40),
            bar, ""]
    if not passed:
        failed = [g.name for g in gates if not g.passed]
        out.append("Failed: " + "; ".join(failed) + ".")
        out.append("Rejection is the expected outcome. A strategy that survives "
                   "every gate has earned a paper test, not a live account.")
    return "\n".join(out)
