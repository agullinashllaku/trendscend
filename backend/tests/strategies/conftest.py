"""Shared fixtures for strategy tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from trendscend.schemas.market import OHLCV

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "btc_100_bars.json"


def load_btc_100_bars() -> list[OHLCV]:
    """Load the 100-bar golden fixture as OHLCV objects."""
    raw: list[list[float]] = json.loads(_FIXTURE_PATH.read_text())
    return [
        OHLCV(
            timestamp=datetime.fromtimestamp(bar[0] / 1000.0, tz=UTC),
            open=Decimal(str(bar[1])),
            high=Decimal(str(bar[2])),
            low=Decimal(str(bar[3])),
            close=Decimal(str(bar[4])),
            volume=Decimal(str(bar[5])),
        )
        for bar in raw
    ]


@pytest.fixture()
def btc_100_bars() -> list[OHLCV]:
    """100-bar golden fixture: rising (0–49), choppy (50–74), declining (75–99)."""
    return load_btc_100_bars()


@pytest.fixture()
def rising_bars(btc_100_bars: list[OHLCV]) -> list[OHLCV]:
    """Bars 0–49: steadily rising from 30000 to 50000."""
    return btc_100_bars[:50]


@pytest.fixture()
def choppy_bars(btc_100_bars: list[OHLCV]) -> list[OHLCV]:
    """Bars 50–74: choppy/ranging around 50000."""
    return btc_100_bars[50:75]


@pytest.fixture()
def declining_bars(btc_100_bars: list[OHLCV]) -> list[OHLCV]:
    """Bars 75–99: declining from 50000 to 35000."""
    return btc_100_bars[75:]
