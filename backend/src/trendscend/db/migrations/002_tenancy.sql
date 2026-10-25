-- USER-OWNED TABLES: users, invites, audit_log. Each has a user_id column and
-- FORCED row-level security keyed on the transaction-local setting
-- app.current_user (set by the API per request via set_config(..., true)).
-- Shared reference tables (e.g. candles, see 001_candles.sql) have no RLS.
--
-- Isolation model (PLAN.md §7, §10):
--   * trendscend_app is the role the API runs queries as. NOLOGIN (deployments
--     GRANT it to their login role), NOBYPASSRLS, never a table owner.
--   * FORCE ROW LEVEL SECURITY makes policies apply to the table owner too.
--   * NULLIF(current_setting('app.current_user', true), '')::uuid yields NULL
--     when the setting is unset or empty, so no row matches: fail closed.
--   * Least privilege: no INSERT on users (5b adds a SECURITY DEFINER
--     function) and no UPDATE on users at all (a self-UPDATE of email would leak
--     whether another email exists via the UNIQUE violation; role/status
--     self-edits would escalate). invites UPDATE is limited to redeemed_at.
--     Only an active owner may INSERT invites.
-- Roles are cluster-wide, so creation is guarded to keep this re-runnable.

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'trendscend_app') THEN
        CREATE ROLE trendscend_app NOLOGIN NOBYPASSRLS;
    END IF;
END
$$;
ALTER ROLE trendscend_app NOLOGIN NOBYPASSRLS NOSUPERUSER NOCREATEDB NOCREATEROLE;

CREATE TABLE IF NOT EXISTS users (
    id         UUID PRIMARY KEY,
    user_id    UUID GENERATED ALWAYS AS (id) STORED,
    email      TEXT NOT NULL UNIQUE CHECK (email = lower(email)),
    role       TEXT NOT NULL CHECK (role IN ('owner', 'user')),
    status     TEXT NOT NULL CHECK (status IN ('active', 'disabled')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- user_id is the inviting owner.
CREATE TABLE IF NOT EXISTS invites (
    id          UUID PRIMARY KEY,
    user_id     UUID NOT NULL REFERENCES users (id),
    token_hash  BYTEA NOT NULL UNIQUE,
    expires_at  TIMESTAMPTZ NOT NULL,
    redeemed_at TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- No FK on user_id: audit entries must outlive the user row.
CREATE TABLE IF NOT EXISTS audit_log (
    id         BIGSERIAL PRIMARY KEY,
    user_id    UUID NOT NULL,
    action     TEXT NOT NULL,
    detail     JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE users     ENABLE ROW LEVEL SECURITY;
ALTER TABLE users     FORCE  ROW LEVEL SECURITY;
ALTER TABLE invites   ENABLE ROW LEVEL SECURITY;
ALTER TABLE invites   FORCE  ROW LEVEL SECURITY;
ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_log FORCE  ROW LEVEL SECURITY;

DROP POLICY IF EXISTS users_isolation ON users;
CREATE POLICY users_isolation ON users
    USING      (user_id = NULLIF(current_setting('app.current_user', true), '')::uuid)
    WITH CHECK (user_id = NULLIF(current_setting('app.current_user', true), '')::uuid);

-- A policy takes a single command, so SELECT and UPDATE are separate.
-- (invites_isolation is the pre-split name; dropped for re-runs.)
DROP POLICY IF EXISTS invites_isolation ON invites;
DROP POLICY IF EXISTS invites_select ON invites;
CREATE POLICY invites_select ON invites
    FOR SELECT
    USING (user_id = NULLIF(current_setting('app.current_user', true), '')::uuid);

DROP POLICY IF EXISTS invites_update ON invites;
CREATE POLICY invites_update ON invites
    FOR UPDATE
    USING      (user_id = NULLIF(current_setting('app.current_user', true), '')::uuid)
    WITH CHECK (user_id = NULLIF(current_setting('app.current_user', true), '')::uuid);

-- Only an active owner may create invites (for themselves as inviter).
DROP POLICY IF EXISTS invites_owner_insert ON invites;
CREATE POLICY invites_owner_insert ON invites
    FOR INSERT
    WITH CHECK (
        user_id = NULLIF(current_setting('app.current_user', true), '')::uuid
        AND EXISTS (
            SELECT 1 FROM users u
            WHERE u.id = invites.user_id AND u.role = 'owner' AND u.status = 'active'
        )
    );

DROP POLICY IF EXISTS audit_log_isolation ON audit_log;
CREATE POLICY audit_log_isolation ON audit_log
    USING      (user_id = NULLIF(current_setting('app.current_user', true), '')::uuid)
    WITH CHECK (user_id = NULLIF(current_setting('app.current_user', true), '')::uuid);

-- audit_log append-only guard: these triggers reject UPDATE/DELETE/TRUNCATE for the
-- app role and for non-superuser owners going through normal DML. They are NOT
-- absolute: the table owner or a superuser can disable or drop them, and
-- session_replication_role = replica bypasses them.
CREATE OR REPLACE FUNCTION audit_log_reject_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only (% rejected)', TG_OP
        USING ERRCODE = 'restrict_violation';
END
$$;

DROP TRIGGER IF EXISTS audit_log_immutable ON audit_log;
CREATE TRIGGER audit_log_immutable
    BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION audit_log_reject_mutation();

DROP TRIGGER IF EXISTS audit_log_immutable_truncate ON audit_log;
CREATE TRIGGER audit_log_immutable_truncate
    BEFORE TRUNCATE ON audit_log
    FOR EACH STATEMENT EXECUTE FUNCTION audit_log_reject_mutation();

REVOKE ALL ON users, invites, audit_log FROM PUBLIC;
REVOKE ALL ON users, invites, audit_log FROM trendscend_app;
GRANT SELECT ON users TO trendscend_app;
GRANT SELECT, INSERT ON invites TO trendscend_app;
GRANT UPDATE (redeemed_at) ON invites TO trendscend_app;
GRANT SELECT, INSERT ON audit_log TO trendscend_app;
GRANT USAGE ON SEQUENCE audit_log_id_seq TO trendscend_app;
