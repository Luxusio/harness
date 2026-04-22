"""AC-001 + AC-002: prewrite_gate dormant-repo fail-open + regression guard.

Tests use a temporary directory as a fake repo root — they do NOT touch the
real repo's tasks dir or .active pointer.  Each test builds the minimal
harness scaffold (manifest.yaml + tasks dir) inside a tempdir, then invokes
prewrite_gate.py via subprocess with a Write payload for a .py file.

Failure modes tested:
  AC-001 — no open tasks → fail-open (exit 0, no deny JSON)
  AC-002 — open task present but .active absent → still deny no-active-task
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATE = os.path.join(REPO_ROOT, "plugin", "scripts", "prewrite_gate.py")


def _invoke(tmpdir: str, file_path: str) -> subprocess.CompletedProcess:
    """Invoke prewrite_gate.py with a Write payload inside tmpdir as cwd."""
    payload = json.dumps({
        "tool_name": "Write",
        "tool_input": {"file_path": file_path, "content": "x = 1\n"},
    })
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_ROOT"] = os.path.join(REPO_ROOT, "plugin")
    # Ensure skip env is NOT set so default gate behaviour applies.
    env.pop("HARNESS_SKIP_PREWRITE", None)
    return subprocess.run(
        [sys.executable, GATE],
        input=payload,
        capture_output=True,
        text=True,
        cwd=tmpdir,
        env=env,
        timeout=10,
    )


def _parse_decision(stdout: str):
    """Return (decision, reason) or (None, None) from hook stdout."""
    if not stdout or not stdout.strip():
        return None, None
    try:
        data = json.loads(stdout)
        hso = data.get("hookSpecificOutput") or {}
        return hso.get("permissionDecision"), hso.get("permissionDecisionReason")
    except Exception:
        return None, None


def _scaffold(tmpdir: str) -> str:
    """Create minimal harness scaffold; return absolute path to tasks_dir."""
    manifest_dir = os.path.join(tmpdir, "doc", "harness")
    os.makedirs(manifest_dir, exist_ok=True)
    # Touch manifest.yaml so find_repo_root recognises tmpdir as repo root.
    open(os.path.join(manifest_dir, "manifest.yaml"), "w").close()
    tasks_dir = os.path.join(manifest_dir, "tasks")
    os.makedirs(tasks_dir, exist_ok=True)
    return tasks_dir


def _write_task_state(tasks_dir: str, task_name: str, status: str) -> str:
    """Create TASK__<task_name>/TASK_STATE.yaml with given status. Returns task dir."""
    task_dir = os.path.join(tasks_dir, f"TASK__{task_name}")
    os.makedirs(task_dir, exist_ok=True)
    state_path = os.path.join(task_dir, "TASK_STATE.yaml")
    with open(state_path, "w") as f:
        f.write(f"task_id: TASK__{task_name}\n")
        f.write(f"status: {status}\n")
        f.write("runtime_verdict: pending\n")
    return task_dir


class TestDormantRepoFailsOpen(unittest.TestCase):
    """AC-001: empty tasks dir → fail-open."""

    def test_dormant_repo_fails_open(self):
        """Empty tasks dir + no .active → gate allows silently (exit 0, no deny)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            _scaffold(tmpdir)
            target = os.path.join(tmpdir, "foo.py")
            r = _invoke(tmpdir, target)
            self.assertEqual(r.returncode, 0)
            decision, _ = _parse_decision(r.stdout)
            self.assertNotEqual(decision, "deny",
                                f"Expected fail-open but got deny. stdout={r.stdout!r}")

    def test_dormant_repo_with_closed_tasks_fails_open(self):
        """tasks dir contains only a closed task → fail-open."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = _scaffold(tmpdir)
            _write_task_state(tasks_dir, "old", "closed")
            target = os.path.join(tmpdir, "foo.py")
            r = _invoke(tmpdir, target)
            self.assertEqual(r.returncode, 0)
            decision, _ = _parse_decision(r.stdout)
            self.assertNotEqual(decision, "deny",
                                f"closed task should fail-open. stdout={r.stdout!r}")

    def test_dormant_repo_with_stale_tasks_fails_open(self):
        """tasks dir contains only a stale task → fail-open."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = _scaffold(tmpdir)
            _write_task_state(tasks_dir, "old", "stale")
            target = os.path.join(tmpdir, "foo.py")
            r = _invoke(tmpdir, target)
            self.assertEqual(r.returncode, 0)
            decision, _ = _parse_decision(r.stdout)
            self.assertNotEqual(decision, "deny",
                                f"stale task should fail-open. stdout={r.stdout!r}")


class TestOpenTaskWithoutActivePointerDenies(unittest.TestCase):
    """AC-002: open task exists but .active absent → still deny no-active-task."""

    def test_open_task_without_active_pointer_still_denies(self):
        """TASK__wip status=created + no .active → deny with no-active-task reason."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = _scaffold(tmpdir)
            _write_task_state(tasks_dir, "wip", "created")
            # No .active file written.
            target = os.path.join(tmpdir, "foo.py")
            r = _invoke(tmpdir, target)
            self.assertEqual(r.returncode, 0)
            decision, reason = _parse_decision(r.stdout)
            self.assertEqual(decision, "deny",
                             f"Expected deny but got: stdout={r.stdout!r}")
            self.assertIn("no-active-task", reason or "",
                          f"Expected no-active-task in reason: {reason!r}")

    def test_mixed_closed_and_open_still_denies(self):
        """One closed + one created → open task triggers deny."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = _scaffold(tmpdir)
            _write_task_state(tasks_dir, "done", "closed")
            _write_task_state(tasks_dir, "wip", "created")
            # No .active file.
            target = os.path.join(tmpdir, "foo.py")
            r = _invoke(tmpdir, target)
            self.assertEqual(r.returncode, 0)
            decision, reason = _parse_decision(r.stdout)
            self.assertEqual(decision, "deny",
                             f"Mixed tasks should deny. stdout={r.stdout!r}")
            self.assertIn("no-active-task", reason or "",
                          f"Expected no-active-task in reason: {reason!r}")


if __name__ == "__main__":
    unittest.main()
