"""Small in-memory fake of the redis.asyncio calls the API uses."""

from __future__ import annotations

from typing import Any


class Clock:
    def __init__(self, now: float = 1_000_000_020.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class FakeRedis:
    def __init__(self, clock: Clock | None = None) -> None:
        self.clock = clock or Clock()
        self.counts: dict[str, int] = {}
        self.expires_at: dict[str, float] = {}
        self.fail = False
        self.ping_delay = 0.0
        self.closed = False
        self.created_without_ttl: list[str] = []

    def _purge(self) -> None:
        for k in [k for k, t in self.expires_at.items() if t <= self.clock.now]:
            self.counts.pop(k, None)
            del self.expires_at[k]

    async def eval(self, _script: str, numkeys: int, *args: Any) -> list[int]:
        """Atomic INCR + EXPIRE-if-no-ttl, returning [count, ttl]."""
        if self.fail:
            raise ConnectionError("redis down: redis://:pw@h")
        assert numkeys == 1
        key, window = str(args[0]), int(args[1])
        self._purge()
        self.counts[key] = self.counts.get(key, 0) + 1
        if key not in self.expires_at:
            self.expires_at[key] = self.clock.now + window
        return [self.counts[key], int(self.expires_at[key] - self.clock.now)]

    async def ping(self) -> bool:
        import asyncio

        if self.fail:
            raise ConnectionError("redis down")
        if self.ping_delay:
            await asyncio.sleep(self.ping_delay)
        return True

    async def aclose(self) -> None:
        self.closed = True
