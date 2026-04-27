"""Regression tests for plugin/scripts/stop_gate.py.

Covers the four ACs in TASK__stop-hook-when-task-active:
  AC-001  block JSON emitted on stdout when .active exists
  AC-002  silent stdout when .active absent
  AC-003  reason names the task_id and the two legitimate exits
  AC-004  never raises on malformed / missing inputs
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

from conftest import SCRIPTS_DIR

STOP_GATE = os.path.join(SCRIPTS_DIR, "stop_gate.py")


def _fake_repo(tmp_path, active_contents: str | None = None) -> str:
    """Create a tmp fake repo with a .git dir. Optionally write .active."""
    (tmp_path / ".git").mkdir()
    tasks_dir = tmp_path / "doc" / "harness" / "tasks"
    tasks_dir.mkdir(parents=True)
    if active_contents is not None:
        (tasks_dir / ".active").write_text(active_contents, encoding="utf-8")
    return str(tmp_path)


def _run(cwd: str, stdin: str = "{}") -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, STOP_GATE],
        input=stdin,
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=5.0,
    )


def test_blocks_when_active(tmp_path):
    """AC-001: .active present → stdout is JSON with decision=block."""
    repo = _fake_repo(tmp_path, active_contents="TASK__example-active-task\n")
    result = _run(repo)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip(), "expected JSON on stdout"
    payload = json.loads(result.stdout)
    assert payload["decision"] == "block"
    assert isinstance(payload.get("reason"), str) and payload["reason"]


def test_silent_when_no_active(tmp_path):
    """AC-002: .active absent → empty stdout, exit 0."""
    repo = _fake_repo(tmp_path, active_contents=None)
    result = _run(repo)

    assert result.returncode == 0, result.stderr
    assert result.stdout == "", f"expected empty stdout, got {result.stdout!r}"


def test_reason_contains_task_id_and_exits(tmp_path):
    """AC-003: reason names the active task_id and both legitimate exits."""
    repo = _fake_repo(tmp_path, active_contents="TASK__alpha-beta-gamma\n")
    result = _run(repo)

    payload = json.loads(result.stdout)
    reason = payload["reason"]
    assert "TASK__alpha-beta-gamma" in reason, reason
    assert "task_close" in reason, reason
    assert "cancel" in reason.lower(), reason


def test_reason_handles_full_path_active(tmp_path):
    """AC-003: .active written as a full task_dir path (the MCP's format) still
    surfaces just the TASK__ basename in the reason, not the whole path."""
    full_path = str(tmp_path / "doc" / "harness" / "tasks" / "TASK__from-path")
    repo = _fake_repo(tmp_path, active_contents=full_path + "\n")
    result = _run(repo)

    payload = json.loads(result.stdout)
    reason = payload["reason"]
    assert "TASK__from-path" in reason, reason
    # Full path should not appear verbatim — only the basename.
    assert full_path not in reason, reason


def test_safe_on_error(tmp_path):
    """AC-004: malformed input never raises — empty stdin + corrupt .active."""
    # .active is a directory, not a regular file → triggers read error path.
    (tmp_path / ".git").mkdir()
    tasks_dir = tmp_path / "doc" / "harness" / "tasks"
    tasks_dir.mkdir(parents=True)
    (tasks_dir / ".active").mkdir()  # directory in place of the expected file

    result = _run(str(tmp_path), stdin="")  # empty stdin, unreadable marker
    assert result.returncode == 0, result.stderr
    # .active exists but is a dir → os.path.isfile() is False → silent pass.
    assert result.stdout == "", f"expected silent exit, got {result.stdout!r}"

    # Second branch: .active is a file containing bytes that are not decodable.
    import shutil as _shutil
    _shutil.rmtree(str(tasks_dir / ".active"))
    (tasks_dir / ".active").write_bytes(b"\xff\xfe\xfd invalid utf-8\n")

    result2 = _run(str(tmp_path), stdin="{not valid json}")
    assert result2.returncode == 0, result2.stderr
    # Either a clean block JSON with fallback task_id, or empty stdout — both are acceptable;
    # the invariant is "no crash, exit 0, nothing on stderr from a raise".
    if result2.stdout.strip():
        payload = json.loads(result2.stdout)
        assert payload["decision"] == "block"
