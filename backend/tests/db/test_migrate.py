"""Tests for the migration runner (TDD).

Unit tests use a fake connection; integration tests run each scenario in a
throwaway schema so they never touch the real ``schema_migrations`` table.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import asyncpg
import pytest
import pytest_asyncio

from trendscend.db.migrate import apply_migrations

SKIP_IF_NO_DB = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set — skipping DB integration tests",
)


class FakeTx:
    def __init__(self, conn: FakeConn) -> None:
        self.conn = conn

    async def __aenter__(self) -> None:
        self.conn.log.append("begin")

    async def __aexit__(self, *exc: object) -> None:
        self.conn.log.append("rollback" if exc[0] else "commit")


class FakeConn:
    """Minimal asyncpg.Connection stand-in recording executed SQL."""

    def __init__(self, applied: list[str] | None = None) -> None:
        self.applied = list(applied or [])
        self.log: list[str] = []

    def transaction(self) -> FakeTx:
        return FakeTx(self)

    async def execute(self, sql: str, *args: Any) -> str:
        if "INSERT INTO schema_migrations" in sql:
            self.applied.append(args[0])
            self.log.append(f"record:{args[0]}")
        else:
            self.log.append(f"sql:{sql.strip()}")
        return "OK"

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, str]]:
        return [{"version": v} for v in self.applied]

    async def fetchval(self, sql: str, *args: Any) -> None:
        return None


def _write(directory: Path, name: str, sql: str) -> None:
    (directory / name).write_text(sql)


class TestUnit:
    async def test_applies_in_numeric_order_and_skips_applied(self, tmp_path: Path) -> None:
        _write(tmp_path, "010_c.sql", "SELECT 10;")
        _write(tmp_path, "002_b.sql", "SELECT 2;")
        _write(tmp_path, "001_a.sql", "SELECT 1;")
        _write(tmp_path, "README.md", "ignored")
        conn = FakeConn(applied=["001_a"])
        result = await apply_migrations(conn, tmp_path)  # type: ignore[arg-type]
        assert result == ["002_b", "010_c"]
        assert [
            x
            for x in conn.log
            if x.startswith("sql:SELECT ")
            and "pg_advisory" not in x
            and "schema_migrations" not in x
        ] == [
            "sql:SELECT 2;",
            "sql:SELECT 10;",
        ]

    async def test_second_call_returns_empty(self, tmp_path: Path) -> None:
        _write(tmp_path, "001_a.sql", "SELECT 1;")
        conn = FakeConn()
        assert await apply_migrations(conn, tmp_path) == ["001_a"]  # type: ignore[arg-type]
        assert await apply_migrations(conn, tmp_path) == []  # type: ignore[arg-type]

    async def test_duplicate_numeric_prefix_rejected(self, tmp_path: Path) -> None:
        _write(tmp_path, "001_a.sql", "SELECT 1;")
        _write(tmp_path, "001_b.sql", "SELECT 2;")
        with pytest.raises(ValueError, match="duplicate"):
            await apply_migrations(FakeConn(), tmp_path)  # type: ignore[arg-type]


@pytest_asyncio.fixture(loop_scope="session")
async def scratch_conn() -> AsyncIterator[asyncpg.Connection]:
    """Connection whose search_path is a throwaway schema."""
    schema = f"mig_{uuid.uuid4().hex[:12]}"
    admin = await asyncpg.connect(os.environ["TEST_DATABASE_URL"])
    await admin.execute(f'CREATE SCHEMA "{schema}"')
    conn = await asyncpg.connect(
        os.environ["TEST_DATABASE_URL"], server_settings={"search_path": schema}
    )
    try:
        yield conn
    finally:
        await conn.close()
        await admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await admin.close()


@SKIP_IF_NO_DB
@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
class TestIntegration:
    async def test_apply_is_idempotent(
        self, scratch_conn: asyncpg.Connection, tmp_path: Path
    ) -> None:
        _write(tmp_path, "001_a.sql", "CREATE TABLE a (x int);")
        _write(tmp_path, "002_b.sql", "CREATE TABLE b (x int);")
        assert await apply_migrations(scratch_conn, tmp_path) == ["001_a", "002_b"]
        assert await apply_migrations(scratch_conn, tmp_path) == []

    async def test_applies_in_numeric_order(
        self, scratch_conn: asyncpg.Connection, tmp_path: Path
    ) -> None:
        # 002 depends on 001; 010 on 002. Lexical order would also pass, so
        # include 10 vs 2 to prove numeric (not string) ordering.
        _write(tmp_path, "010_c.sql", "INSERT INTO t VALUES (10);")
        _write(tmp_path, "002_b.sql", "INSERT INTO t VALUES (2);")
        _write(tmp_path, "001_a.sql", "CREATE TABLE t (x int);")
        applied = await apply_migrations(scratch_conn, tmp_path)
        assert applied == ["001_a", "002_b", "010_c"]
        rows = await scratch_conn.fetch("SELECT version FROM schema_migrations ORDER BY applied_at")
        assert [r["version"] for r in rows] == applied

    async def test_failed_migration_rolls_back_and_is_not_recorded(
        self, scratch_conn: asyncpg.Connection, tmp_path: Path
    ) -> None:
        _write(tmp_path, "001_ok.sql", "CREATE TABLE ok (x int);")
        _write(tmp_path, "002_bad.sql", "CREATE TABLE half (x int); SELECT 1/0;")
        with pytest.raises(asyncpg.DataError):
            await apply_migrations(scratch_conn, tmp_path)
        assert await scratch_conn.fetchval("SELECT to_regclass('half')") is None
        versions = [
            r["version"] for r in await scratch_conn.fetch("SELECT version FROM schema_migrations")
        ]
        assert versions == ["001_ok"]
