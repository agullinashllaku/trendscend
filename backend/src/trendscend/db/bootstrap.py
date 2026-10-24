"""Create or repair the API's database login role (run as owner/superuser).

``python -m trendscend.db.bootstrap`` applies migrations as the owner, then
makes the login role named by ``API_DB_ROLE`` exactly: LOGIN, no privileged
attributes, no role-level GUC defaults, member of ``trendscend_app`` only. It
is idempotent and repairs drift on every run.

Security properties:
- Every identifier/literal is rendered server-side with ``format('%I'/'%L')``;
  no value is ever interpolated into SQL by Python.
- The password never appears in output, logs or exception text: any failure in
  the SQL phase is re-raised as ``RuntimeError("bootstrap failed")`` with the
  original exception suppressed.
- Caveat: the server itself may log the statement text (``log_statement``
  ``ddl``/``all``) which contains the password; keep server statement logging
  off or restricted for this operation. PostgreSQL hashes the password
  (SCRAM-SHA-256 when ``password_encryption`` is scram-sha-256) before storage.
"""

from __future__ import annotations

import asyncio
import os
import re
import sys

import asyncpg
from pydantic import SecretStr

from trendscend.db.migrate import apply_migrations

_ROLE_RE = re.compile(r"[a-z_][a-z0-9_]{0,62}")  # fullmatch only: `$` would allow "x\n"
_APP_ROLE = "trendscend_app"
_RESERVED = frozenset({_APP_ROLE, "postgres"})
_ATTRS = "LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB NOREPLICATION INHERIT"
_ENV_VARS = ("OWNER_DATABASE_URL", "API_DB_ROLE", "API_DB_PASSWORD")


def _validate_name(login_role: str) -> None:
    if _ROLE_RE.fullmatch(login_role) is None:
        raise ValueError("login_role must match ^[a-z_][a-z0-9_]{0,62}$")
    if login_role.startswith("pg_") or login_role in _RESERVED:
        raise ValueError("login_role is reserved")


async def _run(conn: asyncpg.Connection, login_role: str, password: str) -> None:
    async with conn.transaction():
        me = await conn.fetchrow("SELECT current_user AS cu, session_user AS su")
        if me is not None and login_role in (me["cu"], me["su"]):
            raise ValueError("login_role must not be the owner's own role")
        exists = await conn.fetchval("SELECT 1 FROM pg_roles WHERE rolname = $1", login_role)
        verb = "ALTER" if exists else "CREATE"
        stmt = await conn.fetchval(
            f"SELECT format('{verb} ROLE %I {_ATTRS} VALID UNTIL ''infinity'' PASSWORD %L', "
            "$1::text, $2::text)",
            login_role,
            password,
        )
        await conn.execute(stmt)
        if exists:
            await conn.execute(
                await conn.fetchval(
                    "SELECT format('ALTER ROLE %I RESET ALL', $1::text)", login_role
                )
            )
            per_db = await conn.fetch(
                "SELECT d.datname FROM pg_db_role_setting s "
                "JOIN pg_roles r ON r.oid = s.setrole "
                "JOIN pg_database d ON d.oid = s.setdatabase WHERE r.rolname = $1",
                login_role,
            )
            for row in per_db:
                await conn.execute(
                    await conn.fetchval(
                        "SELECT format('ALTER ROLE %I IN DATABASE %I RESET ALL', "
                        "$1::text, $2::text)",
                        login_role,
                        row["datname"],
                    )
                )
            # Drop every direct membership (including trendscend_app, to clear
            # any ADMIN/NOINHERIT drift), then grant exactly the one we want.
            held = await conn.fetch(
                "SELECT g.rolname AS role FROM pg_auth_members m "
                "JOIN pg_roles g ON g.oid = m.roleid JOIN pg_roles u ON u.oid = m.member "
                "WHERE u.rolname = $1",
                login_role,
            )
            for row in held:
                await conn.execute(
                    await conn.fetchval(
                        "SELECT format('REVOKE %I FROM %I', $1::text, $2::text)",
                        row["role"],
                        login_role,
                    )
                )
        await conn.execute(
            await conn.fetchval(
                "SELECT format('GRANT %I TO %I', $1::text, $2::text)", _APP_ROLE, login_role
            )
        )


async def bootstrap_api_role(
    conn: asyncpg.Connection, login_role: str, password: SecretStr
) -> None:
    """Create/repair ``login_role``; raises ValueError (bad input) or RuntimeError (generic)."""
    _validate_name(login_role)
    secret = password.get_secret_value()
    if not secret:
        raise ValueError("password must not be empty")
    try:
        await _run(conn, login_role, secret)
    except ValueError:
        raise
    except Exception:
        # Deliberately drop the original: driver errors can echo statement text.
        raise RuntimeError("bootstrap failed") from None


async def _main(owner_url: str, login_role: str, password: SecretStr) -> None:
    conn = await asyncpg.connect(owner_url)
    try:
        await apply_migrations(conn)
        await bootstrap_api_role(conn, login_role, password)
    finally:
        await conn.close()


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args:
        print(
            "usage: python -m trendscend.db.bootstrap (configure via environment)", file=sys.stderr
        )
        return 2
    for name in _ENV_VARS:
        if not os.environ.get(name):
            print(f"Missing required environment variable: {name}", file=sys.stderr)
            return 2
    try:
        _validate_name(os.environ["API_DB_ROLE"])  # fail before touching the network
        asyncio.run(
            _main(
                os.environ["OWNER_DATABASE_URL"],
                os.environ["API_DB_ROLE"],
                SecretStr(os.environ["API_DB_PASSWORD"]),
            )
        )
    except ValueError:
        print("Invalid API_DB_ROLE or API_DB_PASSWORD", file=sys.stderr)
        return 2
    except Exception:
        print("bootstrap failed", file=sys.stderr)
        return 1
    print("ok")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
