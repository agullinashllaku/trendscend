# Phase 5a hardening post-merge review (PR #9, range 0e91924..4941b74)

Date: 2026-10-25. Run per the owner rule in CLAUDE.md: review gates run on the merged diff before the next plan is written. PR #9 merged with all 5 CI checks green on its first run with the new SHA/digest pins, `--locked` installs and pinned uv (so the "unverified until CI" items from the PR are now verified).

## Gate status

| Gate | Result |
|---|---|
| Diff-focused security review (>=0.8 confidence bar) | No high-confidence findings. Checked SQL injection in `bootstrap.py`/migration 003, adopt-guard escalation, SCRAM verifier correctness vs RFC 5802/7677, startup-check bypass, pool reset, secrets in errors, proxy-header trust, CI/supply chain. |
| `security-reviewer` subagent (adversarial, live probes on PG 16.15 from a throwaway container of the pinned image) | No Critical/High. 3 Medium, 4 Low, 5 threat-model overclaims. |
| `/codex:review` (standard) | "No actionable defects." |
| `/codex:adversarial-review` | 1 high: direct `GRANT EXECUTE` of file functions to the login role is not caught by the startup check (already a known carry-forward; now flagged by three independent reviewers). |
| `risk-auditor` | N/A (no `risk/` or `execution/` changes). |

As after Phase 5a, the confidence-thresholded review found nothing while the adversarial passes found real issues: the thresholded review alone is not sufficient for `db/`/`api/` changes.

## Findings

### Medium
- **M-A: the API login role can change its own settings and password, and they persist.** As the bootstrapped role (no special rights): `ALTER ROLE <self> SET` for `search_path`, `role`, `row_security`, `statement_timeout`, `lock_timeout`, `TimeZone`, `timescaledb.telemetry_level` (overrides 003), `ALTER ROLE <self> IN DATABASE .. SET default_transaction_read_only`, `SET local_preload_libraries='x'` (every new login then fails: persistent brick), and `ALTER ROLE <self> PASSWORD '<attacker's>'`. `DISCARD ALL` re-applies the planted defaults and `assert_safe_db_role` passes with all of them planted. Blocked: `app.*` defaults (so the tenant GUC cannot be preset), `session_replication_role`, `log_statement`, `VALID UNTIL`, `CONNECTION LIMIT`, `SET SESSION AUTHORIZATION`, `SET ROLE` to the owner. Precondition: SQL execution as the API role (the M4 threat model). **Owner: 5b**, designed with the M4 session-token tenant binding. Fix: per-connection `create_pool(init=...)` role assertion that also fails on non-null `pg_roles.rolconfig` / any `pg_db_role_setting` row for the session user; re-`SET` critical settings after `DISCARD ALL` (`search_path`, `TimeZone`, `statement_timeout`, `row_security=on`, `default_transaction_read_only=off`); deploy: `pg_hba` limits, `log_statement=ddl`, alert on `ALTER ROLE` by the API role, re-run bootstrap on every deploy.
- **M-B: the API role can stop the bootstrap from repairing it.** `lo_create(0)` (PUBLIC-executable) gives the role a `pg_shdepend` row, so re-running bootstrap raises "refusing to adopt existing role". A direct session holding `pg_try_advisory_lock(0x7472656E64730002)` makes the bootstrap block forever (no `lock_timeout`). Fix: treat `pg_largeobject_metadata` shdepend rows as repairable (`lo_unlink` / `ALTER LARGE OBJECT .. OWNER TO` as owner); migration revoking EXECUTE on `lo_create`, `lo_creat`, `lo_from_bytea`, `lo_import` (both) from PUBLIC; `SET LOCAL lock_timeout` before the advisory lock with a fixed failure message.
- **M-C: migration 003 can silently do nothing.** If the migration role is not the grantor of the extension functions (managed Postgres), `REVOKE .. FROM PUBLIC` only warns; the migration is recorded as applied and `add_job` stays executable. TEMP is safe either way (the startup check refuses `holds_db_temp`). Fix: end 003 (or a new 004) with an assertion that raises if any targeted function has `proacl IS NULL` or a `grantee = 0` EXECUTE entry; optionally add a `has_function_privilege` predicate to the startup query.
- **Codex high / carry-forward: direct `GRANT EXECUTE` on `pg_read_file`, `pg_ls_dir`, `pg_read_binary_file`, `pg_stat_file`, `lo_import`/`lo_export`.** Not caught by membership-based startup checks. Fix: add `has_function_privilege` predicates (all overloads) to `assert_safe_db_role` and have the bootstrap revoke explicit grants on them from the login role.

### Low
- **L-1** only 5 TimescaleDB functions revoked; the rest remain PUBLIC-executable (TimescaleDB's own ownership checks block the dangerous ones; `restore_fk_check_triggers()` runs; `_timescaledb_config.bgw_job` and `timescaledb_information.jobs` readable). Allowlist approach before user-defined jobs/policies/caggs. Owner 5d (unchanged).
- **L-2** bootstrap never checks Postgres stored the verifier it sent (a wrong format would be hashed as plaintext: API locked out while the CLI prints `ok`). Fix: after create/alter, `SELECT rolpassword = $1` as owner, raise the generic error on mismatch.
- **L-3** nothing in GitHub enforces CI before merge (plan blocks branch protection/rulesets, HTTP 403; `sha_pinning_required` false). Dependabot PR #10 will fail the digest-equality test but merging it is not blocked. Review digest bumps manually; pin a versioned tag (e.g. `2.30.2-pg16`) instead of `latest-pg16`.
- **L-4** bootstrap clears CREATE/TEMP only on the current database; the role keeps PUBLIC's default CONNECT/TEMP on `postgres` and other databases. Deploy: `REVOKE CONNECT ON DATABASE postgres FROM PUBLIC` or `pg_hba` limited to the app database.

### Threat-model overclaims to correct (docs)
- Postgres "session state planted (temp objects, SET ROLE, GUCs)" row: role-level settings planted with `ALTER ROLE .. SET` survive `DISCARD ALL`; only session-level state is covered.
- Bootstrap row "drift is repaired on re-run": the role can prevent the repair (M-B) and a self-changed password is only undone by a re-run.
- Ambient-privileges (003) row: holds only when the migration role is the grantor (M-C).
- Startup-assertion row: omits the CREATE/TEMP checks the code does have; "boot only" must also say role-level settings and password are not checked.
- Open risk L3 `lo_create` (5d): understates it; it now also defeats the bootstrap repair (M-B).

### Environment notes (not repo defects)
- `~/Documents` is iCloud-synced; `backend/.venv` is inside it and contains iCloud-conflict duplicates (` 2` names: e.g. `pytest 2`, `py 2.test`; 19 in `.venv/bin`, 9 in `site-packages`). The editable-install `.pth` also carries the macOS `hidden` flag, so `python -m trendscend.*` fails locally without `PYTHONPATH=src` (pytest is unaffected). Recommended: keep the repo (or at least `.venv`) outside iCloud, or set `UV_PROJECT_ENVIRONMENT` to a path outside `~/Documents`; then `uv sync --locked --extra dev`.
- The local Docker compose stack was not running at review time.

## Triage / sequencing
- **Fix before the 5b plan** (small second hardening PR recommended): M-B, M-C, Codex-high file-function probe, L-2, the threat-model corrections.
- **Carry into the 5b plan as explicit tasks**: M-A (with the M4 session-token tenant binding), plus the existing 5b carry-over list in CLAUDE.md.
- **5d / deploy**: L-1, L-3, L-4, M8 audit trail, pg_stat_activity visibility.
