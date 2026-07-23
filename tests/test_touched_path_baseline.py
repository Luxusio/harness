from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parent.parent
LIB = REPO_ROOT / "plugin" / "scripts" / "_lib.py"

spec = importlib.util.spec_from_file_location("harness_lib_for_baseline_tests", LIB)
lib = importlib.util.module_from_spec(spec)
sys.path.insert(0, str(REPO_ROOT / "plugin" / "scripts"))
spec.loader.exec_module(lib)


def _run(cmd: list[str], cwd: Path) -> None:
    subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True)


def _mk_repo(tmp: str) -> Path:
    repo = Path(tmp)
    _run(["git", "init", "-q"], repo)
    _run(["git", "config", "user.email", "a@b"], repo)
    _run(["git", "config", "user.name", "a"], repo)
    (repo / ".gitignore").write_text("doc/harness/tasks/\n", encoding="utf-8")
    (repo / "existing.txt").write_text("base\n", encoding="utf-8")
    _run(["git", "add", ".gitignore", "existing.txt"], repo)
    _run(["git", "commit", "-qm", "init"], repo)
    return repo


def _task_dir(repo: Path) -> Path:
    return repo / "doc" / "harness" / "tasks" / "TASK__baseline"


class TestTouchedPathBaseline(unittest.TestCase):
    def test_task_start_records_baseline_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _mk_repo(tmp)
            (repo / "existing.txt").write_text("dirty before task\n", encoding="utf-8")
            td = _task_dir(repo)
            lib.ensure_task_scaffold(str(td), "TASK__baseline")
            baseline = td / "TASK_BASELINE.json"
            data = json.loads(baseline.read_text(encoding="utf-8"))
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=repo,
                check=True, capture_output=True, text=True,
            ).stdout.strip()
        self.assertEqual(data["version"], 1)
        self.assertEqual(data["head_sha"], head)
        self.assertIn("existing.txt", data["dirty_paths"])

    def test_unchanged_baseline_dirty_path_is_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _mk_repo(tmp)
            (repo / "existing.txt").write_text("dirty before task\n", encoding="utf-8")
            td = _task_dir(repo)
            lib.ensure_task_scaffold(str(td), "TASK__baseline")
            touched = lib.sync_from_git_diff(str(td))
        self.assertNotIn("existing.txt", touched)

    def test_baseline_dirty_path_is_included_after_content_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _mk_repo(tmp)
            (repo / "existing.txt").write_text("dirty before task\n", encoding="utf-8")
            td = _task_dir(repo)
            lib.ensure_task_scaffold(str(td), "TASK__baseline")
            (repo / "existing.txt").write_text("changed during task\n", encoding="utf-8")
            touched = lib.sync_from_git_diff(str(td))
        self.assertIn("existing.txt", touched)

    def test_new_post_baseline_path_is_included(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _mk_repo(tmp)
            td = _task_dir(repo)
            lib.ensure_task_scaffold(str(td), "TASK__baseline")
            (repo / "new.txt").write_text("new during task\n", encoding="utf-8")
            touched = lib.sync_from_git_diff(str(td))
        self.assertIn("new.txt", touched)

    def test_present_corrupt_baseline_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _mk_repo(tmp)
            (repo / "existing.txt").write_text("dirty before task\n", encoding="utf-8")
            td = _task_dir(repo)
            lib.ensure_task_scaffold(str(td), "TASK__baseline")
            (td / "TASK_BASELINE.json").write_text("{not json", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "baseline integrity"):
                lib.sync_from_git_diff(str(td))

    def test_symlinked_baseline_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _mk_repo(tmp)
            td = _task_dir(repo)
            lib.ensure_task_scaffold(str(td), "TASK__baseline")
            baseline = td / "TASK_BASELINE.json"
            outside = repo / "outside-baseline.json"
            outside.write_bytes(baseline.read_bytes())
            baseline.unlink()
            baseline.symlink_to(outside)
            with self.assertRaisesRegex(RuntimeError, "baseline integrity"):
                lib.sync_from_git_diff(str(td))

    def test_baseline_requires_absolute_matching_repository_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _mk_repo(tmp)
            td = _task_dir(repo)
            lib.ensure_task_scaffold(str(td), "TASK__baseline")
            baseline = td / "TASK_BASELINE.json"
            original = json.loads(baseline.read_text(encoding="utf-8"))
            previous_cwd = os.getcwd()
            os.chdir(repo)
            try:
                for stored_root in (None, "", ".", "relative/repo"):
                    with self.subTest(stored_root=stored_root):
                        data = dict(original)
                        if stored_root is None:
                            data.pop("repo_root", None)
                        else:
                            data["repo_root"] = stored_root
                        baseline.write_text(json.dumps(data), encoding="utf-8")
                        with self.assertRaisesRegex(RuntimeError, "baseline integrity"):
                            lib.sync_from_git_diff(str(td))
            finally:
                os.chdir(previous_cwd)

    def test_unchanged_pre_task_dirt_stays_excluded_after_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _mk_repo(tmp)
            path = repo / "existing.txt"
            path.write_text("dirty before task\n", encoding="utf-8")
            td = _task_dir(repo)
            lib.ensure_task_scaffold(str(td), "TASK__baseline")
            _run(["git", "add", "existing.txt"], repo)
            _run(["git", "commit", "-qm", "commit prior dirt unchanged"], repo)

            touched = lib.sync_from_git_diff(str(td))

        self.assertNotIn("existing.txt", touched)

    def test_committed_path_diff_failure_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _mk_repo(tmp)
            td = _task_dir(repo)
            lib.ensure_task_scaffold(str(td), "TASK__baseline")
            original_run = lib.subprocess.run

            def fail_diff(command, *args, **kwargs):
                if len(command) > 1 and command[1] == "diff" and "--name-only" in command:
                    return subprocess.CompletedProcess(command, 1, b"", b"failure")
                return original_run(command, *args, **kwargs)

            with mock.patch.object(lib.subprocess, "run", side_effect=fail_diff):
                with self.assertRaisesRegex(RuntimeError, "Git diff unavailable"):
                    lib.sync_from_git_diff(str(td))

    def test_committed_path_diff_timeout_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _mk_repo(tmp)
            td = _task_dir(repo)
            lib.ensure_task_scaffold(str(td), "TASK__baseline")
            original_run = lib.subprocess.run

            def timeout_diff(command, *args, **kwargs):
                if len(command) > 1 and command[1] == "diff" and "--name-only" in command:
                    raise subprocess.TimeoutExpired(command, 5)
                return original_run(command, *args, **kwargs)

            with mock.patch.object(lib.subprocess, "run", side_effect=timeout_diff):
                with self.assertRaisesRegex(RuntimeError, "Git diff unavailable"):
                    lib.sync_from_git_diff(str(td))

    def test_required_qa_lenses_ignore_unchanged_pre_task_dirty_api_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _mk_repo(tmp)
            manifest = repo / "doc" / "harness" / "manifest.yaml"
            manifest.parent.mkdir(parents=True)
            manifest.write_text("type: library\n", encoding="utf-8")
            api_path = repo / "src" / "api" / "route.py"
            api_path.parent.mkdir(parents=True)
            api_path.write_text("dirty before task\n", encoding="utf-8")
            td = _task_dir(repo)
            lib.ensure_task_scaffold(str(td), "TASK__baseline")

            lenses = lib._required_qa_lenses(str(td))

        self.assertEqual(lenses, ["qa-cli"])

    def test_completion_timestamp_does_not_stale_same_second_prior_edit(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _mk_repo(tmp)
            manifest = repo / "doc" / "harness" / "manifest.yaml"
            manifest.parent.mkdir(parents=True)
            manifest.write_text("type: library\n", encoding="utf-8")
            td = _task_dir(repo)
            lib.ensure_task_scaffold(str(td), "TASK__baseline")
            source = repo / "src" / "main.py"
            source.parent.mkdir(parents=True)
            source.write_text("changed during task\n", encoding="utf-8")
            lib.sync_from_git_diff(str(td))
            lib.record_subagent_receipt(
                str(td),
                {
                    "agent_id": "qa-cli-1",
                    "agent_type": "harness:qa-cli",
                    "status": "completed",
                    "verdict": "PASS",
                    "summary": "VERDICT: PASS",
                },
            )

            stale, stale_path = lib.runtime_is_stale(str(td))

        self.assertFalse(stale, stale_path)


if __name__ == "__main__":
    unittest.main()
