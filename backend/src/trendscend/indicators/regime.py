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


def efficiency_ratio(candles: list[OHLCV], period: int = 20) -> list[float]:
    """Kaufman's Efficiency Ratio: net directional move / total path length.

    ER = |close[t] - close[t-period]| / sum(|close[i] - close[i-1]|)

    ER ≈ 1.0 → highly efficient/trending (price moved one direction).
    ER ≈ 0.0 → noisy/ranging (lots of back-and-forth).

    Args:
        candles: OHLCV bars in ascending time order.
        period:  Look-back window (default 20).

    Returns:
        List of ER values (length = len(candles) - period), or [] if
        len(candles) <= period.
    """
    if len(candles) <= period:
        return []

    closes = [float(c.close) for c in candles]
    result: list[float] = []
    for i in range(period, len(closes)):
        net_move = abs(closes[i] - closes[i - period])
        path = sum(abs(closes[j] - closes[j - 1]) for j in range(i - period + 1, i + 1))
        result.append(net_move / path if path > 0.0 else 0.0)
    return result


def ema_slope(candles: list[OHLCV], ema_period: int, slope_period: int = 1) -> list[float]:
    """Normalised EMA slope: (ema[t] - ema[t-slope_period]) / ema[t-slope_period].

    Positive → EMA trending up; negative → trending down.
    Normalising by the EMA value makes the slope comparable across price levels.

    Args:
        candles:      OHLCV bars in ascending time order.
        ema_period:   Period for the EMA.
        slope_period: How many EMA steps back to measure the change (default 1).

    Returns:
        List of normalised slope values, or [] if there are insufficient EMA
        values (need > slope_period EMA outputs).
    """
    from trendscend.indicators.trend import ema as _ema

    ema_vals = _ema(candles, ema_period)
    if len(ema_vals) <= slope_period:
        return []
    result: list[float] = []
    for i in range(slope_period, len(ema_vals)):
        prev = ema_vals[i - slope_period]
        result.append((ema_vals[i] - prev) / prev if prev != 0.0 else 0.0)
    return result
