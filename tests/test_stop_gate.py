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
from pathlib import Path
from datetime import datetime, timedelta, timezone

from conftest import SCRIPTS_DIR

sys.path.insert(0, SCRIPTS_DIR)
import _lib  # noqa: E402

STOP_GATE = os.path.join(SCRIPTS_DIR, "stop_gate.py")


def _fake_repo(tmp_path, active_contents: str | None = None) -> str:
    """Create a tmp fake repo with a .git dir. Optionally write .active."""
    (tmp_path / ".git").mkdir()
    tasks_dir = tmp_path / "doc" / "harness" / "tasks"
    tasks_dir.mkdir(parents=True)
    (tmp_path / "doc" / "harness" / "manifest.yaml").write_text("type: test\n", encoding="utf-8")
    if active_contents is not None:
        (tasks_dir / ".active").write_text(active_contents, encoding="utf-8")
    return str(tmp_path)


def _run(cwd: str, stdin: str = "{}", env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    proc_env = os.environ.copy()
    proc_env.update(env or {})
    return subprocess.run(
        [sys.executable, STOP_GATE],
        input=stdin,
        capture_output=True,
        text=True,
        cwd=cwd,
        env=proc_env,
        timeout=5.0,
    )


def _write_claude_start(repo: str, task_id: str, session_id: str, *, ts: str = "") -> None:
    task_dir = os.path.join(repo, "doc", "harness", "tasks", task_id)
    os.makedirs(task_dir, exist_ok=True)
    control_path = Path(task_dir) / "TASK.json"
    if not control_path.exists():
        control_path.write_text(json.dumps({
            "run_id": _lib.new_uuid7(), "execution_mode": "standard",
            "required_lenses": ["review-code", "qa-cli"],
            "close_receipt_fingerprint": None,
        }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    run_id = _lib.read_task_control(task_dir)["run_id"]
    sessions = Path(repo) / "doc/harness/tasks/.active_sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    (sessions / f"{session_id}.json").write_text(json.dumps({
        "session_id": session_id, "task_dir": task_dir, "task_id": task_id,
        "run_id": run_id, "updated": _lib.now_iso(),
    }) + "\n", encoding="utf-8")
    (Path(repo) / "doc/harness/tasks/.active").write_text(
        task_dir + "\n", encoding="utf-8",
    )
    row = {
        "ts": ts or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "event": "started",
        "source": "claude_hook",
        "task_run_id": run_id,
        "runtime_id": f"claude:{session_id}:agent-bg",
        "agent_id": "agent-bg",
        "agent_type": "harness:qa-cli",
        "lens": "qa-cli",
        "verdict": "",
        "summary": "",
    }
    with open(os.path.join(task_dir, "RECEIPTS.jsonl"), "w", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")


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


def test_removed_goal_payload_capture_does_not_write_on_stop(tmp_path):
    repo = _fake_repo(tmp_path, active_contents=None)
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        '{"type":"system","content":"Goal set: GOAL TASK내용 감지 테스트"}\n',
        encoding="utf-8",
    )
    out_dir = tmp_path / "goal-probe"
    payload = {
        "hook_event_name": "Stop",
        "session_id": "claude-goal-test",
        "transcript_path": str(transcript),
    }

    result = _run(
        repo,
        stdin=json.dumps(payload),
        env={
            "HARNESS_CAPTURE_GOAL_PAYLOADS": "1",
            "HARNESS_GOAL_PAYLOAD_DIR": str(out_dir),
            "HARNESS_RUNTIME": "claude",
        },
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert not out_dir.exists()


def test_reason_contains_task_id_and_exits(tmp_path):
    """AC-003: reason names the active task_id and legitimate stop exits."""
    repo = _fake_repo(tmp_path, active_contents="TASK__alpha-beta-gamma\n")
    result = _run(repo)

    payload = json.loads(result.stdout)
    reason = payload["reason"]
    assert "TASK__alpha-beta-gamma" in reason, reason
    assert "task_close" in reason, reason
    assert "runtime_verdict=PASS" in reason, reason
    assert "harness:stop-judge" in reason, reason
    assert "BLOCKED_ENV" in reason, reason
    assert "cancel the task" not in reason, reason
    assert "AskUserQuestion" not in reason, reason


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
    (tmp_path / "doc" / "harness" / "manifest.yaml").write_text("type: test\n", encoding="utf-8")
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


def test_blocks_for_active_background_subagent_without_manual_command(tmp_path):
    """Unmatched receipts cause Stop to auto-wait, then block."""
    repo = _fake_repo(tmp_path, active_contents="TASK__with-bg\n")
    _write_claude_start(repo, "TASK__with-bg", "sess-bg")

    result = _run(
        repo,
        stdin=json.dumps({"session_id": "sess-bg", "hook_event_name": "Stop"}),
        env={"HARNESS_BACKGROUND_WAIT_SECS": "0"},
    )

    payload = json.loads(result.stdout)
    assert payload["decision"] == "block"
    reason = payload["reason"]
    assert "background subagent work still running" in reason
    assert "agent-bg" in reason
    assert "wait_background.py" not in reason
    assert payload.get("next_action_command", "") == ""


def test_stale_background_record_does_not_mask_normal_stop_gate(tmp_path):
    """Stale starts are ignored, so the existing open-task reason is emitted."""
    repo = _fake_repo(tmp_path, active_contents="TASK__stale-bg\n")
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat().replace("+00:00", "Z")
    _write_claude_start(repo, "TASK__stale-bg", "sess-stale", ts=old)

    result = _run(
        repo,
        stdin=json.dumps({"session_id": "sess-stale", "hook_event_name": "Stop"}),
        env={"HARNESS_BACKGROUND_WAIT_SECS": "0", "HARNESS_BACKGROUND_STALE_SECS": "1"},
    )

    payload = json.loads(result.stdout)
    assert payload["decision"] == "block"
    assert "task start -> plan -> develop -> QA -> close" in payload["reason"]
    assert "review and task_verify are internal close gates" in payload["reason"]
    assert "background subagent work still running" not in payload["reason"]


def test_malformed_receipt_stream_blocks_normal_stop(tmp_path):
    repo = _fake_repo(tmp_path, active_contents="TASK__bad-receipts\n")
    task_dir = os.path.join(repo, "doc", "harness", "tasks", "TASK__bad-receipts")
    os.makedirs(task_dir, exist_ok=True)
    _write_claude_start(repo, "TASK__bad-receipts", "sess-bad")
    with open(os.path.join(task_dir, "RECEIPTS.jsonl"), "w", encoding="utf-8") as handle:
        handle.write('{"legacy":true}\n')

    result = _run(
        repo,
        stdin=json.dumps({"session_id": "sess-bad", "hook_event_name": "Stop"}),
        env={"HARNESS_BACKGROUND_WAIT_SECS": "0"},
    )

    payload = json.loads(result.stdout)
    assert payload["decision"] == "block"
    assert "malformed or unsafe" in payload["reason"]
    assert "fresh task run" in payload["reason"]


def test_malformed_receipt_stream_blocks_recursive_stop(tmp_path):
    repo = _fake_repo(tmp_path, active_contents="TASK__bad-recursive\n")
    task_dir = os.path.join(repo, "doc", "harness", "tasks", "TASK__bad-recursive")
    os.makedirs(task_dir, exist_ok=True)
    _write_claude_start(repo, "TASK__bad-recursive", "sess-bad-recursive")
    with open(os.path.join(task_dir, "RECEIPTS.jsonl"), "w", encoding="utf-8") as handle:
        handle.write('{"legacy":true}\n')

    result = _run(repo, stdin=json.dumps({
        "session_id": "sess-bad-recursive", "hook_event_name": "Stop",
        "stop_hook_active": True,
    }))

    payload = json.loads(result.stdout)
    assert payload["decision"] == "block"
    assert "malformed or unsafe" in payload["reason"]


def test_stop_hook_active_with_active_background_silently_allows(tmp_path):
    """Recursive Stop hook continuation should not re-block while background work runs."""
    repo = _fake_repo(tmp_path, active_contents="TASK__recursive-bg\n")
    _write_claude_start(repo, "TASK__recursive-bg", "sess-1")

    result = _run(
        repo,
        stdin=json.dumps({"session_id": "sess-1", "hook_event_name": "Stop", "stop_hook_active": True}),
        env={"HARNESS_BACKGROUND_WAIT_SECS": "0"},
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_stop_hook_active_without_active_background_still_blocks_open_task(tmp_path):
    """Recursive Stop without active background records still protects the open task."""
    repo = _fake_repo(tmp_path, active_contents="TASK__recursive-no-bg\n")

    result = _run(
        repo,
        stdin=json.dumps({"session_id": "sess-1", "hook_event_name": "Stop", "stop_hook_active": True}),
        env={"HARNESS_BACKGROUND_WAIT_SECS": "0"},
    )

    payload = json.loads(result.stdout)
    assert payload["decision"] == "block"
    assert "task start -> plan -> develop -> QA -> close" in payload["reason"]
    assert "review and task_verify are internal close gates" in payload["reason"]
    assert "background subagent work still running" not in payload["reason"]


def test_missing_receipts_do_not_prescribe_receipt_only_reruns(tmp_path):
    """Stop guidance keeps unrun work possible but blocks receipt-only retries."""
    task_id = "TASK__missing-receipts"
    repo = _fake_repo(tmp_path, active_contents=task_id + "\n")
    task_dir = Path(repo) / "doc/harness/tasks" / task_id
    task_dir.mkdir(parents=True)
    (task_dir / "TASK.json").write_text(json.dumps({
        "run_id": _lib.new_uuid7(),
        "execution_mode": "standard",
        "required_lenses": ["review-code", "qa-cli"],
        "close_receipt_fingerprint": None,
    }), encoding="utf-8")
    (task_dir / "PLAN.md").write_text("# Plan\n", encoding="utf-8")

    result = _run(repo)

    payload = json.loads(result.stdout)
    action = payload["next_action_command"]
    assert "if a required review has not actually completed" in action
    assert "do not rerun review solely for a receipt" in action
    assert "do not rerun either lens" in action
    assert "stop-judge/task_blocked" in action
    assert "generic attestation-blocker reason" in action
