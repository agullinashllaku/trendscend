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
- Only a client-computed SCRAM-SHA-256 verifier is sent to the server; the
  plaintext never appears in any statement, bind parameter or server log. The
  verifier can still appear in server logs on errors (offline brute-force
  only), so use a long random password. Passwords must be ASCII (asyncpg
  SASLprep-normalises at login, which would mismatch a raw-UTF-8 verifier).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import os
import re
import sys

import asyncpg
from pydantic import SecretStr

from trendscend.db.migrate import apply_migrations

_ROLE_RE = re.compile(r"[a-z_][a-z0-9_]{0,62}")  # fullmatch only: `$` would allow "x\n"
_APP_ROLE = "trendscend_app"
_RESERVED = frozenset({_APP_ROLE, "postgres"})
_ATTRS = (
    "LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB NOREPLICATION INHERIT "
    "CONNECTION LIMIT -1"
)
# Distinct from the migration lock (0x7472_656E_6473_0001): serialises bootstraps.
_LOCK_KEY = 0x7472_656E_6473_0002
_ENV_VARS = ("OWNER_DATABASE_URL", "API_DB_ROLE", "API_DB_PASSWORD")


def _validate_name(login_role: str) -> None:
    if _ROLE_RE.fullmatch(login_role) is None:
        raise ValueError("login_role must match ^[a-z_][a-z0-9_]{0,62}$")
    if login_role.startswith("pg_") or login_role in _RESERVED:
        raise ValueError("login_role is reserved")


class _RefusedError(Exception):
    """Input/state refusal; converted to ValueError outside the except block."""


def _scram_verifier(password: str, *, salt: bytes | None = None, iterations: int = 4096) -> str:
    """Client-side SCRAM-SHA-256 verifier, so the plaintext never reaches the server."""
    salt = os.urandom(16) if salt is None else salt
    salted = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    client_key = hmac.new(salted, b"Client Key", "sha256").digest()
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.new(salted, b"Server Key", "sha256").digest()

    def b64(raw: bytes) -> str:
        return base64.b64encode(raw).decode("ascii")

    return f"SCRAM-SHA-256${iterations}:{b64(salt)}${b64(stored_key)}:{b64(server_key)}"


async def _run(conn: asyncpg.Connection, login_role: str, password: str) -> None:
    async with conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock($1)", _LOCK_KEY)
        me = await conn.fetchrow("SELECT current_user AS cu, session_user AS su")
        if me is not None and login_role in (me["cu"], me["su"]):
            raise _RefusedError("login_role must not be the owner's own role")
        raw_iters = await conn.fetchval("SELECT current_setting('scram_iterations', true)")
        verifier = _scram_verifier(password, iterations=int(raw_iters) if raw_iters else 4096)
        existing = await conn.fetchrow(
            "SELECT oid, rolsuper FROM pg_roles WHERE rolname = $1", login_role
        )
        if existing is not None:
            owns = await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM pg_shdepend WHERE deptype = 'o' "
                "AND refclassid = 'pg_authid'::regclass AND refobjid = $1) "
                "OR EXISTS (SELECT 1 FROM pg_database WHERE datdba = $1)",
                existing["oid"],
            )
            if existing["rolsuper"] or owns:
                raise _RefusedError("refusing to adopt existing role")
        verb = "ALTER" if existing is not None else "CREATE"
        stmt = await conn.fetchval(
            f"SELECT format('{verb} ROLE %I {_ATTRS} VALID UNTIL ''infinity'' PASSWORD %L', "
            "$1::text, $2::text)",
            login_role,
            verifier,
        )
        await conn.execute(stmt)
        if existing is not None:
            reset_sql = await conn.fetchval(
                "SELECT format('ALTER ROLE %I RESET ALL', $1::text)", login_role
            )
            await conn.execute(reset_sql)
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
            # Drop every direct membership whoever granted it (a plain REVOKE only
            # removes grants made by the current user), then grant exactly one.
            held = await conn.fetch(
                "SELECT g.rolname AS role, gr.rolname AS grantor FROM pg_auth_members m "
                "JOIN pg_roles g ON g.oid = m.roleid JOIN pg_roles u ON u.oid = m.member "
                "JOIN pg_roles gr ON gr.oid = m.grantor WHERE u.rolname = $1",
                login_role,
            )
            for row in held:
                await conn.execute(
                    await conn.fetchval(
                        "SELECT format('REVOKE %I FROM %I GRANTED BY %I CASCADE', "
                        "$1::text, $2::text, $3::text)",
                        row["role"],
                        login_role,
                        row["grantor"],
                    )
                )
        await conn.execute(
            await conn.fetchval(
                "SELECT format('GRANT %I TO %I', $1::text, $2::text)", _APP_ROLE, login_role
            )
        )
        final = await conn.fetch(
            "SELECT g.rolname AS role, m.admin_option, m.inherit_option, m.set_option "
            "FROM pg_auth_members m JOIN pg_roles g ON g.oid = m.roleid "
            "JOIN pg_roles u ON u.oid = m.member WHERE u.rolname = $1",
            login_role,
        )
        if [tuple(r) for r in final] != [(_APP_ROLE, False, True, True)]:
            raise RuntimeError("membership verification failed")  # rolls back


async def bootstrap_api_role(
    conn: asyncpg.Connection, login_role: str, password: SecretStr
) -> None:
    """Create/repair ``login_role``; raises ValueError (bad input) or RuntimeError (generic)."""
    _validate_name(login_role)
    secret = password.get_secret_value()
    if not secret:
        raise ValueError("password must not be empty")
    if not secret.isascii() or "\x00" in secret:
        # asyncpg SASLprep-normalises at login, which would not match a verifier
        # computed on raw non-ASCII bytes. Never echo the password.
        raise ValueError("password must be ASCII without NUL")
    refused: str | None = None
    failed = False
    try:
        await _run(conn, login_role, secret)
    except _RefusedError as exc:
        refused = str(exc)
    except Exception:
        failed = True
    finally:
        secret = ""  # nosec B105 - wiping the local, not a hardcoded password
    # Raised outside the except block so no __context__/__cause__ is attached.
    if refused is not None:
        raise ValueError(refused)
    if failed:
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
