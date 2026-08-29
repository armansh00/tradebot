"""Dual-momentum trend strategy. Pure function: signals in, targets + reasons out.
The LLM never decides anything here; there is no LLM here."""
from __future__ import annotations
from .config import Config


def decide(cfg: Config, snapshots: dict[str, dict], equity: float) -> dict:
    """Return {"targets": {symbol: notional}, "decisions": {symbol: reason}}.

    Rules (pre-registered in config.yaml):
      1. A symbol is eligible only if price > its 200-day SMA and data suffices.
      2. Rank eligible symbols by 12-1 momentum; positive momentum only.
      3. Hold the top N equally weighted in (equity minus cash buffer).
      4. Nothing eligible -> hold cash. Cash is a position, not a failure.
    """
    st = cfg.strategy
    investable = max(equity * (1 - st.cash_buffer_pct / 100.0), 0.0)

    eligible: list[tuple[str, float]] = []
    decisions: dict[str, dict] = {}
    for sym, snap in snapshots.items():
        reason: dict = {"snapshot": snap, "eligible": False, "selected": False}
        if not snap["enough_data"]:
            reason["rule"] = "insufficient history"
        elif not snap["above_sma"]:
            reason["rule"] = f"price {snap['price']} below {st.sma_days}d SMA {snap['sma']}"
        elif snap["momentum"] is None or snap["momentum"] <= 0:
            reason["rule"] = f"12-1 momentum {snap['momentum']} not positive"
        else:
            reason["eligible"] = True
            reason["rule"] = f"above SMA and momentum {snap['momentum']:+.2%}"
            eligible.append((sym, snap["momentum"]))
        decisions[sym] = reason

    eligible.sort(key=lambda t: t[1], reverse=True)
    picks = [sym for sym, _ in eligible[: st.top_n]]

    targets: dict[str, float] = {}
    if picks:
        per = round(investable / len(picks), 2)
        for rank, sym in enumerate(picks, start=1):
            targets[sym] = per
            decisions[sym]["selected"] = True
            decisions[sym]["rank"] = rank
            decisions[sym]["target_notional"] = per

    return {"targets": targets, "decisions": decisions}


def diff_orders(cfg: Config, targets: dict[str, float],
                positions: dict[str, float]) -> list[dict]:
    """Turn target notionals vs current position notionals into orders.
    Sells first (frees cash), then buys."""
    st = cfg.strategy
    orders: list[dict] = []
    for sym in sorted(set(positions) | set(targets)):
        current = positions.get(sym, 0.0)
        target = targets.get(sym, 0.0)
        delta = round(target - current, 2)
        if abs(delta) < st.min_order_notional:
            continue
        orders.append({
            "symbol": sym,
            "side": "buy" if delta > 0 else "sell",
            "notional": abs(delta),
            "from_notional": round(current, 2),
            "to_notional": round(target, 2),
        })
    orders.sort(key=lambda o: 0 if o["side"] == "sell" else 1)
    return orders
