"""Tests for receipt-backed Claude subagent lifecycle helpers."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from conftest import SCRIPTS_DIR

sys.path.insert(0, SCRIPTS_DIR)
import _lib  # noqa: E402
import subagent_lifecycle  # noqa: E402


def _repo(tmp_path: Path) -> tuple[str, str]:
    (tmp_path / ".git").mkdir()
    task_dir = tmp_path / "doc/harness/tasks/TASK__bg"
    task_dir.mkdir(parents=True)
    _lib.ensure_task_scaffold(str(task_dir), "TASK__bg")
    (tmp_path / "doc/harness/manifest.yaml").write_text("type: test\n", encoding="utf-8")
    return str(tmp_path), str(task_dir)


def _bind(repo: str, task_dir: str, session_id: str) -> None:
    _lib.write_active_marker(repo, task_dir, session_id=session_id)


def _transcript(
    tmp_path: Path,
    monkeypatch,
    task_dir: str,
    session_id: str,
    agent_id: str,
    final_message: str,
    *,
    agent_type: str,
) -> str:
    claude = tmp_path / ".claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude))
    path = claude / "projects/project" / session_id / "subagents" / f"agent-{agent_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    run_started = datetime.fromisoformat(
        _lib.task_run_started_at(_lib.read_task_control(task_dir)).replace("Z", "+00:00")
    )
    timestamp = (run_started + timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
    items = [
        {
            "timestamp": timestamp,
            "agentId": agent_id,
            "sessionId": session_id,
            "attachment": {
                "type": "hook_additional_context",
                "hookName": "SubagentStart",
                "hookEvent": "SubagentStart",
                "content": [f"Agent {agent_type} started ({agent_id})"],
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


def _stop_payload(
    session_id: str, agent_id: str, agent_type: str, transcript: str, final_message: str,
) -> dict[str, object]:
    return {
        "hook_event_name": "SubagentStop",
        "session_id": session_id,
        "agent_id": agent_id,
        "agent_type": agent_type,
        "agent_transcript_path": transcript,
        "last_assistant_message": final_message,
    }


def _receipts(task_dir: str) -> list[dict[str, str]]:
    path = Path(task_dir) / "RECEIPTS.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_start_and_real_stop_use_only_receipts(tmp_path, monkeypatch):
    repo, task_dir = _repo(tmp_path)
    session_id, agent_id, agent_type = "sess-1", "agent-1", "harness:qa-cli"
    final_message = "VERDICT: PASS\nchecks passed"
    _bind(repo, task_dir, session_id)
    started = subagent_lifecycle.register_subagent_start(repo, {
        "session_id": session_id, "agent_id": agent_id, "agent_type": agent_type,
    })
    assert started["status"] == "active"
    assert started["runtime_id"] == "claude:sess-1:agent-1"
    assert len(subagent_lifecycle.active_records(
        repo, task_id="TASK__bg", session_id=session_id,
    )) == 1

    transcript = _transcript(
        tmp_path, monkeypatch, task_dir, session_id, agent_id, final_message,
        agent_type=agent_type,
    )
    stopped = subagent_lifecycle.mark_subagent_stop(
        repo, _stop_payload(session_id, agent_id, agent_type, transcript, final_message),
    )
    assert stopped["status"] == "done"
    assert subagent_lifecycle.active_records(
        repo, task_id="TASK__bg", session_id=session_id,
    ) == []
    receipts = _receipts(task_dir)
    assert [(item["event"], item["verdict"]) for item in receipts] == [
        ("started", ""), ("completed", "PASS"),
    ]
    assert {item["source"] for item in receipts} == {"claude_hook"}
    assert {item["runtime_id"] for item in receipts} == {"claude:sess-1:agent-1"}
    assert not (Path(repo) / "doc/harness/runtime/background.json").exists()
    assert not (Path(repo) / "doc/harness/runtime/background.json.lock").exists()


def test_invalid_events_create_no_diagnostic_authority(tmp_path):
    repo, task_dir = _repo(tmp_path)
    _bind(repo, task_dir, "sess-1")
    assert subagent_lifecycle.register_subagent_start(
        repo, {"session_id": "sess-1", "agent_type": "harness:qa-cli"},
    ) == {}
    assert subagent_lifecycle.mark_subagent_stop(
        repo, {"session_id": "sess-1", "agent_id": "agent-1"},
    ) == {}
    assert subagent_lifecycle.handle_subagent_hook(repo, {"event": "unknown"}) == {}
    assert subagent_lifecycle.register_subagent_start(repo, {
        "session_id": "sess-1", "agent_id": "bad:agent",
        "agent_type": "harness:qa-cli",
    }) == {}
    assert not (Path(task_dir) / "RECEIPTS.jsonl").exists()
    assert not (Path(repo) / "doc/harness/runtime").exists()


def test_stop_only_provenance_rejects_foreign_missing_start_symlink_and_prior_run(
    tmp_path, monkeypatch,
):
    repo, task_dir = _repo(tmp_path)
    session_id, agent_id, agent_type = "sess-proof", "agent-proof", "harness:qa-cli"
    final_message = "VERDICT: PASS"
    _bind(repo, task_dir, session_id)
    transcript = Path(_transcript(
        tmp_path, monkeypatch, task_dir, session_id, agent_id, final_message,
        agent_type=agent_type,
    ))
    base = _stop_payload(session_id, agent_id, "spoofed-type", str(transcript), final_message)

    foreign = dict(base, session_id="foreign")
    assert subagent_lifecycle.mark_subagent_stop(repo, foreign) == {}
    missing = dict(base)
    missing.pop("session_id")
    assert subagent_lifecycle.mark_subagent_stop(repo, missing) == {}

    original = transcript.read_text(encoding="utf-8")
    transcript.write_text(original.splitlines()[1] + "\n", encoding="utf-8")
    assert subagent_lifecycle.mark_subagent_stop(repo, base) == {}
    transcript.write_text(original, encoding="utf-8")

    real = transcript.with_name("runtime-copy.jsonl")
    transcript.rename(real)
    transcript.symlink_to(real)
    assert subagent_lifecycle.mark_subagent_stop(repo, base) == {}
    transcript.unlink()
    real.rename(transcript)

    prior_ms = _lib.uuid7_timestamp_ms(_lib.read_task_control(task_dir)["run_id"])
    make_uuid7 = _lib.new_uuid7
    monkeypatch.setattr(_lib, "new_uuid7", lambda: make_uuid7(prior_ms + 2_000))
    with _lib.receipt_stream_transaction(task_dir):
        _lib.begin_task_run(task_dir)
        _lib.write_active_marker(repo, task_dir, session_id=session_id)
    assert subagent_lifecycle.mark_subagent_stop(repo, base) == {}
    assert not (Path(task_dir) / "RECEIPTS.jsonl").exists()


def test_stop_only_uses_transcript_type_and_conflicting_replay_does_not_append(
    tmp_path, monkeypatch,
):
    repo, task_dir = _repo(tmp_path)
    session_id, agent_id = "sess-type", "agent-type"
    _bind(repo, task_dir, session_id)
    first = "VERDICT: PASS\nfirst"
    transcript = _transcript(
        tmp_path, monkeypatch, task_dir, session_id, agent_id, first,
        agent_type="harness:qa-cli",
    )
    payload = _stop_payload(session_id, agent_id, "harness:review-security", transcript, first)
    assert subagent_lifecycle.mark_subagent_stop(repo, payload)["status"] == "done"
    assert {row["agent_type"] for row in _receipts(task_dir)} == {"harness:qa-cli"}

    second = "VERDICT: FAIL\nchanged"
    _transcript(
        tmp_path, monkeypatch, task_dir, session_id, agent_id, second,
        agent_type="harness:qa-cli",
    )
    changed = _stop_payload(session_id, agent_id, "harness:qa-cli", transcript, second)
    assert subagent_lifecycle.mark_subagent_stop(repo, changed)["status"] == "receipt_pending"
    assert len(_receipts(task_dir)) == 2


def test_concurrent_starts_publish_each_identity_once(tmp_path):
    repo, task_dir = _repo(tmp_path)
    session_id = "sess-start-race"
    _bind(repo, task_dir, session_id)
    results = []
    threads = [threading.Thread(target=lambda i=i: results.append(
        subagent_lifecycle.register_subagent_start(repo, {
            "session_id": session_id, "agent_id": f"agent-{i}",
            "agent_type": "harness:qa-cli",
        })
    )) for i in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(results) == 12
    assert {row["agent_id"] for row in _receipts(task_dir)} == {
        f"agent-{i}" for i in range(12)
    }


def test_start_replay_is_idempotent(tmp_path):
    repo, task_dir = _repo(tmp_path)
    _bind(repo, task_dir, "sess-replay")
    payload = {
        "session_id": "sess-replay", "agent_id": "agent-replay",
        "agent_type": "harness:qa-cli",
    }
    assert subagent_lifecycle.register_subagent_start(repo, payload)["status"] == "active"
    assert subagent_lifecycle.register_subagent_start(repo, payload)["status"] == "duplicate_start"
    assert len(_receipts(task_dir)) == 1


def test_stop_only_pair_is_atomic_and_replay_is_idempotent(tmp_path, monkeypatch):
    repo, task_dir = _repo(tmp_path)
    session_id, agent_id, agent_type = "sess-stop", "agent-stop", "harness:qa-cli"
    final_message = "VERDICT: PASS\nfocused checks passed"
    _bind(repo, task_dir, session_id)
    transcript = _transcript(
        tmp_path, monkeypatch, task_dir, session_id, agent_id, final_message,
        agent_type=agent_type,
    )
    payload = _stop_payload(session_id, agent_id, agent_type, transcript, final_message)

    first = subagent_lifecycle.mark_subagent_stop(repo, payload)
    second = subagent_lifecycle.mark_subagent_stop(repo, payload)

    assert first["status"] == "done" and first["started_from_stop"] is True
    assert second["status"] == "duplicate_stop"
    assert len(_receipts(task_dir)) == 2


def test_stop_only_append_failure_rolls_back_and_retries(tmp_path, monkeypatch):
    repo, task_dir = _repo(tmp_path)
    session_id, agent_id, agent_type = "sess-retry", "agent-retry", "harness:qa-cli"
    final_message = "VERDICT: PASS"
    _bind(repo, task_dir, session_id)
    transcript = _transcript(
        tmp_path, monkeypatch, task_dir, session_id, agent_id, final_message,
        agent_type=agent_type,
    )
    payload = _stop_payload(session_id, agent_id, agent_type, transcript, final_message)
    monkeypatch.setattr(_lib, "_runtime_receipt_write_authorized", lambda *_args: True)
    real_record = subagent_lifecycle.record_subagent_receipt
    calls = 0

    def fail_second_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected completion failure")
        return real_record(*args, **kwargs)

    monkeypatch.setattr(subagent_lifecycle, "record_subagent_receipt", fail_second_once)
    assert subagent_lifecycle.mark_subagent_stop(repo, payload)["status"] == "receipt_pending"
    assert not (Path(task_dir) / "RECEIPTS.jsonl").exists()
    assert subagent_lifecycle.mark_subagent_stop(repo, payload)["status"] == "done"
    assert len(_receipts(task_dir)) == 2


def test_real_stop_append_failure_leaves_start_retryable(tmp_path, monkeypatch):
    repo, task_dir = _repo(tmp_path)
    session_id, agent_id, agent_type = "sess-real-retry", "agent-real-retry", "harness:qa-cli"
    final_message = "VERDICT: PASS"
    _bind(repo, task_dir, session_id)
    subagent_lifecycle.register_subagent_start(repo, {
        "session_id": session_id, "agent_id": agent_id, "agent_type": agent_type,
    })
    transcript = _transcript(
        tmp_path, monkeypatch, task_dir, session_id, agent_id, final_message,
        agent_type=agent_type,
    )
    payload = _stop_payload(session_id, agent_id, agent_type, transcript, final_message)
    monkeypatch.setattr(_lib, "_runtime_receipt_write_authorized", lambda *_args: True)
    real_record = subagent_lifecycle.record_subagent_receipt
    failed = False

    def fail_once(*args, **kwargs):
        nonlocal failed
        if not failed:
            failed = True
            raise OSError("injected completion failure")
        return real_record(*args, **kwargs)

    monkeypatch.setattr(subagent_lifecycle, "record_subagent_receipt", fail_once)
    assert subagent_lifecycle.mark_subagent_stop(repo, payload)["status"] == "receipt_pending"
    assert [item["event"] for item in _receipts(task_dir)] == ["started"]
    assert subagent_lifecycle.mark_subagent_stop(repo, payload)["status"] == "done"
    assert [item["event"] for item in _receipts(task_dir)] == ["started", "completed"]


def test_concurrent_stop_only_events_publish_one_pair(tmp_path, monkeypatch):
    repo, task_dir = _repo(tmp_path)
    session_id, agent_id, agent_type = "sess-race", "agent-race", "harness:qa-cli"
    final_message = "VERDICT: PASS"
    _bind(repo, task_dir, session_id)
    transcript = _transcript(
        tmp_path, monkeypatch, task_dir, session_id, agent_id, final_message,
        agent_type=agent_type,
    )
    payload = _stop_payload(session_id, agent_id, agent_type, transcript, final_message)
    results: list[dict[str, object]] = []
    threads = [threading.Thread(
        target=lambda: results.append(subagent_lifecycle.mark_subagent_stop(repo, payload)),
    ) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(_receipts(task_dir)) == 2
    assert {result["status"] for result in results} == {"done", "duplicate_stop"}


def test_real_start_type_conflict_fails_closed_and_stays_active(tmp_path, monkeypatch):
    repo, task_dir = _repo(tmp_path)
    session_id, agent_id = "sess-conflict", "agent-conflict"
    _bind(repo, task_dir, session_id)
    subagent_lifecycle.register_subagent_start(repo, {
        "session_id": session_id, "agent_id": agent_id,
        "agent_type": "harness:qa-cli",
    })
    final_message = "VERDICT: PASS"
    transcript = _transcript(
        tmp_path, monkeypatch, task_dir, session_id, agent_id, final_message,
        agent_type="harness:review-security",
    )
    result = subagent_lifecycle.mark_subagent_stop(repo, _stop_payload(
        session_id, agent_id, "harness:qa-cli", transcript, final_message,
    ))
    assert result["status"] == "receipt_pending"
    assert len(_receipts(task_dir)) == 1
    assert len(subagent_lifecycle.active_records(
        repo, task_id="TASK__bg", session_id=session_id,
    )) == 1


def test_active_records_are_current_run_and_session_scoped(tmp_path):
    repo, task_dir = _repo(tmp_path)
    _bind(repo, task_dir, "owner-session")
    subagent_lifecycle.register_subagent_start(repo, {
        "session_id": "owner-session", "agent_id": "owner-agent",
        "agent_type": "harness:qa-cli",
    })
    assert subagent_lifecycle.active_records(
        repo, task_id="TASK__bg", session_id="foreign-session",
    ) == []

    prior = _lib.read_task_control(task_dir)["run_id"]
    with _lib.receipt_stream_transaction(task_dir):
        _lib.begin_task_run(task_dir)
        _lib.write_active_marker(repo, task_dir, session_id="owner-session")
    assert _lib.read_task_control(task_dir)["run_id"] != prior
    assert subagent_lifecycle.active_records(
        repo, task_id="TASK__bg", session_id="owner-session",
    ) == []


def test_active_records_ignore_other_session_receipts_in_same_run(tmp_path):
    repo, task_dir = _repo(tmp_path)
    _bind(repo, task_dir, "owner-session")
    run_id = _lib.read_task_control(task_dir)["run_id"]
    rows = []
    for session_id, agent_id in (
        ("owner-session", "owner-agent"), ("foreign-session", "foreign-agent"),
    ):
        rows.append({
            "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "event": "started", "source": "claude_hook", "task_run_id": run_id,
            "runtime_id": f"claude:{session_id}:{agent_id}", "agent_id": agent_id,
            "agent_type": "harness:qa-cli", "lens": "qa-cli", "verdict": "",
            "summary": "",
        })
    (Path(task_dir) / "RECEIPTS.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )

    active = subagent_lifecycle.active_records(
        repo, task_id="TASK__bg", session_id="owner-session",
    )
    assert [item["id"] for item in active] == ["owner-agent"]


def test_stale_valid_start_expires_but_invalid_and_future_stay_active(tmp_path):
    repo, task_dir = _repo(tmp_path)
    session_id = "sess-time"
    _bind(repo, task_dir, session_id)
    run_id = _lib.read_task_control(task_dir)["run_id"]
    base = {
        "event": "started", "source": "claude_hook", "task_run_id": run_id,
        "agent_type": "harness:qa-cli", "lens": "qa-cli", "verdict": "",
        "summary": "",
    }
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat().replace("+00:00", "Z")
    future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat().replace("+00:00", "Z")
    rows = [
        {**base, "ts": old, "runtime_id": f"claude:{session_id}:old", "agent_id": "old"},
        {**base, "ts": "not-a-time", "runtime_id": f"claude:{session_id}:invalid", "agent_id": "invalid"},
        {**base, "ts": future, "runtime_id": f"claude:{session_id}:future", "agent_id": "future"},
    ]
    (Path(task_dir) / "RECEIPTS.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8",
    )
    active = subagent_lifecycle.active_records(
        repo, task_id="TASK__bg", session_id=session_id, stale_secs=60,
    )
    assert {item["id"] for item in active} == {"invalid", "future"}


def test_background_hook_ignores_non_harness_repo(tmp_path):
    repo = tmp_path / "plain"
    repo.mkdir()
    (repo / ".git").mkdir()
    result = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS_DIR, "background_hook.py"), "--event", "stop"],
        cwd=repo,
        input=json.dumps({"session_id": "sess-1", "agent_id": "agent-1"}),
        text=True,
        capture_output=True,
        timeout=5,
    )
    assert result.returncode == 0, result.stderr
    assert not (repo / "doc/harness").exists()


def test_background_hook_publishes_start_without_registry(tmp_path):
    repo, task_dir = _repo(tmp_path)
    _bind(repo, task_dir, "sess-hook")
    result = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS_DIR, "background_hook.py"), "--event", "start"],
        cwd=repo,
        input=json.dumps({
            "cwd": repo,
            "session_id": "sess-hook",
            "agent_id": "agent-hook",
            "agent_type": "harness:qa-cli",
        }),
        text=True,
        capture_output=True,
        timeout=5,
    )
    assert result.returncode == 0, result.stderr
    assert [item["event"] for item in _receipts(task_dir)] == ["started"]
    assert not (Path(repo) / "doc/harness/runtime").exists()


def test_background_hook_publishes_stop_only_pair_without_registry(tmp_path, monkeypatch):
    repo, task_dir = _repo(tmp_path)
    session_id, agent_id, agent_type = "sess-stop-hook", "agent-stop-hook", "harness:qa-cli"
    final_message = "VERDICT: PASS"
    _bind(repo, task_dir, session_id)
    transcript = _transcript(
        tmp_path, monkeypatch, task_dir, session_id, agent_id, final_message,
        agent_type=agent_type,
    )
    result = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS_DIR, "background_hook.py"), "--event", "stop"],
        cwd=repo,
        input=json.dumps(_stop_payload(
            session_id, agent_id, agent_type, transcript, final_message,
        )),
        text=True,
        capture_output=True,
        timeout=5,
    )
    assert result.returncode == 0, result.stderr
    assert [(item["event"], item["verdict"]) for item in _receipts(task_dir)] == [
        ("started", ""), ("completed", "PASS"),
    ]
    assert not (Path(repo) / "doc/harness/runtime").exists()
