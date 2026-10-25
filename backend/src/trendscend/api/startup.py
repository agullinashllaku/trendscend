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
#
# Beyond RLS bypass and ownership, the query also rejects capabilities that let a
# compromised API role escape RLS indirectly: REPLICATION (reads WAL for every
# tenant), CREATEROLE (can mint/grant roles), the server-file/program built-in
# roles (read/write server files, run programs), ownership of a trigger function
# on an RLS table (can DROP FUNCTION ... CASCADE, removing the audit triggers, or
# ALTER FUNCTION ... SECURITY DEFINER; CREATE OR REPLACE is not possible here as
# no role has CREATE on a schema), and TRUNCATE/TRIGGER/REFERENCES on RLS tables
# (TRUNCATE ignores RLS; column-level REFERENCES counts too, since FK checks
# ignore RLS and give a cross-tenant existence oracle). A session_replication_role
# other than 'origin' is rejected because triggers (append-only, RI) do not fire.
# CREATE and TEMPORARY on the database are rejected too (CREATE lets a role add
# schemas; TEMP lets it create temp tables/objects). has_database_privilege
# already covers PUBLIC and inherited grants; it is evaluated for every role the
# session role is a MEMBER of (r itself included), so a NOINHERIT member of a
# CREATE holder, who can still SET ROLE to it, is caught as well. The
# bootstrap revokes both and migration 003 removed TEMP from PUBLIC.
# pg_read_all_data/pg_write_all_data/pg_monitor/pg_signal_backend
# were verified on PG16 NOT to bypass FORCE RLS and are deliberately not listed.
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
       ) AS controls_database,
       r.rolreplication AS rolreplication,
       r.rolcreaterole AS rolcreaterole,
       EXISTS (
           SELECT 1 FROM pg_roles r3
           WHERE r3.rolname IN (
                 'pg_read_server_files', 'pg_write_server_files',
                 'pg_execute_server_program')
             AND pg_has_role(r.oid, r3.oid, 'MEMBER')
       ) AS can_use_server_files,
       EXISTS (
           SELECT 1 FROM pg_trigger t
           JOIN pg_class c ON c.oid = t.tgrelid
           JOIN pg_namespace n ON n.oid = c.relnamespace
           JOIN pg_proc p ON p.oid = t.tgfoid
           WHERE NOT t.tgisinternal
             AND c.relrowsecurity
             AND c.relkind IN ('r', 'p')
             AND n.nspname NOT IN ('pg_catalog', 'information_schema')
             AND pg_has_role(r.oid, p.proowner, 'MEMBER')
       ) AS controls_rls_functions,
       EXISTS (
           SELECT 1 FROM pg_roles r4
           JOIN pg_class c ON c.relrowsecurity AND c.relkind IN ('r', 'p')
           JOIN pg_namespace n ON n.oid = c.relnamespace
           WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
             AND pg_has_role(r.oid, r4.oid, 'MEMBER')
             AND (has_table_privilege(r4.oid, c.oid, 'TRUNCATE')
                  OR has_table_privilege(r4.oid, c.oid, 'TRIGGER')
                  OR has_any_column_privilege(r4.oid, c.oid, 'REFERENCES'))
       ) AS has_dangerous_table_privs,
       current_setting('session_replication_role') <> 'origin'
           AS replication_role_not_origin,
       EXISTS (
           SELECT 1 FROM pg_roles r5
           WHERE pg_has_role(r.oid, r5.oid, 'MEMBER')
             AND has_database_privilege(r5.oid, current_database(), 'CREATE')
       ) AS holds_db_create,
       EXISTS (
           SELECT 1 FROM pg_roles r6
           WHERE pg_has_role(r.oid, r6.oid, 'MEMBER')
             AND has_database_privilege(r6.oid, current_database(), 'TEMP')
       ) AS holds_db_temp
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
    superuser/BYPASSRLS role; or has REPLICATION, CREATEROLE, membership of a
    server-file/program built-in role, control of a trigger function on an RLS
    table, TRUNCATE/TRIGGER/REFERENCES on an RLS table, or CREATE/TEMPORARY on
    the database (directly or via any role it can assume).
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
    flag("rolreplication", "REPLICATION")
    flag("rolcreaterole", "CREATEROLE")
    flag("can_use_server_files", "member of a server-file/program built-in role")
    flag(
        "controls_rls_functions",
        "owns or can assume ownership of a trigger function on RLS tables",
    )
    flag("has_dangerous_table_privs", "holds TRUNCATE/TRIGGER/REFERENCES on RLS tables")
    flag("replication_role_not_origin", "session_replication_role is not 'origin'")
    flag("holds_db_create", "holds CREATE on the database")
    flag("holds_db_temp", "holds TEMPORARY on the database")
    if problems:
        raise RuntimeError(
            "Unsafe database role for the API (" + ", ".join(problems) + "); "
            "connect as a non-superuser, non-BYPASSRLS role that is a member of "
            "trendscend_app, has no CREATEROLE/REPLICATION or server-file/program "
            "roles, owns no RLS tables, schema, database or trigger function on RLS "
            "tables, holds no TRUNCATE/TRIGGER/REFERENCES on RLS tables and no "
            "CREATE/TEMPORARY on the database, and runs "
            "with session_replication_role 'origin'"
        )
