"""Pure signal math. No I/O, no broker, no side effects — unit-testable."""
from __future__ import annotations
import pandas as pd


def sma(closes: pd.Series, days: int) -> float | None:
    """Simple moving average of the last `days` closes. None if not enough data."""
    if len(closes) < days:
        return None
    return float(closes.iloc[-days:].mean())


def momentum_12_1(closes: pd.Series, lookback: int, skip: int) -> float | None:
    """Classic 12-1 momentum: return over `lookback` days excluding the most
    recent `skip` days. None if not enough data."""
    if len(closes) < lookback + 1:
        return None
    end = closes.iloc[-(skip + 1)]
    start = closes.iloc[-(lookback + 1)]
    if start <= 0:
        return None
    return float(end / start - 1.0)


def snapshot(closes: pd.Series, sma_days: int, lookback: int, skip: int) -> dict:
    """Everything the strategy needs to decide on one symbol, in one record.
    This record is logged verbatim so 'why did you buy X' has an exact answer."""
    price = float(closes.iloc[-1])
    s = sma(closes, sma_days)
    m = momentum_12_1(closes, lookback, skip)
    return {
        "price": round(price, 4),
        "sma": round(s, 4) if s is not None else None,
        "above_sma": (s is not None and price > s),
        "momentum": round(m, 6) if m is not None else None,
        "enough_data": (s is not None and m is not None),
    }
