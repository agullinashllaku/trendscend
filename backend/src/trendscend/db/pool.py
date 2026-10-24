"""Connection pool factory with a hardened per-release reset.

asyncpg's default reset (``pg_advisory_unlock_all; CLOSE ALL; UNLISTEN *;
RESET ALL``) does not drop temporary objects and does not undo session-level
``SET ROLE`` / ``SET SESSION AUTHORIZATION``. State planted by one request
(e.g. a ``pg_temp.audit_log`` shadow table) could then leak into later requests
that reuse the pooled connection. ``DISCARD ALL`` closes all of those gaps.

asyncpg calls our hook after its own ``Connection._reset()`` (which rolls back
any open or aborted transaction) and outside a transaction, so ``DISCARD ALL``
is legal there. If the hook raises, asyncpg terminates the connection and the
next acquire opens a fresh one. ``DISCARD ALL`` also deallocates server-side
prepared statements, so the client-side statement cache is dropped as well.
"""

from __future__ import annotations

import asyncpg


async def reset_connection(conn: asyncpg.Connection) -> None:
    await conn.execute("DISCARD ALL")
    # DISCARD ALL deallocates server-side prepared statements, but asyncpg's
    # client-side statement cache still points at them (next use would raise
    # InvalidSQLStatementNameError). reload_schema_state() is the public API
    # that drops those caches; asyncpg refcounts statements so ones still in
    # use by other pooled connections are not closed underneath them.
    await conn.reload_schema_state()


async def create_pool(dsn: str, *, min_size: int = 1, max_size: int = 10) -> asyncpg.Pool:
    return await asyncpg.create_pool(
        dsn, min_size=min_size, max_size=max_size, reset=reset_connection
    )
