"""Startup safety assertions for the API process."""

from __future__ import annotations

import asyncpg

TENANCY_TABLES = ("users", "invites", "audit_log")

# Evaluated against current_user (not session_user) so a SET [LOCAL] ROLE
# downgrade is judged by the role whose privileges actually apply.
_ROLE_QUERY = """
SELECT r.rolsuper AS rolsuper,
       r.rolbypassrls AS rolbypassrls,
       EXISTS (
           SELECT 1 FROM pg_tables t
           WHERE t.tableowner = current_user
             AND t.tablename = ANY($1::text[])
             AND t.schemaname = ANY (current_schemas(false))
       ) AS owns_tenancy_tables
FROM pg_roles r
WHERE r.rolname = current_user
"""


async def assert_safe_db_role(conn: asyncpg.Connection) -> None:
    """Fail startup if the DB role would bypass row-level security.

    Raises RuntimeError (message carries no credentials or DSN) if the current
    role is a superuser, has BYPASSRLS, or owns a tenancy table (table owners
    are only subject to RLS because of FORCE; do not rely on that alone).
    """
    row = await conn.fetchrow(_ROLE_QUERY, list(TENANCY_TABLES))
    if row is None:
        raise RuntimeError("Unsafe database role: current role not found in pg_roles")
    problems: list[str] = []
    if row["rolsuper"]:
        problems.append("superuser")
    if row["rolbypassrls"]:
        problems.append("BYPASSRLS")
    if row["owns_tenancy_tables"]:
        problems.append("owns tenancy tables")
    if problems:
        raise RuntimeError(
            "Unsafe database role for the API (" + ", ".join(problems) + "); "
            "connect as a non-superuser, non-BYPASSRLS role that is a member of "
            "trendscend_app and owns no tenancy tables"
        )
