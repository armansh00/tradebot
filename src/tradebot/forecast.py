"""Scoring the nightly forecast. Separate from whoever wrote it, on purpose.

A model does not get to decide whether its own prediction was right. The same
session can be written up as "close" or as "a miss" depending on who holds the
pen, and nobody has to be lying for that to corrupt the record. So the
forecast is an immutable input, the market data is an immutable input, and the
verdict is arithmetic.

Three commitments are scored here and one is not:

- **Range.** Hit or miss against the stated interval, plus how far outside it
  landed. A binary grade calls a 0.01pp miss and a 0.50pp miss the same thing.
  A proper interval score (Winkler) needs a declared coverage level, and the
  forecasts do not state one, so it is not computed rather than invented.
- **Direction.** Brier, against a climatology declared from data that ends
  before any forecast existed. Skill is deliberately NOT computed for a single
  day: with n=1 the reference Brier can sit arbitrarily close to zero and the
  ratio says nothing. `aggregate` computes it across a run of sessions.
- **Relative call.** Sign of one sector return minus the other.
- **Invalidation.** These have referenced WTI before 09:30 ET. This repository
  cannot query crude futures, so the condition is marked unadjudicable and
  stays that way. A commitment nobody can settle is not a commitment, and
  recording it as "unverified" every day is the honest way to keep saying so.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date as _date, datetime, timedelta, timezone

UNADJUDICABLE = "unadjudicable"


# ---------------------------------------------------------------- the market

@dataclass(frozen=True)
class Market:
    """Everything the scoring needs, and nothing it does not."""
    prior_close: float
    high: float
    low: float
    close: float
    sector: dict            # {"SMH": pct_return, "XLE": pct_return}
    source: str             # where these numbers came from

    def range_pct(self) -> float:
        return (self.high - self.low) / self.prior_close * 100.0

    def down(self) -> int:
        return 1 if self.close < self.prior_close else 0


def market_from_bars(closes: dict, bars: dict, source: str) -> Market:
    """Build the inputs from daily bars.

    `bars` is the forecast day's OHLC for SPY; `closes` carries the prior
    close for SPY and the prior and current closes for the sector pair.
    """
    sector = {}
    for sym in ("SMH", "XLE"):
        prev, now = closes.get(sym, (None, None))
        if prev and now:
            sector[sym] = round((now / prev - 1) * 100, 4)
    return Market(prior_close=float(closes["SPY"][0]),
                  high=float(bars["high"]), low=float(bars["low"]),
                  close=float(bars["close"]), sector=sector, source=source)


# ---------------------------------------------------------------- the scoring

def score(forecast: dict, market: Market, climatology: dict | None = None) -> dict:
    """One session, four commitments, no discretion."""
    out = {
        "forecast_for": forecast["forecast_for"],
        "scored_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "data_source": market.source,
    }

    lo = float(forecast["spy_expected_range_low_pct"])
    hi = float(forecast["spy_expected_range_high_pct"])
    realized = round(market.range_pct(), 4)
    inside = lo <= realized <= hi
    out["range"] = {
        "predicted_low_pct": lo, "predicted_high_pct": hi,
        "realized_pct": realized,
        "inside": inside,
        # Distance outside the interval, so a near miss and a rout are not the
        # same number. Zero when inside — this is a penalty, not a margin.
        "miss_pct": 0.0 if inside else round(min(abs(realized - lo),
                                                 abs(realized - hi)), 4),
        "interval_score": None,
        "interval_score_note": "needs a declared coverage level; the forecast "
                               "states none, so it is not computed",
    }

    p = float(forecast["spy_p_down"])
    y = market.down()
    out["direction"] = {
        "p_down": p, "outcome_down": bool(y),
        "prior_close": market.prior_close, "close": market.close,
        "return_pct": round((market.close / market.prior_close - 1) * 100, 4),
        "brier": round((p - y) ** 2, 6),
    }
    if climatology and climatology.get("p_down") is not None:
        pc = float(climatology["p_down"])
        out["direction"]["climatology_p_down"] = pc
        out["direction"]["climatology_brier"] = round((pc - y) ** 2, 6)
        out["direction"]["skill_note"] = (
            "no skill score for a single session: with n=1 the reference "
            "Brier can sit arbitrarily near zero and the ratio is noise. "
            "See aggregate().")

    # `relative_call_instruments` is ordered [predicted winner, predicted
    # loser]. Reading the winner out of an English phrase like
    # "semiconductors_over_energy" would work until the day someone writes
    # "energy_under_semis" and the scorer silently inverts a verdict.
    call = str(forecast.get("relative_call", ""))
    a, b = (forecast.get("relative_call_instruments") or ["SMH", "XLE"])[:2]
    ra, rb = market.sector.get(a), market.sector.get(b)
    if ra is None or rb is None:
        out["relative"] = {"call": call, "status": UNADJUDICABLE,
                           "reason": f"no return for {a if ra is None else b}"}
    else:
        out["relative"] = {"call": call, "predicted_winner": a,
                           f"{a}_pct": ra, f"{b}_pct": rb,
                           "spread_pp": round(ra - rb, 4), "hit": bool(ra > rb)}

    inv = forecast.get("invalidation") or {}
    out["invalidation"] = {
        **inv, "status": UNADJUDICABLE,
        "reason": "this repository has no source for the named instrument; "
                  "the condition cannot be settled and is not guessed at",
    }
    return out


def aggregate(scored: list[dict], climatology: dict | None = None) -> dict:
    """Across a run of sessions, where the statistics start to mean something.

    The Brier skill score lives here rather than in `score` because it is a
    property of a set. Reported with n attached, because a skill score without
    its sample size is a number pretending to be evidence.
    """
    direction = [s["direction"] for s in scored if "brier" in s.get("direction", {})]
    ranges = [s["range"] for s in scored if "realized_pct" in s.get("range", {})]
    rel = [s["relative"] for s in scored if "hit" in s.get("relative", {})]

    out = {"n": len(scored)}
    if direction:
        b = sum(d["brier"] for d in direction) / len(direction)
        out["direction"] = {"n": len(direction), "mean_brier": round(b, 6),
                            "mean_p_down": round(sum(d["p_down"] for d in direction)
                                                 / len(direction), 4),
                            "down_days": sum(1 for d in direction if d["outcome_down"])}
        ref = [d.get("climatology_brier") for d in direction]
        if all(r is not None for r in ref) and ref:
            bc = sum(ref) / len(ref)
            out["direction"]["climatology_brier"] = round(bc, 6)
            out["direction"]["brier_skill_score"] = (
                round(1 - b / bc, 4) if bc > 0 else None)
    if ranges:
        out["range"] = {"n": len(ranges),
                        "hit_rate": round(sum(r["inside"] for r in ranges) / len(ranges), 4),
                        "mean_miss_pct": round(sum(r["miss_pct"] for r in ranges)
                                               / len(ranges), 4)}
    if rel:
        out["relative"] = {"n": len(rel),
                           "hit_rate": round(sum(r["hit"] for r in rel) / len(rel), 4)}
    if climatology:
        out["climatology"] = climatology
    return out


# ------------------------------------------------------------- climatology

def climatology_from_closes(closes, through: str) -> dict:
    """The unconditional rate of down days, from a window that ends before any
    forecast in this repository existed.

    Declared once and then left alone. A baseline chosen after the results are
    in is not a baseline, and the way to keep that honest is to fix the window
    mechanically — every daily bar up to the pre-registered research cutoff —
    rather than to pick one.
    """
    vals = list(closes)
    downs = sum(1 for i in range(1, len(vals)) if vals[i] < vals[i - 1])
    n = max(len(vals) - 1, 0)
    return {"symbol": "SPY", "through": through, "sessions": n,
            "down_days": downs,
            "p_down": round(downs / n, 6) if n else None,
            "declared_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "note": "window ends at the pre-registered research cutoff, before "
                    "any forecast in this repository was written"}
