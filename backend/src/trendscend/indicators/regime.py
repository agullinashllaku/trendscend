"""Regime indicator functions.

Provides indicators for identifying market regime (trending vs. ranging).
All functions accept a list of OHLCV candles and return a list[float].
They return an empty list when there is insufficient data for the given period.
"""

from __future__ import annotations

from trendscend.schemas.market import OHLCV


def adx(candles: list[OHLCV], period: int = 14) -> list[float]:
    """Average Directional Index (ADX) using Wilder's smoothing.

    ADX measures trend strength regardless of direction:
    - ADX < 25: ranging/weak trend
    - ADX ≥ 25: trending market

    Computed from Directional Movement (+DM, -DM) and True Range, all
    Wilder-smoothed.  Requires at least ``2 * period + 1`` bars.

    Args:
        candles: OHLCV bars in ascending time order.
        period:  Smoothing period (default 14).

    Returns:
        List of ADX values.  Length = ``len(candles) - 2 * period``,
        or ``[]`` if ``len(candles) < 2 * period + 1``.
    """
    min_bars = 2 * period + 1
    if len(candles) < min_bars:
        return []

    # Step 1: Compute per-bar +DM, -DM, TR
    plus_dm: list[float] = []
    minus_dm: list[float] = []
    true_ranges: list[float] = []

    for i in range(1, len(candles)):
        high = float(candles[i].high)
        low = float(candles[i].low)
        prev_high = float(candles[i - 1].high)
        prev_low = float(candles[i - 1].low)
        prev_close = float(candles[i - 1].close)

        up_move = high - prev_high
        down_move = prev_low - low

        pdm = up_move if up_move > down_move and up_move > 0 else 0.0
        mdm = down_move if down_move > up_move and down_move > 0 else 0.0

        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))

        plus_dm.append(pdm)
        minus_dm.append(mdm)
        true_ranges.append(tr)

    # Step 2: Wilder's smoothed sums (seed = sum of first `period` values)
    def wilder_smooth(values: list[float], p: int) -> list[float]:
        seed = sum(values[:p])
        result = [seed]
        alpha = 1.0 / p
        for v in values[p:]:
            result.append(result[-1] * (1.0 - alpha) + v * alpha * p)
        return result

    smooth_tr = wilder_smooth(true_ranges, period)
    smooth_pdm = wilder_smooth(plus_dm, period)
    smooth_mdm = wilder_smooth(minus_dm, period)

    # Step 3: +DI and -DI
    plus_di: list[float] = []
    minus_di: list[float] = []
    for i in range(len(smooth_tr)):
        atr_val = smooth_tr[i]
        if atr_val == 0.0:
            plus_di.append(0.0)
            minus_di.append(0.0)
        else:
            plus_di.append(100.0 * smooth_pdm[i] / atr_val)
            minus_di.append(100.0 * smooth_mdm[i] / atr_val)

    # Step 4: DX = 100 * |+DI - -DI| / (+DI + -DI)
    dx_values: list[float] = []
    for pdi, mdi in zip(plus_di, minus_di, strict=False):
        denom = pdi + mdi
        if denom == 0.0:
            dx_values.append(0.0)
        else:
            dx_values.append(100.0 * abs(pdi - mdi) / denom)

    # Step 5: Wilder-smooth DX over `period` to get ADX
    if len(dx_values) < period:
        return []

    adx_seed = sum(dx_values[:period]) / period
    adx_result: list[float] = [adx_seed]
    alpha = 1.0 / period
    for dx in dx_values[period:]:
        adx_result.append(adx_result[-1] * (1.0 - alpha) + dx * alpha)

    return adx_result
