"""Tests for Claude subagent background registry helpers."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path

from conftest import SCRIPTS_DIR

sys.path.insert(0, SCRIPTS_DIR)
import background_registry  # noqa: E402
import _lib  # noqa: E402


def _repo(tmp_path):
    (tmp_path / ".git").mkdir()
    tasks = tmp_path / "doc" / "harness" / "tasks"
    task_dir = tasks / "TASK__bg"
    task_dir.mkdir(parents=True)
    _lib.ensure_task_scaffold(str(task_dir), "TASK__bg")
    (tasks / ".active").write_text(str(task_dir), encoding="utf-8")
    return str(tmp_path), str(task_dir)


def _mark_harness_enabled(repo: str) -> None:
    manifest = os.path.join(repo, "doc", "harness", "manifest.yaml")
    os.makedirs(os.path.dirname(manifest), exist_ok=True)
    with open(manifest, "w", encoding="utf-8") as f:
        f.write("type: test\n")


def _bind_session(repo: str, task_dir: str, session_id: str) -> None:
    _lib.write_active_marker(repo, task_dir, session_id=session_id)


def _write_agent_transcript(
    tmp_path: Path, monkeypatch, session_id: str, agent_id: str, final_message: str,
    *, agent_type: str | None = None,
) -> str:
    claude = tmp_path / ".claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude))
    path = claude / "projects/project" / session_id / "subagents" / f"agent-{agent_id}.jsonl"
    path.parent.mkdir(parents=True)
    task_dir = next((tmp_path / "doc/harness/tasks").glob("TASK__*"))
    run_started = datetime.fromisoformat(
        _lib.task_run_started_at(_lib.read_task_control(task_dir)).replace("Z", "+00:00")
    )
    timestamp = (run_started + timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
    transcript_type = agent_type or agent_id
    items = [
        {
            "timestamp": timestamp,
            "agentId": agent_id,
            "sessionId": session_id,
            "attachment": {
                "type": "hook_additional_context",
                "hookName": "SubagentStart",
                "hookEvent": "SubagentStart",
                "content": [f"Agent {transcript_type} started ({agent_id})"],
            },
        },
        {
            "timestamp": timestamp,
            "agentId": agent_id,
            "sessionId": session_id,
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": final_message}],
            },
        },
    ]
    path.write_text("".join(json.dumps(item) + "\n" for item in items), encoding="utf-8")
    return str(path)


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


def test_subagent_start_records_task_local_receipt(tmp_path):
    repo, task_dir = _repo(tmp_path)
    _bind_session(repo, task_dir, "sess-1")
    started = background_registry.register_subagent_start(
        repo,
        {
            "session_id": "sess-1",
            "agent_id": "agent-qa",
            "agent_type": "harness:qa-cli",
        },
        task_dir=task_dir,
    )

    receipt_path = os.path.join(task_dir, "RECEIPTS.jsonl")
    assert started["subagent_receipt_id"].startswith("subagent-")
    assert os.path.isfile(receipt_path)
    with open(receipt_path, encoding="utf-8") as f:
        lines = f.readlines()
    assert len(lines) == 1
    receipt = json.loads(lines[0])
    assert receipt["source"] == "subagent_start_hook"
    assert receipt["event"] == "started"
    assert receipt["agent_id"] == "agent-qa"
    assert receipt["agent_type"] == "harness:qa-cli"
    assert receipt["lens"] == "qa-cli"
    assert receipt["summary"] == "subagent start hook observed"

    stopped = background_registry.mark_subagent_stop(
        repo,
        {
            "session_id": "sess-1",
            "agent_id": "agent-qa",
            "agent_transcript_path": "/tmp/qa-transcript.jsonl",
            "last_assistant_message": "PASS focused checks",
        },
    )

    assert stopped["status"] == "done"
    with open(receipt_path, encoding="utf-8") as f:
        receipts = [json.loads(line) for line in f]
    assert len(receipts) == 2
    assert receipts[-1]["event"] == "completed"
    assert receipts[-1]["verdict"] == "PENDING"
    conversation = os.path.join(task_dir, "CONVERSATION.md")
    assert os.path.isfile(conversation)
    with open(conversation, encoding="utf-8") as f:
        body = f.read()
    assert "# Conversation" in body
    assert "Subagent: harness:qa-cli" in body
    assert "source=subagent_stop_hook" in body
    assert "PASS focused checks" in body


def test_background_hook_skips_non_harness_repo(tmp_path):
    repo = tmp_path / "project"
    repo.mkdir()
    (repo / ".git").mkdir()

    result = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS_DIR, "background_hook.py"), "--event", "stop"],
        cwd=repo,
        input=json.dumps({
            "session_id": "sess-1",
            "agent_id": "agent-1",
            "agent_type": "harness:ac-worker",
        }),
        text=True,
        capture_output=True,
        timeout=5,
    )

    assert result.returncode == 0, result.stderr
    assert not (repo / "doc" / "harness").exists()


def test_background_hook_payload_cwd_non_harness_repo_does_not_write(tmp_path):
    repo = tmp_path / "project"
    repo.mkdir()
    (repo / ".git").mkdir()
    plugin_cwd = tmp_path / "plugin"
    plugin_cwd.mkdir()

    result = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS_DIR, "background_hook.py"), "--event", "stop"],
        cwd=plugin_cwd,
        input=json.dumps({
            "cwd": str(repo),
            "session_id": "sess-1",
            "agent_id": "agent-1",
            "agent_type": "harness:ac-worker",
        }),
        text=True,
        capture_output=True,
        timeout=5,
    )

    assert result.returncode == 0, result.stderr
    assert not (repo / "doc" / "harness").exists()


def test_background_hook_rejects_invalid_parent_workspace(tmp_path):
    root = tmp_path / "workspace"
    child = root / "api"
    (child / ".git").mkdir(parents=True)
    manifest = root / "doc/harness/manifest.yaml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("version: 5\ntype: api\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS_DIR, "background_hook.py"), "--event", "stop"],
        cwd=child,
        input=json.dumps({
            "cwd": str(child),
            "session_id": "sess-1",
            "agent_id": "agent-1",
            "agent_type": "harness:qa-cli",
        }),
        text=True,
        capture_output=True,
        timeout=5,
    )

    assert result.returncode == 0, result.stderr
    assert not os.path.exists(background_registry.registry_path(str(root)))


def test_background_hook_writes_in_harness_enabled_repo(tmp_path):
    repo_path, _task_dir = _repo(tmp_path)
    _mark_harness_enabled(repo_path)

    result = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS_DIR, "background_hook.py"), "--event", "stop"],
        cwd=repo_path,
        input=json.dumps({
            "session_id": "sess-1",
            "agent_id": "agent-1",
            "agent_type": "harness:ac-worker",
        }),
        text=True,
        capture_output=True,
        timeout=5,
    )

    assert result.returncode == 0, result.stderr
    assert os.path.isfile(background_registry.registry_path(repo_path))


def test_official_subagent_stop_fields_are_preserved(tmp_path, monkeypatch):
    repo, task_dir = _repo(tmp_path)
    _bind_session(repo, task_dir, "sess-1")
    final_message = "VERDICT: PASS\ndone"
    transcript = _write_agent_transcript(
        tmp_path, monkeypatch, "sess-1", "agent-1", final_message,
        agent_type="general-purpose",
    )
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
            "agent_transcript_path": transcript,
            "last_assistant_message": final_message,
        },
    )

    assert stopped["status"] == "done"
    assert stopped["agent_type"] == "general-purpose"
    assert stopped["transcript_path"] == transcript
    assert stopped["last_assistant_message"] == final_message
    receipts = [json.loads(line) for line in (Path(task_dir) / "RECEIPTS.jsonl").read_text().splitlines()]
    assert receipts[-1]["event"] == "completed"
    assert receipts[-1]["verdict"] == "PASS"


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


def test_unmatched_stop_without_active_task_records_nonblocking_diagnostic(tmp_path):
    repo, task_dir = _repo(tmp_path)
    (Path(task_dir).parent / ".active").unlink()
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
    assert "diff_fingerprint" not in record
    assert background_registry.active_records(repo, task_id="TASK__bg", session_id="sess-1") == []


def test_stop_only_runtime_records_complete_current_task_lifecycle(tmp_path, monkeypatch):
    repo, task_dir = _repo(tmp_path)
    session_id = "sess-stop-only"
    agent_id = "qa-cli-stop-only"
    final_message = "VERDICT: PASS\nfocused checks passed"
    _bind_session(repo, task_dir, session_id)
    transcript = _write_agent_transcript(
        tmp_path, monkeypatch, session_id, agent_id, final_message,
    )
    stopped = background_registry.mark_subagent_stop(
        repo,
        {
            "hook_event_name": "SubagentStop",
            "session_id": session_id,
            "agent_id": agent_id,
            "agent_type": agent_id,
            "cwd": repo,
            "agent_transcript_path": transcript,
            "last_assistant_message": final_message,
        },
    )

    assert stopped["status"] == "done"
    assert stopped["started_from_stop"] is True
    assert stopped["task_id"] == "TASK__bg"
    assert stopped["task_dir"] == task_dir
    assert stopped["subagent_receipt_id"].startswith("subagent-")
    assert stopped["completion_receipt_id"].startswith("subagent-")
    receipts = [
        json.loads(line)
        for line in (Path(task_dir) / "RECEIPTS.jsonl").read_text().splitlines()
    ]
    assert [(item["event"], item["verdict"]) for item in receipts] == [
        ("started", ""), ("completed", "PASS"),
    ]
    assert all(item["task_run_id"] == _lib.read_task_control(task_dir)["run_id"] for item in receipts)
    assert all(item["runtime_session_id"] == "sess-stop-only" for item in receipts)
    assert all(item["runtime_thread_id"] == "qa-cli-stop-only" for item in receipts)

    replay = background_registry.mark_subagent_stop(repo, {
        "hook_event_name": "SubagentStop", "session_id": session_id,
        "agent_id": agent_id, "agent_type": agent_id,
        "agent_transcript_path": transcript, "last_assistant_message": final_message,
    })
    assert replay["status"] == "duplicate_stop"
    assert len((Path(task_dir) / "RECEIPTS.jsonl").read_text().splitlines()) == 2


def test_stop_only_receipt_pair_failure_rolls_back_and_retries(tmp_path, monkeypatch):
    repo, task_dir = _repo(tmp_path)
    session_id = "sess-retry"
    agent_id = "qa-cli-retry"
    final_message = "VERDICT: PASS"
    _bind_session(repo, task_dir, session_id)
    transcript = _write_agent_transcript(
        tmp_path, monkeypatch, session_id, agent_id, final_message,
    )
    payload = {
        "hook_event_name": "SubagentStop", "session_id": session_id,
        "agent_id": agent_id, "agent_type": agent_id,
        "agent_transcript_path": transcript, "last_assistant_message": final_message,
    }
    real_record = background_registry.record_subagent_receipt
    calls = 0

    def fail_second_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected completion append failure")
        return real_record(*args, **kwargs)

    monkeypatch.setattr(background_registry, "record_subagent_receipt", fail_second_once)
    first = background_registry.mark_subagent_stop(repo, payload)
    assert first["status"] == "receipt_pending"
    assert not (Path(task_dir) / "RECEIPTS.jsonl").exists()

    second = background_registry.mark_subagent_stop(repo, payload)
    assert second["status"] == "done"
    receipts = [
        json.loads(line)
        for line in (Path(task_dir) / "RECEIPTS.jsonl").read_text().splitlines()
    ]
    assert [(item["event"], item["verdict"]) for item in receipts] == [
        ("started", ""), ("completed", "PASS"),
    ]


def test_stop_only_fallback_rejects_missing_or_foreign_session(tmp_path, monkeypatch):
    repo, task_dir = _repo(tmp_path)
    _bind_session(repo, task_dir, "owner-session")
    final_message = "VERDICT: PASS"
    for session_id in ("", "foreign-session"):
        agent_id = f"qa-cli-{session_id or 'missing'}"
        transcript = _write_agent_transcript(
            tmp_path, monkeypatch, session_id or "missing-session", agent_id, final_message,
        )
        payload = {
            "hook_event_name": "SubagentStop",
            "agent_id": agent_id,
            "agent_type": agent_id,
            "agent_transcript_path": transcript,
            "last_assistant_message": final_message,
        }
        if session_id:
            payload["session_id"] = session_id
        record = background_registry.mark_subagent_stop(repo, payload)
        assert record["status"] == "unmatched_stop"
    assert not (Path(task_dir) / "RECEIPTS.jsonl").exists()


def test_stop_only_uses_transcript_agent_type_not_payload_claim(tmp_path, monkeypatch):
    repo, task_dir = _repo(tmp_path)
    session_id = "sess-type-proof"
    agent_id = "agent-type-proof"
    final_message = "VERDICT: PASS"
    _bind_session(repo, task_dir, session_id)
    transcript = _write_agent_transcript(
        tmp_path, monkeypatch, session_id, agent_id, final_message,
        agent_type="harness:qa-cli",
    )

    record = background_registry.mark_subagent_stop(repo, {
        "hook_event_name": "SubagentStop", "session_id": session_id,
        "agent_id": agent_id, "agent_type": "harness:review-security",
        "agent_transcript_path": transcript, "last_assistant_message": final_message,
    })

    assert record["status"] == "done"
    receipts = [
        json.loads(line)
        for line in (Path(task_dir) / "RECEIPTS.jsonl").read_text().splitlines()
    ]
    assert {item["agent_type"] for item in receipts} == {"harness:qa-cli"}
    assert {item["lens"] for item in receipts} == {"qa-cli"}


def test_stop_only_rejects_transcript_without_runtime_start_proof(tmp_path, monkeypatch):
    repo, task_dir = _repo(tmp_path)
    session_id = "sess-no-start-proof"
    agent_id = "qa-cli-no-start-proof"
    final_message = "VERDICT: PASS"
    _bind_session(repo, task_dir, session_id)
    transcript = Path(_write_agent_transcript(
        tmp_path, monkeypatch, session_id, agent_id, final_message,
    ))
    transcript.write_text(transcript.read_text().splitlines()[1] + "\n", encoding="utf-8")

    record = background_registry.mark_subagent_stop(repo, {
        "hook_event_name": "SubagentStop", "session_id": session_id,
        "agent_id": agent_id, "agent_type": agent_id,
        "agent_transcript_path": str(transcript), "last_assistant_message": final_message,
    })

    assert record["status"] == "unmatched_stop"
    assert not (Path(task_dir) / "RECEIPTS.jsonl").exists()


def test_stop_only_rejects_symlinked_transcript_path(tmp_path, monkeypatch):
    repo, task_dir = _repo(tmp_path)
    session_id = "sess-symlink-proof"
    agent_id = "qa-cli-symlink-proof"
    final_message = "VERDICT: PASS"
    _bind_session(repo, task_dir, session_id)
    transcript = Path(_write_agent_transcript(
        tmp_path, monkeypatch, session_id, agent_id, final_message,
    ))
    real_transcript = transcript.with_name("runtime-copy.jsonl")
    transcript.rename(real_transcript)
    transcript.symlink_to(real_transcript)

    record = background_registry.mark_subagent_stop(repo, {
        "hook_event_name": "SubagentStop", "session_id": session_id,
        "agent_id": agent_id, "agent_type": agent_id,
        "agent_transcript_path": str(transcript), "last_assistant_message": final_message,
    })

    assert record["status"] == "unmatched_stop"
    assert not (Path(task_dir) / "RECEIPTS.jsonl").exists()


def test_stop_only_fallback_rejects_transcript_from_prior_run(tmp_path, monkeypatch):
    repo, task_dir = _repo(tmp_path)
    session_id = "sess-rotated"
    agent_id = "qa-cli-prior-run"
    final_message = "VERDICT: PASS"
    _bind_session(repo, task_dir, session_id)
    transcript = _write_agent_transcript(
        tmp_path, monkeypatch, session_id, agent_id, final_message,
    )
    prior_run_ms = _lib.uuid7_timestamp_ms(
        _lib.read_task_control(task_dir)["run_id"]
    )
    make_uuid7 = _lib.new_uuid7
    monkeypatch.setattr(_lib, "new_uuid7", lambda: make_uuid7(prior_run_ms + 2_000))
    with _lib.receipt_stream_transaction(task_dir):
        _lib.begin_task_run(task_dir)
        _lib.write_active_marker(repo, task_dir, session_id=session_id)

    record = background_registry.mark_subagent_stop(repo, {
        "hook_event_name": "SubagentStop", "session_id": session_id,
        "agent_id": agent_id, "agent_type": agent_id,
        "agent_transcript_path": transcript, "last_assistant_message": final_message,
    })
    assert record["status"] == "unmatched_stop"
    assert not (Path(task_dir) / "RECEIPTS.jsonl").exists()


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
