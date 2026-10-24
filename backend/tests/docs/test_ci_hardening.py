"""Supply-chain hardening guards for CI and compose (review finding M5)."""

import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).parents[3]
CI = ROOT / ".github" / "workflows" / "ci.yml"
COMPOSE = ROOT / "infra" / "docker-compose.yml"
DEPENDABOT = ROOT / ".github" / "dependabot.yml"

SHA_REF = re.compile(r"^[\w./-]+@[0-9a-f]{40}$")
DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")


def _ci() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(CI.read_text())
    return data


def test_workflow_has_least_privilege_permissions() -> None:
    assert _ci().get("permissions") == {"contents": "read"}


def test_uv_sync_is_locked() -> None:
    runs = [
        str(step["run"])
        for job in _ci()["jobs"].values()
        for step in job["steps"]
        if "uv sync" in str(step.get("run", ""))
    ]
    assert runs, "no uv sync steps found"
    for run in runs:
        assert "--locked" in run, run


def test_actions_pinned_to_sha() -> None:
    uses = [
        str(step["uses"])
        for job in _ci()["jobs"].values()
        for step in job["steps"]
        if "uses" in step and not str(step["uses"]).startswith("./")
    ]
    assert uses
    for u in uses:
        assert SHA_REF.match(u), f"not pinned to a full commit SHA: {u}"
        assert not u.endswith(("@master", "@main"))


def test_actions_have_tag_comment() -> None:
    for line in CI.read_text().splitlines():
        if "uses:" in line and "@" in line:
            assert re.search(r"#\s*v\d", line), f"missing trailing tag comment: {line}"


def test_images_pinned_by_digest() -> None:
    images = [str(s["image"]) for s in _ci()["jobs"]["test"]["services"].values()]
    compose = yaml.safe_load(COMPOSE.read_text())
    images += [str(s["image"]) for s in compose["services"].values() if "image" in s]
    assert len(images) >= 4
    for image in images:
        assert DIGEST.search(image), f"image not pinned by digest: {image}"


def test_dependabot_covers_ecosystems() -> None:
    cfg = yaml.safe_load(DEPENDABOT.read_text())
    got = {
        (u["package-ecosystem"], u["directory"], u["schedule"]["interval"]) for u in cfg["updates"]
    }
    assert {
        ("github-actions", "/", "weekly"),
        ("uv", "/backend", "weekly"),
        ("docker", "/infra", "weekly"),
    } <= got
