-- 003_hardening: remove ambient privileges from PUBLIC (Phase 5a review M1, M2).
--
-- M1: PostgreSQL grants TEMPORARY on every database to PUBLIC. A role could
--     CREATE TEMP TABLE audit_log(...) and, because pg_temp is searched first
--     for unqualified relation names, redirect an unqualified INSERT into it,
--     silently bypassing the real (RLS-protected) table. trendscend_app never
--     needs temp tables, so the privilege is revoked from PUBLIC. The owner /
--     superuser is unaffected. Do NOT re-grant it to application roles.
--
-- M2: every public TimescaleDB function has a NULL ACL (executable by PUBLIC).
--     add_job/alter_job/delete_job/run_job let any LOGIN role schedule or run
--     arbitrary background jobs; get_telemetry_report leaks database metadata.
--     trendscend_app needs none of these, so EXECUTE is revoked from PUBLIC on
--     every overload. Telemetry is also switched off for the database (applies
--     to NEW sessions only).
--
-- (run_job is a PROCEDURE in current TimescaleDB, hence the prokind switch.)
-- Idempotent: REVOKE of an absent privilege and re-setting a GUC are no-ops.

DO $$
BEGIN
    EXECUTE format('REVOKE TEMPORARY ON DATABASE %I FROM PUBLIC', current_database());
END
$$;

DO $$
DECLARE
    fn regprocedure;
    kind text;
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'timescaledb') THEN
        FOR fn, kind IN
            SELECT p.oid::regprocedure,
                   CASE p.prokind WHEN 'p' THEN 'PROCEDURE' ELSE 'FUNCTION' END
            FROM pg_proc p
            JOIN pg_depend d ON d.classid = 'pg_proc'::regclass
                            AND d.objid = p.oid
                            AND d.deptype = 'e'
            JOIN pg_extension e ON e.oid = d.refobjid AND e.extname = 'timescaledb'
            WHERE p.proname IN ('add_job', 'alter_job', 'delete_job', 'run_job',
                                'get_telemetry_report')
        LOOP
            EXECUTE format('REVOKE EXECUTE ON %s %s FROM PUBLIC', kind, fn);
        END LOOP;

        EXECUTE format('ALTER DATABASE %I SET timescaledb.telemetry_level = off',
                       current_database());
    END IF;
END
$$;
