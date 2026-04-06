"""Regression tests for the task lifecycle fixes.

Covers:
  - task_start bootstraps canonical task dirs from task_id/slug
  - task_close persists status: closed + closed_at
  - CHECKS.yaml status aliases are normalized case-insensitively
  - absolute hook paths become repo-relative changed files
  - file_changed_sync only invalidates the active task when file list is missing
  - task-local artifact writes do not invalidate runtime verdicts
  - hctl verify anchors verification commands at repo root
  - manual path updates work without git diff
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_DIR = REPO_ROOT / "plugin" / "scripts"
HCTL = SCRIPT_DIR / "hctl.py"

sys.path.insert(0, str(SCRIPT_DIR))
os.environ["HARNESS_SKIP_STDIN"] = "1"

import file_changed_sync  # type: ignore  # noqa: E402
from _lib import parse_changed_files, yaml_array, yaml_field  # type: ignore  # noqa: E402
from task_completed_gate import compute_completion_failures, _parse_checks_yaml  # type: ignore  # noqa: E402
from task_index import update_active_task  # type: ignore  # noqa: E402


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _make_repo_root(base: Path, *, smoke_command: str | None = None) -> Path:
    repo = base
    _write(
        repo / "doc" / "harness" / "manifest.yaml",
        textwrap.dedent(
            f"""
            name: temp-harness-test
            type: app
            project_meta:
              shape: app
            profiles:
              observability_enabled: false
            teams:
              provider: none
            {f'smoke_command: {smoke_command}' if smoke_command else ''}
            """
        ).strip()
        + "\n",
    )
    (repo / "doc" / "harness" / "tasks").mkdir(parents=True, exist_ok=True)
    return repo


def _make_non_mutating_passing_task(task_dir: Path) -> None:
    _write(
        task_dir / "TASK_STATE.yaml",
        "\n".join(
            [
                f"task_id: {task_dir.name}",
                "status: implemented",
                "lane: refactor",
                "mutates_repo: false",
                "plan_verdict: PASS",
                "runtime_verdict: PASS",
                "runtime_verdict_freshness: current",
                "document_verdict: skipped",
                "document_verdict_freshness: current",
                "execution_mode: standard",
                "orchestration_mode: solo",
                "workflow_violations: []",
                "artifact_provenance_required: false",
                "directive_capture_state: clean",
                "complaint_capture_state: clean",
                "updated: 2026-01-01T00:00:00Z",
            ]
        )
        + "\n",
    )
    _write(task_dir / "PLAN.md", "# Plan\n")
    _write(task_dir / "CRITIC__plan.md", "verdict: PASS\n")
    _write(
        task_dir / "HANDOFF.md",
        "# Handoff\n## Current state\nDone.\n## Verification\nNone required.\n",
    )


def _make_mutating_task(task_dir: Path, *, runtime_verdict: str = "PASS", document_verdict: str = "PASS") -> None:
    _write(
        task_dir / "TASK_STATE.yaml",
        "\n".join(
            [
                f"task_id: {task_dir.name}",
                "status: implemented",
                "lane: build",
                "mutates_repo: true",
                "plan_verdict: PASS",
                f"runtime_verdict: {runtime_verdict}",
                "runtime_verdict_freshness: current",
                f"document_verdict: {document_verdict}",
                "document_verdict_freshness: current",
                "doc_changes_detected: false",
                "execution_mode: standard",
                "orchestration_mode: solo",
                "workflow_violations: []",
                "artifact_provenance_required: false",
                "directive_capture_state: clean",
                "complaint_capture_state: clean",
                "touched_paths: [\"src/app.py\"]",
                "roots_touched: [\"src\"]",
                "verification_targets: [\"src/app.py\"]",
                "updated: 2026-01-01T00:00:00Z",
            ]
        )
        + "\n",
    )
    _write(task_dir / "PLAN.md", "# Plan\n")
    _write(task_dir / "CRITIC__plan.md", "verdict: PASS\n")
    _write(task_dir / "CRITIC__runtime.md", "verdict: PASS\n")
    _write(task_dir / "CRITIC__document.md", "verdict: PASS\n")
    _write(task_dir / "DOC_SYNC.md", "none\n")
    _write(task_dir / "HANDOFF.md", "# Handoff\n## Current state\nDone.\n## Verification\npytest\n")


class TaskLifecycleRegressionTests(unittest.TestCase):
    def _run_hctl(self, repo_root: Path, *args: str, cwd: Path | None = None):
        result = subprocess.run(
            [sys.executable, str(HCTL), *args],
            cwd=str(cwd or repo_root),
            capture_output=True,
            text=True,
            env={**os.environ, "HARNESS_SKIP_STDIN": "1"},
        )
        return result

    def test_task_start_bootstraps_from_task_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo_root(Path(tmp))
            result = self._run_hctl(repo, "start", "--task-id", "local-setup-doc")
            self.assertEqual(result.returncode, 0, result.stderr)
            task_dir = repo / "doc" / "harness" / "tasks" / "TASK__local-setup-doc"
            self.assertTrue((task_dir / "TASK_STATE.yaml").is_file())
            self.assertTrue((task_dir / "REQUEST.md").is_file())
            self.assertTrue((task_dir / "CHECKS.yaml").is_file())
            state_file = task_dir / "TASK_STATE.yaml"
            self.assertEqual(yaml_field("task_id", str(state_file)), "TASK__local-setup-doc")
            self.assertEqual(yaml_field("runtime_verdict_freshness", str(state_file)), "current")
            self.assertEqual(yaml_field("document_verdict_freshness", str(state_file)), "current")
            self.assertIn("task_dir:", result.stdout)

    def test_task_close_persists_closed_status_and_timestamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo_root(Path(tmp))
            task_dir = repo / "doc" / "harness" / "tasks" / "TASK__close_me"
            _make_non_mutating_passing_task(task_dir)

            result = self._run_hctl(repo, "close", "--task-dir", str(task_dir))
            self.assertEqual(result.returncode, 0, result.stderr)
            state_file = task_dir / "TASK_STATE.yaml"
            self.assertEqual(yaml_field("status", str(state_file)), "closed")
            self.assertTrue((yaml_field("closed_at", str(state_file)) or "").endswith("Z"))

    def test_checks_aliases_are_case_insensitive_for_strict_close_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo_root(Path(tmp))
            task_dir = repo / "doc" / "harness" / "tasks" / "TASK__checks"
            _make_non_mutating_passing_task(task_dir)
            _write(
                task_dir / "CHECKS.yaml",
                textwrap.dedent(
                    """
                    close_gate: strict_high_risk
                    checks:
                      - id: AC-001
                        title: first
                        status: PASS
                      - id: AC-002
                        title: second
                        status: pass
                    """
                ).strip()
                + "\n",
            )

            criteria = _parse_checks_yaml(str(task_dir / "CHECKS.yaml"))
            self.assertEqual([c["status"] for c in criteria], ["passed", "passed"])
            failures = compute_completion_failures(str(task_dir))
            self.assertFalse(any("STRICT CLOSE GATE" in f for f in failures), failures)

    def test_checks_pending_alias_is_normalized_to_planned(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo_root(Path(tmp))
            task_dir = repo / "doc" / "harness" / "tasks" / "TASK__checks_pending"
            _make_non_mutating_passing_task(task_dir)
            _write(
                task_dir / "CHECKS.yaml",
                textwrap.dedent(
                    """
                    checks:
                      - id: AC-001
                        title: first
                        status: pending
                    """
                ).strip()
                + "\n",
            )

            criteria = _parse_checks_yaml(str(task_dir / "CHECKS.yaml"))
            self.assertEqual([c["status"] for c in criteria], ["planned"])

    def test_parse_changed_files_normalizes_absolute_repo_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo_root(Path(tmp))
            absolute = str(repo / "src" / "feature.py")
            cwd_before = os.getcwd()
            os.chdir(repo)
            try:
                payload = json.dumps({"file_path": absolute})
                self.assertEqual(parse_changed_files(payload), ["src/feature.py"])
            finally:
                os.chdir(cwd_before)

    def test_file_changed_sync_missing_file_list_invalidates_only_active_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo_root(Path(tmp))
            task_a = repo / "doc" / "harness" / "tasks" / "TASK__active"
            task_b = repo / "doc" / "harness" / "tasks" / "TASK__other"
            _make_mutating_task(task_a)
            _make_mutating_task(task_b)
            update_active_task(str(task_a), tasks_dir=str(repo / "doc" / "harness" / "tasks"))

            cwd_before = os.getcwd()
            os.chdir(repo)
            try:
                with self.assertRaises(SystemExit) as cm:
                    file_changed_sync.main()
                self.assertEqual(cm.exception.code, 0)
            finally:
                os.chdir(cwd_before)

            state_a = str(task_a / "TASK_STATE.yaml")
            state_b = str(task_b / "TASK_STATE.yaml")
            self.assertEqual(yaml_field("runtime_verdict", state_a), "PASS")
            self.assertEqual(yaml_field("runtime_verdict_freshness", state_a), "stale")
            self.assertEqual(yaml_field("document_verdict", state_a), "PASS")
            self.assertEqual(yaml_field("document_verdict_freshness", state_a), "stale")
            self.assertEqual(yaml_field("runtime_verdict", state_b), "PASS")
            self.assertEqual(yaml_field("runtime_verdict_freshness", state_b), "current")
            self.assertEqual(yaml_field("document_verdict", state_b), "PASS")
            self.assertEqual(yaml_field("document_verdict_freshness", state_b), "current")

    def test_task_artifact_write_does_not_invalidate_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo_root(Path(tmp))
            task_dir = repo / "doc" / "harness" / "tasks" / "TASK__artifact"
            _make_mutating_task(task_dir)
            update_active_task(str(task_dir), tasks_dir=str(repo / "doc" / "harness" / "tasks"))

            cwd_before = os.getcwd()
            os.chdir(repo)
            try:
                file_changed_sync.process_changed_file(f"doc/harness/tasks/{task_dir.name}/TASK_STATE.yaml")
            finally:
                os.chdir(cwd_before)

            state_file = task_dir / "TASK_STATE.yaml"
            self.assertEqual(yaml_field("runtime_verdict", str(state_file)), "PASS")
            self.assertEqual(yaml_field("runtime_verdict_freshness", str(state_file)), "current")
            self.assertEqual(yaml_field("document_verdict", str(state_file)), "PASS")
            self.assertEqual(yaml_field("document_verdict_freshness", str(state_file)), "current")

    def test_hctl_verify_runs_from_repo_root_even_when_called_elsewhere(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo_root(Path(tmp), smoke_command="test -f doc/common/LOCAL_SETUP.md")
            _write(repo / "doc" / "common" / "LOCAL_SETUP.md", "ok\n")
            task_dir = repo / "doc" / "harness" / "tasks" / "TASK__verify"
            _make_non_mutating_passing_task(task_dir)
            nested_cwd = repo / "nested" / "child"
            nested_cwd.mkdir(parents=True, exist_ok=True)

            result = self._run_hctl(repo, "verify", "--task-dir", str(task_dir), cwd=nested_cwd)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("RESULT: all checks passed", result.stdout)

    def test_manual_task_update_paths_works_without_git(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo_root(Path(tmp))
            task_dir = repo / "doc" / "harness" / "tasks" / "TASK__update_paths"
            _make_non_mutating_passing_task(task_dir)
            state_file = task_dir / "TASK_STATE.yaml"
            _write(
                state_file,
                state_file.read_text(encoding="utf-8")
                + 'touched_paths: []\nroots_touched: []\nverification_targets: []\n',
            )

            result = self._run_hctl(
                repo,
                "update",
                "--task-dir",
                str(task_dir),
                "--touched-path",
                "src/app.py",
                "--verification-target",
                "src/app.py",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(yaml_array("touched_paths", str(state_file)), ["src/app.py"])
            self.assertEqual(yaml_array("verification_targets", str(state_file)), ["src/app.py"])
            self.assertEqual(yaml_array("roots_touched", str(state_file)), ["src"])


if __name__ == "__main__":
    unittest.main()
