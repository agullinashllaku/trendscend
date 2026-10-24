"""Startup safety assertions for the API process."""

from __future__ import annotations

import asyncpg

# Evaluated against current_user (not session_user) so a SET [LOCAL] ROLE
# downgrade is judged by the role whose privileges actually apply.
_ROLE_QUERY = """
SELECT r.rolsuper AS rolsuper,
       r.rolbypassrls AS rolbypassrls,
       EXISTS (
           SELECT 1 FROM pg_class c
           JOIN pg_namespace n ON n.oid = c.relnamespace
           WHERE c.relrowsecurity
             AND c.relkind IN ('r', 'p')
             AND n.nspname NOT IN ('pg_catalog', 'information_schema')
             AND pg_has_role(current_user, c.relowner, 'USAGE')
       ) AS controls_rls_tables,
       EXISTS (
           SELECT 1 FROM pg_roles r2
           WHERE (r2.rolsuper OR r2.rolbypassrls)
             AND pg_has_role(current_user, r2.oid, 'MEMBER')
       ) AS can_assume_privileged
FROM pg_roles r
WHERE r.rolname = current_user
"""


async def assert_safe_db_role(conn: asyncpg.Connection) -> None:
    """Fail startup if the DB role would bypass row-level security.

    Raises RuntimeError (message carries no credentials or DSN) if the current
    role is a superuser, has BYPASSRLS, is (directly or via INHERIT membership)
    the owner of ANY RLS-enabled table in a non-system schema (an owner can
    disable RLS or drop policies), or is a member of a superuser/BYPASSRLS role
    (rolsuper/rolbypassrls are not inherited, but SET ROLE escalates).
    """
    row = await conn.fetchrow(_ROLE_QUERY)
    if row is None:
        raise RuntimeError("Unsafe database role: current role not found in pg_roles")
    problems: list[str] = []
    if row["rolsuper"]:
        problems.append("superuser")
    if row["rolbypassrls"]:
        problems.append("BYPASSRLS")
    if row["controls_rls_tables"]:
        problems.append("owns or inherits ownership of RLS tables")
    if row["can_assume_privileged"]:
        problems.append("member of a superuser/BYPASSRLS role")
    if problems:
        raise RuntimeError(
            "Unsafe database role for the API (" + ", ".join(problems) + "); "
            "connect as a non-superuser, non-BYPASSRLS role that is a member of "
            "trendscend_app and owns no RLS tables"
        )
