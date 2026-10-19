"""Shared candle utility helpers for scoring modules."""

from __future__ import annotations

from datetime import datetime

from trendscend.schemas.market import OHLCV


def _closest_close(candles: list[OHLCV], ts: datetime) -> float | None:
    """Return the close price of the candle whose timestamp is closest to ts."""
    if not candles:
        return None
    best = min(candles, key=lambda c: abs((c.timestamp - ts).total_seconds()))
    return float(best.close)
