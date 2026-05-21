"""Tests for Claude subagent background registry helpers."""
from __future__ import annotations

import json
import os
import sys
import threading

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


def test_official_subagent_stop_fields_are_preserved(tmp_path):
    repo, task_dir = _repo(tmp_path)
    background_registry.register_subagent_start(
        repo,
        {
            "hook_event_name": "SubagentStart",
            "session_id": "sess-1",
            "agent_id": "agent-1",
            "agent_type": "general-purpose",
        },
        task_dir=task_dir,
    )
    stopped = background_registry.mark_subagent_stop(
        repo,
        {
            "hook_event_name": "SubagentStop",
            "session_id": "sess-1",
            "stop_hook_active": False,
            "agent_id": "agent-1",
            "agent_type": "general-purpose",
            "agent_transcript_path": "/tmp/agent.jsonl",
            "last_assistant_message": "done",
        },
    )

    assert stopped["status"] == "done"
    assert stopped["agent_type"] == "general-purpose"
    assert stopped["transcript_path"] == "/tmp/agent.jsonl"
    assert stopped["last_assistant_message"] == "done"


def test_missing_agent_id_does_not_create_false_active_record(tmp_path):
    repo, task_dir = _repo(tmp_path)
    record = background_registry.register_subagent_start(
        repo,
        {"session_id": "sess-1", "agent_type": "general-purpose"},
        task_dir=task_dir,
    )

    assert record["status"] == "ignored_start_missing_agent_id"
    assert record["reason"]
    assert background_registry.active_records(repo, task_id="TASK__bg", session_id="sess-1") == []
    with open(background_registry.registry_path(repo), encoding="utf-8") as f:
        data = json.load(f)
    assert data["records"][0]["status"] == "ignored_start_missing_agent_id"


def test_session_filter_does_not_match_default_records_for_other_sessions(tmp_path):
    repo, task_dir = _repo(tmp_path)
    background_registry.register_subagent_start(
        repo,
        {"session_id": "default", "agent_id": "agent-default"},
        task_dir=task_dir,
    )

    assert background_registry.active_records(repo, task_id="TASK__bg", session_id="sess-real") == []


def test_stale_active_record_is_marked_and_ignored(tmp_path):
    repo, task_dir = _repo(tmp_path)
    background_registry.register_subagent_start(
        repo,
        {"session_id": "sess-1", "agent_id": "agent-stale"},
        task_dir=task_dir,
    )
    path = background_registry.registry_path(repo)
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


def test_prune_marks_stale_and_caps_records(tmp_path):
    repo, task_dir = _repo(tmp_path)
    for i in range(5):
        background_registry.register_subagent_start(
            repo,
            {"session_id": "sess-1", "agent_id": f"agent-{i}"},
            task_dir=task_dir,
        )
    path = background_registry.registry_path(repo)
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    data["records"][0]["updated_ts"] = 1
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)

    background_registry.prune(repo, keep=3, stale_secs=1)

    with open(path, encoding="utf-8") as f:
        pruned = json.load(f)
    assert len(pruned["records"]) == 3
    assert all("agent-" in r["id"] for r in pruned["records"])


def test_unmatched_stop_records_nonblocking_diagnostic(tmp_path):
    repo, _task_dir = _repo(tmp_path)
    record = background_registry.mark_subagent_stop(
        repo,
        {
            "session_id": "sess-1",
            "agent_id": "agent-missing",
            "agent_type": "general-purpose",
            "agent_transcript_path": "/tmp/missing.jsonl",
        },
    )

    assert record["status"] == "unmatched_stop"
    assert record["agent_id"] == "agent-missing"
    assert record["transcript_path"] == "/tmp/missing.jsonl"
    assert background_registry.active_records(repo, task_id="TASK__bg", session_id="sess-1") == []


def test_stop_without_agent_id_does_not_close_random_active_record(tmp_path):
    repo, task_dir = _repo(tmp_path)
    background_registry.register_subagent_start(
        repo,
        {"session_id": "sess-1", "agent_id": "agent-active"},
        task_dir=task_dir,
    )

    record = background_registry.mark_subagent_stop(repo, {"session_id": "sess-1"})

    assert record["status"] == "unmatched_stop"
    active = background_registry.active_records(repo, task_id="TASK__bg", session_id="sess-1")
    assert len(active) == 1
    assert active[0]["id"] == "agent-active"


def test_concurrent_starts_do_not_lose_records(tmp_path):
    repo, task_dir = _repo(tmp_path)

    def start(i: int) -> None:
        background_registry.register_subagent_start(
            repo,
            {"session_id": "sess-1", "agent_id": f"agent-{i}"},
            task_dir=task_dir,
        )

    threads = [threading.Thread(target=start, args=(i,)) for i in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    active = background_registry.active_records(repo, task_id="TASK__bg", session_id="sess-1")
    assert {r["id"] for r in active} == {f"agent-{i}" for i in range(20)}
