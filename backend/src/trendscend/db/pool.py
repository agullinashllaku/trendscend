"""Connection pool factory with a hardened per-release reset.

asyncpg's default reset (``pg_advisory_unlock_all; CLOSE ALL; UNLISTEN *;
RESET ALL``) does not drop temporary objects and does not undo session-level
``SET ROLE`` / ``SET SESSION AUTHORIZATION``. State planted by one request
(e.g. a ``pg_temp.audit_log`` shadow table) could then leak into later requests
that reuse the pooled connection. ``DISCARD ALL`` closes all of those gaps.

asyncpg calls our hook after its own ``Connection._reset()`` (which rolls back
any open or aborted transaction) and outside a transaction, so ``DISCARD ALL``
is legal there. If the hook raises or times out, asyncpg terminates the
connection and the next acquire opens a fresh one.

``DISCARD ALL`` deallocates server-side prepared statements. asyncpg's
client-side statement cache would keep names that no longer exist
(``InvalidSQLStatementNameError``), and the only cache-dropping API
(``reload_schema_state``) clears the caches of every connection in the pool,
including ones serving other requests. The pool therefore disables the cache
(``statement_cache_size=0``): asyncpg then uses unnamed statements and there
are no cached names to go stale. The cache would be useless anyway, since it is
wiped on every release.
"""

from __future__ import annotations

import asyncpg

# Bound DISCARD ALL so a hung server cannot block release forever; on timeout
# asyncpg's release path terminates the connection.
RESET_TIMEOUT_S = 5.0


async def reset_connection(conn: asyncpg.Connection) -> None:
    await conn.execute("DISCARD ALL", timeout=RESET_TIMEOUT_S)


async def create_pool(dsn: str, *, min_size: int = 1, max_size: int = 10) -> asyncpg.Pool:
    return await asyncpg.create_pool(
        dsn,
        min_size=min_size,
        max_size=max_size,
        reset=reset_connection,
        statement_cache_size=0,
    )
