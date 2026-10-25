# Phase 5a Hardening: Post-Merge Review Fixes

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix every must-fix security finding from the Phase 5a post-merge review gates (security-reviewer agent + Codex) before Phase 5b starts, and add the missing deployment path for the owner/login role separation.

**Architecture:** One new migration (`003_hardening.sql`) removes ambient privileges (TEMP, TimescaleDB admin functions) from PUBLIC; the API pool gets a `DISCARD ALL` reset hook; `assert_safe_db_role` gains the missing predicates; a role-bootstrap module + an end-to-end test prove the owner-migrates / login-serves contract; CI and compose are pinned and least-privilege; the launch config disables uvicorn's proxy-header and access-log defaults; the rate limiter keys IPv6 by /64; the threat model is corrected to stop overclaiming. No new user-facing features.

**Tech Stack:** Python 3.12, FastAPI, uvicorn, asyncpg 0.32 (`create_pool(reset=...)` exists), Postgres 16 + TimescaleDB 2.30, Redis 7, GitHub Actions.

**Spec:** `docs/reviews/phase5a-post-merge-review.md` (findings M1, M2, M3, M5, M6, M7, L6, threat-model overclaims, Codex P1 "deployment path"), `PLAN.md` §10, `AGENTS.md`. Owner decisions (2026-10-24): hardening ships as its own PR before 5b; tenant binding to a session token (M4) is designed in the 5b plan, not here.

## Global Constraints

- Python 3.12 strict typing; ruff selectors E,F,I,N,W,UP,B,S; before each commit: `cd backend && uv run ruff check src tests && uv run ruff format src tests && uv run mypy src && uv run bandit -r src -c pyproject.toml`.
- Never edit an already-applied migration's SQL behaviour (the runner has no checksum). New behaviour goes in a new numbered migration; comment-only fixes to `002_tenancy.sql` are allowed.
- Postgres roles are CLUSTER-WIDE: every test that creates a role must do so inside a transaction it rolls back, or drop it in a `finally`; unique `zz_`-prefixed names; after any DB test run, `SELECT rolname FROM pg_roles WHERE rolname LIKE 'zz\_%'` must return nothing.
- Integration tests use `TEST_DATABASE_URL` (superuser login) and `TEST_REDIS_URL`, marker `integration`; RLS/privilege assertions run after `SET LOCAL ROLE` to a non-superuser (`trendscend_app` or a temp member of it). A superuser connection proves nothing.
- Error messages never contain DSNs, passwords or role names of other roles.
- All SQL identifiers/literals built from input use server-side `format('%I' / '%L')`; no string interpolation of caller values.
- Coverage: ≥95% branch on `db/tenant.py`, `db/pool.py`, `api/startup.py`, `db/bootstrap.py`; ≥80% overall; do not lower any existing figure.
- Work in worktree `.worktrees/phase-5a-hardening` on branch `phase-5a-hardening`. This touches `db/` RLS-adjacent code and `api/` security: after the final review, the controller runs `/security-review`, the `security-reviewer` subagent and `/codex:review` (+ adversarial) on the merged diff BEFORE the 5b plan is written (CLAUDE.md owner rule).

## Review Focus

- A request on a pooled connection that previously created `pg_temp.audit_log` must not see or write it → pinned T2.
- A login role in `pg_execute_server_program` / `pg_read_server_files` / `pg_write_server_files`, with `REPLICATION`, `CREATEROLE`, TRUNCATE/TRIGGER/REFERENCES on an RLS table, or owning the audit trigger function must be refused at startup → pinned T3.
- A pinned CI action SHA or image digest that does not exist, or a `uses:` that silently stays on a mutable tag → pinned T4.
- Bootstrap run twice, with a role name or password containing quotes/semicolons/newlines, or against a role that already exists with excess privileges → pinned T5.
- Two IPv6 addresses in the same /64 sharing a bucket while IPv4 and IPv4-mapped IPv6 stay unchanged → pinned T6.
- An injected pool silently skipping the role check in non-test use → pinned T7.

---

## File Structure

**New files:**
- `backend/src/trendscend/db/migrations/003_hardening.sql` — revoke TEMP and TimescaleDB admin/telemetry functions from PUBLIC (T1)
- `backend/src/trendscend/db/pool.py` — `create_pool`, `reset_connection` (T2)
- `backend/src/trendscend/db/bootstrap.py` — `bootstrap_api_role`, `main` (T5)
- `backend/src/trendscend/api/__main__.py` — `uvicorn_options`, `main` (T7)
- `.github/dependabot.yml` — actions, pip(uv), docker (T4)
- `backend/tests/db/test_hardening_migration.py`, `test_pool.py`, `test_bootstrap.py`
- `backend/tests/api/test_deployment_contract.py`, `test_launch_config.py`
- `backend/tests/docs/test_ci_hardening.py`

**Modified files:**
- `backend/src/trendscend/api/startup.py` (T3), `api/app.py` (T2 pool, T7 seam), `api/ratelimit.py` (T6)
- `backend/tests/api/test_startup.py`, `test_ratelimit.py`, `test_app.py`
- `.github/workflows/ci.yml`, `infra/docker-compose.yml` (T4)
- `docs/threat-model.md`, `backend/src/trendscend/db/migrations/002_tenancy.sql` (comment only), `infra/README-tenancy.md` (T8)
- `docs/reviews/phase5a-post-merge-review.md` (fix the stale "5b Task 0" wording), `docs/progress.md`, `CLAUDE.md` (T8)

---

### Task 1: Migration 003 — remove ambient privileges from PUBLIC (M1, M2)

**Files:**
- Create: `backend/src/trendscend/db/migrations/003_hardening.sql`
- Test: `backend/tests/db/test_hardening_migration.py`

**Interfaces:**
- Consumes: `apply_migrations` (migrate.py), `owner_pool` fixture (tests/conftest.py).
- Produces: after migration, a non-superuser role cannot `CREATE TEMP TABLE`, cannot execute TimescaleDB admin functions `add_job`, `alter_job`, `delete_job`, `run_job`, `get_telemetry_report`, and the database has `timescaledb.telemetry_level = off`. Idempotent (re-running changes nothing); safe when the timescaledb extension is absent (skip that block).

- [ ] **Step 1: Write failing tests** (integration; each runs inside a rolled-back transaction, creating `zz_h_member` LOGIN-less role granted `trendscend_app`, then `SET LOCAL ROLE zz_h_member`): `test_temp_table_creation_denied` (`CREATE TEMP TABLE audit_log(x int)` raises `InsufficientPrivilegeError`); `test_timescale_admin_functions_not_executable` parametrised over `add_job`, `alter_job`, `delete_job`, `run_job`, `get_telemetry_report` using `has_function_privilege('zz_h_member', p.oid, 'EXECUTE')` over `pg_proc` rows named so in schema `public` belonging to the extension → all false (and at least one overload per name exists, so the test cannot pass vacuously); `test_telemetry_off` (`current_setting('timescaledb.telemetry_level')` is `off` on a fresh connection to the test DB); `test_migration_is_idempotent` (re-run the file's SQL as the owner twice; no error); `test_owner_still_works` (the owner connection can still create a temp table).
- [ ] **Step 2: Run** `cd backend && TEST_DATABASE_URL=... uv run pytest tests/db/test_hardening_migration.py -m integration --no-cov -v`. Expected: FAIL (no 003 applied / privileges still granted).
- [ ] **Step 3: Implement `003_hardening.sql`.** `DO $$` block using `format('REVOKE TEMPORARY ON DATABASE %I FROM PUBLIC', current_database())`; a second block, guarded by `EXISTS (SELECT 1 FROM pg_extension WHERE extname='timescaledb')`, looping over `pg_proc` rows (extension members via `pg_depend`) whose `proname` is in the five names and running `REVOKE EXECUTE ON FUNCTION %s FROM PUBLIC` with `pg_get_function_identity_arguments`; and `EXECUTE format('ALTER DATABASE %I SET timescaledb.telemetry_level = off', current_database())` (inside the same guard). Header comment explains each revoke and that `trendscend_app` needs none of these. Apply via the normal runner (the test conftest already applies pending migrations).
- [ ] **Step 4: Run** the same command, then the whole `tests/db` + `tests/api` integration set. Expected: PASS, nothing else regresses (any existing test that relied on TEMP under the app role must be fixed to use the owner connection, not re-granted).
- [ ] **Step 5: Commit** `fix(db): revoke TEMP and TimescaleDB admin functions from PUBLIC (003)`.

---

### Task 2: Pool reset hook — `DISCARD ALL` (M1)

**Files:**
- Create: `backend/src/trendscend/db/pool.py`
- Modify: `backend/src/trendscend/api/app.py` (use `create_pool`)
- Test: `backend/tests/db/test_pool.py`, `backend/tests/api/test_app.py` (assert wiring)

**Interfaces:**
- Consumes: asyncpg 0.32 `create_pool(..., reset=Callable[[Connection], Awaitable[None]])` (runs after asyncpg's own `_reset`).
- Produces: `async def reset_connection(conn: asyncpg.Connection) -> None` (executes `DISCARD ALL`); `async def create_pool(dsn: str, *, min_size: int = 1, max_size: int = 10) -> asyncpg.Pool` passing `reset=reset_connection`. `app._db_lifespan` uses it.

- [ ] **Step 1: Write failing tests** (pool `max_size=1` against the superuser DSN so the same connection is re-acquired): `test_temp_table_does_not_survive_release` (create temp table, release, re-acquire, `to_regclass('pg_temp.t')` is NULL); `test_session_role_does_not_survive_release` (`SET ROLE trendscend_app`, release, re-acquire, `current_user == session_user`); `test_session_authorization_and_guc_reset` (`SET SESSION AUTHORIZATION`, `set_config('app.current_user', x, false)` do not survive); `test_advisory_locks_released`; `test_reset_failure_discards_connection` (monkeypatched reset raising → connection is terminated, next acquire works); `test_app_lifespan_uses_create_pool` (monkeypatch `trendscend.api.app.create_pool` and assert it is called with the DSN secret value and the app's pool is its return value).
- [ ] **Step 2: Run** `uv run pytest tests/db/test_pool.py tests/api/test_app.py -m "integration or not integration" --no-cov`. Expected: FAIL (module missing).
- [ ] **Step 3: Implement** `db/pool.py` per Interfaces; replace the `asyncpg.create_pool(...)` call in `app.py` with `create_pool(cfg.database_url.get_secret_value(), min_size=1, max_size=10)`.
- [ ] **Step 4: Run** the same command plus `uv run pytest -q -m "not integration"`. Expected: PASS.
- [ ] **Step 5: Commit** `fix(db): DISCARD ALL on pooled connection release`.

---

### Task 3: Widen `assert_safe_db_role` (M3)

**Files:**
- Modify: `backend/src/trendscend/api/startup.py`
- Test: `backend/tests/api/test_startup.py`

**Interfaces:**
- Consumes: existing `_ROLE_QUERY` / `assert_safe_db_role(conn)` shape (rows evaluated for `session_user` and `current_user`; fixed-string `RuntimeError`).
- Produces: additional per-role boolean columns and problem strings: `rolreplication` → "REPLICATION"; `rolcreaterole` → "CREATEROLE"; `can_use_server_files` (MEMBER of `pg_read_server_files`, `pg_write_server_files` or `pg_execute_server_program`) → "member of a server-file/program built-in role"; `controls_rls_functions` (MEMBER of the owner of any function that is the trigger function of a trigger on an RLS table, via `pg_trigger`→`pg_proc.proowner`) → "owns or can assume ownership of a trigger function on RLS tables"; `has_dangerous_table_privs` (`has_table_privilege(r.oid, c.oid, 'TRUNCATE')` or `'TRIGGER'` or `'REFERENCES'` on any RLS-enabled table) → "holds TRUNCATE/TRIGGER/REFERENCES on RLS tables". The realistic deployment (LOGIN role granted only `trendscend_app`, `NOCREATEROLE NOREPLICATION`) must still pass.

- [ ] **Step 1: Write failing tests** (live, each in a rolled-back transaction with a `zz_s_*` role entered via `SET LOCAL SESSION AUTHORIZATION`, plus unit tests with the fake connection for each new column/message): one rejection test per new predicate — member of `pg_execute_server_program`; member of `pg_read_server_files`; member of `pg_write_server_files`; `REPLICATION` role; `CREATEROLE` role; role granted TRUNCATE on `audit_log`; role granted TRIGGER on `users`; role granted REFERENCES on `invites`; role that owns a trigger function on an RLS table (create `zz_s_own` owning a plpgsql function used by a trigger on a scratch RLS table created inside the transaction, member of it → rejected); and `test_realistic_login_role_still_passes` using `GRANT trendscend_app TO zz_s_login` with `NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOREPLICATION`. Assert all messages stay fixed strings (no role names).
- [ ] **Step 2: Run** `uv run pytest tests/api/test_startup.py -m "integration or not integration" --no-cov`. Expected: FAIL for the new cases (show this red run).
- [ ] **Step 3: Implement** the new predicates in `_ROLE_QUERY` using `pg_has_role(r.oid, <role oid or name>, 'MEMBER')` consistently, extend `flag(...)` calls, update the module comment. Do not change existing messages.
- [ ] **Step 4: Run** the same command plus coverage for `api/startup.py` (`--cov=trendscend.api.startup --cov-branch`, expect 100%).
- [ ] **Step 5: Commit** `fix(api): startup role check covers server-file roles, replication, createrole, trigger/truncate privileges`.

---

### Task 4: CI and compose hardening (M5)

**Files:**
- Modify: `.github/workflows/ci.yml`, `infra/docker-compose.yml`
- Create: `.github/dependabot.yml`
- Test: `backend/tests/docs/test_ci_hardening.py`

**Interfaces:**
- Consumes: existing `test_ci_config.py` conventions (parse YAML with `yaml.safe_load`, repo root from `Path(__file__).parents[3]`).
- Produces: workflow-level `permissions: contents: read` (jobs that need more override, e.g. `security` keeps its own); every `uv sync` uses `--locked`; every third-party `uses:` pinned to a 40-hex commit SHA with a trailing `# <tag>` comment (including `aquasecurity/trivy-action`, no `@master`; `actions/*`, `astral-sh/setup-uv`, `codecov/codecov-action`, `gitleaks/gitleaks-action`); service and compose images pinned `image:tag@sha256:<digest>` for `timescale/timescaledb` and `redis`; Dependabot config for `github-actions`, `uv` (directory `/backend`), `docker` (directory `/infra`), weekly.

- [ ] **Step 1: Write failing tests**: `test_workflow_has_least_privilege_permissions` (top-level `permissions == {"contents": "read"}`); `test_uv_sync_is_locked` (every `run` containing `uv sync` contains `--locked`); `test_actions_pinned_to_sha` (every `uses:` that is not a local path matches `^[\w./-]+@[0-9a-f]{40}(\s|$)`, and none ends with `@master`/`@main`/a bare tag); `test_images_pinned_by_digest` (service images in ci.yml and images in docker-compose.yml match `@sha256:[0-9a-f]{64}`); `test_dependabot_covers_ecosystems`.
- [ ] **Step 2: Run** `uv run pytest tests/docs/test_ci_hardening.py --no-cov`. Expected: FAIL.
- [ ] **Step 3: Implement.** Resolve each action's SHA with `gh api repos/<owner>/<repo>/git/ref/tags/<tag> --jq .object` (follow annotated tags to the commit with `git/tags/<sha>`), resolving `trivy-action` to its latest released tag, not `master`; resolve image digests with `docker buildx imagetools inspect <image>:<tag>` (or `docker manifest inspect`); verify every resolved SHA/digest exists before writing it. Keep the existing tags as trailing comments. Keep `--cov-fail-under=80` and all jobs.
- [ ] **Step 4: Run** the test file and `uv run pytest tests/docs -q --no-cov`; validate with `python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))"`. The real proof is the PR's CI run (controller checks it is green).
- [ ] **Step 5: Commit** `ci: least-privilege permissions, --locked installs, SHA/digest pins, dependabot`.

---

### Task 5: Role bootstrap and deployment contract (Codex P1)

**Files:**
- Create: `backend/src/trendscend/db/bootstrap.py`
- Test: `backend/tests/db/test_bootstrap.py`, `backend/tests/api/test_deployment_contract.py`
- Modify: `infra/README-tenancy.md`

**Interfaces:**
- Consumes: `assert_safe_db_role`, `create_pool`, `apply_migrations`.
- Produces:
  - `async def bootstrap_api_role(conn: asyncpg.Connection, login_role: str, password: SecretStr) -> None` — run as the owner/superuser: `login_role` must fullmatch `^[a-z_][a-z0-9_]{0,62}$` (else `ValueError`); creates the role if missing or re-asserts attributes if present: `LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB NOREPLICATION INHERIT`, sets the password, `GRANT trendscend_app TO login_role`, and REVOKEs any other direct role memberships the role has (pg_auth_members) so a re-run repairs drift; all identifiers/literals built with server-side `format('%I'/'%L')`; never logs or returns the password.
  - `def main(argv: list[str] | None = None) -> int` — reads `OWNER_DATABASE_URL`, `API_DB_ROLE`, `API_DB_PASSWORD` from the environment (missing → exit code 2 naming the variable only), connects as the owner, runs `apply_migrations` then `bootstrap_api_role`, prints only "ok"; entry `python -m trendscend.db.bootstrap`.

- [ ] **Step 1: Write failing tests** (live; role names `zz_b_*`, dropped in `finally`, never left behind): `test_bootstrap_creates_safe_login_role` (afterwards the role has the expected `pg_roles` flags and is a member of `trendscend_app` only); `test_bootstrap_is_idempotent_and_repairs_drift` (grant it `CREATEROLE` and membership in a scratch role manually, re-run, drift gone); `test_rejects_bad_role_names` (`""`, `"Robert'); DROP TABLE users;--"`, `"a b"`, 64-char name, `"pg_x\n"`); `test_password_with_quotes_and_newlines_works` (password `p'a"s\ns;--` round-trips: connect as the new role with it); `test_password_not_in_exceptions_or_repr` (force a failure, assert the password is absent from the exception text and from `caplog`); `test_main_missing_env_names_variable_only`. Deployment contract (`test_deployment_contract.py`): `test_app_boots_with_bootstrapped_login_role` (build the DSN for the new role from `TEST_DATABASE_URL`, run the real `create_app` lifespan with `environment="staging"`, `/readyz` → 200, `assert_safe_db_role` passed, and as that role `SELECT count(*) FROM users` → 0 with no tenant context); `test_app_refuses_owner_dsn` (lifespan with the superuser DSN → `RuntimeError`); `test_app_refuses_login_without_trendscend_app_membership` is NOT required (a role with no privileges simply sees nothing) — instead assert that a bootstrapped role cannot `CREATE TEMP TABLE`, cannot `CREATE TABLE` in `public`, cannot call `add_job` (ties T1 to the contract).
- [ ] **Step 2: Run** `uv run pytest tests/db/test_bootstrap.py tests/api/test_deployment_contract.py -m "integration or not integration" --no-cov`. Expected: FAIL.
- [ ] **Step 3: Implement** `db/bootstrap.py`. Update `infra/README-tenancy.md` to use `python -m trendscend.db.bootstrap` as the supported path (keep the manual SQL as a reference) and state which role runs what.
- [ ] **Step 4: Run** the same command; check `pg_roles` for leftovers (expect none); run `db/bootstrap.py` coverage (≥95% branch).
- [ ] **Step 5: Commit** `feat(db): role bootstrap and end-to-end deployment contract test`.

---

### Task 6: IPv6 rate-limit keys by /64 (M6)

**Files:**
- Modify: `backend/src/trendscend/api/ratelimit.py`
- Test: `backend/tests/api/test_ratelimit.py`

**Interfaces:**
- Consumes: `client_ip(scope, trusted_proxies) -> str` (returns the normalised address; IPv4-mapped IPv6 already normalised to IPv4).
- Produces: `def rate_limit_key(ip: str) -> str` — IPv4 → unchanged; IPv6 → `str(ip_network(f"{ip}/64", strict=False).network_address) + "/64"`; unparseable (the existing raw-peer fallback) → unchanged, truncated to 64 chars. `RateLimiter.hit` is called with that key by the middleware. Redis key shape stays `rl:{key}:{minute}`.

- [ ] **Step 1: Write failing tests**: two addresses in `2001:db8:1:2::1` and `2001:db8:1:2:ffff::9` share one bucket and hit the limit together; `2001:db8:1:3::1` is a different bucket; IPv4 and IPv4-mapped IPv6 (`::ffff:1.2.3.4`) still share a bucket; key never contains `%`; keyspace test (1000 random addresses inside one /64 create exactly one key in the fake Redis).
- [ ] **Step 2: Run** `uv run pytest tests/api/test_ratelimit.py --no-cov`. Expected: FAIL.
- [ ] **Step 3: Implement** `rate_limit_key` and use it in `RateLimitMiddleware` where `client_ip` is currently passed to `hit`.
- [ ] **Step 4: Run** the file and the unit suite. Expected: PASS.
- [ ] **Step 5: Commit** `fix(api): rate-limit IPv6 clients by /64`.

---

### Task 7: Launch config and injected-pool seam (M7, L6)

**Files:**
- Create: `backend/src/trendscend/api/__main__.py`
- Modify: `backend/src/trendscend/api/app.py`
- Test: `backend/tests/api/test_launch_config.py`, `backend/tests/api/test_app.py`

**Interfaces:**
- Produces:
  - `def uvicorn_options(settings: Settings) -> dict[str, object]` returning at least `proxy_headers=False`, `access_log=False`, `server_header=False`, `date_header=True`, `host` default `"127.0.0.1"` (overridable only via `API_HOST`), `port` from `API_PORT` default 8000, `factory=True`; and `main() -> None` calling `uvicorn.run("trendscend.api.app:create_app", **uvicorn_options(load_settings()))`.
  - `create_app(settings=None, *, pool=None, redis=None, trust_injected_pool: bool = False)` — when `pool` is injected and `trust_injected_pool` is False, the lifespan runs `assert_safe_db_role` on a connection from that pool exactly as for a created pool; `trust_injected_pool=True` (tests with fakes) skips it. Existing tests that inject fake pools pass `trust_injected_pool=True`.

- [ ] **Step 1: Write failing tests**: `test_uvicorn_options_disable_proxy_headers_and_access_log` (exact values above); `test_host_defaults_to_loopback`; `test_main_calls_uvicorn_run_with_options` (monkeypatch `uvicorn.run`, assert kwargs, no real server); `test_injected_pool_is_role_checked_by_default` (fake pool whose connection triggers `assert_safe_db_role` failure → lifespan raises); `test_trust_injected_pool_skips_check`; `test_dev_flag_still_required_to_skip_in_prod` unchanged behaviour.
- [ ] **Step 2: Run** `uv run pytest tests/api/test_launch_config.py tests/api/test_app.py --no-cov`. Expected: FAIL.
- [ ] **Step 3: Implement**; update existing tests' `create_app(...)` calls minimally with `trust_injected_pool=True` (do not weaken their assertions).
- [ ] **Step 4: Run** the two files, then `uv run pytest -q -m "not integration"`. Expected: PASS.
- [ ] **Step 5: Commit** `fix(api): safe uvicorn launch options; role-check injected pools by default`.

---

### Task 8: Threat-model corrections, docs and wrap-up

**Files:**
- Modify: `docs/threat-model.md`, `backend/src/trendscend/db/migrations/002_tenancy.sql` (comment only), `infra/README-tenancy.md`, `docs/reviews/phase5a-post-merge-review.md`, `docs/progress.md`
- Test: `backend/tests/docs/test_threat_model.py`

**Interfaces:**
- Produces: `docs/threat-model.md` owner fixes — every item in the review's "Threat-model overclaims" list re-owned or reworded (API session auth → 5b; "Pydantic strict validation" → 5b first request models; request/audit logging with user id → 5b/5d; "auto-generated isolation test per route" → "declaration harness; tests hand-written per route"; owner-metrics-only role checks → 5b; insider/owner threat #6 → states the DB owner role is a superuser and that DBA plaintext access is not mitigated in 5a; SameSite/CSRF → 5b/5e; RLS on key table → 5c; worker/ingest roles → 5d); new open-risk rows: forgeable tenant GUC (owner 5b, decided: bind to session token), audit trail forgeable by app role (5d), global row-count leak via `pg_class.reltuples`/`approximate_row_count` (accepted), uvicorn proxy-headers/access-log (mitigated in T7); the 002 audit-trigger comment no longer claims immutability "including the owner".

- [ ] **Step 1: Write failing tests** in `tests/docs/test_threat_model.py`: `test_forgeable_tenant_guc_is_listed_as_open_risk_owned_by_5b`; `test_no_5a_claim_for_session_auth_or_csrf` (those rows' owner cells do not contain `5a`); `test_audit_trigger_comment_does_not_claim_owner_immutability` (grep `002_tenancy.sql` for the phrase "including the owner" → absent); `test_review_file_has_no_stale_5b_task_0_hardening_claim`.
- [ ] **Step 2: Run** `uv run pytest tests/docs -v --no-cov`. Expected: FAIL.
- [ ] **Step 3: Edit the docs** as listed; fix the review file's carry-over wording ("5a-hardening PR (this plan)"); add a "Phase 5a hardening" section to `docs/progress.md` (IN PROGRESS until merged). Do NOT edit `CLAUDE.md` (updated after merge, per AGENTS.md).
- [ ] **Step 4: Run** `uv run pytest -q` with both env vars (full suite incl. integration), expect green, coverage ≥80%, and the `pg_roles` leftover check empty.
- [ ] **Step 5: Commit** `docs: correct threat-model ownership, record hardening`.
- [ ] **Step 6 (controller, after the final whole-branch review):** push, open PR, wait for green CI; then run `/security-review`, the `security-reviewer` subagent, `/codex:review` and `/codex:adversarial-review` on the merged diff and record results in `docs/reviews/`. The 5b plan is written only after that (CLAUDE.md owner rule).

---

## Self-Review Notes

- **Spec coverage:** M1 → T1+T2; M2 → T1; M3 → T3; M5 → T4; M6 → T6; M7 → T7; L6 → T7; Codex P1 deployment path → T5; threat-model overclaims and 002 comment → T8. Deferred by owner decision to 5b: M4 tenant binding, L1 invite redemption, L4 harness gaps, L5 edge disclosure, L7 pool-access lint, disabled-user rejection, 422 echo, migration checksum, Python pin.
- **Types:** `create_pool` (T2) is what `app._db_lifespan` and the T5 deployment test call; `assert_safe_db_role` signature unchanged (T3) and reused by T5/T7; `rate_limit_key(ip)` (T6) consumes `client_ip`'s existing return type.
- **Risk notes:** T4's pins can only be fully proven by the PR's CI run; T1 revokes may break tests that created temp tables under the app role (fix tests, never re-grant); T5's login-role tests require password auth on the test Postgres (the compose image uses scram for TCP — if the test DB trusts localhost, the password round-trip test still holds because it asserts connectability, not rejection).
