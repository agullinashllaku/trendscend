"""Supply-chain hardening guards for CI and compose (review finding M5)."""

import copy
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).parents[3]
CI = ROOT / ".github" / "workflows" / "ci.yml"
COMPOSE = ROOT / "infra" / "docker-compose.yml"
DEPENDABOT = ROOT / ".github" / "dependabot.yml"

SHA_REF = re.compile(r"^[\w./-]+@[0-9a-f]{40}$")
DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _load_ci() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(CI.read_text())
    return data


def _ci() -> dict[str, Any]:
    return _load_ci()


# ---- pure checkers (also exercised against mutated copies below) ----


def check_permissions(wf: dict[str, Any]) -> list[str]:
    errs = []
    if wf.get("permissions") != {"contents": "read"}:
        errs.append(f"top-level permissions: {wf.get('permissions')!r}")
    for name, job in wf["jobs"].items():
        perms = job.get("permissions")
        if perms is None:
            continue
        if isinstance(perms, str):
            errs.append(f"{name}: string permissions {perms!r}")
            continue
        for scope, level in perms.items():
            if level not in {"read", "none"}:
                errs.append(f"{name}: {scope}: {level}")
    return errs


def check_installs(wf: dict[str, Any]) -> list[str]:
    errs = []
    for name, job in wf["jobs"].items():
        for step in job["steps"]:
            run = str(step.get("run", ""))
            if "uv sync" in run and "--locked" not in run:
                errs.append(f"{name}: unlocked uv sync: {run}")
            if re.search(r"\bpip3?\s+install\b", run) or "uv pip install" in run:
                errs.append(f"{name}: unlocked pip install: {run}")
    return errs


def ci_images(wf: dict[str, Any]) -> list[str]:
    images: list[str] = []
    for job in wf["jobs"].values():
        for svc in (job.get("services") or {}).values():
            images.append(str(svc["image"]))
        container = job.get("container")
        if container:
            images.append(str(container if isinstance(container, str) else container["image"]))
    return images


def check_images(images: list[str]) -> list[str]:
    return [i for i in images if not DIGEST.search(i)]


def check_uses_pins(text: str) -> list[str]:
    errs = []
    shas: dict[str, set[str]] = defaultdict(set)
    for line in text.splitlines():
        m = re.search(r"\buses:\s*(\S+)(.*)$", line)
        if not m or m.group(1).startswith("./"):
            continue
        ref, rest = m.group(1), m.group(2)
        if not SHA_REF.match(ref) or ref.endswith(("@master", "@main")):
            errs.append(f"not SHA-pinned: {ref}")
            continue
        if not re.search(r"#\s*v\d+\.\d+\.\d+\b", rest):
            errs.append(f"missing # vX.Y.Z comment: {line.strip()}")
        action, sha = ref.split("@")
        shas[action].add(sha)
    errs += [f"{a} pinned to multiple SHAs" for a, s in shas.items() if len(s) > 1]
    return errs


# ---- tests against the real files ----


def test_workflow_has_least_privilege_permissions() -> None:
    assert check_permissions(_ci()) == []


def test_uv_sync_is_locked() -> None:
    wf = _ci()
    assert any("uv sync" in str(s.get("run", "")) for j in wf["jobs"].values() for s in j["steps"])
    assert check_installs(wf) == []


def test_actions_pinned_to_sha_with_tag_comment() -> None:
    assert "uses:" in CI.read_text()
    assert check_uses_pins(CI.read_text()) == []


def test_images_pinned_by_digest() -> None:
    images = ci_images(_ci())
    compose = yaml.safe_load(COMPOSE.read_text())
    images += [str(s["image"]) for s in compose["services"].values() if "image" in s]
    assert len(images) >= 4
    assert check_images(images) == []


def _digests(images: list[str]) -> dict[str, str]:
    out = {}
    for i in images:
        name = i.split(":")[0].split("@")[0]
        m = DIGEST.search(i)
        assert m, i
        out[name] = i.split("@")[1]
    return out


def test_ci_service_digests_match_compose_digests() -> None:
    ci = _digests(ci_images(_ci()))
    compose_doc = yaml.safe_load(COMPOSE.read_text())
    compose = _digests([str(s["image"]) for s in compose_doc["services"].values()])
    for name in ("timescale/timescaledb", "redis"):
        assert name in ci and name in compose
        assert ci[name] == compose[name], f"{name} digest differs between ci.yml and compose"


def test_setup_uv_steps_pin_exact_version_and_checksum() -> None:
    steps = [
        s
        for j in _ci()["jobs"].values()
        for s in j["steps"]
        if str(s.get("uses", "")).startswith("astral-sh/setup-uv@")
    ]
    assert len(steps) == 4
    for s in steps:
        w = s.get("with", {})
        assert SEMVER.match(str(w.get("version", ""))), w
        assert SHA256.match(str(w.get("checksum", ""))), w
    assert len({(s["with"]["version"], s["with"]["checksum"]) for s in steps}) == 1


def test_dependabot_covers_ecosystems() -> None:
    cfg = yaml.safe_load(DEPENDABOT.read_text())
    got = {
        (u["package-ecosystem"], u["directory"], u["schedule"]["interval"]) for u in cfg["updates"]
    }
    assert {
        ("github-actions", "/", "weekly"),
        ("uv", "/backend", "weekly"),
        ("docker-compose", "/infra", "weekly"),
    } <= got
    assert "docker" not in {e for e, _, _ in got}


def test_ci_has_digest_sync_comment() -> None:
    assert "docker-compose.yml" in CI.read_text().split("services:")[0].splitlines()[-1] or any(
        "docker-compose.yml" in ln and ln.lstrip().startswith("#")
        for ln in CI.read_text().splitlines()
    )


# ---- negative cases: mutated copies must be rejected ----


def _mut(fn: Any) -> dict[str, Any]:
    wf = copy.deepcopy(_ci())
    fn(wf)
    return wf


@pytest.mark.parametrize(
    "mutate",
    [
        lambda w: w["jobs"]["security"].update(permissions="write-all"),
        lambda w: w["jobs"]["security"]["permissions"].update(contents="write"),
        lambda w: w["jobs"]["security"]["permissions"].update({"pull-requests": "write"}),
        lambda w: w["jobs"]["lint"].update(permissions={"contents": "write"}),
        lambda w: w.update(permissions={"contents": "read", "id-token": "write"}),
    ],
)
def test_permission_mutations_rejected(mutate: Any) -> None:
    assert check_permissions(_mut(mutate)) != []


@pytest.mark.parametrize(
    "run",
    ["uv pip install requests", "pip install requests", "uv sync --extra dev"],
)
def test_install_mutations_rejected(run: str) -> None:
    wf = _mut(lambda w: w["jobs"]["lint"]["steps"].append({"run": run}))
    assert check_installs(wf) != []


def test_image_mutations_rejected() -> None:
    wf = _mut(
        lambda w: (
            w["jobs"]["lint"].update(container={"image": "python:3.12"})
            or w["jobs"]["typecheck"].update(services={"x": {"image": "redis:7"}})
        )
    )
    bad = check_images(ci_images(wf))
    assert "python:3.12" in bad and "redis:7" in bad


def test_uses_mutations_rejected() -> None:
    text = CI.read_text()
    sha = re.search(r"actions/checkout@([0-9a-f]{40})", text)
    assert sha
    assert check_uses_pins(text.replace(sha.group(1), "v4", 1)) != []
    assert check_uses_pins(re.sub(r"(actions/checkout@\w{40}) # v\S+", r"\1", text, count=1)) != []
    assert check_uses_pins(text.replace(sha.group(1), "0" * 40, 1)) != []
    assert check_uses_pins(text.replace("trivy-action@", "trivy-action@master#", 1)) != []
