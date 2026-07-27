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
    def test_new_task_in_unborn_git_repo_fails_before_state_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _run(["git", "init", "-q"], repo)
            td = _task_dir(repo)

            with self.assertRaisesRegex(RuntimeError, "baseline capture unavailable"):
                lib.ensure_task_scaffold(str(td), "TASK__baseline")

            self.assertFalse((td / "TASK_STATE.yaml").exists())
            self.assertFalse((td / "TASK_BASELINE.json").exists())

    def test_new_task_blocks_when_baseline_fingerprinting_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _mk_repo(tmp)
            td = _task_dir(repo)
            with mock.patch.object(
                lib, "_changed_path_fingerprints",
                side_effect=RuntimeError("snapshot unavailable"),
            ):
                with self.assertRaisesRegex(RuntimeError, "baseline capture unavailable"):
                    lib.ensure_task_scaffold(str(td), "TASK__baseline")

            self.assertFalse((td / "TASK_STATE.yaml").exists())
            self.assertFalse((td / "TASK_BASELINE.json").exists())

    def test_new_task_preserves_baseline_fingerprinting_failure_cause(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _mk_repo(tmp)
            td = _task_dir(repo)
            with mock.patch.object(
                lib,
                "_changed_path_fingerprints",
                side_effect=RuntimeError(
                    "Git changed-path snapshot unavailable: worktree enumeration timed out"
                ),
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "task baseline capture unavailable.*worktree enumeration timed out",
                ):
                    lib.ensure_task_scaffold(str(td), "TASK__baseline")

            self.assertFalse((td / "TASK_STATE.yaml").exists())
            self.assertFalse((td / "TASK_BASELINE.json").exists())

    def test_changed_path_enumeration_preserves_legacy_limit_without_deadline(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _mk_repo(tmp)
            with mock.patch.object(
                lib.subprocess,
                "run",
                side_effect=subprocess.TimeoutExpired(["git", "diff"], 5),
            ) as run:
                with self.assertRaisesRegex(
                    RuntimeError,
                    r"working tree diff timed out after 5\.0s",
                ):
                    lib._uncached_git_changed_paths(str(repo))

            self.assertEqual(run.call_args.kwargs["timeout"], 5.0)

    def test_head_read_preserves_legacy_limit_without_deadline(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _mk_repo(tmp)
            with mock.patch.object(
                lib.subprocess,
                "run",
                side_effect=subprocess.TimeoutExpired(["git", "rev-parse"], 2),
            ) as run:
                with self.assertRaisesRegex(
                    RuntimeError,
                    r"git HEAD read timed out after 2\.0s",
                ):
                    with lib.review_snapshot_scope():
                        lib._git_head_snapshot(str(repo))

            self.assertEqual(run.call_args.kwargs["timeout"], 2.0)

    def test_new_baseline_preserves_head_timeout_and_exit_diagnostics(self):
        failures = (
            (
                subprocess.TimeoutExpired(["git", "rev-parse"], 15),
                r"git HEAD read timed out after 15\.0s",
            ),
            (
                subprocess.CompletedProcess([], 128, stdout="", stderr="bad HEAD"),
                r"git HEAD read exited 128",
            ),
        )
        for failure, message in failures:
            with self.subTest(message=message), tempfile.TemporaryDirectory() as tmp:
                repo = _mk_repo(tmp)
                td = _task_dir(repo)
                kwargs = (
                    {"side_effect": failure}
                    if isinstance(failure, BaseException)
                    else {"return_value": failure}
                )
                with mock.patch.object(lib.subprocess, "run", **kwargs):
                    with self.assertRaisesRegex(
                        RuntimeError, f"task baseline capture unavailable.*{message}"
                    ):
                        with lib.review_snapshot_scope(deadline_seconds=40):
                            lib.ensure_task_scaffold(str(td), "TASK__baseline")
                self.assertFalse((td / "TASK_STATE.yaml").exists())

    def test_existing_baseline_preserves_commit_and_ancestor_diagnostics(self):
        cases = (
            ("commit-timeout", "rev-parse", "timeout", "baseline commit validation timed out"),
            ("commit-exit", "rev-parse", "exit", "baseline commit validation exited 7"),
            ("ancestor-timeout", "merge-base", "timeout", "baseline ancestry validation timed out"),
            ("ancestor-exit", "merge-base", "exit", "baseline ancestry validation exited 7"),
        )
        for name, target, failure_kind, message in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                repo = _mk_repo(tmp)
                td = _task_dir(repo)
                lib.ensure_task_scaffold(str(td), "TASK__baseline")
                baseline = json.loads(
                    (td / "TASK_BASELINE.json").read_text(encoding="utf-8")
                )
                head = baseline["head_sha"]

                def validation_run(command, *args, **kwargs):
                    operation = command[1] if len(command) > 1 else ""
                    if operation == target:
                        if failure_kind == "timeout":
                            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
                        return subprocess.CompletedProcess(command, 7, stdout="", stderr="")
                    if operation == "rev-parse":
                        return subprocess.CompletedProcess(command, 0, stdout=head + "\n")
                    return subprocess.CompletedProcess(command, 0, stdout="")

                with mock.patch.object(
                    lib.subprocess, "run", side_effect=validation_run
                ):
                    with self.assertRaisesRegex(RuntimeError, message):
                        lib._read_task_baseline_snapshot(str(td), str(repo))

    def test_request_snapshot_deadline_stops_git_before_transport_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _mk_repo(tmp)
            with mock.patch.object(
                lib.time, "monotonic", side_effect=[100.0, 141.0]
            ):
                with mock.patch.object(lib.subprocess, "run") as run:
                    with self.assertRaisesRegex(
                        RuntimeError,
                        "snapshot deadline exhausted before working tree diff",
                    ):
                        with lib.review_snapshot_scope(deadline_seconds=40):
                            lib._uncached_git_changed_paths(str(repo))

            run.assert_not_called()

    def test_valid_changed_path_scan_can_continue_after_five_seconds(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _mk_repo(tmp)
            completed = subprocess.CompletedProcess([], 0, stdout=b"")
            with mock.patch.object(
                lib.time, "monotonic", side_effect=[100.0, 106.0, 112.0, 118.0]
            ):
                with mock.patch.object(
                    lib.subprocess, "run", return_value=completed
                ) as run:
                    with lib.review_snapshot_scope(deadline_seconds=40):
                        self.assertEqual(lib._uncached_git_changed_paths(str(repo)), set())

            self.assertEqual(run.call_count, 3)
            self.assertTrue(all(call.kwargs["timeout"] == 15.0 for call in run.call_args_list))

    def test_committed_path_diff_reuses_cache_and_clips_to_remaining_deadline(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _mk_repo(tmp)
            td = _task_dir(repo)
            lib.ensure_task_scaffold(str(td), "TASK__baseline")
            completed = subprocess.CompletedProcess([], 0, stdout=b"")
            with mock.patch.object(
                lib.time, "monotonic", side_effect=[100.0, 139.0]
            ):
                with (
                    mock.patch.object(
                        lib, "_task_baseline_head_sha", return_value="a" * 40
                    ),
                    mock.patch.object(
                        lib.subprocess, "run", return_value=completed
                    ) as run,
                ):
                    with lib.review_snapshot_scope(deadline_seconds=40):
                        self.assertEqual(
                            lib._committed_paths_since_baseline(str(td), str(repo)),
                            set(),
                        )
                        self.assertEqual(
                            lib._committed_paths_since_baseline(str(td), str(repo)),
                            set(),
                        )

            run.assert_called_once()
            self.assertEqual(run.call_args.kwargs["timeout"], 1.0)

    def test_new_task_rejects_generated_baseline_over_path_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _mk_repo(tmp)
            td = _task_dir(repo)
            dirty = {
                f"generated/path-{index:05d}.txt": "missing"
                for index in range(10001)
            }
            with mock.patch.object(
                lib, "_changed_path_fingerprints", return_value=dirty,
            ):
                with self.assertRaisesRegex(RuntimeError, "baseline capture unavailable"):
                    lib.ensure_task_scaffold(str(td), "TASK__baseline")

            self.assertFalse((td / "TASK_STATE.yaml").exists())
            self.assertFalse((td / "TASK_BASELINE.json").exists())

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

    def test_deleted_baseline_fails_closed_after_clean_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _mk_repo(tmp)
            td = _task_dir(repo)
            lib.ensure_task_scaffold(str(td), "TASK__baseline")
            (td / "TASK_BASELINE.json").unlink()
            source = repo / "plugin/runtime.py"
            source.parent.mkdir(parents=True)
            source.write_text("VALUE = 1\n", encoding="utf-8")
            _run(["git", "add", "plugin/runtime.py"], repo)
            _run(["git", "commit", "-qm", "task change"], repo)

            with self.assertRaisesRegex(RuntimeError, "required task baseline missing"):
                lib.sync_from_git_diff(str(td))

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
