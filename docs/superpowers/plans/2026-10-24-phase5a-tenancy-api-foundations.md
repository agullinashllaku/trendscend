# Phase 5a: Threat Model, Tenancy (RLS) & API Skeleton

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Lay the multi-user foundation for Phase 5: the STRIDE threat model, Postgres row-level-security tenancy with a fail-closed per-request user context, and a hardened FastAPI skeleton with an automatic cross-user isolation harness that every later endpoint/table must satisfy.

**Architecture:** A tiny SQL migration runner applies numbered `.sql` files. Migration `002` creates a non-owner, non-`BYPASSRLS` role `trendscend_app`, the `users`, `invites` and `audit_log` tables, and `FORCE ROW LEVEL SECURITY` policies keyed on `app.current_user`. `db/tenant.py` opens a transaction, sets that setting with `set_config(..., true)` and yields the connection; unset means zero rows. The FastAPI app factory adds security headers, request IDs, a Redis-backed per-IP rate limiter and a non-leaking error handler. Two introspection tests (one over the DB catalog, one over `app.routes`) fail CI whenever a new user-owned table lacks RLS or a new route lacks an isolation declaration.

**Tech Stack:** Python 3.12, FastAPI, uvicorn, asyncpg, Pydantic v2, redis-py (asyncio), Postgres 16, pytest + pytest-asyncio, httpx `ASGITransport`.

**Spec:** `PLAN.md` §5.1, §7, §10, §11 and `AGENTS.md` non-negotiables. This is sub-plan 1 of 5 for Phase 5 (5b auth/passkeys, 5c keys + exchange adapter + execution, 5d per-user workers/allocation/co-pilot/audit usage, 5e Next.js + Telegram). Owner decisions: passkeys first (5b), master key from file/env behind a `KeyProvider` interface (5c).

## Global Constraints

- Python 3.12 strict typing; ruff selectors E,F,I,N,W,UP,B,S; run `cd backend && uv run ruff check src tests && uv run ruff format src tests && uv run mypy src` before each commit.
- Every user-owned table has a `user_id UUID NOT NULL` column, `ENABLE` **and** `FORCE ROW LEVEL SECURITY`, and policies using `NULLIF(current_setting('app.current_user', true), '')::uuid`. Unset or empty setting returns no rows (fail closed).
- The application connects as `trendscend_app`: not a superuser, not the table owner, no `BYPASSRLS`. Migrations run as the owner.
- The owner role (`users.role = 'owner'`) gets no cross-user read access in SQL either. No admin "view as user" (PLAN.md §10).
- Never log request bodies, cookies, `Authorization` headers or anything resembling a key/secret. Error responses never include exception text or stack traces.
- Security headers on every response: `Strict-Transport-Security: max-age=63072000; includeSubDomains`, `Content-Security-Policy: default-src 'none'; frame-ancestors 'none'` (API serves JSON only), `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `Cache-Control: no-store`.
- Rate limit default: 60 requests/minute per client IP, `429` with `Retry-After`. Fail open on Redis outage only for `/healthz`; every other route fails closed with `503`.
- Coverage: ≥95% branch on `backend/src/trendscend/db/tenant.py` and `backend/src/trendscend/auth/` (created in 5b); ≥80% elsewhere. Do not lower overall coverage.
- Integration tests use `TEST_DATABASE_URL` (owner/superuser connection) and `TEST_REDIS_URL`; they carry `@pytest.mark.integration` and skip when the variable is unset, matching `tests/db/test_candle_repo.py`.
- Work in worktree `.worktrees/phase-5a` on branch `phase-5a`. Changes under `db/` RLS and `api/` security middleware need `/security-review` and the `security-reviewer` subagent before merge.

## Review Focus

- Request with no `app.current_user` set (forgotten context) → must return zero rows, never all rows. Pinned in Task 3.
- `app.current_user` leaking across pooled connections after a request ends or errors → next request must start empty. Pinned in Task 3 (transaction-local setting + rollback-on-error test).
- Malformed user id (non-UUID, empty string, SQL metacharacters) passed to the tenant context → rejected before reaching SQL. Pinned in Task 3.
- Table owner or superuser path silently bypassing RLS in tests, giving a false green. Pinned in Task 2 (`FORCE` check + tests run under `SET LOCAL ROLE trendscend_app`).
- Exception text, SQL or stack trace echoed in a 500 response. Pinned in Task 5.
- Rate-limit bypass via spoofed `X-Forwarded-For`. Pinned in Task 6 (header ignored unless client IP is in `TRUSTED_PROXIES`).

---

## File Structure

**New files:**
- `docs/threat-model.md` — STRIDE per component (Task 1)
- `backend/src/trendscend/db/migrate.py` — `apply_migrations()` (Task 2)
- `backend/src/trendscend/db/migrations/002_tenancy.sql` — role, `users`, `invites`, `audit_log`, RLS (Task 2)
- `backend/src/trendscend/db/tenant.py` — `tenant_connection`, `InvalidTenantError` (Task 3)
- `backend/src/trendscend/api/__init__.py`
- `backend/src/trendscend/api/app.py` — `create_app()` (Task 5)
- `backend/src/trendscend/api/settings.py` — `Settings` (Task 5)
- `backend/src/trendscend/api/middleware.py` — `SecurityHeadersMiddleware`, `RequestIdMiddleware` (Task 5)
- `backend/src/trendscend/api/ratelimit.py` — `RateLimiter`, `RateLimitMiddleware` (Task 6)
- `backend/src/trendscend/api/routes/__init__.py`, `backend/src/trendscend/api/routes/health.py` (Task 5)
- `backend/src/trendscend/api/isolation.py` — `public_route`, `user_scoped_route` markers and registry (Task 7)
- `backend/tests/db/test_migrate.py`, `backend/tests/db/test_rls_tenancy.py`, `backend/tests/db/test_tenant.py`
- `backend/tests/api/__init__.py`, `test_app.py`, `test_ratelimit.py`, `test_isolation_harness.py`
- `backend/tests/docs/test_threat_model.py`
- `backend/tests/conftest.py` — shared integration fixtures `owner_pool`, `app_conn_factory`, `two_users` (Task 2)

**Modified files:**
- `backend/pyproject.toml` — add `fastapi`, `uvicorn[standard]`, `redis` (Task 5/6)
- `.github/workflows/ci.yml` — Postgres+Redis services for the test job (Task 8)
- `infra/docker-compose.yml` — none expected (existing Postgres image is reused)

---

### Task 1: Threat model document

**Files:**
- Create: `docs/threat-model.md`
- Test: `backend/tests/docs/test_threat_model.py` (add `backend/tests/docs/__init__.py`)

**Interfaces:**
- Consumes: PLAN.md §3 components, §10 top threats.
- Produces: `docs/threat-model.md` with one `## <Component>` section per component and STRIDE sub-bullets; later sub-plans cite it by component name.

- [ ] **Step 1: Write the failing test** `test_threat_model_covers_components_and_stride`: read `docs/threat-model.md` (path resolved from `Path(__file__).parents[3]`), assert every component in `["Web app", "API", "Auth", "Key store", "Bot worker", "Execution", "Coordinator LLM", "Shared data plane", "Postgres", "Redis", "Telegram"]` appears as a `## ` heading, and that each section contains all six words `Spoofing, Tampering, Repudiation, Information disclosure, Denial of service, Elevation of privilege`. Add `test_threat_model_names_top_threats` asserting the eight PLAN.md §10 top threats each appear (stolen exchange API keys, account takeover, cross-tenant data access, compromised dependency, prompt injection, insider/owner access, runaway orders, database theft).
- [ ] **Step 2: Run** `cd backend && uv run pytest tests/docs -v --no-cov`. Expected: FAIL (file missing).
- [ ] **Step 3: Write `docs/threat-model.md`.** Per component: assets, trust boundary, STRIDE rows each with threat, mitigation, and the sub-plan (5a–5e) or later phase that implements it, plus an "Open risks" list. Mitigations must match PLAN.md §10 (e.g. withdrawal-disabled keys, step-up auth, RLS + app checks, quoted-data prompts, exchange-side stops).
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `docs: add STRIDE threat model for Phase 5`.

---

### Task 2: Migration runner, tenancy schema and RLS

**Files:**
- Create: `backend/src/trendscend/db/migrate.py`, `backend/src/trendscend/db/migrations/002_tenancy.sql`, `backend/tests/conftest.py`
- Test: `backend/tests/db/test_migrate.py`, `backend/tests/db/test_rls_tenancy.py`

**Interfaces:**
- Consumes: `001_candles.sql` (unchanged), asyncpg.
- Produces:
  - `async def apply_migrations(conn: asyncpg.Connection, directory: Path | None = None) -> list[str]` — applies unapplied `NNN_*.sql` files in order inside one transaction each, records them in `schema_migrations(version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ)`, returns the versions applied this call (idempotent: second call returns `[]`).
  - Tables (all `user_id`-keyed, RLS on): `users(id UUID PK, user_id UUID GENERATED ALWAYS AS (id) STORED, email TEXT UNIQUE NOT NULL CHECK (email = lower(email)), role TEXT CHECK IN ('owner','user'), status TEXT CHECK IN ('active','disabled'), created_at TIMESTAMPTZ)`; `invites(id UUID PK, user_id UUID NOT NULL REFERENCES users(id), token_hash BYTEA UNIQUE NOT NULL, expires_at, redeemed_at NULL, created_at)` (`user_id` = the inviting owner); `audit_log(id BIGSERIAL PK, user_id UUID NOT NULL, action TEXT NOT NULL, detail JSONB NOT NULL DEFAULT '{}', created_at TIMESTAMPTZ DEFAULT now())`.
  - Role `trendscend_app` (`NOLOGIN NOBYPASSRLS`; deployments grant it to their login role). Grants: `users` SELECT/UPDATE; `invites` SELECT/INSERT/UPDATE; `audit_log` SELECT/INSERT and `USAGE` on its sequence. No INSERT on `users` for the app role (user creation arrives in 5b through a `SECURITY DEFINER` function).
  - `audit_log` trigger `audit_log_immutable` raising on `UPDATE`/`DELETE`/`TRUNCATE` for all roles.
  - conftest fixtures: `owner_pool` (session-scoped asyncpg pool on `TEST_DATABASE_URL`, runs `apply_migrations` once), `two_users` (inserts users A and B as the owner connection, returns their UUIDs, cleans up), all skipping when `TEST_DATABASE_URL` is unset.

- [ ] **Step 1: Write failing tests.**
  - `test_migrate.py`: `test_apply_is_idempotent` (second call returns `[]`), `test_applies_in_numeric_order` using a tmp dir of three tiny files, `test_failed_migration_rolls_back_and_is_not_recorded`. The first two have a pure-unit variant against a fake connection where possible; the rest are `integration`.
  - `test_rls_tenancy.py` (integration, each query run with `SET LOCAL ROLE trendscend_app` and `set_config('app.current_user', <id>, true)` inside a transaction): `test_user_sees_only_own_row` (A sees 1 row of `users`, id == A); `test_user_cannot_update_other_user` (UPDATE B's row as A affects 0 rows); `test_app_role_cannot_insert_users` (raises `InsufficientPrivilegeError`); `test_invites_isolated_between_users`; `test_audit_log_isolated_between_users`; `test_audit_log_rejects_update_and_delete_even_for_owner_role`; `test_unset_setting_returns_zero_rows` for all three tables; `test_empty_string_setting_returns_zero_rows`; `test_all_user_owned_tables_have_forced_rls`: query `pg_class`/`information_schema.columns` for every public table having a `user_id` column and assert `relrowsecurity AND relforcerowsecurity` and at least one policy in `pg_policies` whose `qual` mentions `app.current_user`; `test_app_role_is_not_superuser_or_bypassrls`.
- [ ] **Step 2: Run** `cd backend && TEST_DATABASE_URL=... uv run pytest tests/db -v --no-cov -m integration`. Expected: FAIL (no migrate module / tables).
- [ ] **Step 3: Implement** `migrate.py` and `002_tenancy.sql` per the Interfaces block. Policies: `USING (user_id = NULLIF(current_setting('app.current_user', true), '')::uuid)` plus the same `WITH CHECK` on writable tables. `FORCE ROW LEVEL SECURITY` on all three. Add a header comment in the SQL mirroring `001_candles.sql` explaining which tables are shared vs user-owned.
- [ ] **Step 4: Run** the same command. Expected: PASS. Also run `uv run pytest -q -m "not integration"` to confirm unit tests and coverage still pass.
- [ ] **Step 5: Commit** `feat(db): migration runner, users/invites/audit_log with forced RLS`.

---

### Task 3: Fail-closed tenant connection

**Files:**
- Create: `backend/src/trendscend/db/tenant.py`
- Test: `backend/tests/db/test_tenant.py`

**Interfaces:**
- Consumes: `asyncpg.Pool`, fixtures from Task 2.
- Produces:
  - `class InvalidTenantError(ValueError)`
  - `@asynccontextmanager async def tenant_connection(pool: asyncpg.Pool, user_id: UUID | str, *, role: str | None = None) -> AsyncIterator[asyncpg.Connection]` — acquires a connection, starts a transaction, runs `SELECT set_config('app.current_user', $1, true)` with the canonical UUID string, optionally `SET LOCAL ROLE` (identifier validated against `^[a-z_][a-z0-9_]*$`), yields, commits on success and rolls back on exception.
  - Pure helper `def normalise_user_id(value: UUID | str) -> str` raising `InvalidTenantError`.

- [ ] **Step 1: Write failing tests.**
  - Unit: `test_normalise_accepts_uuid_and_string`; `test_normalise_rejects_non_uuid` parametrised over `""`, `"abc"`, `"1; DROP TABLE users"`, `"' OR '1'='1"`, `None` (type-ignored), a UUID with trailing newline.
  - Integration: `test_context_applies_inside_transaction` (inside the block `current_setting('app.current_user')` equals the id); `test_setting_not_visible_after_exit` (acquire a fresh connection from a pool of size 1 afterward; `current_setting('app.current_user', true)` is empty/NULL); `test_setting_cleared_after_exception` (raise inside block, then same check, and the transaction is rolled back: an INSERT into `audit_log` made inside is absent); `test_forgotten_context_returns_no_rows` (plain pool connection under `SET LOCAL ROLE trendscend_app` without `tenant_connection` sees zero `users` rows); `test_role_param_rejects_injection` (`role="x; DROP ROLE y"` raises `InvalidTenantError`).
- [ ] **Step 2: Run** `uv run pytest tests/db/test_tenant.py -v --no-cov`. Expected: FAIL (module missing).
- [ ] **Step 3: Implement** as specified. Use parameter binding for the user id; only the role name is interpolated, after regex validation.
- [ ] **Step 4: Run** the same command with `TEST_DATABASE_URL` set. Expected: PASS; `--cov=trendscend.db.tenant --cov-branch` reports ≥95%.
- [ ] **Step 5: Commit** `feat(db): fail-closed tenant_connection with validated user id`.

---

### Task 4: Cross-user test helper for later sub-plans

**Files:**
- Create: `backend/tests/isolation_helpers.py`
- Test: `backend/tests/db/test_isolation_helpers.py`

**Interfaces:**
- Consumes: `tenant_connection`, `two_users` fixture.
- Produces: `async def assert_table_isolated(owner_pool, table: str, make_row: Callable[[UUID], Awaitable[None]], user_a: UUID, user_b: UUID) -> None` — calls `make_row` for A and B as the owner connection, then as A (via `tenant_connection(..., role="trendscend_app")`) asserts `SELECT count(*) FROM {table}` equals A's row count, that selecting B's rows returns none, and that `UPDATE`/`DELETE` of B's rows affect 0 rows. `table` is validated against the catalog list of user-owned tables (no free-form interpolation).

- [ ] **Step 1: Write failing tests** `test_helper_passes_for_users_table`, and `test_helper_fails_for_table_without_rls` (create a temp table with `user_id` and no RLS in the test, expect `AssertionError`) , and `test_helper_rejects_unknown_table_name`.
- [ ] **Step 2: Run** `uv run pytest tests/db/test_isolation_helpers.py -v --no-cov`. Expected: FAIL.
- [ ] **Step 3: Implement** the helper.
- [ ] **Step 4: Run** again. Expected: PASS.
- [ ] **Step 5: Commit** `test: reusable cross-user table isolation helper`.

---

### Task 5: FastAPI app factory, settings, headers, error handling

**Files:**
- Create: `backend/src/trendscend/api/__init__.py`, `settings.py`, `middleware.py`, `app.py`, `routes/__init__.py`, `routes/health.py`
- Modify: `backend/pyproject.toml` (add `fastapi>=0.115`, `uvicorn[standard]>=0.32`; run `uv lock`)
- Test: `backend/tests/api/test_app.py`

**Interfaces:**
- Produces:
  - `class Settings(BaseModel)` with `database_url: str`, `redis_url: str`, `trusted_proxies: frozenset[str]`, `environment: Literal["dev","staging","prod"]`, `rate_limit_per_minute: int = 60`; `def load_settings(env: Mapping[str, str] = os.environ) -> Settings` (reads `DATABASE_URL`, `REDIS_URL`, `TRUSTED_PROXIES` comma-separated, `ENVIRONMENT`, `RATE_LIMIT_PER_MINUTE`; missing required values raise `RuntimeError` naming the variable, never its value).
  - `def create_app(settings: Settings | None = None) -> FastAPI` — lifespan creates/closes the asyncpg pool and Redis client on `app.state.pool` / `app.state.redis`; docs/openapi disabled when `environment == "prod"`.
  - `GET /healthz` → `{"status": "ok"}` (no DB access) and `GET /readyz` → checks DB and Redis, `503` with `{"status": "unavailable"}` otherwise.
  - Middleware: `RequestIdMiddleware` (accepts a valid UUID `X-Request-ID`, else generates one; echoes it), `SecurityHeadersMiddleware` (headers listed under Global Constraints).
  - Generic exception handler returning `500 {"detail": "internal error", "request_id": ...}`.

- [ ] **Step 1: Write failing tests** with `httpx.AsyncClient(transport=ASGITransport(app=app))` and the lifespan bypassed via a test settings/stub pool: `test_healthz_ok`; `test_security_headers_present_on_every_response` (200, 404 and 500 responses; exact header values from Global Constraints); `test_request_id_echoed_and_regenerated_when_invalid` (value `"x\r\nSet-Cookie: a=b"` is replaced); `test_unhandled_exception_returns_generic_500` (route raising `RuntimeError("secret SQL: SELECT * FROM users")`; assert body lacks `secret`, `SELECT`, `Traceback`); `test_docs_disabled_in_prod`; `test_load_settings_missing_var_names_variable_not_value`; `test_error_logs_do_not_contain_authorization_header` (caplog, send `Authorization: Bearer sekrit` to the failing route, assert `sekrit` not in any record).
- [ ] **Step 2: Run** `uv run pytest tests/api/test_app.py -v --no-cov`. Expected: FAIL.
- [ ] **Step 3: Implement** the modules. Use pure ASGI middleware classes (not `BaseHTTPMiddleware`) so exception handling stays correct. Logging: structured JSON via stdlib `logging` with an explicit allowlist of fields (`request_id`, `method`, `path`, `status`, `duration_ms`).
- [ ] **Step 4: Run** the test file, then `uv run mypy src` and `uv run bandit -r src -c pyproject.toml`. Expected: PASS, no new findings.
- [ ] **Step 5: Commit** `feat(api): app factory with security headers, request ids, safe errors`.

---

### Task 6: Redis rate limiting

**Files:**
- Create: `backend/src/trendscend/api/ratelimit.py`
- Modify: `backend/src/trendscend/api/app.py` (install middleware), `backend/pyproject.toml` (add `redis>=5.2`)
- Test: `backend/tests/api/test_ratelimit.py`

**Interfaces:**
- Consumes: `Settings.rate_limit_per_minute`, `Settings.trusted_proxies`, `app.state.redis`.
- Produces:
  - `def client_ip(scope: Scope, trusted_proxies: frozenset[str]) -> str` — peer address; uses the right-most untrusted entry of `X-Forwarded-For` only when the peer is in `trusted_proxies`.
  - `class RateLimiter` with `async def hit(self, key: str) -> RateLimitResult` (`allowed: bool`, `retry_after: int`), fixed window per minute using `INCR` + `EXPIRE` in one Lua script or pipeline so the TTL is always set.
  - `RateLimitMiddleware` — `429` + `Retry-After` when exceeded; `503` when Redis fails (fail closed) except for `/healthz`.

- [ ] **Step 1: Write failing tests.** Unit with a fake in-memory Redis: `test_allows_up_to_limit_then_429`; `test_retry_after_header_positive_int`; `test_window_resets_after_expiry`; `test_xff_ignored_when_peer_not_trusted` (spoofed header cannot mint new buckets); `test_xff_used_when_peer_trusted`; `test_redis_failure_returns_503_except_healthz`; `test_expire_always_set` (fake records that every key got a TTL). Integration (`TEST_REDIS_URL`): one real-Redis smoke test of `hit`.
- [ ] **Step 2: Run** `uv run pytest tests/api/test_ratelimit.py -v --no-cov`. Expected: FAIL.
- [ ] **Step 3: Implement.** Key format `rl:{ip}:{minute_epoch}`. Never include user identifiers or request content in keys.
- [ ] **Step 4: Run** the file plus `tests/api/test_app.py`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(api): per-IP Redis rate limiter, fail-closed`.

---

### Task 7: Automatic route isolation harness

**Files:**
- Create: `backend/src/trendscend/api/isolation.py`
- Test: `backend/tests/api/test_isolation_harness.py`

**Interfaces:**
- Produces:
  - `def public_route(reason: str) -> Callable[[F], F]` — marks a handler as intentionally unauthenticated (requires non-empty reason).
  - `def user_scoped_route(isolation_test: str) -> Callable[[F], F]` — marks a handler as user-scoped and names the pytest node id (e.g. `tests/api/test_bots_isolation.py::test_user_cannot_read_other_bot`) that proves cross-user isolation.
  - `def collect_route_declarations(app: FastAPI) -> list[RouteDeclaration]` where `RouteDeclaration` has `path`, `methods`, `kind: Literal["public","user_scoped","undeclared"]`, `reason_or_test`.
- Apply `@public_route("liveness probe")` to `/healthz` and `@public_route("readiness probe, no user data")` to `/readyz`.

- [ ] **Step 1: Write failing tests.** `test_no_undeclared_routes_in_real_app` (build `create_app` with stub settings; assert every route that is not FastAPI's built-in docs routes has kind `public` or `user_scoped`); `test_undeclared_route_detected` (add a bare route to a throwaway app; collector reports `undeclared`); `test_public_route_requires_reason` (empty string raises `ValueError`); `test_user_scoped_declarations_point_to_existing_tests` (for every `user_scoped` declaration, the referenced file exists and defines the named test function; vacuously passes today but is the CI tripwire for 5b–5d).
- [ ] **Step 2: Run** `uv run pytest tests/api/test_isolation_harness.py -v --no-cov`. Expected: FAIL.
- [ ] **Step 3: Implement** by tagging handlers with a function attribute and reading `route.endpoint` in the collector.
- [ ] **Step 4: Run** the file and the full unit suite `uv run pytest -q -m "not integration"`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(api): isolation declaration harness that fails CI on undeclared routes`.

---

### Task 8: CI database services and phase wrap-up

**Files:**
- Modify: `.github/workflows/ci.yml` (test job), `CLAUDE.md` (Next pointer to 5b only after merge, per AGENTS.md workflow), `docs/progress.md`
- Create: `infra/README-tenancy.md` (short note: how to create the login role and `GRANT trendscend_app TO <login>`)

**Interfaces:**
- Consumes: everything above.
- Produces: CI runs integration tests against `timescale/timescaledb:latest-pg16` and `redis:7-alpine` service containers with `TEST_DATABASE_URL` / `TEST_REDIS_URL` set; the existing `--cov-fail-under=80` still applies.

- [ ] **Step 1: Write the check.** Add `test_ci_workflow_sets_test_database_url` in `backend/tests/docs/test_ci_config.py` parsing the YAML and asserting the test job has a `postgres`-image service, a `redis` service and both env vars.
- [ ] **Step 2: Run** `uv run pytest tests/docs/test_ci_config.py -v --no-cov`. Expected: FAIL.
- [ ] **Step 3: Update `ci.yml`** (service containers with health checks; env vars on the pytest step). Update `docs/progress.md` with a Phase 5a section (status IN PROGRESS until merged).
- [ ] **Step 4: Run** the full suite with both services available locally: `TEST_DATABASE_URL=... TEST_REDIS_URL=... uv run pytest`. Expected: PASS, integration tests no longer skipped, coverage ≥80%.
- [ ] **Step 5: Commit** `ci: run integration tests against Postgres and Redis services`.
- [ ] **Step 6 (controller, after final review):** run `/security-review` and the `security-reviewer` subagent on the branch; run `/codex:review` on `db/` and `api/` RLS and middleware code. Record rejected findings with a reason in the PR description.

---

## Self-Review Notes

- **Spec coverage:** threat model (§10) → T1; RLS + `app.current_user` + cross-user tests (§5.1, §7, §11 Isolation) → T2–T4, T7; API hardening, headers, rate limits, safe errors (§10) → T5–T6; CI integration (§11) → T8. Idempotency keys on state-changing endpoints (§10) deferred to the first sub-plan that adds a state-changing endpoint (5b); `invites`/`users` creation path and sessions are 5b.
- **Types:** `tenant_connection(pool, user_id, *, role)` is used identically in Tasks 3, 4 and referenced by 5b–5d; `Settings` fields in Task 5 match what Task 6 reads.
- **Out of scope here:** auth, passkeys, key storage, exchange adapters, workers, frontend, Telegram (5b–5e).
