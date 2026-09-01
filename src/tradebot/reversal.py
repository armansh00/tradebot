"""Test the rotation idea: buy yesterday's losers, or yesterday's winners.

The hypothesis, in the user's words: if a stock fell today it is likelier to
rise tomorrow, so rotate into the fallers and out of the risers, and let many
small edges compound.

This is short-term reversal (Jegadeesh 1990; Lehmann 1990) — a genuine, long
documented effect. The question is not whether it exists but whether what
survives of it is bigger than the cost of harvesting it at retail scale.
Both directions are tested, because the opposite hypothesis (yesterday's
winners keep running) is equally plausible a priori and testing only the one
you like is how people fool themselves.

Reported gross AND net. If an effect only appears net of costs, or only
disappears net of costs, that fact is the finding.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass
class Result:
    direction: str          # "losers" (reversal) | "winners" (momentum)
    k: int
    days: int
    mean_daily_gross_pct: float
    mean_daily_net_pct: float
    total_net_pct: float
    t_stat_gross: float
    t_stat_net: float
    win_rate: float


def daily_returns(closes: dict[str, pd.Series]) -> pd.DataFrame:
    df = pd.DataFrame({s: v.astype(float).reset_index(drop=True)
                       for s, v in closes.items() if len(v) > 2})
    return df.pct_change().dropna(how="all")


def rotate(rets: pd.DataFrame, *, direction: str, k: int,
           cost_bps_per_side: float) -> Result:
    """Each day: rank by yesterday's return, hold the k extremes for one day.

    Deliberately naive, because that is the idea under test. No filters, no
    timing, no discretion — if the raw effect is not there, dressing it up
    only adds researcher degrees of freedom.
    """
    round_trip = 2 * cost_bps_per_side / 1e4
    daily_gross, daily_net = [], []

    for i in range(1, len(rets)):
        yesterday = rets.iloc[i - 1].dropna()
        today = rets.iloc[i]
        if len(yesterday) < k:
            continue
        ranked = yesterday.sort_values()
        picks = (ranked.index[:k] if direction == "losers"
                 else ranked.index[-k:])
        realised = today[list(picks)].dropna()
        if realised.empty:
            continue
        gross = float(realised.mean())
        # A full rotation every day: out of yesterday's names, into today's.
        daily_gross.append(gross * 100)
        daily_net.append((gross - round_trip) * 100)

    n = len(daily_net)
    if n < 2:
        return Result(direction, k, 0, 0, 0, 0, 0, 0, 0)

    def t_stat(xs):
        m = sum(xs) / len(xs)
        sd = (sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) ** 0.5
        return round(m / (sd / len(xs) ** 0.5), 2) if sd else 0.0

    mean_net = sum(daily_net) / n
    total = 1.0
    for d in daily_net:
        total *= (1 + d / 100)
    return Result(
        direction=direction, k=k, days=n,
        mean_daily_gross_pct=round(sum(daily_gross) / n, 4),
        mean_daily_net_pct=round(mean_net, 4),
        total_net_pct=round((total - 1) * 100, 2),
        t_stat_gross=t_stat(daily_gross),
        t_stat_net=t_stat(daily_net),
        win_rate=round(sum(1 for d in daily_net if d > 0) / n, 3))


def report(results: list[Result], symbols: list[str], cost_bps: float) -> str:
    lines = [
        f"Universe: {', '.join(symbols)}",
        f"Cost: {cost_bps} bps per side, charged on a full rotation each day.",
        "",
        "| rule | k | days | mean daily gross % | t-stat (gross) | "
        "mean daily net % | total net % | t-stat (net) | win rate |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in results:
        name = ("buy yesterday's losers" if r.direction == "losers"
                else "buy yesterday's winners")
        lines.append(f"| {name} | {r.k} | {r.days} | "
                     f"{r.mean_daily_gross_pct:+.4f} | {r.t_stat_gross:+.2f} | "
                     f"{r.mean_daily_net_pct:+.4f} | "
                     f"{r.total_net_pct:+.2f} | {r.t_stat_net:+.2f} | "
                     f"{r.win_rate:.3f} |")

    best = max(results, key=lambda r: r.t_stat_gross)
    lines += [
        "",
        "**How to read the t-stat.** It is how many standard errors the average "
        "daily return sits above zero. Roughly ±2 is the conventional threshold "
        "for 'probably not luck'. Below that, the total return column is a "
        "story about this particular stretch of history and nothing more.",
        "",
    ]
    lines.append("Read the GROSS t-stat first: it asks whether the pattern is "
                 "there at all. The NET t-stat asks whether it survives the "
                 "tolls. Charging a full rotation every day costs "
                 f"{2 * cost_bps:.0f} bps daily, which is a large, certain "
                 "headwind against a small, uncertain edge.\n")
    if best.t_stat_net >= 2:
        lines.append(f"Strongest: {best.direction}, k={best.k}, t = "
                     f"{best.t_stat_net:+.2f} net of costs (gross "
                     f"{best.t_stat_gross:+.2f}). That clears the bar "
                     "on this sample. Note that several variants were tried, so "
                     "the bar should really be higher than 2 — and the next step "
                     "is an out-of-sample window, not a live account.")
    else:
        lines.append(f"Nothing clears the bar. Best is {best.direction}, k="
                     f"{best.k}, t = {best.t_stat_gross:+.2f} gross and "
                     f"{best.t_stat_net:+.2f} net. Compare the "
                     "gross and net columns: where gross is positive and net is "
                     "not, the effect is real and the tolls are bigger than it.")
    return "\n".join(lines)
