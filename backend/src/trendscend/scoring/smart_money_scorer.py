"""Scores SmartMoneyState shifts against realised price returns."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from trendscend.schemas.market import OHLCV
from trendscend.schemas.scoring import SmartMoneyScore
from trendscend.schemas.smart_money import SmartMoneyState
from trendscend.scoring._candle_utils import _closest_close

_MIN_SHIFT = 0.05  # only score shifts > 5pp net-long change


class SmartMoneyScorer:
    """Scores positioning shifts between two SmartMoneyState snapshots."""

    def score(
        self,
        before: SmartMoneyState,
        after: SmartMoneyState,
        candles: dict[str, list[OHLCV]],
    ) -> list[SmartMoneyScore]:
        """Score asset-level shifts between two consecutive snapshots.

        Args:
            before:  Earlier SmartMoneyState snapshot.
            after:   Later SmartMoneyState snapshot.
            candles: Dict of symbol → OHLCV list covering at least 24h after
                     ``after.computed_at``.

        Returns:
            One SmartMoneyScore per asset with a shift > _MIN_SHIFT. Empty if
            no meaningful shift occurred.
        """
        scores: list[SmartMoneyScore] = []
        shift_ts = after.computed_at

        for symbol, after_asset in after.per_asset.items():
            before_asset = before.per_asset.get(symbol)
            if before_asset is None:
                continue
            shift = abs(after_asset.net_long_pct - before_asset.net_long_pct)
            if shift < _MIN_SHIFT:
                continue

            sym_candles = candles.get(symbol, [])
            base = _closest_close(sym_candles, shift_ts)

            def _ret(
                h: timedelta,
                _base: float | None = base,
                _sym_candles: list[OHLCV] = sym_candles,
                _shift_ts: datetime = shift_ts,
            ) -> float | None:
                if _base is None or _base == 0.0:
                    return None
                target = _shift_ts + h
                future = _closest_close(
                    [c for c in _sym_candles if c.timestamp >= _shift_ts], target
                )
                return (future - _base) / _base if future is not None else None

            scores.append(
                SmartMoneyScore(
                    snapshot_at=shift_ts,
                    symbol=symbol,
                    net_long_pct_before=before_asset.net_long_pct,
                    net_long_pct_after=after_asset.net_long_pct,
                    return_1h=_ret(timedelta(hours=1)),
                    return_4h=_ret(timedelta(hours=4)),
                    return_24h=_ret(timedelta(hours=24)),
                    scored_at=datetime.now(UTC),
                )
            )
        return scores
