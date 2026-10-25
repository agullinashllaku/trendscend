"""Guards the Phase 5 STRIDE threat model against silent gaps."""

import re
from pathlib import Path

DOC = Path(__file__).parents[3] / "docs" / "threat-model.md"

COMPONENTS = [
    "Web app",
    "API",
    "Auth",
    "Key store",
    "Bot worker",
    "Execution",
    "Coordinator LLM",
    "Shared data plane",
    "Postgres",
    "Redis",
    "Telegram",
]
STRIDE = [
    "Spoofing",
    "Tampering",
    "Repudiation",
    "Information disclosure",
    "Denial of service",
    "Elevation of privilege",
]
TOP_THREATS = [
    "stolen exchange API keys",
    "account takeover",
    "cross-tenant data access",
    "compromised dependency",
    "prompt injection",
    "insider/owner access",
    "runaway orders",
    "database theft",
]


def _sections() -> dict[str, str]:
    text = DOC.read_text(encoding="utf-8")
    parts = text.split("\n## ")[1:]
    out: dict[str, str] = {}
    for part in parts:
        title, _, body = part.partition("\n")
        out[title.strip()] = body
    return out


def test_threat_model_covers_components_and_stride() -> None:
    sections = _sections()
    for component in COMPONENTS:
        assert component in sections, f"missing '## {component}' heading"
        for word in STRIDE:
            assert word in sections[component], f"{component}: missing {word}"


def test_threat_model_names_top_threats() -> None:
    text = DOC.read_text(encoding="utf-8").lower()
    for threat in TOP_THREATS:
        assert threat.lower() in text, f"missing top threat: {threat}"


def test_top_threats_table_maps_each_threat_to_mitigation_and_owner() -> None:
    text = DOC.read_text(encoding="utf-8")
    rows = [ln for ln in text.splitlines() if ln.startswith("| ") and ln[2].isdigit()]
    assert len(rows) == len(TOP_THREATS)
    for row, threat in zip(rows, TOP_THREATS, strict=True):
        cells = [c.strip() for c in row.strip("|").split("|")]
        assert threat.lower() in cells[1].lower()
        assert cells[2] and cells[3], f"{threat}: missing mitigation or owner"


# --- Phase 5a hardening (Task 8): ownership honesty ---------------------------

ROOT = Path(__file__).parents[3]
REVIEW = ROOT / "docs" / "reviews" / "phase5a-post-merge-review.md"
PROGRESS = ROOT / "docs" / "progress.md"
MIGRATION_002 = ROOT / "backend" / "src" / "trendscend" / "db" / "migrations" / "002_tenancy.sql"
HARDENING_PLAN = "docs/superpowers/plans/2026-10-24-phase5a-hardening.md"

# "5a" on its own: not part of "5a-hardening" and not inside another token.
_BARE_5A = re.compile(r"(?<![\w-])5a(?![\w-])")


def _table_rows() -> list[list[str]]:
    """Every markdown table row in the doc as stripped cells (separators dropped)."""
    rows = []
    for line in DOC.read_text(encoding="utf-8").splitlines():
        if line.startswith("|") and not set(line) <= set("|- "):
            rows.append([c.strip() for c in line.strip().strip("|").split("|")])
    return rows


def _open_risk_lines() -> list[str]:
    return [
        ln
        for ln in DOC.read_text(encoding="utf-8").splitlines()
        if ln.startswith("**Open risks:**")
    ]


def test_forgeable_tenant_guc_is_listed_as_open_risk_owned_by_5b() -> None:
    hits = [ln for ln in _open_risk_lines() if "forgeable tenant" in ln.lower()]
    assert hits, "forgeable tenant GUC (M4) missing from Open risks"
    line = hits[0]
    assert "5b" in line
    assert "2026-10-24" in line
    assert "SECURITY DEFINER" in line and "session token" in line


def test_no_5a_claim_for_session_auth_csrf_audit_writes_workers_or_key_rls() -> None:
    keywords = re.compile(
        r"session auth|csrf|samesite|request/audit logging|rls on key table|"
        r"workers have read-only|worker/ingest|owner role sees|role checks",
        re.IGNORECASE,
    )
    checked = 0
    for cells in _table_rows():
        if len(cells) < 3:
            continue
        text = " ".join(cells[:-1])
        if keywords.search(text):
            checked += 1
            assert not _BARE_5A.search(cells[-1]), f"5a owns an overclaim: {cells}"
    assert checked >= 6


def test_overclaims_are_reworded() -> None:
    text = DOC.read_text(encoding="utf-8")
    assert "auto-generated" not in text
    assert "Pydantic strict validation (5a)" not in text
    assert "declaration harness" in text
    # Insider/owner row states the truth about the DB owner role.
    row = next(c for c in _table_rows() if c[0] == "6")
    assert "superuser" in row[2].lower()
    assert "not mitigated" in row[2].lower()
    assert "5d" in row[3] and not _BARE_5A.search(row[3])


def test_hardening_claim_rows_name_5a_hardening() -> None:
    phrases = [
        "DISCARD ALL",
        "REVOKE TEMPORARY",
        "trust_injected_pool",
        "SCRAM verifier",
        "proxy_headers=False",
        "/64",
        "uv sync --locked",
        "server-file",
    ]
    rows = _table_rows()
    for phrase in phrases:
        matching = [c for c in rows if len(c) >= 3 and phrase in " ".join(c[:-1])]
        assert matching, f"no table row mentions {phrase!r}"
        for cells in matching:
            assert "5a-hardening" in cells[-1], f"{phrase!r} row lacks 5a-hardening owner"


def test_open_risks_record_hardening_review_findings() -> None:
    risks = " ".join(_open_risk_lines())
    for needle in [
        "audit_append",
        "audit_log_id_seq",
        "security_invoker",
        "pg_read_file",
        "boot only",
        "reltuples",
        "gitleaks-action",
        "pg_stat_activity",
        "NOTIFY",
        "TimescaleDB internals",
    ]:
        assert needle in risks, f"open risks missing {needle!r}"


def test_top_threat_4_states_what_exists_precisely() -> None:
    row = next(c for c in _table_rows() if c[0] == "4")
    existing, _, missing = row[2].partition("Not yet in place")
    for present in ["--locked", "SHA", "permissions", "digest", "Dependabot"]:
        assert present.lower() in existing.lower(), f"top-threat 4 'Existing' lacks {present}"
    for open_item in ["SBOM", "Trivy container-image scan", "gitleaks"]:
        assert open_item in missing, f"top-threat 4 'Not yet in place' lacks {open_item}"
    assert "docker-compose.yml only" in existing  # Dependabot scope caveat
    assert "5a-hardening" in row[3]


def test_per_route_isolation_tests_are_not_owned_by_5a_alone() -> None:
    checked = 0
    for cells in _table_rows():
        if len(cells) < 3:
            continue
        if re.search(r"per[ -]route", " ".join(cells[:-1]), re.IGNORECASE):
            checked += 1
            if _BARE_5A.search(cells[-1]):
                assert "5b" in cells[-1], f"per-route claim owned by 5a alone: {cells}"
    assert checked >= 2


def test_each_overclaim_row_owner_has_no_bare_5a() -> None:
    markers = [
        "Pydantic strict",
        "Session auth on every route",
        "Request/audit logging",
        "Role checks, step-up",
        "CSRF tokens",
        "RLS on key table",
        "Workers have read-only",
        "Worker identity",
    ]
    rows = _table_rows()
    for marker in markers:
        hits = [c for c in rows if len(c) >= 3 and marker in " ".join(c[:-1])]
        assert hits, f"no row for {marker!r}"
        for cells in hits:
            assert not _BARE_5A.search(cells[-1]), f"{marker!r} owned by 5a: {cells[-1]}"


def test_bootstrap_row_states_scram_verifier_log_residual() -> None:
    row = next(c for c in _table_rows() if "db.bootstrap" in " ".join(c))
    text = " ".join(row).lower()
    assert "verifier" in text and "server logs" in text and "offline" in text
    assert "long, random, ascii" in text
    risks = " ".join(_open_risk_lines())
    assert "verifier" in risks and "offline" in risks


def test_hardening_rows_state_their_limits() -> None:
    text = DOC.read_text(encoding="utf-8")
    assert "allow_unsafe_db_role" in text and 'environment == "dev"' in text
    assert "test-only by convention" in text
    assert "NEW sessions only" in text
    assert "pg_shdepend" in text and "repaired, not refused" in text
    assert "to be written" in text and "environment variables" in text
    assert "it is not enforced" not in text


def test_open_risks_have_owners() -> None:
    risks = " ".join(_open_risk_lines()).lower()
    for needle in [
        "owner 5d: `pg_stat_activity`",
        "owner 5b/5d: role-assertion drift",
        "owner 5b: explicit GRANT EXECUTE",
        "Deploy/infra (planned, not yet owned): the gitleaks-action",
        "Trivy and its vulnerability database",
    ]:
        assert needle.lower() in risks, f"open risks missing {needle!r}"


def test_audit_trigger_comment_does_not_claim_owner_immutability() -> None:
    sql = MIGRATION_002.read_text(encoding="utf-8")
    assert "including the owner" not in sql
    assert "session_replication_role" in sql


def test_review_file_has_no_stale_5b_task_0_hardening_claim() -> None:
    review = REVIEW.read_text(encoding="utf-8")
    assert "5b Task 0" not in review
    assert HARDENING_PLAN in review
    assert "## Hardening status" in review


def test_progress_has_phase_5a_hardening_section() -> None:
    progress = PROGRESS.read_text(encoding="utf-8")
    assert "## Phase 5a hardening" in progress
    assert progress.index("## Phase 5a hardening") > progress.index("## Phase 5a:")
    assert "session-token tenant binding (M4)" in progress
