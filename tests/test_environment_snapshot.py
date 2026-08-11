"""AC-001: environment_snapshot.py writes ENVIRONMENT_SNAPSHOT.md.

Covers required fields, git-status avoidance, no-manifest fallback, and
raise-swallowed behaviour.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT = REPO_ROOT / "plugin" / "scripts" / "environment_snapshot.py"

spec = importlib.util.spec_from_file_location("environment_snapshot", SNAPSHOT)
env_snapshot_mod = importlib.util.module_from_spec(spec)
sys.path.insert(0, str(REPO_ROOT / "plugin" / "scripts"))
spec.loader.exec_module(env_snapshot_mod)


def _mk_git_repo(base: Path) -> None:
    (base / ".git").mkdir()


def _mk_manifest(base: Path, body: str) -> None:
    doc_h = base / "doc" / "harness"
    doc_h.mkdir(parents=True, exist_ok=True)
    (doc_h / "manifest.yaml").write_text(body, encoding="utf-8")


class TestEnvironmentSnapshot(unittest.TestCase):

    def test_happy_path_writes_required_sections(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _mk_git_repo(base)
            _mk_manifest(base, (
                'test_command: "python3 -m pytest"\n'
                'build_command: "make"\n'
                'dev_command: ""\n'
                'project_meta:\n  shape: library\n  ports: []\n'
                'tooling:\n  ast_grep_ready: true\n  lsp_ready: false\n'
                '  observability_ready: false\n  chrome_devtools_ready: false\n'
            ))
            task_dir = base / "task"
            task_dir.mkdir()
            path = env_snapshot_mod.snapshot(str(task_dir), str(base))
        self.assertTrue(path.endswith("ENVIRONMENT_SNAPSHOT.md"))
        # Re-read from cached tmpdir-relative path before tempdir cleanup
        # (note: we already exited the with block, so instead re-run inside):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _mk_git_repo(base)
            _mk_manifest(base, (
                'test_command: "python3 -m pytest"\n'
                'project_meta:\n  shape: library\n'
                'tooling:\n  ast_grep_ready: true\n'
            ))
            task_dir = base / "task"
            task_dir.mkdir()
            path = env_snapshot_mod.snapshot(str(task_dir), str(base))
            body = Path(path).read_text(encoding="utf-8")
        self.assertIn("## Repo", body)
        self.assertIn("## Manifest", body)
        self.assertIn("## Tooling", body)
        self.assertIn("## Tool managers", body)
        self.assertIn("## Tool versions", body)
        self.assertIn("## Root entries", body)
        self.assertIn("python3 -m pytest", body)
        self.assertIn("ast_grep_ready: true", body)
        self.assertIn("project_shape: `library`", body)
        self.assertIn("branch: `not-probed`", body)

    def test_snapshot_does_not_call_git_or_render_dirty_bit(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _mk_git_repo(base)
            (base / "new.txt").write_text("uncommitted\n")
            task_dir = base / "task"
            task_dir.mkdir()

            calls: list[list[str]] = []
            real_run = subprocess.run

            def guarded_run(cmd, *args, **kwargs):
                calls.append(list(cmd))
                self.assertNotEqual(list(cmd)[:1], ["git"])
                return real_run(cmd, *args, **kwargs)

            with mock.patch.object(env_snapshot_mod.subprocess, "run", side_effect=guarded_run):
                path = env_snapshot_mod.snapshot(str(task_dir), str(base))

            body = Path(path).read_text(encoding="utf-8")
        self.assertNotIn("dirty:", body)
        self.assertFalse(any(call and call[0] == "git" for call in calls))

    def test_no_manifest_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _mk_git_repo(base)  # no manifest
            task_dir = base / "task"
            task_dir.mkdir()
            path = env_snapshot_mod.snapshot(str(task_dir), str(base))
            body = Path(path).read_text(encoding="utf-8")
        # Manifest fields render as empty strings, not crash
        self.assertIn("test_command: ``", body)
        self.assertIn("ast_grep_ready: unknown", body)
        self.assertIn("## Tool versions", body)

    def test_manifest_empty_scalars_preserve_existing_empty_semantics(self):
        fields = env_snapshot_mod._manifest_fields(
            "\n".join(
                [
                    "test_command: null",
                    "build_command: ~",
                    "dev_command:",
                    "smoke_command: []",
                    'healthcheck_command: ""',
                ]
            )
        )

        self.assertEqual(
            fields,
            {
                "test_command": "",
                "build_command": "",
                "dev_command": "",
                "smoke_command": "",
                "healthcheck_command": "",
                "project_shape": "",
            },
        )

    def test_tool_version_probe_is_bounded_and_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _mk_git_repo(base)
            task_dir = base / "task"
            task_dir.mkdir()

            calls: list[list[str]] = []

            def fake_run(cmd, *args, **kwargs):
                calls.append(list(cmd))
                self.assertLessEqual(kwargs.get("timeout", 99), 3)
                class Result:
                    returncode = 0
                    stdout = "tool 1.2.3\n"
                    stderr = ""
                return Result()

            with mock.patch.object(env_snapshot_mod.subprocess, "run", side_effect=fake_run):
                with mock.patch.object(env_snapshot_mod.shutil, "which", return_value="/bin/tool"):
                    path = env_snapshot_mod.snapshot(str(task_dir), str(base))

            body = Path(path).read_text(encoding="utf-8")
        flattened = [" ".join(c) for c in calls]
        self.assertTrue(all("install" not in c and "update" not in c for c in flattened))
        self.assertIn("activate:", body)
        self.assertIn("tool 1.2.3", body)

    def test_probe_budget_caps_timeouts_and_skips_after_exhaustion(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            task_dir = base / "task"
            task_dir.mkdir()
            now = [10.0]
            calls: list[tuple[list[str], float]] = []

            def fake_run(cmd, *args, **kwargs):
                timeout = kwargs["timeout"]
                calls.append((list(cmd), timeout))
                now[0] += min(1.5, timeout)

                class Result:
                    returncode = 0
                    stdout = "tool 1.2.3\n"
                    stderr = ""

                return Result()

            with mock.patch.object(
                env_snapshot_mod.time, "monotonic", side_effect=lambda: now[0]
            ):
                with mock.patch.object(
                    env_snapshot_mod.subprocess, "run", side_effect=fake_run
                ):
                    with mock.patch.object(
                        env_snapshot_mod.shutil, "which", return_value="/bin/tool"
                    ):
                        path = env_snapshot_mod.snapshot(str(task_dir), str(base))

            body = Path(path).read_text(encoding="utf-8")

        self.assertEqual(len(calls), 3)
        self.assertEqual([timeout for _, timeout in calls], [3.0, 2.5, 1.0])
        self.assertEqual(now[0], 14.0)
        self.assertIn("- volta: `missing`", body)
        self.assertNotIn("- git:", body)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO regression requires POSIX")
    def test_snapshot_rejects_fifo_manifest_without_blocking(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            manifest = base / "doc/harness/manifest.yaml"
            manifest.parent.mkdir(parents=True)
            os.mkfifo(manifest)
            task_dir = base / "task"
            task_dir.mkdir()

            started = time.monotonic()
            with (
                mock.patch.object(env_snapshot_mod, "_tool_managers", return_value={}),
                mock.patch.object(env_snapshot_mod, "_tool_versions", return_value={}),
            ):
                path = env_snapshot_mod.snapshot(str(task_dir), str(base))
            elapsed = time.monotonic() - started

            self.assertLess(elapsed, 1.0)
            body = Path(path).read_text(encoding="utf-8")
            self.assertIn("test_command: ``", body)
            self.assertIn("ast_grep_ready: unknown", body)

    def test_snapshot_replaces_symlink_leaf_without_touching_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            task_dir = base / "task"
            task_dir.mkdir()
            sentinel = base / "outside.txt"
            sentinel.write_text("keep", encoding="utf-8")
            output = task_dir / env_snapshot_mod.ARTIFACT_NAME
            output.symlink_to(sentinel)

            with (
                mock.patch.object(env_snapshot_mod, "_tool_managers", return_value={}),
                mock.patch.object(env_snapshot_mod, "_tool_versions", return_value={}),
            ):
                path = env_snapshot_mod.snapshot(str(task_dir), str(base))

            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")
            self.assertFalse(output.is_symlink())
            self.assertTrue(output.is_file())
            self.assertEqual(path, str(output))

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO regression requires POSIX")
    def test_snapshot_replaces_fifo_output_without_blocking(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            task_dir = base / "task"
            task_dir.mkdir()
            output = task_dir / env_snapshot_mod.ARTIFACT_NAME
            os.mkfifo(output)

            started = time.monotonic()
            with (
                mock.patch.object(env_snapshot_mod, "_tool_managers", return_value={}),
                mock.patch.object(env_snapshot_mod, "_tool_versions", return_value={}),
            ):
                path = env_snapshot_mod.snapshot(str(task_dir), str(base))
            elapsed = time.monotonic() - started

            self.assertLess(elapsed, 1.0)
            self.assertTrue(output.is_file())
            self.assertEqual(path, str(output))

    def test_raise_swallowed_returns_empty_string(self):
        # task_dir is a file, not a dir → os.makedirs inside snapshot raises
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _mk_git_repo(base)
            bad_path = base / "not-a-dir.txt"
            bad_path.write_text("x")
            # snapshot tries os.makedirs(task_dir) — that SUCCEEDS on existing file
            # when using exist_ok=False behaviour… actually os.makedirs with exist_ok=True
            # would raise FileExistsError only if the leaf is not a directory.
            # Force a real failure: pass None as task_dir.
            result = env_snapshot_mod.snapshot(None, str(base))
        self.assertEqual(result, "")

    def test_root_entries_capped(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _mk_git_repo(base)
            # Create 30 visible entries
            for i in range(30):
                (base / f"file_{i:02d}.txt").write_text("x")
            task_dir = base / "task"
            task_dir.mkdir()
            path = env_snapshot_mod.snapshot(str(task_dir), str(base))
            body = Path(path).read_text(encoding="utf-8")
        # Only first 20 should appear
        self.assertIn("file_00.txt", body)
        self.assertIn("file_19.txt", body)
        self.assertNotIn("file_20.txt", body)


if __name__ == "__main__":
    unittest.main()
