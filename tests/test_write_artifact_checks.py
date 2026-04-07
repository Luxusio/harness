"""Regression tests for P4: write_artifact --checks updates CHECKS.yaml.

Ensures that `write_artifact critic-runtime --checks AC-001:PASS` correctly
updates CHECKS.yaml, and that omitting --checks leaves CHECKS.yaml unchanged.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_DIR = REPO_ROOT / "plugin" / "scripts"
WRITE_ARTIFACT = REPO_ROOT / "plugin" / "scripts" / "write_artifact.py"
sys.path.insert(0, str(SCRIPT_DIR))
os.environ["HARNESS_SKIP_STDIN"] = "1"


_MINIMAL_TASK_STATE = """\
task_id: TASK__test-wa
status: implemented
mutates_repo: true
plan_verdict: PASS
runtime_verdict: pending
runtime_verdict_freshness: current
document_verdict: skipped
document_verdict_freshness: current
doc_changes_detected: false
execution_mode: standard
orchestration_mode: solo
workflow_violations: []
artifact_provenance_required: false
directive_capture_state: clean
complaint_capture_state: clean
touched_paths: []
roots_touched: []
verification_targets: []
"""

_MINIMAL_CHECKS_YAML = """\
schema_version: 1
close_gate: standard
checks:
  - id: AC-001
    title: "first check"
    status: planned
    kind: functional
    evidence_refs: []
    reopen_count: 0
    last_updated: "2026-01-01T00:00:00Z"
  - id: AC-002
    title: "second check"
    status: planned
    kind: functional
    evidence_refs: []
    reopen_count: 0
    last_updated: "2026-01-01T00:00:00Z"
"""


def _setup_task_dir(tmp: str) -> None:
    Path(tmp, "TASK_STATE.yaml").write_text(_MINIMAL_TASK_STATE, encoding="utf-8")
    Path(tmp, "CHECKS.yaml").write_text(_MINIMAL_CHECKS_YAML, encoding="utf-8")


def _run_write_artifact(task_dir: str, extra_args: list[str]) -> subprocess.CompletedProcess:
    cmd = [
        sys.executable, str(WRITE_ARTIFACT), "critic-runtime",
        "--task-dir", task_dir,
        "--verdict", "PASS",
        "--execution-mode", "standard",
        "--transcript", "pytest passed",
        "--summary", "all tests pass",
    ] + extra_args
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env={**os.environ, "HARNESS_SKIP_STDIN": "1"},
    )


def _read_checks_statuses(task_dir: str) -> dict[str, str]:
    """Parse CHECKS.yaml and return {id: status} using stdlib only."""
    checks_file = Path(task_dir) / "CHECKS.yaml"
    content = checks_file.read_text(encoding="utf-8")
    result: dict[str, str] = {}
    current_id: str | None = None
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("- id:"):
            current_id = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("status:") and current_id is not None:
            result[current_id] = stripped.split(":", 1)[1].strip()
            current_id = None
    return result


class WriteArtifactChecksTests(unittest.TestCase):

    def test_checks_updated_when_flag_provided(self):
        """--checks AC-001:PASS updates AC-001 to passed; AC-002 stays planned."""
        with tempfile.TemporaryDirectory() as tmp:
            _setup_task_dir(tmp)
            result = _run_write_artifact(tmp, ["--checks", "AC-001:PASS"])
            self.assertEqual(
                result.returncode, 0,
                msg=f"write_artifact failed:\nstdout={result.stdout}\nstderr={result.stderr}",
            )
            statuses = _read_checks_statuses(tmp)
            self.assertEqual(statuses.get("AC-001"), "passed",
                             msg=f"AC-001 should be 'passed', got: {statuses}")
            self.assertEqual(statuses.get("AC-002"), "planned",
                             msg=f"AC-002 should remain 'planned', got: {statuses}")

    def test_checks_unchanged_when_flag_omitted(self):
        """Omitting --checks leaves CHECKS.yaml statuses unchanged."""
        with tempfile.TemporaryDirectory() as tmp:
            _setup_task_dir(tmp)
            result = _run_write_artifact(tmp, [])
            self.assertEqual(
                result.returncode, 0,
                msg=f"write_artifact failed:\nstdout={result.stdout}\nstderr={result.stderr}",
            )
            statuses = _read_checks_statuses(tmp)
            self.assertEqual(statuses.get("AC-001"), "planned",
                             msg=f"AC-001 should remain 'planned', got: {statuses}")
            self.assertEqual(statuses.get("AC-002"), "planned",
                             msg=f"AC-002 should remain 'planned', got: {statuses}")

    def test_checks_multiple_criteria(self):
        """--checks AC-001:PASS,AC-002:PASS updates both to passed."""
        with tempfile.TemporaryDirectory() as tmp:
            _setup_task_dir(tmp)
            result = _run_write_artifact(tmp, ["--checks", "AC-001:PASS,AC-002:PASS"])
            self.assertEqual(
                result.returncode, 0,
                msg=f"write_artifact failed:\nstdout={result.stdout}\nstderr={result.stderr}",
            )
            statuses = _read_checks_statuses(tmp)
            self.assertEqual(statuses.get("AC-001"), "passed",
                             msg=f"AC-001 should be 'passed', got: {statuses}")
            self.assertEqual(statuses.get("AC-002"), "passed",
                             msg=f"AC-002 should be 'passed', got: {statuses}")


if __name__ == "__main__":
    unittest.main()
