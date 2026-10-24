"""Fail-closed per-request tenant context for Postgres row-level security.

``tenant_connection`` is the only sanctioned way for request-scoped code to get
a database connection. It opens a transaction and sets ``app.current_user`` with
``set_config(..., true)`` (transaction-local), which the RLS policies read. The
setting dies with the transaction, so it can never leak to the next user of a
pooled connection. A connection obtained without this helper has no setting and
therefore sees no rows (policies compare against ``NULLIF(..., '')::uuid``).

User ids are parsed with ``uuid.UUID`` and must already be in canonical
lowercase hyphenated form; anything else is rejected rather than repaired.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg

_ROLE_RE = re.compile(r"[a-z_][a-z0-9_]*")


class InvalidTenantError(ValueError):
    """Raised for a malformed user id or role name; nothing touches the DB."""


def normalise_user_id(value: uuid.UUID | str) -> str:
    """Return the canonical lowercase hyphenated UUID string, or raise."""
    if isinstance(value, uuid.UUID):
        return str(value)
    if not isinstance(value, str):
        raise InvalidTenantError("user id must be a UUID or str")
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        raise InvalidTenantError("user id is not a valid UUID") from None
    canonical = str(parsed)
    if value != canonical:  # whitespace, braces, urn:, uppercase, no hyphens...
        raise InvalidTenantError("user id must be canonical lowercase hyphenated form")
    return canonical


def _validate_role(role: str) -> str:
    # fullmatch: '$' would accept a trailing newline.
    if not isinstance(role, str) or _ROLE_RE.fullmatch(role) is None:
        raise InvalidTenantError("invalid role name")
    return '"' + role + '"'


@asynccontextmanager
async def tenant_connection(
    pool: asyncpg.Pool, user_id: uuid.UUID | str, *, role: str | None = None
) -> AsyncIterator[asyncpg.Connection]:
    """Yield a connection in a transaction scoped to ``user_id``.

    Inputs are validated before a connection is acquired. Commits on success,
    rolls back on any exception. ``role`` (tests only) issues
    ``SET LOCAL ROLE`` so RLS applies even when the pool role bypasses it. It
    does NOT make a privileged pool safe: handlers and repositories must never
    receive a pool whose session_user is privileged.

    Security contract (trust boundary):

    1. The yielded object is a live asyncpg connection. This helper cannot stop
       raw SQL inside the block (COMMIT, ROLLBACK, RESET ROLE, SET ROLE,
       ``set_config(..., false)``) from ending the transaction or widening
       access.
    2. COMMIT or RESET ROLE inside the block drops the ``role=`` downgrade and
       returns the connection to the pool's login role.
    3. Therefore repository/endpoint code must never issue transaction-control
       or session-level SQL (SET/RESET/COMMIT/ROLLBACK/set_config) on this
       connection.
    4. COMMIT or ROLLBACK inside the block also clears ``app.current_user``
       (it is transaction-local), so later statements in the block see no
       tenant.
    5. The real production safeguard is that the pool's login role is
       non-superuser, non-BYPASSRLS and owns no tenancy tables, schema or
       database; ``trendscend.api.startup.assert_safe_db_role`` enforces this
       at startup for both the session and the current role. ``role=`` exists
       for tests, where the pool role is a superuser.
    """
    uid = normalise_user_id(user_id)
    quoted_role = _validate_role(role) if role is not None else None
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.current_user', $1, true)", uid)
        if quoted_role is not None:
            # Safe: the fullmatch regex excludes the double-quote character, so the
            # quoted identifier needs no escaping.
            await conn.execute(f"SET LOCAL ROLE {quoted_role}")  # noqa: S608
        yield conn
