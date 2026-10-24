"""Minimal forward-only SQL migration runner.

Applies ``NNN_name.sql`` files from the migrations directory in numeric order,
each inside its own transaction together with its ``schema_migrations`` row, so
a failing file leaves no partial schema and is not recorded. A session-level
advisory lock serialises concurrent runners (e.g. several containers booting).
"""

from __future__ import annotations

import re
from pathlib import Path

import asyncpg

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_NAME_RE = re.compile(r"^(\d+)_.+\.sql$")
# Arbitrary constant identifying "trendscend migrations" for pg_advisory_lock.
_LOCK_KEY = 0x7472_656E_6473_0001


def _discover(directory: Path) -> list[tuple[str, Path]]:
    found: dict[int, tuple[str, Path]] = {}
    for path in directory.iterdir():
        match = _NAME_RE.match(path.name)
        if match is None:
            continue
        number = int(match.group(1))
        if number in found:
            raise ValueError(
                f"duplicate migration number {number}: {found[number][1].name}, {path.name}"
            )
        found[number] = (path.stem, path)
    return [found[n] for n in sorted(found)]


async def apply_migrations(conn: asyncpg.Connection, directory: Path | None = None) -> list[str]:
    """Apply unapplied migrations in order; return the versions applied now."""
    migrations = _discover(directory or MIGRATIONS_DIR)
    await conn.execute("SELECT pg_advisory_lock($1)", _LOCK_KEY)
    try:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version    TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        done = {r["version"] for r in await conn.fetch("SELECT version FROM schema_migrations")}
        applied: list[str] = []
        for version, path in migrations:
            if version in done:
                continue
            async with conn.transaction():
                await conn.execute(path.read_text())
                await conn.execute("INSERT INTO schema_migrations (version) VALUES ($1)", version)
            applied.append(version)
        return applied
    finally:
        await conn.execute("SELECT pg_advisory_unlock($1)", _LOCK_KEY)
