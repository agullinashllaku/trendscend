"""Guards the CI workflow: integration tests must get Postgres and Redis services."""

from pathlib import Path
from typing import Any

import yaml

CI = Path(__file__).parents[3] / ".github" / "workflows" / "ci.yml"


def _test_job() -> dict[str, Any]:
    data = yaml.safe_load(CI.read_text())
    job: dict[str, Any] = data["jobs"]["test"]
    return job


def test_ci_workflow_sets_test_database_url() -> None:
    job = _test_job()
    services = job.get("services", {})
    images = [str(s.get("image", "")) for s in services.values()]
    assert any("timescaledb" in i or i.startswith("postgres") for i in images)
    assert any(i.startswith("redis") for i in images)
    for s in services.values():
        assert "--health-cmd" in str(s.get("options", "")), "service needs a health check"
    pytest_steps = [st for st in job["steps"] if "pytest" in str(st.get("run", ""))]
    assert pytest_steps, "no pytest step"
    env = pytest_steps[0].get("env", {})
    assert str(env.get("TEST_DATABASE_URL", "")).startswith("postgresql://")
    assert str(env.get("TEST_REDIS_URL", "")).startswith("redis://")
    assert "--cov-fail-under=80" in pytest_steps[0]["run"]
