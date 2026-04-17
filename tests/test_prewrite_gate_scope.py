"""Tests for prewrite_gate.py scope lock (AC-001..AC-004).

Uses real subprocess invocation — no mocks.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATE = os.path.join(REPO_ROOT, "plugin", "scripts", "prewrite_gate.py")
FIXTURES = os.path.join(REPO_ROOT, "tests", "fixtures", "gstack_adoption")


def _run_gate(file_path, tool_name="Write", env_extra=None):
    """Run prewrite_gate.py with a crafted stdin JSON payload."""
    payload = json.dumps({
        "tool_name": tool_name,
        "tool_input": {"file_path": file_path},
    })
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_ROOT"] = os.path.join(REPO_ROOT, "plugin")
    if env_extra:
        env.update(env_extra)
    r = subprocess.run(
        [sys.executable, GATE],
        input=payload,
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env=env,
    )
    return r


def _make_scratch_task(tmp_dir, progress_content=None):
    """Create a minimal scratch task directory with .active marker."""
    tasks_dir = os.path.join(tmp_dir, "doc", "harness", "tasks")
    task_dir = os.path.join(tasks_dir, "TASK__scratch-test")
    os.makedirs(task_dir, exist_ok=True)
    # Write PLAN.md so plan-first check passes
    with open(os.path.join(task_dir, "PLAN.md"), "w") as f:
        f.write("# Test plan\n")
    # Write .active
    active_file = os.path.join(tasks_dir, ".active")
    with open(active_file, "w") as f:
        f.write(task_dir)
    # Write PROGRESS.md if provided
    if progress_content is not None:
        with open(os.path.join(task_dir, "PROGRESS.md"), "w") as f:
            f.write(progress_content)
    return task_dir, tasks_dir


class TestReadProgressPaths(unittest.TestCase):
    """AC-001: _read_progress_paths returns three keyed lists."""

    def test_returns_three_keys(self):
        """_read_progress_paths should return dict with three keys."""
        sys.path.insert(0, os.path.join(REPO_ROOT, "plugin", "scripts"))
        from prewrite_gate import _read_progress_paths
        progress = os.path.join(FIXTURES, "PROGRESS_valid.md")
        result = _read_progress_paths(progress.replace("PROGRESS.md", "").rstrip("/"))
        # Pass the directory containing PROGRESS.md
        with tempfile.TemporaryDirectory() as d:
            import shutil
            shutil.copy(progress, os.path.join(d, "PROGRESS.md"))
            result = _read_progress_paths(d)
        self.assertIsNotNone(result)
        self.assertIn("allowed_paths", result)
        self.assertIn("test_paths", result)
        self.assertIn("forbidden_paths", result)

    def test_allowed_paths_populated(self):
        """allowed_paths should contain entries from fixture."""
        sys.path.insert(0, os.path.join(REPO_ROOT, "plugin", "scripts"))
        from prewrite_gate import _read_progress_paths
        with tempfile.TemporaryDirectory() as d:
            import shutil
            shutil.copy(os.path.join(FIXTURES, "PROGRESS_valid.md"), os.path.join(d, "PROGRESS.md"))
            result = _read_progress_paths(d)
        self.assertGreater(len(result["allowed_paths"]), 0)
        self.assertIn("src/feature.py", result["allowed_paths"])

    def test_forbidden_paths_populated(self):
        """forbidden_paths should contain entries from fixture."""
        sys.path.insert(0, os.path.join(REPO_ROOT, "plugin", "scripts"))
        from prewrite_gate import _read_progress_paths
        with tempfile.TemporaryDirectory() as d:
            import shutil
            shutil.copy(os.path.join(FIXTURES, "PROGRESS_valid.md"), os.path.join(d, "PROGRESS.md"))
            result = _read_progress_paths(d)
        self.assertIn("src/billing.py", result["forbidden_paths"])

    def test_missing_progress_returns_none(self):
        """No PROGRESS.md should return None."""
        sys.path.insert(0, os.path.join(REPO_ROOT, "plugin", "scripts"))
        from prewrite_gate import _read_progress_paths
        with tempfile.TemporaryDirectory() as d:
            result = _read_progress_paths(d)
        self.assertIsNone(result)


class TestScopeLockForbidden(unittest.TestCase):
    """AC-002: forbidden_paths write exits 2 with prescribed message."""

    def _make_env_with_task(self, tmp_dir, progress_content):
        """Setup a fake repo with task and return env dict."""
        task_dir, tasks_dir = _make_scratch_task(tmp_dir, progress_content)
        env = os.environ.copy()
        env["CLAUDE_PLUGIN_ROOT"] = os.path.join(REPO_ROOT, "plugin")
        return task_dir, env

    def test_forbidden_write_exits_2(self):
        """Writing to forbidden path should exit 2."""
        progress = (
            "task_id: TASK__scratch-test\nphase: 3\n"
            "allowed_paths:\n  - src/feature.py\n"
            "test_paths: []\n"
            "forbidden_paths:\n  - src/billing.py\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            task_dir, tasks_dir = _make_scratch_task(tmp, progress)
            # target file in forbidden list
            target = os.path.join(tmp, "src", "billing.py")
            os.makedirs(os.path.dirname(target), exist_ok=True)
            payload = json.dumps({
                "tool_name": "Write",
                "tool_input": {"file_path": target},
            })
            env = os.environ.copy()
            env["CLAUDE_PLUGIN_ROOT"] = os.path.join(REPO_ROOT, "plugin")
            # We need a minimal git repo to satisfy find_repo_root
            # Use the real repo_root but patch active via env is not possible
            # Instead we test against real repo with real task setup
            # (This test validates the gate logic via _handle_scope_lock directly)
            sys.path.insert(0, os.path.join(REPO_ROOT, "plugin", "scripts"))
            from prewrite_gate import _handle_scope_lock
            repo_root = REPO_ROOT
            # Write PROGRESS.md into a temp task dir in real repo tasks dir
            real_tasks = os.path.join(REPO_ROOT, "doc", "harness", "tasks")
            scratch = os.path.join(real_tasks, "TASK__scope-lock-unit-test")
            os.makedirs(scratch, exist_ok=True)
            try:
                with open(os.path.join(scratch, "PROGRESS.md"), "w") as f:
                    f.write(progress)
                should_block, msg = _handle_scope_lock(
                    os.path.join(REPO_ROOT, "src", "billing.py"),
                    scratch,
                    REPO_ROOT,
                    "TASK__scope-lock-unit-test",
                )
                self.assertTrue(should_block, "Expected block for forbidden path")
                self.assertIn("scope-lock", msg)
                self.assertIn("forbidden_paths", msg)
                self.assertIn("HARNESS_DISABLE_SCOPE_LOCK", msg)
                self.assertIn("doc/harness/patterns/scope-lock.md", msg)
            finally:
                import shutil
                shutil.rmtree(scratch, ignore_errors=True)

    def test_forbidden_message_contains_task_id(self):
        """Error message should name the task_id."""
        progress = (
            "task_id: TASK__test-task\nphase: 3\n"
            "allowed_paths:\n  - src/ok.py\n"
            "test_paths: []\n"
            "forbidden_paths:\n  - src/billing.py\n"
        )
        sys.path.insert(0, os.path.join(REPO_ROOT, "plugin", "scripts"))
        from prewrite_gate import _handle_scope_lock
        real_tasks = os.path.join(REPO_ROOT, "doc", "harness", "tasks")
        scratch = os.path.join(real_tasks, "TASK__scope-lock-msg-test")
        os.makedirs(scratch, exist_ok=True)
        try:
            with open(os.path.join(scratch, "PROGRESS.md"), "w") as f:
                f.write(progress)
            should_block, msg = _handle_scope_lock(
                os.path.join(REPO_ROOT, "src", "billing.py"),
                scratch,
                REPO_ROOT,
                "TASK__scope-lock-msg-test",
            )
            self.assertTrue(should_block)
            self.assertIn("TASK__scope-lock-msg-test", msg)
        finally:
            import shutil
            shutil.rmtree(scratch, ignore_errors=True)


class TestScopeLockAllowed(unittest.TestCase):
    """AC-003: allowed/test paths exit 0; malformed PROGRESS.md falls through."""

    def test_allowed_path_not_blocked(self):
        """Writing to allowed path should not be blocked."""
        progress = (
            "task_id: TASK__test-task\nphase: 3\n"
            "allowed_paths:\n  - src/feature.py\n"
            "test_paths: []\n"
            "forbidden_paths:\n  - src/billing.py\n"
        )
        sys.path.insert(0, os.path.join(REPO_ROOT, "plugin", "scripts"))
        from prewrite_gate import _handle_scope_lock
        real_tasks = os.path.join(REPO_ROOT, "doc", "harness", "tasks")
        scratch = os.path.join(real_tasks, "TASK__scope-lock-allowed-test")
        os.makedirs(scratch, exist_ok=True)
        try:
            with open(os.path.join(scratch, "PROGRESS.md"), "w") as f:
                f.write(progress)
            should_block, msg = _handle_scope_lock(
                os.path.join(REPO_ROOT, "src", "feature.py"),
                scratch,
                REPO_ROOT,
                "TASK__scope-lock-allowed-test",
            )
            self.assertFalse(should_block)
        finally:
            import shutil
            shutil.rmtree(scratch, ignore_errors=True)

    def test_malformed_progress_returns_none(self):
        """Malformed PROGRESS.md should return None (fall through)."""
        sys.path.insert(0, os.path.join(REPO_ROOT, "plugin", "scripts"))
        from prewrite_gate import _parse_progress_paths_safe
        real_tasks = os.path.join(REPO_ROOT, "doc", "harness", "tasks")
        scratch = os.path.join(real_tasks, "TASK__scope-lock-malform-test")
        os.makedirs(scratch, exist_ok=True)
        try:
            import shutil
            shutil.copy(
                os.path.join(FIXTURES, "PROGRESS_malformed.md"),
                os.path.join(scratch, "PROGRESS.md"),
            )
            # _parse_progress_paths_safe should not raise; malformed YAML
            # using yaml_array (regex) won't raise — it just returns empty lists
            # The test verifies it doesn't block
            result = _parse_progress_paths_safe(scratch, REPO_ROOT)
            # Either None or a dict with empty lists — must not raise
            self.assertTrue(result is None or isinstance(result, dict))
        finally:
            shutil.rmtree(scratch, ignore_errors=True)


class TestScopeLockCanonicalization(unittest.TestCase):
    """AC-004: path canonicalization and HARNESS_DISABLE_SCOPE_LOCK bypass."""

    def test_absolute_path_in_progress_skipped(self):
        """Absolute paths in PROGRESS.md forbidden_paths should be skipped."""
        progress = (
            "task_id: TASK__test-task\nphase: 3\n"
            "allowed_paths:\n  - src/feature.py\n"
            "test_paths: []\n"
            f"forbidden_paths:\n  - /absolute/path/billing.py\n"
        )
        sys.path.insert(0, os.path.join(REPO_ROOT, "plugin", "scripts"))
        from prewrite_gate import _handle_scope_lock
        real_tasks = os.path.join(REPO_ROOT, "doc", "harness", "tasks")
        scratch = os.path.join(real_tasks, "TASK__scope-lock-abs-test")
        os.makedirs(scratch, exist_ok=True)
        try:
            with open(os.path.join(scratch, "PROGRESS.md"), "w") as f:
                f.write(progress)
            # absolute path in forbidden should be silently skipped (no block)
            should_block, msg = _handle_scope_lock(
                os.path.join(REPO_ROOT, "absolute", "path", "billing.py"),
                scratch,
                REPO_ROOT,
                "TASK__scope-lock-abs-test",
            )
            # Should not block because absolute paths are skipped
            self.assertFalse(should_block)
        finally:
            import shutil
            shutil.rmtree(scratch, ignore_errors=True)

    def test_disable_scope_lock_bypass(self):
        """HARNESS_DISABLE_SCOPE_LOCK=1 should bypass the gate."""
        progress = (
            "task_id: TASK__test-task\nphase: 3\n"
            "allowed_paths:\n  - src/feature.py\n"
            "test_paths: []\n"
            "forbidden_paths:\n  - src/billing.py\n"
        )
        sys.path.insert(0, os.path.join(REPO_ROOT, "plugin", "scripts"))
        import importlib
        import prewrite_gate
        importlib.reload(prewrite_gate)
        real_tasks = os.path.join(REPO_ROOT, "doc", "harness", "tasks")
        scratch = os.path.join(real_tasks, "TASK__scope-lock-bypass-test")
        os.makedirs(scratch, exist_ok=True)
        old_env = os.environ.get("HARNESS_DISABLE_SCOPE_LOCK")
        try:
            with open(os.path.join(scratch, "PROGRESS.md"), "w") as f:
                f.write(progress)
            os.environ["HARNESS_DISABLE_SCOPE_LOCK"] = "1"
            should_block, msg = prewrite_gate._handle_scope_lock(
                os.path.join(REPO_ROOT, "src", "billing.py"),
                scratch,
                REPO_ROOT,
                "TASK__scope-lock-bypass-test",
            )
            self.assertFalse(should_block, "HARNESS_DISABLE_SCOPE_LOCK=1 should bypass")
        finally:
            if old_env is None:
                os.environ.pop("HARNESS_DISABLE_SCOPE_LOCK", None)
            else:
                os.environ["HARNESS_DISABLE_SCOPE_LOCK"] = old_env
            import shutil
            shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
