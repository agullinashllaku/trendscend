# Phase 5a post-merge review (PR #8, range 149e16a..ecfc9bb)

Date: 2026-10-24. Run per the owner rule in CLAUDE.md: review gates run on the merged diff before the next plan is written.

## Gate status

| Gate | Result |
|---|---|
| `/security-review` (diff-focused, >=0.8 confidence bar) | No high-confidence findings. Attack paths checked: SQL injection in tenant_connection, RLS bypass, startup-check escalation, X-Forwarded-For spoofing, header injection, data exposure, node-id file reads, CI workflow, migration runner. |
| `security-reviewer` subagent (adversarial, cross-task) | No Critical/High. 6 Medium, 8 Low/Info, plus threat-model overclaims (below). Probed live on PG 16.15 / timescaledb 2.30.2. |
| `/codex:review` (standard, `--base 149e16a --scope branch`) | 2 P1s, triaged below: 1 rejected with reason, 1 accepted. |
| `/codex:adversarial-review` (db/ + api/ focus) | 1 high: forgeable tenant GUC. Independently confirms M4 from the security-reviewer gate (two providers agree). |
| `risk-auditor` | Not applicable (no `risk/` or `execution/` changes). |

The two Claude-side gates disagreed in depth: the confidence-thresholded review found nothing, the adversarial one found real issues. The thresholded `/security-review` is not sufficient on its own for db/ and api/ changes.

## Must fix before 5b starts (shipped as its own PR, "5a hardening", plan `docs/superpowers/plans/2026-10-24-phase5a-hardening.md`)

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

## Codex triage (2026-10-24)

- **ACCEPT, escalated to owner: forgeable tenant identity (adversarial, high; = M4).** `app.current_user` is a plain GUC; any SQL run as the app role (injection, bad repository) can `set_config` it and read another tenant. Two independent providers flagged this. Decision needed for 5b: bind the tenant to an unforgeable value (SECURITY DEFINER `enter_session(token)` checking a hashed session row) vs. accept as a documented residual risk with a lint that only `db/tenant.py` calls `set_config` plus independent injection controls.
- **REJECT: "startup check rejects a database-owner login" (standard P1, `startup.py:42-47`).** Intentional. On PG15+ the database owner implicitly owns schema `public` (via `pg_database_owner`) and can `DROP TABLE audit_log` (proven live by the security-reviewer gate). `infra/README-tenancy.md` already requires the login role not to own the database or schema. Codex's own comment is internally inconsistent on this point.
- **ACCEPT, fixed in the 5a hardening PR: no integrated deployment path for the role separation (standard P1, `002_tenancy.sql:28-35`).** Role separation (owner runs migrations; a separate LOGIN role granted `trendscend_app` serves requests) is only documented. Add a bootstrap script (create login role, grant, verify) used by compose/dev, a documented `uvicorn` launch config, and an end-to-end test that boots the app against a DB prepared that way and refuses a misprovisioned one.

## Hardening status

Fixes ship in the 5a hardening PR (branch `phase-5a-hardening`, plan `docs/superpowers/plans/2026-10-24-phase5a-hardening.md`); status is IN PROGRESS until that PR merges. Commit subjects are cited because hashes change on merge.

| Finding | Status | Fixed by / owner |
|---|---|---|
| M1 temp objects shadow tables, outlive pooled checkouts | Fixed | `fix(db): revoke TEMP and TimescaleDB admin functions from PUBLIC (003)`; `fix(db): DISCARD ALL on pooled connection release`; `fix(db): disable statement cache and bound DISCARD ALL in pool reset` (also `revoke CREATE/TEMPORARY on database` in the bootstrap). Open: schema-qualifying names and `SET search_path` for future SECURITY DEFINER functions, owner 5b |
| M2 TimescaleDB functions executable by PUBLIC | Fixed (migration 003, telemetry off for new sessions) | Remaining TimescaleDB internals and views readable: open risk, revisit before user-defined jobs/policies/caggs |
| M3 startup role check gaps | Fixed | `fix(api): startup role check covers server-file roles, replication, createrole, trigger/truncate privileges`; `... column REFERENCES and non-origin session_replication_role`. Open: boot-only check, explicit GRANT EXECUTE of `pg_read_file`/`pg_ls_dir`/`lo_*` undetected |
| M5 CI hardening | Fixed in config; full proof only from the PR's own CI run | `ci: least-privilege permissions, --locked installs, SHA/digest pins, dependabot`; `ci: dependabot docker-compose ecosystem, pin uv version+checksum`. Open: SBOM, Trivy image scan, gitleaks-action binary checksum |
| M6 IPv6 rate-limit keys | Fixed (/64 bucketing) | `fix(api): rate-limit IPv6 clients by /64`. Open: Redis `noeviction` or a separate Redis for authoritative 5b state, owner 5b |
| M7 uvicorn proxy headers / access log | Fixed | `fix(api): safe uvicorn launch options; role-check injected pools by default` (`python -m trendscend.api`) |
| L6 `create_app(pool=...)` skips role check | Fixed | same commit; injected pools are role-checked unless `trust_injected_pool=True` (tests only) |
| Codex deployment-path P1 | Fixed | `feat(db): role bootstrap and end-to-end deployment contract test` plus SCRAM verifier, adoption guard and ACL follow-ups |
| Threat-model overclaims and the 002 comment | Fixed (docs only; the 002 change is comment-only, applied databases unaffected) | `docs: correct threat-model ownership, record hardening` |
| M4 forgeable tenant GUC | Open, DECIDED 2026-10-24 | 5b: bind tenant to an unforgeable session token via a SECURITY DEFINER function |
| M8 forgeable audit trail | Open | 5d |
| L1 invite redemption, L4 harness gaps, L5 edge disclosure, L7 pool-access lint | Open | 5b |
| L2 global row-count leak | Accepted | recorded in the threat model |
| L3 advisory-lock key can block migrations, `lo_create`, NOTIFY/LISTEN trust, `pg_stat_activity` visibility | Open | 5d |
| Views over RLS tables / SECURITY DEFINER functions unlinted | Open | 5b |
| Disabled-user rejection, migration checksum, 422 input echo, route-template logging, Python pin | Open | 5b |
