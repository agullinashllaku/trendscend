"""Scores NewsEvents against realised price returns at 1h/4h/24h horizons."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from trendscend.schemas.market import OHLCV
from trendscend.schemas.news import NewsEvent
from trendscend.schemas.scoring import NewsEventScore

_HORIZONS = {"1h": timedelta(hours=1), "4h": timedelta(hours=4), "24h": timedelta(hours=24)}


def _closest_close(candles: list[OHLCV], ts: datetime) -> float | None:
    """Return the close price of the candle whose timestamp is closest to ts."""
    if not candles:
        return None
    best = min(candles, key=lambda c: abs((c.timestamp - ts).total_seconds()))
    return float(best.close)


class NewsScorer:
    """Matches a NewsEvent to realised OHLCV returns after it was published."""

    def score(self, event: NewsEvent, candles_after: list[OHLCV]) -> NewsEventScore:
        """Compute price returns at 1h/4h/24h after the event's published_at.

        Args:
            event:         The news event to score.
            candles_after: OHLCV bars in ascending time order that cover the
                           event's published_at and at least 24h after.

        Returns:
            NewsEventScore with None for horizons that lack candle coverage.
        """
        base_price = _closest_close(candles_after, event.published_at)

        def _return(horizon: timedelta) -> float | None:
            if base_price is None or base_price == 0.0:
                return None
            target_ts = event.published_at + horizon
            post_event = [c for c in candles_after if c.timestamp >= event.published_at]
            future_price = _closest_close(post_event, target_ts)
            if future_price is None:
                return None
            nearest = min(post_event, key=lambda c: abs((c.timestamp - target_ts).total_seconds()))
            gap = abs((nearest.timestamp - target_ts).total_seconds())
            if gap > horizon.total_seconds() * 0.5:
                return None
            return (future_price - base_price) / base_price

        return NewsEventScore(
            event_id=event.id,
            symbol=event.assets[0] if event.assets else "unknown",
            direction=event.direction,
            severity=event.severity,
            return_1h=_return(_HORIZONS["1h"]),
            return_4h=_return(_HORIZONS["4h"]),
            return_24h=_return(_HORIZONS["24h"]),
            scored_at=datetime.now(UTC),
        )
