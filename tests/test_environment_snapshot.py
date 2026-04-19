"""AC-001: environment_snapshot.py writes ENVIRONMENT_SNAPSHOT.md.

Covers required fields, dirty-bit reporting, no-manifest fallback, and
raise-swallowed behaviour.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT = REPO_ROOT / "plugin" / "scripts" / "environment_snapshot.py"

spec = importlib.util.spec_from_file_location("environment_snapshot", SNAPSHOT)
env_snapshot_mod = importlib.util.module_from_spec(spec)
sys.path.insert(0, str(REPO_ROOT / "plugin" / "scripts"))
spec.loader.exec_module(env_snapshot_mod)


def _mk_git_repo(base: Path) -> None:
    (base / ".git").mkdir()
    # Shim: environment_snapshot calls `git branch --show-current` and
    # `git status --porcelain` via subprocess. Actually init a real git repo
    # so these commands succeed.
    subprocess.run(["git", "init", "-q"], cwd=base, check=True)
    subprocess.run(["git", "-c", "user.email=a@b", "-c", "user.name=a",
                    "commit", "--allow-empty", "-qm", "init"], cwd=base, check=True)


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
        self.assertIn("## Root entries", body)
        self.assertIn("python3 -m pytest", body)
        self.assertIn("ast_grep_ready: true", body)
        self.assertIn("project_shape: `library`", body)

    def test_dirty_bit_reflects_porcelain_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _mk_git_repo(base)
            (base / "new.txt").write_text("uncommitted\n")
            task_dir = base / "task"
            task_dir.mkdir()
            path = env_snapshot_mod.snapshot(str(task_dir), str(base))
            body = Path(path).read_text(encoding="utf-8")
        self.assertIn("dirty: True", body)

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
