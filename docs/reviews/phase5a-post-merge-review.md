# Phase 5a post-merge review (PR #8, range 149e16a..ecfc9bb)

Date: 2026-10-24. Run per the owner rule in CLAUDE.md: review gates run on the merged diff before the next plan is written.

## Gate status

| Gate | Result |
|---|---|
| `/security-review` (diff-focused, >=0.8 confidence bar) | No high-confidence findings. Attack paths checked: SQL injection in tenant_connection, RLS bypass, startup-check escalation, X-Forwarded-For spoofing, header injection, data exposure, node-id file reads, CI workflow, migration runner. |
| `security-reviewer` subagent (adversarial, cross-task) | No Critical/High. 6 Medium, 8 Low/Info, plus threat-model overclaims (below). Probed live on PG 16.15 / timescaledb 2.30.2. |
| `/codex:review` | NOT RUN: `codex` CLI is not installed on this machine (`npm install -g @openai/codex && codex login` needed). Gate remains open. |
| `risk-auditor` | Not applicable (no `risk/` or `execution/` changes). |

The two Claude-side gates disagreed in depth: the confidence-thresholded review found nothing, the adversarial one found real issues. The thresholded `/security-review` is not sufficient on its own for db/ and api/ changes.

## Must fix before 5b starts (planned as 5b Task 0, "5a hardening")

- **M1 temp objects shadow tables and outlive pooled checkouts.** `TEMP` privilege on the database is granted to PUBLIC; as the app role `CREATE TEMP TABLE audit_log(...)` made unqualified `INSERT INTO audit_log` write to the temp table. asyncpg's pool reset does not `DISCARD TEMP`. Fix: `REVOKE TEMPORARY ON DATABASE ... FROM PUBLIC` in a migration; pool reset that runs `DISCARD ALL`; schema-qualify table names; every future SECURITY DEFINER function gets `SET search_path = pg_catalog, public, pg_temp`.
- **M2 TimescaleDB functions executable by PUBLIC.** `add_job` succeeded for a LOGIN role (persistence/DoS via SQL injection); `get_telemetry_report()` leaks db metadata. Fix: migration revoking EXECUTE on job/admin/telemetry functions from PUBLIC; `timescaledb.telemetry_level=off` in deploy config.
- **M3 `assert_safe_db_role` still passes** members of `pg_read_server_files`, `pg_write_server_files`, `pg_execute_server_program`; owners of the audit trigger function; roles holding TRUNCATE/TRIGGER/REFERENCES on RLS tables; optionally CREATEROLE. (Supersedes the parked item in the Phase 5a SDD ledger.)
- **M5 CI hardening.** No top-level `permissions:` (only the security job has one); `uv sync` should be `--locked`; actions pinned by tag not SHA (`aquasecurity/trivy-action@master` pre-existing, riskiest); service images not digest-pinned. Fork PRs checked: no abuse found (`pull_request`, no secrets).
- **Threat-model overclaims** (fix the doc or move the owner): API session auth (5b), "Pydantic strict validation (5a)" (no request models exist), request/audit logging with user id (nothing writes audit_log yet), "auto-generated isolation test per route" (harness only checks a declared test exists), owner-metrics-only role checks, insider/owner threat #6 (DB owner is a superuser), SameSite/CSRF (5b/5e), RLS on key table (5c), worker/ingest roles (5d). Also `002_tenancy.sql:99` claims audit_log is append-only "including the owner" — false (owner can disable/drop the trigger; superuser can use `session_replication_role=replica`).

## Carry into the 5b plan

- **M4 (design decision — owner input needed):** `app.current_user` is a plain GUC; any SQL (injection or a bad repository) can `set_config` it and read another tenant. RLS only protects against forgotten WHERE clauses. Options: record as an explicit open risk; or bind the tenant to an unforgeable value via a SECURITY DEFINER `enter_session(token)` that checks a hashed session row. At minimum a lint/test that only `db/tenant.py` contains `set_config`.
- **M6** IPv6 rate-limit keys by /64 before any login endpoint ships; Redis `noeviction` (or separate Redis) for authoritative 5b state (sessions, lockout).
- **M7** uvicorn must run with `--no-proxy-headers --no-access-log --no-server-header` (its proxy-headers middleware rewrites `scope["client"]` and bypasses `TRUSTED_PROXIES`; its access log records query strings). Add to launch config plus a test/doc.
- **L1** invite redemption via SECURITY DEFINER, revoke `UPDATE(redeemed_at)`; SECURITY DEFINER functions owned by a dedicated non-superuser role.
- **L4** isolation helper only scans `public` tables with a column literally named `user_id`; policy lint accepts `true OR current_setting(...)`; TRUNCATE privilege unchecked.
- **L5** staging serves `/docs`; `/healthz/` redirect reflects the Host header (no TrustedHost); client-chosen X-Request-ID.
- **L6** `create_app(pool=...)` silently skips `assert_safe_db_role`.
- **L7** lint that only `db/tenant.py` and `routes/health.py` touch `pool.acquire/fetch*`.
- Also from the Phase 5a ledger: disabled-user rejection on every request, migration checksum, 422 input echo, route-template logging, timescaledb digest pin, Python 3.12 pin.

## Carry into 5d

- **M8** audit trail written by the same role that serves requests: it can forge its own entries and burn `audit_log_id_seq`. Use SECURITY DEFINER `audit_append(action enum, detail)` with allowlist and size cap, revoke direct INSERT, `ENABLE ALWAYS TRIGGER`, non-superuser table owner, hash chain or external WORM sink.
- NOTIFY/LISTEN payloads must not be trusted; `lo_create`, advisory-lock key `0x7472656E64730001` can block migrations (L3); all API sessions share one login role so `pg_stat_activity` shows each other's SQL text.

## Info

Global row counts leak via `pg_class.reltuples` / `approximate_row_count` (L2, accepted; add to threat model). Supply chain: `uv.lock` is hash-pinned (2916 sha256 entries) but CI doesn't enforce `--locked` (see M5).
