"""Fail loudly in CI if integration env vars are missing (tests would silently skip)."""

import os


def test_integration_env_present_in_ci() -> None:
    if os.getenv("CI", "").lower() != "true":
        return
    assert os.getenv("TEST_DATABASE_URL"), "TEST_DATABASE_URL unset in CI"
    assert os.getenv("TEST_REDIS_URL"), "TEST_REDIS_URL unset in CI"
