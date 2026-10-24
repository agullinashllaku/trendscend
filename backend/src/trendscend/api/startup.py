"""Startup safety assertions for the API process."""

from __future__ import annotations

import asyncpg

# Every predicate is evaluated for BOTH session_user and current_user. A
# SET [LOCAL] ROLE downgrade is NOT trusted: RESET ROLE (or SET ROLE) returns to
# the session user, so a privileged session user is unsafe whatever current_user
# is. When the two are the same role the query returns a single row.
#
# All role-membership tests use 'MEMBER' (not 'USAGE'): a NOINHERIT member of a
# role cannot use its privileges implicitly but can still SET ROLE to it.
_ROLE_QUERY = """
SELECT r.rolsuper AS rolsuper,
       r.rolbypassrls AS rolbypassrls,
       EXISTS (
           SELECT 1 FROM pg_class c
           JOIN pg_namespace n ON n.oid = c.relnamespace
           WHERE c.relrowsecurity
             AND c.relkind IN ('r', 'p')
             AND n.nspname NOT IN ('pg_catalog', 'information_schema')
             AND pg_has_role(r.oid, c.relowner, 'MEMBER')
       ) AS controls_rls_tables,
       EXISTS (
           SELECT 1 FROM pg_roles r2
           WHERE (r2.rolsuper OR r2.rolbypassrls)
             AND pg_has_role(r.oid, r2.oid, 'MEMBER')
       ) AS can_assume_privileged,
       EXISTS (
           SELECT 1 FROM pg_namespace n
           WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
             AND n.nspname NOT LIKE 'pg\\_%'
             AND pg_has_role(r.oid, n.nspowner, 'MEMBER')
             AND EXISTS (
                 SELECT 1 FROM pg_class c
                 WHERE c.relnamespace = n.oid
                   AND c.relrowsecurity
                   AND c.relkind IN ('r', 'p')
             )
       ) AS controls_rls_schema,
       EXISTS (
           SELECT 1 FROM pg_database d
           WHERE d.datname = current_database()
             AND pg_has_role(r.oid, d.datdba, 'MEMBER')
       ) AS controls_database
FROM pg_roles r
WHERE r.rolname IN (session_user, current_user)
"""


async def assert_safe_db_role(conn: asyncpg.Connection) -> None:
    """Fail startup if the DB role would bypass row-level security.

    Checked for both session_user and current_user. Raises RuntimeError (the
    message is a fixed string carrying no credentials or DSN) if either is a
    superuser, has BYPASSRLS, is a MEMBER (direct or indirect, inherited or not,
    since SET ROLE works regardless) of the owner of any RLS-enabled table, of
    the owner of a schema containing RLS tables, of the database owner (all of
    which can disable RLS, drop policies or drop tables), or of a
    superuser/BYPASSRLS role.
    """
    rows = await conn.fetch(_ROLE_QUERY)
    if not rows:
        raise RuntimeError("Unsafe database role: current role not found in pg_roles")
    problems: list[str] = []

    def flag(key: str, text: str) -> None:
        if any(r[key] for r in rows) and text not in problems:
            problems.append(text)

    flag("rolsuper", "superuser")
    flag("rolbypassrls", "BYPASSRLS")
    flag("controls_rls_tables", "owns or inherits ownership of RLS tables")
    flag("can_assume_privileged", "member of a superuser/BYPASSRLS role")
    flag("controls_rls_schema", "owns or can assume ownership of a schema holding RLS tables")
    flag("controls_database", "owns or can assume ownership of the database")
    if problems:
        raise RuntimeError(
            "Unsafe database role for the API (" + ", ".join(problems) + "); "
            "connect as a non-superuser, non-BYPASSRLS role that is a member of "
            "trendscend_app and owns no RLS tables, schema or database"
        )
