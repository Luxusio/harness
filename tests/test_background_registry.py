"""Tests for Claude subagent background registry helpers."""
from __future__ import annotations

import os
import sys

from conftest import SCRIPTS_DIR

sys.path.insert(0, SCRIPTS_DIR)
import background_registry  # noqa: E402


def _repo(tmp_path):
    (tmp_path / ".git").mkdir()
    tasks = tmp_path / "doc" / "harness" / "tasks"
    task_dir = tasks / "TASK__bg"
    task_dir.mkdir(parents=True)
    (tasks / ".active").write_text(str(task_dir), encoding="utf-8")
    return str(tmp_path), str(task_dir)


def test_subagent_start_and_stop_updates_registry(tmp_path):
    repo, task_dir = _repo(tmp_path)
    start = {
        "session_id": "sess-1",
        "agent_id": "agent-1",
        "agent_type": "harness:qa-cli",
    }
    record = background_registry.register_subagent_start(repo, start, task_dir=task_dir)

    assert record["status"] == "active"
    assert record["task_id"] == "TASK__bg"
    assert record["agent_type"] == "harness:qa-cli"
    assert background_registry.active_records(repo, task_id="TASK__bg", session_id="sess-1")

    stopped = background_registry.mark_subagent_stop(
        repo,
        {
            "session_id": "sess-1",
            "agent_id": "agent-1",
            "agent_transcript_path": "/tmp/transcript.jsonl",
        },
    )
    assert stopped["status"] == "done"
    assert stopped["transcript_path"] == "/tmp/transcript.jsonl"
    assert background_registry.active_records(repo, task_id="TASK__bg", session_id="sess-1") == []


def test_stale_active_record_is_marked_and_ignored(tmp_path):
    repo, task_dir = _repo(tmp_path)
    background_registry.register_subagent_start(
        repo,
        {"session_id": "sess-1", "agent_id": "agent-stale"},
        task_dir=task_dir,
    )
    path = background_registry.registry_path(repo)
    import json
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    data["records"][0]["updated_ts"] = 1
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)

    assert background_registry.active_records(
        repo,
        task_id="TASK__bg",
        session_id="sess-1",
        stale_secs=1,
    ) == []
    with open(path, encoding="utf-8") as f:
        updated = json.load(f)
    assert updated["records"][0]["status"] == "stale"


def test_wait_for_clear_returns_active_after_timeout(tmp_path):
    repo, task_dir = _repo(tmp_path)
    background_registry.register_subagent_start(
        repo,
        {"session_id": "sess-1", "agent_id": "agent-active"},
        task_dir=task_dir,
    )
    result = background_registry.wait_for_clear(
        repo,
        task_id="TASK__bg",
        session_id="sess-1",
        timeout_secs=0,
    )
    assert result["cleared"] is False
    assert result["active"][0]["id"] == "agent-active"
