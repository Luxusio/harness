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

    active_marker = os.path.join(tasks_dir, ".active")
    prev_active = None
    if os.path.isfile(active_marker):
        with open(active_marker, "r", encoding="utf-8") as f:
            prev_active = f.read()

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
        if prev_active is not None:
            with open(active_marker, "w", encoding="utf-8") as f:
                f.write(prev_active)
        else:
            try:
                os.unlink(active_marker)
            except OSError:
                pass


__all__ = [
    "REPO_ROOT",
    "SCRIPTS_DIR",
    "invoke_hook",
    "parse_decision",
    "scratch_task_in_real_repo",
]
