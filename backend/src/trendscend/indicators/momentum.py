"""Momentum indicator functions.

All functions accept a list of OHLCV candles and return a list[float].
They return an empty list when there is insufficient data for the given period.
"""

from __future__ import annotations

import math

from trendscend.indicators.trend import ema
from trendscend.schemas.market import OHLCV

# Annualisation factor: √(252 * 6) for 4-hour bars (6 bars per day × 252 trading days)
_ANNUALISATION_FACTOR = math.sqrt(252 * 6)


def realized_vol(candles: list[OHLCV], period: int) -> list[float]:
    """Annualised realized volatility from log returns over ``period`` bars.

    Computed as the annualised standard deviation of log returns over a
    rolling ``period``-bar window.  Assumes 4-hour bars (6 bars per day,
    252 trading days per year).

    Args:
        candles: OHLCV bars in ascending time order.  At least ``period + 1``
                 bars are required (one extra to form the first return).
        period:  Look-back window length.

    Returns:
        List of annualised volatility values (length = ``len(candles) - period``),
        or ``[]`` if ``len(candles) <= period``.
    """
    if len(candles) <= period:
        return []

    closes = [float(c.close) for c in candles]
    # Compute log returns
    log_returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]

    result: list[float] = []
    for i in range(period, len(log_returns) + 1):
        window = log_returns[i - period : i]
        if len(window) < period:
            continue
        mean = sum(window) / period
        variance = sum((r - mean) ** 2 for r in window) / period
        std = math.sqrt(variance)
        result.append(std * _ANNUALISATION_FACTOR)

    return result


def tsmom_signal(candles: list[OHLCV], fast_period: int, slow_period: int) -> list[float]:
    """EMA crossover signal: fast_ema - slow_ema.

    The sign of the result gives the momentum direction:
    positive → upward momentum, negative → downward momentum.

    Args:
        candles:     OHLCV bars in ascending time order.
        fast_period: Period for the faster (shorter) EMA.
        slow_period: Period for the slower (longer) EMA.  Must be ≥ ``fast_period``.

    Returns:
        List of (fast_ema - slow_ema) values aligned to the slow EMA output,
        or ``[]`` if there is insufficient data for the slow EMA.
    """
    fast_ema = ema(candles, fast_period)
    slow_ema = ema(candles, slow_period)

    if not fast_ema or not slow_ema:
        return []

    # slow_ema is shorter (starts later); align by taking the last len(slow_ema) values
    # from fast_ema.  EMA output length = len(candles) - period + 1.
    fast_aligned = fast_ema[-(len(slow_ema)) :]
    return [f - s for f, s in zip(fast_aligned, slow_ema, strict=False)]
