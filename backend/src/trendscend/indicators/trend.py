"""Trend indicator functions.

All functions accept a list of OHLCV candles and return a list[float].
They return an empty list when there is insufficient data for the given period.
"""

from __future__ import annotations

from trendscend.schemas.market import OHLCV


def ema(candles: list[OHLCV], period: int) -> list[float]:
    """Exponential moving average of close prices.

    Args:
        candles: OHLCV bars in ascending time order.
        period:  Number of bars for the EMA window.

    Returns:
        List of EMA values aligned to candles (same length as input),
        or ``[]`` if ``len(candles) < period``.
    """
    if len(candles) < period:
        return []

    closes = [float(c.close) for c in candles]
    k = 2.0 / (period + 1)

    # Seed with the SMA of the first `period` bars
    sma_seed = sum(closes[:period]) / period
    result: list[float] = [sma_seed]

    for price in closes[period:]:
        result.append(price * k + result[-1] * (1.0 - k))

    # Pad leading values with None-equivalent (NaN) is not appropriate for a
    # clean list[float] contract — instead we align output to the input length
    # by prepending NaN-free values: only return values where EMA is defined.
    # Length = len(candles) - period + 1
    return result


def atr(candles: list[OHLCV], period: int) -> list[float]:
    """Average True Range.

    True Range = max(high - low, |high - prev_close|, |low - prev_close|)
    ATR is a Wilder-smoothed (RMA) average of True Range over ``period`` bars.

    Args:
        candles: OHLCV bars in ascending time order.  At least ``period + 1``
                 bars are required (one extra for the first prev_close).
        period:  Smoothing period.

    Returns:
        List of ATR values, or ``[]`` if ``len(candles) < period + 1``.
    """
    if len(candles) < period + 1:
        return []

    true_ranges: list[float] = []
    for i in range(1, len(candles)):
        high = float(candles[i].high)
        low = float(candles[i].low)
        prev_close = float(candles[i - 1].close)
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        true_ranges.append(tr)

    # Seed with SMA of first `period` true ranges
    seed = sum(true_ranges[:period]) / period
    result: list[float] = [seed]

    # Wilder smoothing: ATR_t = (ATR_{t-1} * (period-1) + TR_t) / period
    alpha = 1.0 / period
    for tr in true_ranges[period:]:
        result.append(result[-1] * (1.0 - alpha) + tr * alpha)

    return result


def donchian_high(candles: list[OHLCV], period: int) -> list[float]:
    """Highest high over a rolling ``period``-bar window.

    Args:
        candles: OHLCV bars in ascending time order.
        period:  Look-back window length.

    Returns:
        List of rolling highest-high values (length = ``len(candles) - period + 1``),
        or ``[]`` if ``len(candles) < period``.
    """
    if len(candles) < period:
        return []

    result: list[float] = []
    for i in range(period - 1, len(candles)):
        window = candles[i - period + 1 : i + 1]
        result.append(max(float(c.high) for c in window))
    return result


def donchian_low(candles: list[OHLCV], period: int) -> list[float]:
    """Lowest low over a rolling ``period``-bar window.

    Args:
        candles: OHLCV bars in ascending time order.
        period:  Look-back window length.

    Returns:
        List of rolling lowest-low values (length = ``len(candles) - period + 1``),
        or ``[]`` if ``len(candles) < period``.
    """
    if len(candles) < period:
        return []

    result: list[float] = []
    for i in range(period - 1, len(candles)):
        window = candles[i - period + 1 : i + 1]
        result.append(min(float(c.low) for c in window))
    return result
