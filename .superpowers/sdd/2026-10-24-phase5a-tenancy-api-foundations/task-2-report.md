# Task 2 report

Status: DONE_WITH_CONCERNS. Commit: see `git log -1` on phase-5a (feat(db): migration runner, users/invites/audit_log with forced RLS).

Tests (TDD: first run failed on missing trendscend.db.migrate):
- `uv run pytest tests/db/test_migrate.py tests/db/test_rls_tenancy.py --no-cov -m integration` -> 20 passed (run twice, re-runnable)
- `uv run pytest -q -m "not integration"` -> 484 passed, coverage 90.27%
- ruff check/format clean, mypy clean.

Choices / deviations (smallest safe):
1. Column-limited UPDATE grants: users UPDATE(email) only, invites UPDATE(redeemed_at) only. A table-wide UPDATE would let a user set role='owner' or re-enable status. Tests prove escalation raises InsufficientPrivilegeError. 5b may need to widen via SECURITY DEFINER functions.
2. audit_log has no FK on user_id (entries outlive users).
3. Truncate trigger is a separate statement-level trigger (`audit_log_immutable_truncate`); row trigger is `audit_log_immutable`. Both raise restrict_violation. A superuser can still DROP TRIGGER / session_replication_role=replica; not preventable in-DB.
4. Runner: version = file stem (e.g. `001_candles`), numeric ordering, duplicate numbers raise ValueError, session advisory lock around the run. Extension creation (timescaledb) is in conftest, not in a migration.
5. Test DB role is superuser, so every RLS test seeds then does SET LOCAL ROLE trendscend_app in a rolled-back transaction; seeded invites/audit rows never persist (audit_log rows can't be deleted). Only users A/B are committed (two_users) and removed.
6. Session-scoped fixtures use loop_scope="session"; new tests use pytest.mark.asyncio(loop_scope="session").

Concern: pre-existing tests/db/test_candle_repo.py integration tests fail with a live DB (module-scoped fixture on a different event loop; fails identically with --noconftest, so not caused by this task). Not fixed (out of scope).
Per CLAUDE.md, this touches the isolation boundary: needs /security-review + security-reviewer before merge.

## Fix round 1

Changes (002_tenancy.sql edited in place):
1. invites: PostgreSQL allows one command per policy, so `invites_isolation` was replaced by `invites_select` (FOR SELECT), `invites_update` (FOR UPDATE, USING+WITH CHECK) and `invites_owner_insert` (FOR INSERT, requires user_id = current user AND an active owner row in users). The old policy name is dropped for re-runs.
2. users: UPDATE grant removed entirely (SELECT only). Replaced the email-update test with tests that email/role/status UPDATE raise InsufficientPrivilegeError (own and other rows) and that the email-existence probe raises it too.
3. New tests: regular user insert fails, active owner succeeds, disabled owner fails, owner cannot insert for another user. Catalog test now asserts with_check mentions app.current_user for all INSERT/UPDATE/ALL policies.

Before the SQL fix, 5 new/changed tests failed (TDD red). One-off reset of the shared test DB (owner connection) so 002 re-applies from scratch:
`DELETE FROM schema_migrations WHERE version='002_tenancy'; DROP TABLE invites, audit_log, users CASCADE; DROP FUNCTION audit_log_reject_mutation() CASCADE`
(the role trendscend_app is cluster-wide and kept; the migration is idempotent about it.)

Results: ruff clean, mypy clean, integration (run twice) `24 passed, 3 deselected`, unit `484 passed, 32 deselected`, coverage 90.27%.
