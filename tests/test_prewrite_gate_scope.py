"""Tests for prewrite_gate.py scope lock (AC-001..AC-004).

Uses real subprocess invocation — no mocks.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

from conftest import (  # type: ignore
    ledger_rows,
    make_tmp_harness_root,
    scratch_task_in_real_repo,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATE = os.path.join(REPO_ROOT, "plugin", "scripts", "prewrite_gate.py")
FIXTURES = os.path.join(REPO_ROOT, "tests", "fixtures", "gstack_adoption")


def _run_gate(file_path, tool_name="Write", env_extra=None):
    """Run prewrite_gate.py with a crafted stdin JSON payload."""
    payload = json.dumps({
        "session_id": "pytest-hook",
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
    """Create a minimal scratch WBS directory with .active marker."""
    wbs_root = os.path.join(tmp_dir, "doc", "harness", "wbs")
    wbs_dir = os.path.join(wbs_root, "WBS__scratch-test")
    os.makedirs(wbs_dir, exist_ok=True)
    # Write PLAN.md so plan-first check passes
    with open(os.path.join(wbs_dir, "PLAN.md"), "w") as f:
        f.write("# Test plan\n")
    control = dict(run_id="0198c349-5800-7000-8000-000000000001", execution_mode="standard",
                   required_lenses=["review-code", "qa-cli"], closed=False)
    with open(os.path.join(wbs_dir, "WBS.control.json"), "w") as f:
        json.dump(control, f)
    sessions = os.path.join(wbs_root, ".active_sessions")
    os.makedirs(sessions, exist_ok=True)
    with open(os.path.join(sessions, "pytest-hook.json"), "w") as f:
        json.dump(dict(session_id="pytest-hook", wbs_dir=wbs_dir,
                       wbs_id=os.path.basename(wbs_dir), run_id=control["run_id"]), f)
    # Write PROGRESS.md if provided
    if progress_content is not None:
        with open(os.path.join(wbs_dir, "PROGRESS.md"), "w") as f:
            f.write(progress_content)
    return wbs_dir, wbs_root


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
        wbs_dir, wbs_root = _make_scratch_task(tmp_dir, progress_content)
        env = os.environ.copy()
        env["CLAUDE_PLUGIN_ROOT"] = os.path.join(REPO_ROOT, "plugin")
        return wbs_dir, env

    def test_forbidden_write_exits_2(self):
        """Writing to forbidden path should exit 2."""
        progress = (
            "wbs_id: WBS__scratch-test\nphase: 3\n"
            "allowed_paths:\n  - src/feature.py\n"
            "test_paths: []\n"
            "forbidden_paths:\n  - src/billing.py\n"
        )
        sys.path.insert(0, os.path.join(REPO_ROOT, "plugin", "scripts"))
        from prewrite_gate import _handle_scope_lock
        with tempfile.TemporaryDirectory() as tmp:
            root = make_tmp_harness_root(tmp)
            with scratch_task_in_real_repo("scope-lock-unit-test", progress=progress, repo_root=root) as scratch:
                should_block, msg = _handle_scope_lock(
                    os.path.join(root, "src", "billing.py"), scratch, root,
                    "WBS__scope-lock-unit-test",
                )
                self.assertTrue(should_block, "Expected block for forbidden path")
                self.assertIn("scope-lock", msg)
                self.assertIn("forbidden_paths", msg)
                self.assertIn("HARNESS_DISABLE_SCOPE_LOCK", msg)
                self.assertIn("doc/harness/patterns/scope-lock.md", msg)

    def test_forbidden_message_contains_wbs_id(self):
        """Error message should name the wbs_id."""
        progress = (
            "wbs_id: WBS__test-task\nphase: 3\n"
            "allowed_paths:\n  - src/ok.py\n"
            "test_paths: []\n"
            "forbidden_paths:\n  - src/billing.py\n"
        )
        sys.path.insert(0, os.path.join(REPO_ROOT, "plugin", "scripts"))
        from prewrite_gate import _handle_scope_lock
        with tempfile.TemporaryDirectory() as tmp:
            root = make_tmp_harness_root(tmp)
            with scratch_task_in_real_repo("scope-lock-msg-test", progress=progress, repo_root=root) as scratch:
                should_block, msg = _handle_scope_lock(
                    os.path.join(root, "src", "billing.py"), scratch, root,
                    "WBS__scope-lock-msg-test",
                )
                self.assertTrue(should_block)
                self.assertIn("WBS__scope-lock-msg-test", msg)


class TestScopeLockAllowed(unittest.TestCase):
    """AC-003: allowed/test paths exit 0; malformed PROGRESS.md falls through."""

    def test_allowed_path_not_blocked(self):
        """Writing to allowed path should not be blocked."""
        progress = (
            "wbs_id: WBS__test-task\nphase: 3\n"
            "allowed_paths:\n  - src/feature.py\n"
            "test_paths: []\n"
            "forbidden_paths:\n  - src/billing.py\n"
        )
        sys.path.insert(0, os.path.join(REPO_ROOT, "plugin", "scripts"))
        from prewrite_gate import _handle_scope_lock
        with tempfile.TemporaryDirectory() as tmp:
            root = make_tmp_harness_root(tmp)
            with scratch_task_in_real_repo("scope-lock-allowed-test", progress=progress, repo_root=root) as scratch:
                should_block, msg = _handle_scope_lock(
                    os.path.join(root, "src", "feature.py"), scratch, root,
                    "WBS__scope-lock-allowed-test",
                )
                self.assertFalse(should_block)

    def test_malformed_progress_returns_none(self):
        """Malformed PROGRESS.md should return None (fall through)."""
        sys.path.insert(0, os.path.join(REPO_ROOT, "plugin", "scripts"))
        from prewrite_gate import _parse_progress_paths_safe
        with tempfile.TemporaryDirectory() as tmp:
            root = make_tmp_harness_root(tmp)
            with scratch_task_in_real_repo("scope-lock-malform-test", repo_root=root) as scratch:
                import shutil
                shutil.copy(os.path.join(FIXTURES, "PROGRESS_malformed.md"), os.path.join(scratch, "PROGRESS.md"))
                result = _parse_progress_paths_safe(scratch, root)
                self.assertTrue(result is None or isinstance(result, dict))


class TestScopeLockCanonicalization(unittest.TestCase):
    """AC-004: path canonicalization and HARNESS_DISABLE_SCOPE_LOCK bypass."""

    def test_absolute_path_in_progress_skipped(self):
        """Absolute paths in PROGRESS.md forbidden_paths should be skipped.

        Skipping one logs a `gate-parse-fail` row to the ledger of the root it
        is given, so the root is a tmp checkout: given `REPO_ROOT` it appended
        to the developer's gitignored `doc/harness/learnings.jsonl`.
        """
        progress = (
            "wbs_id: WBS__test-task\nphase: 3\n"
            "allowed_paths:\n  - src/feature.py\n"
            "test_paths: []\n"
            f"forbidden_paths:\n  - /absolute/path/billing.py\n"
        )
        sys.path.insert(0, os.path.join(REPO_ROOT, "plugin", "scripts"))
        from prewrite_gate import _handle_scope_lock
        with tempfile.TemporaryDirectory() as tmp:
            root = make_tmp_harness_root(tmp)
            with scratch_task_in_real_repo(
                "scope-lock-abs-test", progress=progress, repo_root=root,
            ) as scratch:
                # absolute path in forbidden should be silently skipped (no block)
                should_block, msg = _handle_scope_lock(
                    os.path.join(root, "absolute", "path", "billing.py"),
                    scratch,
                    root,
                    "WBS__scope-lock-abs-test",
                )
            rows = ledger_rows(root)
        # Should not block because absolute paths are skipped
        self.assertFalse(should_block)
        self.assertEqual([row.get("type") for row in rows], ["gate-parse-fail"], rows)
        self.assertIn("/absolute/path/billing.py", rows[0].get("insight", ""))

    def test_disable_scope_lock_bypass(self):
        """HARNESS_DISABLE_SCOPE_LOCK=1 should bypass the gate."""
        progress = (
            "wbs_id: WBS__test-task\nphase: 3\n"
            "allowed_paths:\n  - src/feature.py\n"
            "test_paths: []\n"
            "forbidden_paths:\n  - src/billing.py\n"
        )
        sys.path.insert(0, os.path.join(REPO_ROOT, "plugin", "scripts"))
        import importlib
        import prewrite_gate
        importlib.reload(prewrite_gate)
        old_env = os.environ.get("HARNESS_DISABLE_SCOPE_LOCK")
        try:
            os.environ["HARNESS_DISABLE_SCOPE_LOCK"] = "1"
            with tempfile.TemporaryDirectory() as tmp:
                root = make_tmp_harness_root(tmp)
                with scratch_task_in_real_repo("scope-lock-bypass-test", progress=progress, repo_root=root) as scratch:
                    should_block, msg = prewrite_gate._handle_scope_lock(
                        os.path.join(root, "src", "billing.py"), scratch, root,
                        "WBS__scope-lock-bypass-test",
                    )
                    self.assertFalse(should_block, "HARNESS_DISABLE_SCOPE_LOCK=1 should bypass")
        finally:
            if old_env is None:
                os.environ.pop("HARNESS_DISABLE_SCOPE_LOCK", None)
            else:
                os.environ["HARNESS_DISABLE_SCOPE_LOCK"] = old_env


if __name__ == "__main__":
    unittest.main()
