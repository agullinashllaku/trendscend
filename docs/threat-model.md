# Trendscend Threat Model (STRIDE, Phase 5)

Spec: PLAN.md section 3 (architecture) and section 10 (security). Sub-plans: 5a tenancy/RLS/API hardening, 5b passkey auth/sessions/recovery/step-up, 5c envelope-encrypted keys/withdrawal-permission verification/execution/exchange-side stops, 5d per-user workers/allocation/co-pilot/audit log, 5e frontend/Telegram. Later phases (6 pen test, etc.) are named where used.

## Top threats (PLAN.md section 10)

Owner labels: 5a-5e are the Phase 5 sub-plans; "5a-hardening" is the Phase 5a post-merge hardening PR (plan `docs/superpowers/plans/2026-10-24-phase5a-hardening.md`), which delivers only the controls named with that label; "Phase 0 CI" is existing CI in `.github/workflows/ci.yml`; "Deploy/infra (planned, not yet owned)" means a deployment-hardening step before Phase 6 that no Phase 5 sub-plan delivers.

| # | Threat | Primary mitigations | Owner |
|---|---|---|---|
| 1 | Stolen exchange API keys | Withdrawal-disabled keys verified on submission, envelope encryption, decrypt only in the user's worker | 5c |
| 2 | Account takeover | Passkeys or Argon2id plus mandatory TOTP, step-up auth, 48h live-trading lock after recovery | 5b |
| 3 | Cross-tenant data access | RLS plus app-level user_id checks, cross-user isolation tests per route and table | 5a |
| 4 | Compromised dependency | Existing: pip-audit, Bandit, gitleaks (pre-commit and CI), Trivy filesystem scan, `uv.lock` pinned versions with hashes enforced by `uv sync --locked` in CI (a lockfile mismatch fails the build; there is no separate `--require-hashes` pip install), least-privilege workflow token, GitHub Actions pinned by commit SHA, service images pinned by digest, uv version and checksum pinned, Dependabot for actions, uv and docker-compose. Not yet in place: SBOM generation in CI, Trivy container-image scan, checksum verification of the binary that gitleaks-action downloads, npm audit (planned, once the frontend exists in 5e) | Phase 0 CI for the original controls; 5a-hardening for the lockfile, token, pin and Dependabot controls; the rest is planned, not yet owned (npm audit: planned for 5e) |
| 5 | Prompt injection via news/wallet data | Quoted-data wrapping, tool-less classifier, schema-validated and range-checked output, Risk Manager final | Phase 3-4 code, 5d |
| 6 | Insider/owner access to user data | The database owner role is a superuser: a DBA or anyone holding owner credentials can read all positions and balances in plaintext, and this is NOT mitigated in 5a (only exchange keys are envelope-encrypted, 5c). Planned: no "view as user" feature in the app, break-glass access logged and notified to the user (5d). Owner credentials are used only for migrations and role bootstrap and are never given to the API (5a-hardening documents this in `infra/README-tenancy.md`, it is not enforced) | 5d for break-glass logging; DBA plaintext access: open risk, not yet owned |
| 7 | Runaway orders from exchange API failure | Sanity checks, circuit breakers, exchange-side stops, per-cycle reconciliation | 5c |
| 8 | Database theft | Envelope-encrypted keys (a dump holds no usable secrets), RLS, no KYC data stored; encrypted volumes and backups are deploy/infra | 5c for key encryption; volumes and backups planned, not yet owned |

Each component below lists assets, the trust boundary, STRIDE rows (threat, mitigation, implementing sub-plan) and open risks. No secrets appear in this document; all values are placeholders.

## Web app

**Assets:** session cookie, rendered balances/positions, step-up prompts. **Trust boundary:** user browser to API over TLS (terminated at the deploy edge, not yet owned); the browser is untrusted.

| STRIDE | Threat | Mitigation | Owner |
|---|---|---|---|
| Spoofing | Phishing clone of the site steals credentials (account takeover) | Passkeys (WebAuthn) are origin-bound; no SMS 2FA; HSTS: 5a sets it on API responses, the 5e Next.js app must set it on its own responses; TLS termination is at the deploy edge | 5b; HSTS: 5a (API), 5e (web app); TLS termination: deploy/infra (planned, not yet owned) |
| Tampering | XSS or CSRF alters settings or approves a trade | Strict CSP, no inline scripts, SameSite=strict httpOnly cookies, CSRF tokens. None exist yet: 5a has no cookies or state-changing endpoints | 5b (cookies, CSRF), 5e (web app CSP) |
| Repudiation | User denies approving a co-pilot trade | Approvals logged to the audit log with session/device id | 5d |
| Information disclosure | Secrets or balances leaked into frontend bundles or logs | Keys never sent to the frontend; secret scanning (gitleaks); API returns only masked key metadata | 5c, 5e |
| Denial of service | Request floods against the UI/API | Per-IP application rate limiting (5a); per-user/per-account limits are 5b; edge/network-level flood protection is deploy/infra | 5a per-IP; 5b per-user; edge: planned, not yet owned |
| Elevation of privilege | Client-side flags unlock live mode or admin views | All authorisation is server-side; live mode needs step-up and config flag; no admin "view as user" | 5b, 5d |

**Open risks:** third-party npm packages (compromised dependency) until SBOM/audit gates are in CI for the frontend; pen test in Phase 6.

## API

**Assets:** all user data, order intents, idempotency state. **Trust boundary:** internet to FastAPI; every request is untrusted input.

| STRIDE | Threat | Mitigation | Owner |
|---|---|---|---|
| Spoofing | Forged or replayed requests as another user | Session auth on every route; user id derived only from the session, never from the body. No session auth exists in 5a | 5b |
| Tampering | Mass assignment, malformed input, duplicate state changes | Pydantic strict request models (5b, the first body-accepting endpoints; 5a validates only the `Settings` object); idempotency keys on state-changing endpoints (5b, the first state-changing endpoint) | 5b |
| Repudiation | No trace of who changed what | Request/audit logging with user id and request id. Today access logs carry a request id but no user id, and nothing writes `audit_log` yet | 5b (user id in logs), 5d (audit log writes) |
| Information disclosure | Cross-tenant data access via IDOR | RLS plus app-level user_id checks. The route declaration harness fails CI on undeclared routes; isolation tests are written per route (5b+), not generated | 5a (RLS, harness); 5b+ (per-route tests) |
| Denial of service | Resource exhaustion, expensive queries | Per-IP rate limits (5a); per-user/per-account limits and login lockout (5b); pagination caps and request size limits are planned, first needed when 5b/5d add body-accepting endpoints | 5a per-IP; 5b per-user; caps and size limits: planned, not yet owned |
| Elevation of privilege | Normal user reaches admin or step-up actions | Role checks, step-up auth for sensitive actions, owner role sees operational metrics only. None exist in 5a | 5b |
| Spoofing | Client address spoofed via forwarded headers to evade rate limits or poison logs | Committed launch options (`python -m trendscend.api`): `proxy_headers=False` and empty `forwarded_allow_ips` so uvicorn never rewrites the client address, the app's own `TRUSTED_PROXIES` logic decides; loopback bind by default; `server_header=False` | 5a-hardening |
| Information disclosure | Uvicorn access log records full request lines including query strings | Launch options set `access_log=False` and `log_config=None`; `configure_logging()` owns logging (allowlist) | 5a-hardening |
| Denial of service | One IPv6 client rotates addresses to get unlimited rate-limit buckets and grow Redis keys | Rate-limit keys collapse IPv6 clients to their /64 (IPv4 unchanged), key length capped | 5a-hardening |
| Spoofing | App created with an injected DB pool silently skips the role check | `create_app(pool=...)` runs the same role assertion on injected pools unless `trust_injected_pool=True`, which is for tests only | 5a-hardening |

**Open risks:** OWASP API Top 10 checklist review pending; external pen test (Phase 6); the `/docs` page on staging, `/healthz/` redirect reflecting the Host header (no TrustedHost) and client-chosen X-Request-ID are unfixed review findings (L5, owner 5b).

## Auth

**Assets:** passkey credentials, TOTP seeds, recovery codes, sessions. **Trust boundary:** unauthenticated to authenticated.

| STRIDE | Threat | Mitigation | Owner |
|---|---|---|---|
| Spoofing | Credential stuffing, phished passwords, SIM swap (account takeover) | Passkeys preferred; else Argon2id plus HIBP k-anonymity check plus mandatory TOTP; no SMS 2FA; login rate limits and lockout backoff | 5b |
| Tampering | Session fixation, cookie theft | Short-lived httpOnly/Secure/SameSite=strict cookies, rotation on privilege change | 5b |
| Repudiation | Disputed security changes | Security events in the append-only audit log; new-device and new-IP alerts | 5b, 5d |
| Information disclosure | Recovery codes or TOTP seeds leaked | Seeds encrypted at rest; recovery codes stored hashed and shown once | 5b |
| Denial of service | Lockout abused to block victims | Backoff per account and IP rather than permanent lock; recovery path | 5b |
| Elevation of privilege | Recovery flow abused to enable live trading | Recovery disables live trading for 48h and notifies the user; step-up for key changes, allocation raises, live switch, 2FA changes, exports | 5b |

**Open risks:** device-list UX; recovery social engineering depends on user email security.

## Key store

**Assets:** per-user envelope-encrypted exchange API keys, wrapped data keys, master key. **Trust boundary:** encrypted at rest; plaintext only in a user's worker memory.

| STRIDE | Threat | Mitigation | Owner |
|---|---|---|---|
| Spoofing | Attacker registers keys for another user's account | Key add requires session plus step-up; keys bound to user_id | 5b, 5c |
| Tampering | Ciphertext swapped between users | AEAD with user id as associated data; RLS on key table. No key table exists in 5a | 5c |
| Repudiation | Key added or removed with no record | Key lifecycle events audited | 5c, 5d |
| Information disclosure | Stolen exchange API keys via database theft or logs | Per-user data key wrapped by master key held outside DB and app image (KMS/HSM preferred); never logged; decrypted only in that user's worker | 5c |
| Denial of service | Master key unavailable, all bots stop | Documented key rotation and recovery runbook (`docs/incident-response.md`); bots fail safe | 5c, 6 |
| Elevation of privilege | Withdrawal-enabled key accepted | Verify on submission that withdrawal permission is off; reject and never store such keys; code never calls withdraw | 5c |

**Open risks:** KMS choice versus file-held master key at launch; key rotation drill.

## Bot worker

**Assets:** decrypted key in memory, positions, decision loop. **Trust boundary:** one container per user; isolated from other users' workers.

| STRIDE | Threat | Mitigation | Owner |
|---|---|---|---|
| Spoofing | Worker impersonates another user's bot on the data plane | Worker identity and user id fixed at launch; scoped credentials per worker | 5d |
| Tampering | Compromised worker changes risk limits | Risk Manager deterministic and shared code across modes; limits read from DB under RLS, not worker input; the worker DB role is read-only on risk-limit tables | 5d |
| Repudiation | Decision cycle without record | Every cycle logged as a DecisionRecord plus audit log | 5d |
| Information disclosure | Memory/log leak of keys or balances | Non-root, read-only filesystem, log redaction, no keys in prompts | 5c, 5d |
| Denial of service | One worker starves others | Per-container CPU/memory limits; supervisor restarts; global freeze command | 5d |
| Elevation of privilege | Container escape to host or neighbours | Minimal images, non-root, no shared volumes (5d); Trivy container-image scan is planned | 5d for container layout; image scan: planned, not yet owned |

**Open risks:** Docker Compose supervisor offers weaker isolation than an orchestrator; accepted under 50 users.

## Execution

**Assets:** order flow, exchange sessions, stops. **Trust boundary:** worker to exchange API.

| STRIDE | Threat | Mitigation | Owner |
|---|---|---|---|
| Spoofing | Orders sent under the wrong user's key | Key resolved by user id inside the worker only | 5c |
| Tampering | Runaway orders from exchange API failure or duplicate sends | Idempotent client order ids, price/size/symbol sanity checks, circuit breakers (orders/minute, notional/day), reconciliation every cycle freezes on mismatch | 5c |
| Repudiation | Disputed fills | Immutable order and fill records, audit log | 5c, 5d |
| Information disclosure | Order details leaked via logs | Redacted structured logs | 5c |
| Denial of service | Exchange rate limits or outages leave positions unmanaged | Exchange-side stops attached to every position; backoff; safe freeze state | 5c |
| Elevation of privilege | LLM or co-pilot path bypasses risk checks | Risk Manager is final and deterministic; no LLM output creates an order; live needs config flag plus owner confirmation | 5c, 5d |

**Open risks:** partial-fill and exchange-specific edge cases; chaos tests on staging (Phase 6).

## Coordinator LLM

**Assets:** prompts, model outputs, API budget. **Trust boundary:** public data in, schema-validated bounded output out.

| STRIDE | Threat | Mitigation | Owner |
|---|---|---|---|
| Spoofing | Fake news or wallet labels masquerade as instructions | External text treated as untrusted data | 5d |
| Tampering | Prompt injection steers sizing | Text wrapped as quoted data, classifier has no tools, outputs schema-validated and range-checked, LLM may only reduce size within style limits | 5d (built in Phase 4) |
| Repudiation | Unexplained recommendation | Prompt/response digests stored in DecisionRecord | 5d |
| Information disclosure | Keys, emails or balances in prompts | Only public market/news data and anonymised context; no user identity | 5d |
| Denial of service | Cost blowout or API outage | Timeouts, fallback to code-side weighting, per-user budget caps | 5d |
| Elevation of privilege | LLM output bypasses risk checks | Risk Manager runs after and overrides; model ids read from config | 5c, 5d |

**Open risks:** novel injection techniques; evaluation set for adversarial headlines needs ongoing growth.

## Shared data plane

**Assets:** market data, news, smart-money signals shared across users. **Trust boundary:** external feeds to internal read-only consumers.

| STRIDE | Threat | Mitigation | Owner |
|---|---|---|---|
| Spoofing | Forged feed or poisoned upstream | Fetch only from configured sources over TLS; source tagging | 5d |
| Tampering | Poisoned or stale data drives trades | Staleness checks halt bots; sanity bounds on prices | 5d |
| Repudiation | Unknown data provenance | Source and fetch time stored per record | 5d |
| Information disclosure | User data leaks into shared tables | Shared tables contain no user data; user tables isolated by RLS | 5a |
| Denial of service | Feed outage or flood | Caching, per-source rate limits, stale alerts | 5d |
| Elevation of privilege | Worker writes to shared data or edits its own risk limits | Workers have read-only access to shared data and to risk-limit tables; single ingest writer; the worker and ingest DB roles do not exist yet | 5d |

**Open risks:** malicious or compromised dependency in ingest libraries.

## Postgres

**Assets:** all persistent state, audit log. **Trust boundary:** private network only, never exposed publicly.

| STRIDE | Threat | Mitigation | Owner |
|---|---|---|---|
| Spoofing | Direct DB login with leaked credential | Secrets from env/secret manager; least-privilege roles (5a); network firewall allowing only private access is deploy/infra | 5a; firewall: planned, not yet owned |
| Tampering | Audit rows edited or cross-user writes | RLS on every user table (5a). `audit_log` has triggers that reject UPDATE/DELETE/TRUNCATE for the app role and non-superuser owners using normal DML; the table owner or a superuser can disable or drop them and `session_replication_role = replica` bypasses them. Nothing writes `audit_log` yet; hardened writes are in the open risks | 5a (RLS); 5d (audit writes, tamper resistance) |
| Repudiation | Admin actions untraceable | Break-glass access logged and notified to the user | 5d |
| Information disclosure | Database theft or backup theft; insider/owner access | Keys envelope-encrypted so a dump holds no usable secrets; no KYC data stored; the owner role is a superuser with plaintext access (see top-threat 6, not mitigated in 5a); encrypted volumes and encrypted off-site versioned backups are deploy/infra | 5c for key encryption; volumes and backups: planned, not yet owned (before Phase 6) |
| Denial of service | Connection exhaustion, slow queries | Pooling, statement timeouts and per-role limits are database configuration at deploy time | Deploy/infra (planned, not yet owned) |
| Elevation of privilege | App role bypasses RLS | App connects as non-owner role without BYPASSRLS; cross-user DB tests with RLS | 5a |
| Elevation of privilege | Login role that is over-privileged or can assume a privileged role is accepted at startup | Startup assertion rejects, for session_user AND current_user: superuser, BYPASSRLS, CREATEROLE, REPLICATION, membership of pg_read_server_files / pg_write_server_files / pg_execute_server_program (server-file/program built-in roles), ownership of or membership in the owner of an RLS table, of a schema holding RLS tables, of the database, or of a trigger function on an RLS table, TRUNCATE/TRIGGER/REFERENCES (including column-level REFERENCES) on RLS tables, a non-origin `session_replication_role`, and membership of any superuser/BYPASSRLS role. Evaluated at boot only | 5a-hardening |
| Elevation of privilege | Ambient PUBLIC privileges let any login role plant temp objects or run TimescaleDB admin functions | Migration 003: REVOKE TEMPORARY ON DATABASE from PUBLIC (temp tables shadowing `audit_log`); REVOKE EXECUTE from PUBLIC on every overload of `add_job`, `alter_job`, `delete_job`, `run_job` and `get_telemetry_report`; `timescaledb.telemetry_level = off` | 5a-hardening |
| Tampering | Session state planted by one request (temp objects, SET ROLE, GUCs) leaks into later requests on a pooled connection | Pool reset runs `DISCARD ALL` on every release (bounded by a timeout, the connection is dropped on failure) and `statement_cache_size=0` so no stale prepared-statement names remain | 5a-hardening |
| Information disclosure | Same cross-request session-state leakage reads another request's temp tables or settings | Same `DISCARD ALL` pool reset and `statement_cache_size=0` | 5a-hardening |
| Spoofing | Misprovisioned or password-leaking API login role | Role bootstrap (`python -m trendscend.db.bootstrap`): owner-run, applies migrations, creates or repairs a NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOREPLICATION login role that is a member of `trendscend_app` only; a client-side SCRAM verifier is sent so the plaintext password never reaches the server or its logs; drift is repaired on re-run; an adopt-guard refuses to take over existing superuser, owning, directly-granted or group roles; an end-to-end test boots the app against such a database and refuses a misprovisioned one | 5a-hardening |

**Open risks:** monthly restore drill; migration-role handling. Forgeable tenant identity (review M4, confirmed by two independent reviewers): `app.current_user` is a plain GUC, so any SQL run as the app role (injection, a bad repository) can `set_config` it and read another tenant; RLS today only protects against forgotten WHERE clauses. DECIDED by the owner on 2026-10-24, owner 5b: bind the tenant to an unforgeable session token via a SECURITY DEFINER function (design in the 5b plan). Audit trail forgeable (M8, owner 5d): it would be written by the same role that serves requests, which can forge entries and burn `audit_log_id_seq`; fix with an `audit_append` SECURITY DEFINER function (action allowlist, size cap), direct INSERT revoked, `ENABLE ALWAYS TRIGGER`, a non-superuser table owner, and a hash chain or external WORM sink. Views over RLS tables and SECURITY DEFINER functions are unlinted (owner 5b): views must be `security_invoker`, definer functions need a pinned `search_path` and a dedicated non-superuser owner. An explicit GRANT EXECUTE of `pg_read_file`, `pg_ls_dir` or `lo_*` to the login role is not detected by the role-membership checks. The role assertion is checked at boot only: privilege drift after boot is undetected. Remaining TimescaleDB internals and information views stay readable by the app role (revisit before user-defined jobs, policies or continuous aggregates). Global row counts leak through `pg_class.reltuples` and `approximate_row_count` (accepted, L2). The gitleaks-action binary download is unverified (CI). `pg_stat_activity` shows other API sessions' SQL text to the shared login role. NOTIFY/LISTEN payloads must not be trusted (owner 5d). The DBA/owner superuser can read all plaintext data (top-threat 6; break-glass logging is 5d).

## Redis

**Assets:** rate-limit counters, queues, ephemeral state. **Trust boundary:** private network.

| STRIDE | Threat | Mitigation | Owner |
|---|---|---|---|
| Spoofing | Unauthorised client connects | Redis auth and private-network-only exposure are deploy/infra configuration | Deploy/infra (planned, not yet owned) |
| Tampering | Queue or counter manipulation | Per-user namespaced keys once per-user Redis state exists (5b/5d; 5a defines only per-IP rate-limit keys); no authority decisions rely solely on Redis | 5a per-IP keys; 5b/5d per-user |
| Repudiation | Lost events on restart | Authoritative records live in Postgres, not Redis | 5d |
| Information disclosure | Sessions or keys cached in plaintext | No key material or secrets in Redis; sessions stored hashed | 5b, 5c |
| Denial of service | Redis restart or memory exhaustion | Memory limits and eviction policy are Redis deploy configuration (the existing compose sets maxmemory 256mb allkeys-lru, a dev default only); bots recover and reconcile from Postgres and the exchange (5d) | Limits: deploy/infra (planned, not yet owned); recovery: 5d |
| Elevation of privilege | Cross-user key collision reads others' state | Per-user keys prefixed with user id and isolation tests, added when per-user Redis state is introduced (5a has only per-IP rate-limit keys) | 5b/5d |

**Open risks:** chaos test of Redis restart (Phase 6).

## Telegram

**Assets:** chat link to a user, notifications, approval buttons. **Trust boundary:** third-party messenger; treated as untrusted and unauthenticated for money actions.

| STRIDE | Threat | Mitigation | Owner |
|---|---|---|---|
| Spoofing | Attacker links their chat to a victim account or replays callbacks | Linking via one-time code from an authenticated session; webhook secret token verified | 5e |
| Tampering | Forged callback approves a trade | Signed, single-use, expiring callback data; sensitive approvals require web step-up | 5e |
| Repudiation | Disputed approval from chat | Approvals recorded in the audit log with chat id | 5d, 5e |
| Information disclosure | Balances or identity leaked in messages | Minimal message content; no keys or balances; users can unlink | 5e |
| Denial of service | Bot spam or API limits | Per-chat throttling, message queueing | 5e |
| Elevation of privilege | Chat compromise escalates to live trading or key changes | Telegram cannot add keys, raise allocation, switch to live or export data; kill switch only reduces risk | 5b, 5e |

**Open risks:** Telegram account takeover (SIM swap) remains outside our control; approvals via chat are limited accordingly.
