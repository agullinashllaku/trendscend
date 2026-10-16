"""Smoke test: gate report script produces non-empty output."""

from __future__ import annotations

import subprocess
import sys


def test_gate_report_runs_without_error() -> None:
    """The gate report script must run to completion with exit 0."""
    result = subprocess.run(
        [sys.executable, "research/phase3_gate_report.py", "--fixture"],
        capture_output=True,
        text=True,
        cwd=".",  # backend/
    )
    assert result.returncode == 0, f"Script failed:\n{result.stderr}"
    assert "Phase 3" in result.stdout or "Phase 3" in result.stderr
