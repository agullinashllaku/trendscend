"""Guards the Phase 5 STRIDE threat model against silent gaps."""

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
