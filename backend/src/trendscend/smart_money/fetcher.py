"""A4 Hyperliquid public REST adapter — no auth required (leaderboard + positions)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

_HL_API = "https://api.hyperliquid.xyz/info"


@dataclass
class WalletFill:
    wallet: str
    symbol: str
    side: str
    size: float
    price: float
    timestamp: datetime


@dataclass
class WalletPosition:
    wallet: str
    symbol: str
    side: str
    size: float
    entry_price: float
    unrealized_pnl: float
    leverage: float


@dataclass
class WalletRecord:
    address: str
    pnl_usd: float
    fills: list[WalletFill]
    positions: list[WalletPosition]
    fetched_at: datetime


class HyperliquidFetcher:
    """Fetches public Hyperliquid data: leaderboard and per-wallet state."""

    def __init__(
        self,
        http_post: Callable[[str, dict], Awaitable[dict]] | None = None,
    ) -> None:
        if http_post is None:
            import httpx

            async def _default(url: str, payload: dict) -> dict:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    r = await client.post(url, json=payload)
                    r.raise_for_status()
                    return dict(r.json())

            self._http_post: Callable[[str, dict], Awaitable[dict]] = _default
        else:
            self._http_post = http_post

    async def fetch_leaderboard(self, top_n: int = 200) -> list[tuple[str, float]]:
        """Return (address, account_value) tuples for the top-N traders on Hyperliquid.

        ``account_value`` is the trader's total account equity in USD as reported
        by the leaderboard. It is used as a proxy for PnL when constructing
        WalletRecord — a positive account value indicates a live, funded account.
        """
        data = await self._http_post(_HL_API, {"type": "leaderboard"})
        rows: list[dict] = data.get("leaderboardRows", [])
        return [(row["ethAddress"], float(row.get("accountValue", 0))) for row in rows[:top_n]]

    async def fetch_wallet(self, address: str, account_value: float = 0.0) -> WalletRecord:
        """Fetch positions and fills for a wallet address."""
        positions_data, fills_data = await asyncio.gather(
            self._http_post(_HL_API, {"type": "clearinghouseState", "user": address}),
            self._http_post(_HL_API, {"type": "userFills", "user": address}),
        )

        positions: list[WalletPosition] = []
        for ap in positions_data.get("assetPositions", []):
            pos = ap.get("position", {})
            size = float(pos.get("szi", 0))
            if size == 0:
                continue
            lev_raw = pos.get("leverage", {})
            leverage = float(lev_raw.get("value", 1)) if isinstance(lev_raw, dict) else 1.0
            positions.append(
                WalletPosition(
                    wallet=address,
                    symbol=str(pos.get("coin", "")),
                    side="long" if size > 0 else "short",
                    size=abs(size),
                    entry_price=float(pos.get("entryPx", 0)),
                    unrealized_pnl=float(pos.get("unrealizedPnl", 0)),
                    leverage=leverage,
                )
            )

        fills: list[WalletFill] = []
        for f in fills_data.get("fills", []):
            ts_ms = int(f.get("time", 0))
            fills.append(
                WalletFill(
                    wallet=address,
                    symbol=str(f.get("coin", "")),
                    side=str(f.get("side", "")),
                    size=float(f.get("sz", 0)),
                    price=float(f.get("px", 0)),
                    timestamp=datetime.fromtimestamp(ts_ms / 1000, tz=UTC),
                )
            )

        return WalletRecord(
            address=address,
            pnl_usd=account_value,
            fills=fills,
            positions=positions,
            fetched_at=datetime.now(UTC),
        )
