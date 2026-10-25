"""Startup errors for unparseable/unreachable DATABASE_URL / REDIS_URL never leak."""

from __future__ import annotations

import os
import subprocess  # noqa: S404
import sys
import traceback
from pathlib import Path

import pytest

from tests.api.fakes import FakeRedis
from trendscend.api.app import create_app
from trendscend.api.settings import Settings

SENT = "SENTINELq7Zk93"
GOOD_REDIS = "redis://localhost:6379/0"
GOOD_DB = "postgresql://u:pw@localhost:5432/db"
BACKEND = Path(__file__).resolve().parents[2]


def _settings(db: str, rd: str) -> Settings:
    return Settings(database_url=db, redis_url=rd, environment="dev")  # type: ignore[arg-type]


def _assert_clean(exc: BaseException, generic: str) -> None:
    assert str(exc) == generic
    for text in (str(exc), repr(exc), "".join(traceback.format_exception(exc))):
        assert SENT not in text
    assert exc.__context__ is None and exc.__cause__ is None


async def _run_lifespan(settings: Settings, **kw: object) -> RuntimeError:
    app = create_app(settings, **kw)  # type: ignore[arg-type]
    with pytest.raises(RuntimeError) as ei:
        async with app.router.lifespan_context(app):
            pytest.fail("startup must fail")
    return ei.value


async def test_bad_redis_url_is_sanitised() -> None:
    exc = await _run_lifespan(_settings(GOOD_DB, f"redis://:{SENT}/pw#x@localhost:6379/0"))
    _assert_clean(exc, "Invalid REDIS_URL (value not shown)")


@pytest.mark.parametrize(
    "dsn",
    [
        f"postgresql://user:{SENT}/pw#x@localhost:5432/db",  # unparseable
        f"postgresql://user:{SENT}@127.0.0.1:1/db",  # valid DSN, closed port
    ],
)
async def test_bad_or_unreachable_database_url_is_sanitised(dsn: str) -> None:
    exc = await _run_lifespan(_settings(dsn, GOOD_REDIS), redis=FakeRedis())
    _assert_clean(exc, "Database connection failed; check DATABASE_URL (value not shown)")


async def test_redis_closed_when_database_connect_fails() -> None:
    exc = await _run_lifespan(_settings(f"postgresql://u:{SENT}@127.0.0.1:1/db", GOOD_REDIS))
    _assert_clean(exc, "Database connection failed; check DATABASE_URL (value not shown)")


def _launch(env_extra: dict[str, str]) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", "REDIS_URL")}
    env.update(
        PYTHONPATH=str(BACKEND / "src"),
        ENVIRONMENT="dev",
        API_PORT="8123",
        **env_extra,
    )
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", "trendscend.api"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        cwd=BACKEND,
        check=False,
    )


@pytest.mark.parametrize(
    "db,rd",
    [
        (f"postgresql://user:{SENT}/pw#x@localhost:5432/db", GOOD_REDIS),
        (GOOD_DB, f"redis://:{SENT}/pw#x@localhost:6379/0"),
    ],
)
def test_subprocess_startup_failure_does_not_print_secret(db: str, rd: str) -> None:
    proc = _launch({"DATABASE_URL": db, "REDIS_URL": rd})
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0
    assert SENT not in out
    assert "value not shown" in out
