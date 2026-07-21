from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
from unittest import mock


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "plugin" / "scripts" / "codex_lifecycle_watcher.py"


def _load():
    spec = importlib.util.spec_from_file_location("codex_lifecycle_watcher_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def _write_jsonl(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")


def _child_events(root_id: str, child_id: str, agent_path: str, cwd: str, final: str | None = None):
    events = [{
        "timestamp": "2026-07-21T00:00:00Z",
        "type": "session_meta",
        "payload": {
            "session_id": root_id,
            "id": child_id,
            "parent_thread_id": root_id,
            "cwd": cwd,
            "thread_source": "subagent",
            "agent_path": agent_path,
            "source": {"subagent": {"thread_spawn": {
                "parent_thread_id": root_id,
                "depth": 1,
                "agent_path": agent_path,
            }}},
        },
    }]
    # Forked rollouts can contain a copied parent metadata row. It must not be
    # mistaken for the child identity row.
    events.append({"type": "session_meta", "payload": {"id": root_id, "cwd": cwd}})
    events.extend([
        {"type": "event_msg", "payload": {
            "type": "agent_message", "phase": "final_answer", "message": "historical parent final",
        }},
        {"type": "event_msg", "payload": {
            "type": "task_complete", "last_agent_message": "historical parent final",
        }},
    ])
    events.append({"type": "response_item", "payload": {
        "type": "agent_message", "author": "/root", "recipient": agent_path,
        "content": [{"type": "input_text", "text": (
            f"Message Type: NEW_TASK\nTask name: {agent_path}\nSender: /root\nPayload:\n"
        )}],
    }})
    if final is not None:
        events.extend([
            {"type": "event_msg", "payload": {
                "type": "agent_message", "phase": "final_answer", "message": final,
            }},
            {"type": "event_msg", "payload": {
                "type": "task_complete", "last_agent_message": final,
            }},
        ])
    return events


def _spawn_events(root_id: str, child_id: str, task_name: str, agent_path: str):
    call_id = "call_runtime_123456"
    return [
        {"type": "response_item", "payload": {
            "type": "function_call", "namespace": "collaboration", "name": "spawn_agent",
            "call_id": call_id, "arguments": json.dumps({"task_name": task_name, "message": "encrypted"}),
        }},
        {"type": "event_msg", "payload": {
            "type": "sub_agent_activity", "kind": "started", "event_id": call_id,
            "agent_thread_id": child_id, "agent_path": agent_path,
        }},
        {"type": "response_item", "payload": {
            "type": "function_call_output", "call_id": call_id,
            "output": json.dumps({"task_name": agent_path}),
        }},
    ]


def _delivery(agent_path: str, final: str):
    return {"type": "response_item", "payload": {
        "type": "agent_message", "author": agent_path, "recipient": "/root",
        "content": [{"type": "input_text", "text": (
            f"Message Type: FINAL_ANSWER\nTask name: /root\nSender: {agent_path}\nPayload:\n{final}"
        )}],
    }}


def test_watcher_records_start_then_correlated_review_completion(tmp_path, monkeypatch):
    mod = _load()
    codex_home = tmp_path / ".codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    repo = tmp_path / "repo"
    task_dir = repo / "doc/harness/tasks/TASK__watcher"
    task_dir.mkdir(parents=True)
    root_id = "019f825b-f25f-70c3-8ee8-071f79fa1c42"
    child_id = "019f82a6-ce64-75a3-b01d-92f7b0b4fe6f"
    agent_path = "/root/code_review"
    child = codex_home / "sessions/2026/07/21" / f"rollout-{child_id}.jsonl"
    _write_jsonl(child, _child_events(root_id, child_id, agent_path, str(repo)))

    receipts = []
    def record(_task_dir, receipt):
        entry = {**receipt, "head_sha": receipt.get("head_sha") or "a" * 40,
                 "base_sha": receipt.get("base_sha") or "a" * 40,
                 "diff_fingerprint": receipt.get("diff_fingerprint") or "sha256:before"}
        receipts.append(entry)
        return entry

    watcher = mod.Watcher(str(repo), root_id)
    patches = (
        mock.patch.object(mod, "_active_task_for_session", return_value=str(task_dir)),
        mock.patch.object(mod, "review_diff_fingerprint", return_value="sha256:before"),
        mock.patch.object(mod, "record_subagent_receipt", side_effect=record),
        mock.patch.object(mod, "list_review_receipts", side_effect=lambda _td: receipts),
        mock.patch.object(mod, "list_subagent_receipts", return_value=[]),
    )
    for patcher in patches:
        patcher.start()
    try:
        for event in _spawn_events(root_id, child_id, "code_review", agent_path):
            watcher.feed(event)
        assert [(item["status"], item["lens"]) for item in receipts] == [("started", "review-code")]

        final = "VERDICT: PASS\nFINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0\nClean."
        _write_jsonl(child, _child_events(root_id, child_id, agent_path, str(repo), final))
        watcher.feed(_delivery(agent_path, final))
    finally:
        for patcher in reversed(patches):
            patcher.stop()

    assert [(item["status"], item.get("verdict", "")) for item in receipts] == [
        ("started", ""), ("completed", "PASS"),
    ]
    assert receipts[0]["runtime_thread_id"] == child_id
    assert receipts[0]["runtime_event_id"] == receipts[1]["runtime_event_id"]


def test_watcher_rejects_child_that_completed_before_start_capture(tmp_path, monkeypatch):
    mod = _load()
    codex_home = tmp_path / ".codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    repo = tmp_path / "repo"
    root_id = "019f825b-f25f-70c3-8ee8-071f79fa1c42"
    child_id = "019f82a6-ce64-75a3-b01d-92f7b0b4fe6f"
    agent_path = "/root/qa_cli"
    final = "VERDICT: PASS\nTests passed"
    child = codex_home / "sessions/2026/07/21" / f"rollout-{child_id}.jsonl"
    _write_jsonl(child, _child_events(root_id, child_id, agent_path, str(repo), final))
    receipts = []
    watcher = mod.Watcher(str(repo), root_id)
    with mock.patch.object(mod, "_active_task_for_session", return_value="/task"), \
         mock.patch.object(mod, "record_subagent_receipt", side_effect=lambda td, item: receipts.append(item)), \
         mock.patch.object(mod, "list_review_receipts", return_value=[]), \
         mock.patch.object(mod, "list_subagent_receipts", return_value=[]):
        for event in _spawn_events(root_id, child_id, "qa_cli", agent_path):
            watcher.feed(event)
    assert receipts == []


def test_watcher_marks_completion_pending_when_source_changes(tmp_path, monkeypatch):
    mod = _load()
    codex_home = tmp_path / ".codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    repo = tmp_path / "repo"
    task_dir = repo / "doc/harness/tasks/TASK__watcher"
    task_dir.mkdir(parents=True)
    root_id = "019f825b-f25f-70c3-8ee8-071f79fa1c42"
    child_id = "019f82a6-ce64-75a3-b01d-92f7b0b4fe6f"
    agent_path = "/root/code_review"
    child = codex_home / "sessions/day" / f"rollout-{child_id}.jsonl"
    _write_jsonl(child, _child_events(root_id, child_id, agent_path, str(repo)))
    receipts = []
    fingerprint = ["sha256:before"]

    def record(_task_dir, receipt):
        entry = {**receipt, "head_sha": "a" * 40, "base_sha": "a" * 40,
                 "diff_fingerprint": receipt.get("diff_fingerprint") or fingerprint[0]}
        receipts.append(entry)
        return entry

    watcher = mod.Watcher(str(repo), root_id)
    with mock.patch.object(mod, "_active_task_for_session", return_value=str(task_dir)), \
         mock.patch.object(mod, "review_diff_fingerprint", side_effect=lambda _td: fingerprint[0]), \
         mock.patch.object(mod, "record_subagent_receipt", side_effect=record), \
         mock.patch.object(mod, "list_review_receipts", side_effect=lambda _td: receipts), \
         mock.patch.object(mod, "list_subagent_receipts", return_value=[]):
        for event in _spawn_events(root_id, child_id, "code_review", agent_path):
            watcher.feed(event)
        fingerprint[0] = "sha256:after"
        final = "VERDICT: PASS\nFINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0"
        _write_jsonl(child, _child_events(root_id, child_id, agent_path, str(repo), final))
        watcher.feed(_delivery(agent_path, final))
    assert receipts[-1]["verdict"] == "PENDING"


def test_child_status_rejects_duplicate_child_boundary(tmp_path, monkeypatch):
    mod = _load()
    codex_home = tmp_path / ".codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    root_id = "019f825b-f25f-70c3-8ee8-071f79fa1c42"
    child_id = "019f82a6-ce64-75a3-b01d-92f7b0b4fe6f"
    agent_path = "/root/qa_cli"
    child = codex_home / "sessions/day" / f"rollout-{child_id}.jsonl"
    events = _child_events(root_id, child_id, agent_path, "/repo")
    events.append(events[-1])
    _write_jsonl(child, events)
    assert mod._child_status(child_id, root_id, agent_path, "/repo")[0] == "invalid"


def test_child_status_rejects_malformed_complete_record(tmp_path, monkeypatch):
    mod = _load()
    codex_home = tmp_path / ".codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    root_id = "019f825b-f25f-70c3-8ee8-071f79fa1c42"
    child_id = "019f82a6-ce64-75a3-b01d-92f7b0b4fe6f"
    child = codex_home / "sessions/day" / f"rollout-{child_id}.jsonl"
    _write_jsonl(child, _child_events(root_id, child_id, "/root/qa_cli", "/repo"))
    with child.open("a", encoding="utf-8") as handle:
        handle.write("{malformed}\n")
    assert mod._child_status(child_id, root_id, "/root/qa_cli", "/repo")[0] == "invalid"


def test_child_status_retries_newline_incomplete_tail(tmp_path, monkeypatch):
    mod = _load()
    codex_home = tmp_path / ".codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    root_id = "019f825b-f25f-70c3-8ee8-071f79fa1c42"
    child_id = "019f82a6-ce64-75a3-b01d-92f7b0b4fe6f"
    child = codex_home / "sessions/day" / f"rollout-{child_id}.jsonl"
    _write_jsonl(child, _child_events(root_id, child_id, "/root/qa_cli", "/repo"))
    with child.open("ab") as handle:
        handle.write(b'{"type":"event_msg","payload":{"type":"agent_message"')
    assert mod._child_status(child_id, root_id, "/root/qa_cli", "/repo")[0] == "pending"


def test_child_status_retries_before_child_metadata_is_written(tmp_path, monkeypatch):
    mod = _load()
    codex_home = tmp_path / ".codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    child_id = "019f82a6-ce64-75a3-b01d-92f7b0b4fe6f"
    child = codex_home / "sessions/day" / f"rollout-{child_id}.jsonl"
    child.parent.mkdir(parents=True)
    child.touch()
    assert mod._child_status(
        child_id, "019f825b-f25f-70c3-8ee8-071f79fa1c42", "/root/qa_cli", "/repo"
    )[0] == "pending"


def test_child_status_rejects_cross_repo_and_mismatched_final(tmp_path, monkeypatch):
    mod = _load()
    codex_home = tmp_path / ".codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    root_id = "019f825b-f25f-70c3-8ee8-071f79fa1c42"
    child_id = "019f82a6-ce64-75a3-b01d-92f7b0b4fe6f"
    agent_path = "/root/security_review"
    child = codex_home / "sessions/day" / f"rollout-{child_id}.jsonl"
    events = _child_events(root_id, child_id, agent_path, "/other/repo")
    _write_jsonl(child, events)
    assert mod._child_status(child_id, root_id, agent_path, "/expected/repo")[0] == "invalid"

    events = _child_events(root_id, child_id, agent_path, "/expected/repo", "VERDICT: PASS")
    events[-1]["payload"]["last_agent_message"] = "VERDICT: FAIL"
    _write_jsonl(child, events)
    assert mod._child_status(child_id, root_id, agent_path, "/expected/repo")[0] == "invalid"


def test_child_status_rejects_symlinked_rollout(tmp_path, monkeypatch):
    mod = _load()
    codex_home = tmp_path / ".codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    root_id = "019f825b-f25f-70c3-8ee8-071f79fa1c42"
    child_id = "019f82a6-ce64-75a3-b01d-92f7b0b4fe6f"
    real = tmp_path / "outside.jsonl"
    _write_jsonl(real, _child_events(root_id, child_id, "/root/qa_cli", "/repo"))
    link = codex_home / "sessions/day" / f"rollout-{child_id}.jsonl"
    link.parent.mkdir(parents=True)
    link.symlink_to(real)
    assert mod._find_rollout(child_id) is None


def test_record_receipt_preserves_runtime_provenance(tmp_path):
    scripts = str(REPO / "plugin/scripts")
    if scripts not in os.sys.path:
        os.sys.path.insert(0, scripts)
    import _lib

    task = tmp_path / "TASK__provenance"
    task.mkdir()
    with mock.patch.object(_lib, "review_diff_fingerprint", return_value="sha256:x"), \
         mock.patch.object(_lib, "_git_head_for_receipt", return_value="a" * 40):
        entry = _lib.record_subagent_receipt(task, {
            "agent_id": "/root/qa_cli", "agent_type": "qa_cli", "status": "started",
            "runtime_event_id": "session:call:thread", "runtime_session_id": "session",
            "runtime_thread_id": "thread", "runtime_agent_path": "/root/qa_cli",
        })
    assert entry["runtime_event_id"] == "session:call:thread"
    assert entry["runtime_session_id"] == "session"
    assert entry["runtime_thread_id"] == "thread"
    assert entry["runtime_agent_path"] == "/root/qa_cli"


def test_ensure_launches_once_for_exact_root_rollout(tmp_path, monkeypatch):
    mod = _load()
    codex_home = tmp_path / ".codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    root_id = "019f825b-f25f-70c3-8ee8-071f79fa1c42"
    rollout = codex_home / "sessions/2026/07/21" / f"rollout-{root_id}.jsonl"
    _write_jsonl(rollout, [{
        "type": "session_meta",
        "payload": {
            "session_id": root_id, "id": root_id, "cwd": str(repo),
            "thread_source": "user",
        },
    }])

    process = mock.Mock(pid=43210)
    with mock.patch.object(mod.subprocess, "Popen", return_value=process) as popen, \
         mock.patch.object(mod, "_process_identity", return_value=("watcher", "99")), \
         mock.patch.object(mod, "_watcher_process_matches", return_value=True):
        assert mod.ensure(str(repo), root_id)
        assert mod.ensure(str(repo), root_id)

    assert popen.call_count == 1
    state = json.loads((repo / mod.RUNTIME_SUBDIR / f"{root_id}.json").read_text())
    assert state["pid"] == 43210
    assert state["repo_root"] == str(repo.resolve())
    command = popen.call_args.args[0]
    assert "--watch" in command
    assert command[command.index("--offset") + 1] == str(rollout.stat().st_size)


def test_ensure_replaces_stale_watcher_pid(tmp_path, monkeypatch):
    mod = _load()
    codex_home = tmp_path / ".codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    root_id = "019f825b-f25f-70c3-8ee8-071f79fa1c42"
    rollout = codex_home / "sessions/day" / f"rollout-{root_id}.jsonl"
    _write_jsonl(rollout, [{"type": "session_meta", "payload": {
        "session_id": root_id, "id": root_id, "cwd": str(repo), "thread_source": "user",
    }}])
    state_path = repo / mod.RUNTIME_SUBDIR / f"{root_id}.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps({
        "pid": 123, "thread_id": root_id, "repo_root": str(repo.resolve()),
    }))
    with mock.patch.object(mod, "_watcher_process_matches", return_value=False), \
         mock.patch.object(mod, "_process_identity", return_value=("watcher", "100")), \
         mock.patch.object(mod.subprocess, "Popen", return_value=mock.Mock(pid=456)) as popen:
        assert mod.ensure(str(repo), root_id)
    assert popen.call_count == 1
    assert json.loads(state_path.read_text())["pid"] == 456


def test_ensure_rejects_symlinked_runtime_registry(tmp_path, monkeypatch):
    mod = _load()
    codex_home = tmp_path / ".codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    root_id = "019f825b-f25f-70c3-8ee8-071f79fa1c42"
    rollout = codex_home / "sessions/day" / f"rollout-{root_id}.jsonl"
    _write_jsonl(rollout, [{"type": "session_meta", "payload": {
        "session_id": root_id, "id": root_id, "cwd": str(repo), "thread_source": "user",
    }}])
    runtime_parent = repo / "doc/harness/runtime"
    runtime_parent.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (runtime_parent / "codex-watchers").symlink_to(outside, target_is_directory=True)
    with mock.patch.object(mod.subprocess, "Popen") as popen:
        assert not mod.ensure(str(repo), root_id)
    popen.assert_not_called()


def test_active_task_requires_exact_session_marker_and_state(tmp_path):
    mod = _load()
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    root_id = "019f825b-f25f-70c3-8ee8-071f79fa1c42"
    tasks = repo / "doc/harness/tasks"
    task = tasks / "TASK__active"
    task.mkdir(parents=True)
    (task / "TASK_STATE.yaml").write_text(
        "task_id: TASK__active\nstatus: in_progress\nruntime_verdict: pending\n"
        "touched_paths: []\nplan_session_state: closed\nclosed_at: null\nupdated: now\n"
    )
    import _lib
    _lib.write_active_marker(str(repo), str(task), session_id=root_id)
    marker = tasks / _lib.ACTIVE_SESSIONS_DIRNAME / f"{root_id}.json"
    with mock.patch.object(mod, "resolve_active_task_dir", return_value=str(task)):
        assert mod._active_task_for_session(str(repo), root_id) == str(task.resolve())
    marker.write_text(json.dumps({
        "session_id": "other", "task_dir": str(task), "task_id": "TASK__active",
    }))
    with mock.patch.object(mod, "resolve_active_task_dir", return_value=str(task)):
        assert mod._active_task_for_session(str(repo), root_id) == ""


def test_watcher_reuses_classic_posttooluse_start_receipt(tmp_path, monkeypatch):
    mod = _load()
    codex_home = tmp_path / ".codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    repo = tmp_path / "repo"
    task_dir = repo / "doc/harness/tasks/TASK__watcher"
    task_dir.mkdir(parents=True)
    root_id = "019f825b-f25f-70c3-8ee8-071f79fa1c42"
    child_id = "019f82a6-ce64-75a3-b01d-92f7b0b4fe6f"
    agent_path = "/root/code_review"
    child = codex_home / "sessions/day" / f"rollout-{child_id}.jsonl"
    _write_jsonl(child, _child_events(root_id, child_id, agent_path, str(repo)))
    classic = [{
        "source": "codex_spawn_post_hook", "status": "started", "agent_id": agent_path,
        "agent_type": "code_review", "lens": "review-code", "head_sha": "a" * 40,
        "base_sha": "a" * 40, "diff_fingerprint": "sha256:before",
    }]
    watcher = mod.Watcher(str(repo), root_id)
    with mock.patch.object(mod, "_active_task_for_session", return_value=str(task_dir)), \
         mock.patch.object(mod, "record_subagent_receipt") as record, \
         mock.patch.object(mod, "list_review_receipts", return_value=classic), \
         mock.patch.object(mod, "list_subagent_receipts", return_value=[]):
        for event in _spawn_events(root_id, child_id, "code_review", agent_path):
            watcher.feed(event)
    record.assert_not_called()
    assert watcher.by_path[agent_path]["diff_fingerprint"] == "sha256:before"


def test_duplicate_root_delivery_invalidates_completed_pass(tmp_path, monkeypatch):
    mod = _load()
    codex_home = tmp_path / ".codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    repo = tmp_path / "repo"
    task_dir = repo / "doc/harness/tasks/TASK__watcher"
    task_dir.mkdir(parents=True)
    root_id = "019f825b-f25f-70c3-8ee8-071f79fa1c42"
    child_id = "019f82a6-ce64-75a3-b01d-92f7b0b4fe6f"
    agent_path = "/root/code_review"
    child = codex_home / "sessions/day" / f"rollout-{child_id}.jsonl"
    _write_jsonl(child, _child_events(root_id, child_id, agent_path, str(repo)))
    receipts = []
    def record(_task_dir, receipt):
        entry = {**receipt, "head_sha": receipt.get("head_sha") or "a" * 40,
                 "base_sha": receipt.get("base_sha") or "a" * 40,
                 "diff_fingerprint": receipt.get("diff_fingerprint") or "sha256:before"}
        receipts.append(entry)
        return entry
    watcher = mod.Watcher(str(repo), root_id)
    final = "VERDICT: PASS\nFINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0"
    with mock.patch.object(mod, "_active_task_for_session", return_value=str(task_dir)), \
         mock.patch.object(mod, "review_diff_fingerprint", return_value="sha256:before"), \
         mock.patch.object(mod, "record_subagent_receipt", side_effect=record), \
         mock.patch.object(mod, "list_review_receipts", side_effect=lambda _td: receipts), \
         mock.patch.object(mod, "list_subagent_receipts", return_value=[]):
        for event in _spawn_events(root_id, child_id, "code_review", agent_path):
            watcher.feed(event)
        _write_jsonl(child, _child_events(root_id, child_id, agent_path, str(repo), final))
        delivery = _delivery(agent_path, final)
        watcher.feed(delivery)
        watcher.feed(delivery)
    assert [item.get("verdict") for item in receipts[-2:]] == ["PASS", "PENDING"]
