"""Shared test fixtures for harness gate tests.

Exports:
  REPO_ROOT            — repo root (directory containing this file's parent)
  SCRIPTS_DIR          — plugin/scripts absolute path
  invoke_hook(...)     — subprocess runner for a hook script with stdin JSON
  scratch_task_in_real_repo(...) — context manager creating a scratch task dir
                         with clean finally removal (no leaks on exception)
"""
from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import sys
import uuid


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(REPO_ROOT, "plugin", "scripts")


def invoke_hook(
    script_path: str,
    tool_name: str,
    tool_input: dict | None = None,
    *,
    env_extra: dict | None = None,
    cwd: str | None = None,
    timeout: float = 5.0,
):
    """Run a hook script with a crafted PreToolUse payload on stdin."""
    payload = json.dumps({
        "tool_name": tool_name,
        "tool_input": tool_input or {},
    })
    env = os.environ.copy()
    env.setdefault("CLAUDE_PLUGIN_ROOT", os.path.join(REPO_ROOT, "plugin"))
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, script_path],
        input=payload,
        capture_output=True,
        text=True,
        cwd=cwd or REPO_ROOT,
        env=env,
        timeout=timeout,
    )


def parse_decision(stdout: str):
    """Parse a hook's stdout JSON envelope. Returns (decision, reason) or (None, None)."""
    if not stdout or not stdout.strip():
        return None, None
    try:
        data = json.loads(stdout)
        hso = data.get("hookSpecificOutput") or {}
        return hso.get("permissionDecision"), hso.get("permissionDecisionReason")
    except Exception:
        return None, None


@contextlib.contextmanager
def scratch_task_in_real_repo(
    name: str = "scratch-test",
    *,
    plan: bool = True,
    maintenance: bool = False,
    progress: str | None = None,
    repo_root: str | None = None,
):
    """Create a scratch task dir under the real repo's doc/harness/tasks/.

    The task is removed in ``finally`` — no leaks even on test exception.
    Also writes ``.active`` pointing at this task for the duration.
    """
    root = repo_root or REPO_ROOT
    tasks_dir = os.path.join(root, "doc", "harness", "tasks")
    task_id = f"TASK__{name}"
    task_dir = os.path.join(tasks_dir, task_id)
    os.makedirs(task_dir, exist_ok=True)

    # Save the real .active out of the way via atomic os.rename to a unique
    # sidecar path. In-memory save (the previous approach) was lost whenever an
    # exception fired between capture and restore — corrupting the harness's
    # canonical focus marker. If the process is SIGKILLed mid-fixture, the
    # sidecar `.active.fixture-backup.<pid>.<uuid>` survives and can be
    # restored manually with `mv`.
    active_marker = os.path.join(tasks_dir, ".active")
    backup_marker = (
        f"{active_marker}.fixture-backup.{os.getpid()}.{uuid.uuid4().hex[:8]}"
    )
    had_existing = os.path.isfile(active_marker)
    if had_existing:
        os.rename(active_marker, backup_marker)

    try:
        if plan:
            with open(os.path.join(task_dir, "PLAN.md"), "w", encoding="utf-8") as f:
                f.write("# Test plan\n")
        if maintenance:
            open(os.path.join(task_dir, "MAINTENANCE"), "w").close()
        if progress is not None:
            with open(os.path.join(task_dir, "PROGRESS.md"), "w", encoding="utf-8") as f:
                f.write(progress)
        with open(active_marker, "w", encoding="utf-8") as f:
            f.write(task_dir)
        yield task_dir
    finally:
        shutil.rmtree(task_dir, ignore_errors=True)
        try:
            os.unlink(active_marker)
        except OSError:
            pass
        if had_existing and os.path.isfile(backup_marker):
            os.rename(backup_marker, active_marker)


_SESSION_ACTIVE_BACKUP: str | None = None


def pytest_sessionstart(session):
    """Snapshot the real repo's `.active` marker at session start via atomic
    rename to a sidecar path. Restored in `pytest_sessionfinish`.

    This is a safety net for test paths that mutate `.active` outside the
    `scratch_task_in_real_repo` fixture — notably `test_harness_mcp_server.py`,
    which calls `task_close` against the real `find_repo_root()` and removes
    the marker as a side effect of the close protocol.

    The fixture-level save/restore (`scratch_task_in_real_repo`) remains the
    primary line of defense for normal cases; this hook catches everything
    else with a single move.
    """
    global _SESSION_ACTIVE_BACKUP
    active_path = os.path.join(REPO_ROOT, "doc", "harness", "tasks", ".active")
    if not os.path.isfile(active_path):
        _SESSION_ACTIVE_BACKUP = None
        return
    backup = (
        f"{active_path}.session-backup.{os.getpid()}.{uuid.uuid4().hex[:8]}"
    )
    try:
        os.rename(active_path, backup)
        _SESSION_ACTIVE_BACKUP = backup
    except OSError:
        _SESSION_ACTIVE_BACKUP = None


def pytest_sessionfinish(session, exitstatus):
    """Restore the snapshotted `.active` from `pytest_sessionstart`.

    Best-effort: if a test process was hard-killed mid-suite, the sidecar
    file (`.active.session-backup.<pid>.<uuid>`) survives and the human can
    `mv` it back manually.
    """
    global _SESSION_ACTIVE_BACKUP
    backup = _SESSION_ACTIVE_BACKUP
    _SESSION_ACTIVE_BACKUP = None
    if not backup or not os.path.isfile(backup):
        return
    active_path = os.path.join(REPO_ROOT, "doc", "harness", "tasks", ".active")
    # Clear any in-flight scratch marker first so rename can succeed.
    try:
        os.unlink(active_path)
    except OSError:
        pass
    try:
        os.rename(backup, active_path)
    except OSError:
        pass


__all__ = [
    "REPO_ROOT",
    "SCRIPTS_DIR",
    "invoke_hook",
    "parse_decision",
    "scratch_task_in_real_repo",
]
