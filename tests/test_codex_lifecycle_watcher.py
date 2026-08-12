from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest import mock


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "plugin" / "scripts" / "codex_lifecycle_watcher.py"
RUN_ID = "019feefa-2a00-7000-8000-000000000001"
PRIOR_RUN_ID = "019fee8c-4d00-7000-8000-000000000001"


def _load():
    import sys
    sys.path.insert(0, str(SCRIPT.parent))
    spec = importlib.util.spec_from_file_location("codex_lifecycle_watcher_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def _active_binding(task_dir):
    return {
        "task_dir": str(task_dir),
        "run_id": RUN_ID,
    }


def test_seeded_watcher_private_helpers_cannot_append_authority(tmp_path):
    task = tmp_path / "doc/harness/tasks/TASK__seeded-watcher"
    task.mkdir(parents=True)
    _write_task_control(task)
    probe = f'''\
import sys
from pathlib import Path
sys.path.insert(0, {str(SCRIPT.parent)!r})
import codex_lifecycle_watcher as mod

task = Path({str(task)!r})
watcher = mod.Watcher({str(tmp_path)!r}, "019f825b-f25f-70c3-8ee8-071f79fa1c42")
watcher.calls["call_seededWatcher123"] = {{
    "task_name": "qa_cli_seeded",
    "task_dir": str(task),
    "task_run_id": {RUN_ID!r},
    "output_path": "/root/qa_cli_seeded",
    "agent_path": "/root/qa_cli_seeded",
    "child_id": "019f825b-f25f-70c3-8ee8-071f79fa1c43",
}}
mod._active_task_binding_for_session = lambda *_: {{"task_dir": str(task), "run_id": {RUN_ID!r}}}
mod._child_status = lambda *_: ("running", Path("child"), "")
try:
    watcher._maybe_start("call_seededWatcher123")
except PermissionError as exc:
    assert "runtime-owned" in str(exc)
else:
    raise AssertionError("seeded private helper gained receipt authority")
'''
    completed = subprocess.run(
        [sys.executable, "-c", probe], cwd=REPO, text=True, capture_output=True, check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert not (task / "RECEIPTS.jsonl").exists()


def test_replaced_watch_dependency_cannot_inherit_receipt_authority(tmp_path):
    task = tmp_path / "doc/harness/tasks/TASK__replaced-watch-helper"
    task.mkdir(parents=True)
    _write_task_control(task)
    probe = f'''\
import sys
sys.path.insert(0, {str(SCRIPT.parent)!r})
import codex_lifecycle_watcher as mod

def forged_control_root(_):
    mod.record_subagent_receipt({str(task)!r}, {{
        "event": "started",
        "source": "codex_session_watcher:collaboration",
        "runtime_id": "codex:019f825b-f25f-70c3-8ee8-071f79fa1c42:call_watchHelper123:019f825b-f25f-70c3-8ee8-071f79fa1c43",
        "agent_id": "019f825b-f25f-70c3-8ee8-071f79fa1c43",
        "agent_type": "qa-cli",
        "lens": "qa-cli",
        "verdict": "",
        "summary": "",
    }})
    return {str(tmp_path)!r}

mod._authorized_control_root = forged_control_root
try:
    mod.watch(
        {str(tmp_path)!r},
        "019f825b-f25f-70c3-8ee8-071f79fa1c42",
        "/missing/rollout.jsonl",
        0,
    )
except PermissionError as exc:
    assert "runtime-owned" in str(exc)
else:
    raise AssertionError("replaced watch dependency inherited receipt authority")
'''
    completed = subprocess.run(
        [sys.executable, "-c", probe], cwd=REPO, text=True, capture_output=True, check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert not (task / "RECEIPTS.jsonl").exists()


def test_replaced_transitive_validation_helpers_invalidate_watch_binding(tmp_path):
    task = tmp_path / "doc/harness/tasks/TASK__replaced-transitive-helper"
    task.mkdir(parents=True)
    _write_task_control(task)
    root_id = "019f825b-f25f-70c3-8ee8-071f79fa1c42"
    child_id = "019f82a6-ce64-75a3-b01d-92f7b0b4fe6f"
    probe = f'''\
import json, sys
from pathlib import Path
sys.path.insert(0, {str(SCRIPT.parent)!r})
import codex_lifecycle_watcher as mod

repo = Path({str(tmp_path)!r})
rollout = repo / "rollout.jsonl"
rollout.write_text(json.dumps({{"type":"session_meta","payload":{{
    "id":{root_id!r},"session_id":{root_id!r},"cwd":str(repo),"thread_source":"user"
}}}}) + "\\n" + json.dumps({{"type":"response_item","payload":{{
    "type":"function_call","namespace":"collaboration","name":"spawn_agent",
    "call_id":"call_transitive123","arguments":json.dumps({{"task_name":"qa_cli_transitive"}})
}}}}) + "\\n" + json.dumps({{"type":"response_item","payload":{{
    "type":"function_call_output","call_id":"call_transitive123",
    "output":json.dumps({{"task_name":"/root/qa_cli_transitive"}})
}}}}) + "\\n")
original_open = mod._open_trusted_file
mod._sessions_root = lambda: repo
mod._find_rollout = lambda *_args, **_kwargs: rollout
mod._open_trusted_file = original_open
mod._active_task_binding_for_session = lambda *_: {{
    "task_dir": {str(task)!r}, "run_id": {RUN_ID!r},
}}
mod._find_child_by_agent_path = lambda *_args, **_kwargs: {child_id!r}
mod._child_status = lambda *_args, **_kwargs: ("running", None, "")
try:
    mod.watch(str(repo), {root_id!r}, str(rollout), 0, idle_seconds=1)
except PermissionError as exc:
    assert "runtime-owned" in str(exc) or "binding" in str(exc)
'''
    completed = subprocess.run(
        [sys.executable, "-c", probe], cwd=REPO, text=True, capture_output=True, check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert not (task / "RECEIPTS.jsonl").exists()


def _write_task_control(task: Path, *, run_id: str = RUN_ID) -> None:
    (task / "TASK.json").write_text(json.dumps({
        "run_id": run_id,
        "execution_mode": "standard",
        "required_lenses": ["review-code", "qa-cli"],
        "close_receipt_fingerprint": None,
    }) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")


def _snapshot(entries):
    return type("Snapshot", (), {"entries": tuple(entries)})()


def _child_events(
    root_id: str,
    child_id: str,
    agent_path: str,
    cwd: str,
    final: str | None = None,
    *,
    include_final_event: bool = True,
):
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
        if include_final_event:
            events.append({"type": "event_msg", "payload": {
                "type": "agent_message", "phase": "final_answer", "message": final,
            }})
        events.append({"type": "event_msg", "payload": {
            "type": "task_complete", "last_agent_message": final,
        }})
    return events


def _spawn_events(root_id: str, child_id: str, task_name: str, agent_path: str):
    call_id = "call_runtime_123456"
    return [
        {"timestamp": "2026-08-11T05:00:00Z", "type": "response_item", "payload": {
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


def _spawn_events_without_activity(task_name: str, agent_path: str):
    call_id = "call_runtime_output_only"
    return [
        {"timestamp": "2026-08-11T05:00:00Z", "type": "response_item", "payload": {
            "type": "function_call", "namespace": "collaboration", "name": "spawn_agent",
            "call_id": call_id, "arguments": json.dumps({"task_name": task_name, "message": "encrypted"}),
        }},
        {"type": "response_item", "payload": {
            "type": "function_call_output", "call_id": call_id,
            "output": json.dumps({"task_name": agent_path}),
        }},
    ]


def test_spawn_output_accepts_collaboration_agent_name():
    mod = _load()
    event = {
        "type": "response_item",
        "payload": {
            "type": "function_call_output",
            "call_id": "call_runtime_123456",
            "output": json.dumps({"agent_name": "/root/qa_cli_agent_name"}),
        },
    }

    assert mod._spawn_output(event) == (
        "call_runtime_123456",
        "/root/qa_cli_agent_name",
    )


def test_spawn_output_uses_agent_id_not_display_nickname():
    mod = _load()
    child_id = "019f82a6-ce64-75a3-b01d-92f7b0b4fe6f"
    event = {
        "type": "response_item",
        "payload": {
            "type": "function_call_output",
            "call_id": "call_runtime_123456",
            "output": json.dumps({
                "agent_id": child_id,
                "agent_name": "/root/wrong_display_identity",
                "nickname": "DisplayOnly",
            }),
        },
    }

    assert mod._spawn_output(event) == ("call_runtime_123456", child_id)


def _delivery(agent_path: str, final: str):
    return {"type": "response_item", "payload": {
        "type": "agent_message", "author": agent_path, "recipient": "/root",
        "content": [{"type": "input_text", "text": (
            f"Message Type: FINAL_ANSWER\nTask name: /root\nSender: {agent_path}\nPayload:\n{final}"
        )}],
    }}


def _intermediate_message(agent_path: str):
    return {"type": "response_item", "payload": {
        "type": "agent_message", "author": agent_path, "recipient": "/root",
        "content": [
            {"type": "input_text", "text": (
                f"Message Type: MESSAGE\nTask name: /root\nSender: {agent_path}\nPayload:\n"
            )},
            {"type": "encrypted_content", "encrypted_content": "opaque"},
        ],
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
        entry = dict(receipt)
        receipts.append(entry)
        return entry

    watcher = mod.Watcher(str(repo), root_id)
    patches = (
        mock.patch.object(mod, "_active_task_binding_for_session", return_value=_active_binding(task_dir)),
        mock.patch.object(mod, "record_subagent_receipt", side_effect=record),
        mock.patch.object(mod, "receipt_snapshot", side_effect=lambda _td: _snapshot(receipts)),
    )
    for patcher in patches:
        patcher.start()
    try:
        for event in _spawn_events(root_id, child_id, "code_review", agent_path):
            watcher.feed(event)
        assert [(item["event"], item["lens"]) for item in receipts] == [("started", "review-code")]

        final = "VERDICT: PASS\nFINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0\nClean."
        _write_jsonl(
            child,
            _child_events(
                root_id,
                child_id,
                agent_path,
                str(repo),
                final,
                include_final_event=False,
            ),
        )
        watcher.feed(_delivery(agent_path, final))
    finally:
        for patcher in reversed(patches):
            patcher.stop()

    assert [(item["event"], item.get("verdict", "")) for item in receipts] == [
        ("started", ""), ("completed", "PASS"),
    ]
    assert receipts[0]["runtime_id"] == (
        f"codex:{root_id}:call_runtime_123456:{child_id}"
    )
    assert receipts[0]["runtime_id"] == receipts[1]["runtime_id"]


def test_watcher_discovers_child_when_runtime_omits_activity_event(tmp_path, monkeypatch):
    mod = _load()
    codex_home = tmp_path / ".codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    repo = tmp_path / "repo"
    task_dir = repo / "doc/harness/tasks/TASK__watcher"
    task_dir.mkdir(parents=True)
    root_id = "019f825b-f25f-70c3-8ee8-071f79fa1c42"
    child_id = "019f82a6-ce64-75a3-b01d-92f7b0b4fe6f"
    agent_path = "/root/code_review_output_only"
    child = codex_home / "sessions/2026/08/11" / f"rollout-{child_id}.jsonl"
    _write_jsonl(child, _child_events(root_id, child_id, agent_path, str(repo)))

    receipts = []
    watcher = mod.Watcher(str(repo), root_id)
    with mock.patch.object(
        mod, "_active_task_binding_for_session", return_value=_active_binding(task_dir),
    ), mock.patch.object(
        mod, "record_subagent_receipt", side_effect=lambda _td, item: receipts.append(item) or item,
    ), mock.patch.object(
        mod, "receipt_snapshot", side_effect=lambda _td: _snapshot(receipts),
    ):
        for event in _spawn_events_without_activity("code_review_output_only", agent_path):
            watcher.feed(event)
        assert [(item["event"], item["lens"]) for item in receipts] == [
            ("started", "review-code"),
        ]
        final = "VERDICT: PASS\nFINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0\nClean."
        _write_jsonl(
            child,
            _child_events(
                root_id,
                child_id,
                agent_path,
                str(repo),
                final,
                include_final_event=False,
            ),
        )
        watcher.feed(_delivery(agent_path, final))

    assert [(item["event"], item.get("verdict", "")) for item in receipts] == [
        ("started", ""), ("completed", "PASS"),
    ]
    assert receipts[0]["runtime_id"] == (
        f"codex:{root_id}:call_runtime_output_only:{child_id}"
    )
    assert receipts[0]["agent_id"] == agent_path


def test_child_discovery_fails_closed_for_zero_multiple_and_bounds(tmp_path, monkeypatch):
    mod = _load()
    codex_home = tmp_path / ".codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    repo = tmp_path / "repo"
    repo.mkdir()
    root_id = "019f825b-f25f-70c3-8ee8-071f79fa1c42"
    agent_path = "/root/code_review_discovery"

    assert mod._find_child_by_agent_path(root_id, agent_path, str(repo)) == ""

    for child_id in (
        "019f82a6-ce64-75a3-b01d-92f7b0b4fe6f",
        "019f82a6-ce64-75a3-b01d-92f7b0b4fe70",
    ):
        _write_jsonl(
            codex_home / "sessions/day" / f"rollout-{child_id}.jsonl",
            _child_events(root_id, child_id, agent_path, str(repo)),
        )
    assert mod._find_child_by_agent_path(root_id, agent_path, str(repo)) == ""

    monkeypatch.setattr(mod, "MAX_DISCOVERY_FILES", 0)
    assert mod._find_child_by_agent_path(root_id, agent_path, str(repo)) == ""
    monkeypatch.setattr(mod, "MAX_DISCOVERY_FILES", 4096)
    with mock.patch.object(mod.time, "monotonic", side_effect=[0.0, 3.0]):
        assert mod._find_child_by_agent_path(root_id, agent_path, str(repo)) == ""


def test_child_discovery_can_be_scoped_to_registered_root_rollout_dir(tmp_path, monkeypatch):
    mod = _load()
    codex_home = tmp_path / ".codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    repo = tmp_path / "repo"
    repo.mkdir()
    root_id = "019f825b-f25f-70c3-8ee8-071f79fa1c42"
    agent_path = "/root/code_review_scoped"
    child_id = "019f82a6-ce64-75a3-b01d-92f7b0b4fe6f"
    current = codex_home / "sessions/2026/08/12"
    old = codex_home / "sessions/2026/08/11"
    _write_jsonl(
        current / f"rollout-{child_id}.jsonl",
        _child_events(root_id, child_id, agent_path, str(repo)),
    )
    _write_jsonl(
        old / "rollout-019f82a6-ce64-75a3-b01d-92f7b0b4fe70.jsonl",
        _child_events(
            root_id,
            "019f82a6-ce64-75a3-b01d-92f7b0b4fe70",
            agent_path,
            str(repo),
        ),
    )

    assert mod._find_child_by_agent_path(
        root_id, agent_path, str(repo), current,
    ) == child_id
    assert mod._find_child_by_agent_path(
        root_id, agent_path, str(repo), tmp_path,
    ) == ""


def test_activity_event_without_structured_spawn_output_is_ignored(tmp_path):
    mod = _load()
    watcher = mod.Watcher(str(tmp_path), "019f825b-f25f-70c3-8ee8-071f79fa1c42")
    activity = _spawn_events(
        watcher.root_id,
        "019f82a6-ce64-75a3-b01d-92f7b0b4fe6f",
        "code_review_activity_only",
        "/root/code_review_activity_only",
    )[1]
    watcher.feed(activity)
    assert watcher.calls == {}
    assert watcher.by_agent == {}


def test_prior_run_same_agent_path_cannot_replace_current_start(tmp_path):
    mod = _load()
    task_dir = tmp_path / "doc/harness/tasks/TASK__watcher"
    task_dir.mkdir(parents=True)
    root_id = "019f825b-f25f-70c3-8ee8-071f79fa1c42"
    child_id = "019f82a6-ce64-75a3-b01d-92f7b0b4fe6f"
    agent_path = "/root/code_review"
    runtime_id = f"codex:{root_id}:call_runtime_123456:{child_id}"
    prior = {
        "event": "started",
        "runtime_id": runtime_id,
        "source": "codex_session_watcher:collaboration",
        "agent_id": agent_path,
        "agent_type": "code_review",
        "lens": "review-code",
        "task_run_id": PRIOR_RUN_ID,
    }
    recorded = []

    watcher = mod.Watcher(str(tmp_path), root_id)
    with mock.patch.object(
        mod, "_active_task_binding_for_session", return_value=_active_binding(task_dir),
    ), mock.patch.object(
        mod, "_child_status", return_value=("running", None, ""),
    ), mock.patch.object(
        mod, "_find_child_by_agent_path", return_value=child_id,
    ), mock.patch.object(
        mod, "receipt_snapshot", return_value=_snapshot([prior]),
    ), mock.patch.object(
        mod, "record_subagent_receipt", side_effect=lambda _td, item: recorded.append(item) or item,
    ):
        for event in _spawn_events(root_id, child_id, "code_review", agent_path):
            watcher.feed(event)

    assert len(recorded) == 1
    assert recorded[0]["event"] == "started"
    assert recorded[0]["task_run_id"] == RUN_ID


def test_spawn_task_binding_is_immutable_across_task_switch(tmp_path):
    mod = _load()
    task_a = str(tmp_path / "TASK__a")
    task_b = str(tmp_path / "TASK__b")
    active = [task_a]
    root_id = "019f825b-f25f-70c3-8ee8-071f79fa1c42"
    child_id = "019f82a6-ce64-75a3-b01d-92f7b0b4fe6f"
    agent_path = "/root/qa_cli_task_switch"
    watcher = mod.Watcher(str(tmp_path), root_id)
    spawn, activity, output = _spawn_events(
        root_id, child_id, "qa_cli_task_switch", agent_path,
    )
    with mock.patch.object(
        mod, "_active_task_binding_for_session",
        side_effect=lambda *_args: _active_binding(active[0]),
    ), mock.patch.object(mod, "record_subagent_receipt") as record, \
         mock.patch.object(mod, "_child_status", return_value=("running", None, "")), \
         mock.patch.object(mod, "_find_child_by_agent_path", return_value=child_id):
        watcher.feed(spawn)
        assert watcher.calls["call_runtime_123456"]["task_dir"] == task_a
        active[0] = task_b
        watcher.feed(activity)
        watcher.feed(output)

    assert watcher.calls["call_runtime_123456"]["invalid"] is True
    record.assert_not_called()


def test_replayed_spawn_before_current_task_run_is_rejected(tmp_path):
    mod = _load()
    root_id = "019f825b-f25f-70c3-8ee8-071f79fa1c42"
    watcher = mod.Watcher(str(tmp_path), root_id)
    spawn = _spawn_events(
        root_id,
        "019f82a6-ce64-75a3-b01d-92f7b0b4fe6f",
        "qa_cli_prior_run",
        "/root/qa_cli_prior_run",
    )[0]
    spawn["timestamp"] = "2026-08-11T01:00:00Z"
    with mock.patch.object(mod, "_active_task_binding_for_session", return_value={
        "task_dir": "/task",
        "run_id": PRIOR_RUN_ID,
    }):
        watcher.feed(spawn)

    item = watcher.calls["call_runtime_123456"]
    assert item["invalid"] is True
    assert not item.get("started")


def test_replayed_spawn_earlier_in_same_second_is_rejected():
    mod = _load()
    assert mod._event_precedes_run(
        {"timestamp": "2026-08-11T05:00:00.100000Z"},
        "019fef31-1c04-7000-8000-000000000001",
    )


def test_watcher_records_sequential_unique_qa_names_in_one_root(tmp_path, monkeypatch):
    mod = _load()
    codex_home = tmp_path / ".codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    repo = tmp_path / "repo"
    task_dir = repo / "doc/harness/tasks/TASK__watcher"
    task_dir.mkdir(parents=True)
    root_id = "019f825b-f25f-70c3-8ee8-071f79fa1c42"
    runs = [
        ("019f82a6-ce64-75a3-b01d-92f7b0b4fe6f", "qa_cli_first_r1", "/root/qa_cli_first_r1", "call_runtime_first"),
        ("019f82a6-ce64-75a3-b01d-92f7b0b4fe70", "qa_cli_second_r2", "/root/qa_cli_second_r2", "call_runtime_second"),
    ]
    receipts = []

    def record(_task_dir, receipt):
        entry = dict(receipt)
        receipts.append(entry)
        return entry

    watcher = mod.Watcher(str(repo), root_id)
    with mock.patch.object(mod, "_active_task_binding_for_session", return_value=_active_binding(task_dir)), \
         mock.patch.object(mod, "record_subagent_receipt", side_effect=record), \
         mock.patch.object(mod, "receipt_snapshot", side_effect=lambda _td: _snapshot(receipts)):
        for child_id, task_name, agent_path, call_id in runs:
            child = codex_home / "sessions/day" / f"rollout-{child_id}.jsonl"
            _write_jsonl(child, _child_events(root_id, child_id, agent_path, str(repo)))
            events = _spawn_events(root_id, child_id, task_name, agent_path)
            for event in events:
                payload = event["payload"]
                if payload.get("call_id") == "call_runtime_123456":
                    payload["call_id"] = call_id
                if payload.get("event_id") == "call_runtime_123456":
                    payload["event_id"] = call_id
                watcher.feed(event)
            final = "VERDICT: PASS\nQA passed"
            _write_jsonl(child, _child_events(root_id, child_id, agent_path, str(repo), final))
            watcher.feed(_delivery(agent_path, final))

    completed = [item for item in receipts if item["event"] == "completed"]
    assert [item["lens"] for item in completed] == ["qa-cli", "qa-cli"]
    assert [item["verdict"] for item in completed] == ["PASS", "PASS"]
    assert len({item["agent_id"] for item in completed}) == 2


def test_watcher_ignores_intermediate_message_before_final_delivery(tmp_path, monkeypatch):
    mod = _load()
    codex_home = tmp_path / ".codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    repo = tmp_path / "repo"
    task_dir = repo / "doc/harness/tasks/TASK__watcher"
    task_dir.mkdir(parents=True)
    root_id = "019f825b-f25f-70c3-8ee8-071f79fa1c42"
    child_id = "019f82a6-ce64-75a3-b01d-92f7b0b4fe6f"
    agent_path = "/root/qa_cli_status_r1"
    child = codex_home / "sessions/day" / f"rollout-{child_id}.jsonl"
    _write_jsonl(child, _child_events(root_id, child_id, agent_path, str(repo)))
    receipts = []

    def record(_task_dir, receipt):
        entry = dict(receipt)
        receipts.append(entry)
        return entry

    watcher = mod.Watcher(str(repo), root_id)
    final = "VERDICT: PASS\nQA passed"
    with mock.patch.object(mod, "_active_task_binding_for_session", return_value=_active_binding(task_dir)), \
         mock.patch.object(mod, "record_subagent_receipt", side_effect=record), \
         mock.patch.object(mod, "receipt_snapshot", side_effect=lambda _td: _snapshot(receipts)):
        for event in _spawn_events(root_id, child_id, "qa_cli_status_r1", agent_path):
            watcher.feed(event)
        watcher.feed(_intermediate_message(agent_path))
        assert watcher.by_agent[agent_path].get("root_final") is None

        _write_jsonl(child, _child_events(root_id, child_id, agent_path, str(repo), final))
        watcher.feed(_delivery(agent_path, final))

    assert [(item["event"], item.get("verdict", "")) for item in receipts] == [
        ("started", ""), ("completed", "PASS"),
    ]


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
    with mock.patch.object(mod, "_active_task_binding_for_session", return_value=_active_binding("/task")), \
         mock.patch.object(mod, "record_subagent_receipt", side_effect=lambda td, item: receipts.append(item)), \
         mock.patch.object(mod, "receipt_snapshot", return_value=_snapshot([])):
        for event in _spawn_events(root_id, child_id, "qa_cli", agent_path):
            watcher.feed(event)
    assert receipts == []


def test_watcher_restart_replays_persisted_exact_start_after_child_completes(tmp_path, monkeypatch):
    mod = _load()
    codex_home = tmp_path / ".codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    repo = tmp_path / "repo"
    task_dir = repo / "doc/harness/tasks/TASK__watcher"
    task_dir.mkdir(parents=True)
    root_id = "019f825b-f25f-70c3-8ee8-071f79fa1c42"
    child_id = "019f82a6-ce64-75a3-b01d-92f7b0b4fe6f"
    agent_path = "/root/code_review"
    call_id = "call_runtime_123456"
    runtime_id = f"codex:{root_id}:{call_id}:{child_id}"
    final = "VERDICT: PASS\nFINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0"
    child = codex_home / "sessions/day" / f"rollout-{child_id}.jsonl"
    _write_jsonl(child, _child_events(root_id, child_id, agent_path, str(repo), final))
    receipts = [{
        "event": "started", "agent_id": agent_path, "lens": "review-code",
        "agent_type": "code_review", "task_run_id": RUN_ID,
        "runtime_id": runtime_id,
        "source": "codex_session_watcher:collaboration",
    }]

    def record(_task_dir, receipt):
        receipts.append(receipt)
        return receipt

    watcher = mod.Watcher(str(repo), root_id)
    with mock.patch.object(mod, "_active_task_binding_for_session", return_value=_active_binding(task_dir)), \
         mock.patch.object(mod, "record_subagent_receipt", side_effect=record), \
         mock.patch.object(mod, "receipt_snapshot", side_effect=lambda _td: _snapshot(receipts)):
        for event in _spawn_events(root_id, child_id, "code_review", agent_path):
            watcher.feed(event)
        watcher.feed(_delivery(agent_path, final))

    assert [(item["event"], item.get("verdict")) for item in receipts] == [
        ("started", None), ("completed", "PASS"),
    ]


def test_watcher_keeps_completion_pass_when_source_changes(tmp_path, monkeypatch):
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
        entry = dict(receipt)
        receipts.append(entry)
        return entry

    watcher = mod.Watcher(str(repo), root_id)
    with mock.patch.object(mod, "_active_task_binding_for_session", return_value=_active_binding(task_dir)), \
         mock.patch.object(mod, "record_subagent_receipt", side_effect=record), \
         mock.patch.object(mod, "receipt_snapshot", side_effect=lambda _td: _snapshot(receipts)):
        for event in _spawn_events(root_id, child_id, "code_review", agent_path):
            watcher.feed(event)
        final = "VERDICT: PASS\nFINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0"
        _write_jsonl(child, _child_events(root_id, child_id, agent_path, str(repo), final))
        watcher.feed(_delivery(agent_path, final))
    assert receipts[-1]["verdict"] == "PASS"


def test_watcher_records_child_repo_receipt_for_parent_control_workspace(tmp_path, monkeypatch):
    mod = _load()
    codex_home = tmp_path / ".codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    control_root = tmp_path / "workspace"
    session_cwd = control_root / "pay-api"
    session_cwd.mkdir(parents=True)
    task_dir = control_root / "doc/harness/tasks/TASK__watcher-multigit"
    task_dir.mkdir(parents=True)
    root_id = "019f825b-f25f-70c3-8ee8-071f79fa1c42"
    child_id = "019f82a6-ce64-75a3-b01d-92f7b0b4fe6f"
    agent_path = "/root/qa_cli"
    final = "VERDICT: PASS\nQA passed"
    child = codex_home / "sessions/day" / f"rollout-{child_id}.jsonl"
    _write_jsonl(
        child,
        _child_events(root_id, child_id, agent_path, str(session_cwd)),
    )
    receipts = []

    def record(_task_dir, receipt):
        entry = dict(receipt)
        receipts.append(entry)
        return entry

    watcher = mod.Watcher(
        str(control_root), root_id, session_cwd=str(session_cwd)
    )
    with mock.patch.object(
        mod, "_active_task_binding_for_session", return_value=_active_binding(task_dir)
    ), mock.patch.object(
        mod, "record_subagent_receipt", side_effect=record
    ), mock.patch.object(
        mod, "receipt_snapshot", side_effect=lambda _td: _snapshot(receipts)
    ):
        for event in _spawn_events(root_id, child_id, "qa_cli", agent_path):
            watcher.feed(event)
        _write_jsonl(
            child,
            _child_events(root_id, child_id, agent_path, str(session_cwd), final),
        )
        watcher.feed(_delivery(agent_path, final))

    assert [(item["event"], item.get("verdict")) for item in receipts] == [
        ("started", None),
        ("completed", "PASS"),
    ]


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


def test_child_status_does_not_accept_prompt_task_marker_as_turn_boundary(tmp_path, monkeypatch):
    mod = _load()
    codex_home = tmp_path / ".codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    root_id = "019f825b-f25f-70c3-8ee8-071f79fa1c42"
    child_id = "019f82a6-ce64-75a3-b01d-92f7b0b4fe6f"
    agent_path = "/root/qa_cli"
    child = codex_home / "sessions/day" / f"rollout-{child_id}.jsonl"
    events = _child_events(root_id, child_id, agent_path, "/repo")[:1]
    events.extend([
        {"type": "response_item", "payload": {
            "type": "message", "role": "user", "content": [
                {"type": "input_text", "text": "task_name: qa_cli_marker\nRun QA."},
            ],
        }},
        {"type": "event_msg", "payload": {
            "type": "agent_message", "phase": "final_answer", "message": "VERDICT: PASS",
        }},
        {"type": "event_msg", "payload": {
            "type": "task_complete", "last_agent_message": "VERDICT: PASS",
        }},
    ])
    _write_jsonl(child, events)

    assert mod._child_status(child_id, root_id, agent_path, "/repo")[0] == "pending"


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


def test_ensure_registers_once_without_forking_for_exact_root_rollout(tmp_path, monkeypatch):
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

    assert mod.ensure(str(repo), root_id)
    first = json.loads(mod._state_path(str(repo), root_id).read_text())
    with mock.patch.object(
        mod, "_find_rollout", side_effect=AssertionError("fast path must not scan")
    ), mock.patch.object(
        mod, "_atomic_json", side_effect=AssertionError("fast path must not rewrite")
    ):
        assert mod.ensure(str(repo), root_id)

    state = json.loads(mod._state_path(str(repo), root_id).read_text())
    assert state == first
    assert state["version"] == mod.REGISTRATION_VERSION
    assert state["owner"] == mod.REGISTRATION_OWNER
    assert state["repo_root"] == str(repo.resolve())
    assert state["offset"] == rollout.stat().st_size
    assert "pid" not in state
    assert "process_start" not in state


def test_ensure_stops_recovery_when_deadline_expires_after_discovery(tmp_path, monkeypatch):
    mod = _load()
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    root_id = "019f825b-f25f-70c3-8ee8-071f79fa1c42"
    rollout = tmp_path / f"rollout-{root_id}.jsonl"
    with mock.patch.object(mod, "_valid_current_registration", return_value=False), \
         mock.patch.object(mod, "_find_rollout", return_value=rollout), \
         mock.patch.object(mod, "_deadline_expired", return_value=True), \
         mock.patch.object(mod, "_open_trusted_file") as open_rollout:
        assert not mod.ensure(str(repo), root_id, deadline=1.0)
    open_rollout.assert_not_called()


def test_ensure_replaces_legacy_process_state_with_registration(tmp_path, monkeypatch):
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
    state_path = mod._state_path(str(repo), root_id)
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps({
        "pid": 123, "thread_id": root_id, "repo_root": str(repo.resolve()),
    }))
    assert mod.ensure(str(repo), root_id)
    state = json.loads(state_path.read_text())
    assert state["version"] == mod.REGISTRATION_VERSION
    assert state["owner"] == mod.REGISTRATION_OWNER
    assert "pid" not in state


def test_ensure_restarts_old_registration_at_current_offset(tmp_path, monkeypatch):
    mod = _load()
    codex_home = tmp_path / ".codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    root_id = "019f825b-f25f-70c3-8ee8-071f79fa1c42"
    rollout = codex_home / "sessions/day" / f"rollout-{root_id}.jsonl"
    _write_jsonl(rollout, [{"type": "session_meta", "payload": {
        "session_id": root_id, "id": root_id, "cwd": str(repo), "thread_source": "user",
    }}, {"type": "event_msg", "payload": {"type": "old-event"}}])
    state_path = mod._state_path(str(repo), root_id)
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps({
        "version": 3,
        "owner": "codex_root_hook",
        "thread_id": root_id,
        "repo_root": str(repo.resolve()),
        "session_cwd": str(repo.resolve()),
        "rollout": str(rollout),
        "offset": 0,
        "registered_at": 1.0,
    }))

    assert mod.ensure(str(repo), root_id)
    state = json.loads(state_path.read_text())
    assert state["version"] == mod.REGISTRATION_VERSION
    assert state["offset"] == rollout.stat().st_size
    assert state["registered_at"] > 1.0


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
    runtime_dir = mod._runtime_dir(str(repo))
    runtime_dir.parent.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    runtime_dir.symlink_to(outside, target_is_directory=True)
    assert not mod.ensure(str(repo), root_id)


def test_root_meta_rejects_path_replacement_after_descriptor_open(tmp_path, monkeypatch):
    mod = _load()
    codex_home = tmp_path / ".codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    repo = tmp_path / "repo"
    repo.mkdir()
    root_id = "019f825b-f25f-70c3-8ee8-071f79fa1c42"
    rollout = codex_home / "sessions/day" / f"rollout-{root_id}.jsonl"
    _write_jsonl(rollout, [{"type": "session_meta", "payload": {
        "session_id": root_id, "id": root_id, "cwd": str(repo), "thread_source": "user",
    }}])
    original = mod._root_meta_from_handle

    def replace_after_read(handle, thread_id, repo_root):
        result = original(handle, thread_id, repo_root)
        prior = rollout.with_suffix(".prior")
        rollout.rename(prior)
        _write_jsonl(rollout, [{"type": "session_meta", "payload": {
            "session_id": root_id, "id": root_id, "cwd": str(repo), "thread_source": "user",
        }}])
        return result

    with mock.patch.object(mod, "_root_meta_from_handle", side_effect=replace_after_read):
        assert not mod._root_meta(rollout, root_id, str(repo.resolve()))


def test_child_status_rejects_path_replacement_during_parse(tmp_path, monkeypatch):
    mod = _load()
    codex_home = tmp_path / ".codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    repo = tmp_path / "repo"
    repo.mkdir()
    root_id = "019f825b-f25f-70c3-8ee8-071f79fa1c42"
    child_id = "019f82a6-ce64-75a3-b01d-92f7b0b4fe6f"
    agent_path = "/root/qa_cli"
    final = "VERDICT: PASS"
    rollout = codex_home / "sessions/day" / f"rollout-{child_id}.jsonl"
    _write_jsonl(rollout, _child_events(root_id, child_id, agent_path, str(repo), final))
    original = mod._load_json_line
    swapped = False

    def replace_after_first_line(raw):
        nonlocal swapped
        result = original(raw)
        if not swapped:
            swapped = True
            prior = rollout.with_suffix(".prior")
            rollout.rename(prior)
            _write_jsonl(rollout, _child_events(root_id, child_id, agent_path, str(repo), final))
        return result

    with mock.patch.object(mod, "_load_json_line", side_effect=replace_after_first_line):
        status, _, _ = mod._child_status(child_id, root_id, agent_path, str(repo.resolve()))
    assert status == "invalid"


def test_rollout_rejects_group_or_world_writable_session_ancestor(tmp_path, monkeypatch):
    mod = _load()
    codex_home = tmp_path / ".codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    root_id = "019f825b-f25f-70c3-8ee8-071f79fa1c42"
    rollout = codex_home / "sessions/day" / f"rollout-{root_id}.jsonl"
    _write_jsonl(rollout, [{"type": "session_meta", "payload": {"id": root_id}}])
    rollout.parent.chmod(0o777)
    try:
        assert mod._find_rollout(root_id) is None
    finally:
        rollout.parent.chmod(0o700)


def test_rollout_rejects_group_or_world_writable_root_and_child_files(tmp_path, monkeypatch):
    mod = _load()
    codex_home = tmp_path / ".codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    repo = tmp_path / "repo"
    repo.mkdir()
    root_id = "019f825b-f25f-70c3-8ee8-071f79fa1c42"
    child_id = "019f82a6-ce64-75a3-b01d-92f7b0b4fe6f"
    root = codex_home / "sessions/day" / f"rollout-{root_id}.jsonl"
    child = codex_home / "sessions/day" / f"rollout-{child_id}.jsonl"
    _write_jsonl(root, [{"type": "session_meta", "payload": {
        "session_id": root_id, "id": root_id, "cwd": str(repo), "thread_source": "user",
    }}])
    _write_jsonl(child, _child_events(root_id, child_id, "/root/qa_cli", str(repo)))
    for mode in (0o620, 0o602):
        root.chmod(mode)
        child.chmod(mode)
        assert mod._find_rollout(root_id) is None
        assert mod._child_status(child_id, root_id, "/root/qa_cli", str(repo.resolve()))[0] == "pending"
    root.chmod(0o600)
    child.chmod(0o600)


def test_registrations_revalidates_exact_root_and_rejects_symlink(tmp_path, monkeypatch):
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
    assert mod.ensure(str(repo), root_id)
    assert [item["thread_id"] for item in mod.registrations(str(repo))] == [root_id]

    state_path = mod._state_path(str(repo), root_id)
    outside = tmp_path / "outside.json"
    outside.write_text(state_path.read_text())
    state_path.unlink()
    state_path.symlink_to(outside)
    assert mod.registrations(str(repo)) == []

    state_path.unlink()
    os.link(outside, state_path)
    assert mod.registrations(str(repo)) == []


def test_registrations_prunes_expired_root_state(tmp_path, monkeypatch):
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
    assert mod.ensure(str(repo), root_id)
    state_path = mod._state_path(str(repo), root_id)
    state = json.loads(state_path.read_text())
    expired = 1.0
    state["registered_at"] = expired
    state_path.write_text(json.dumps(state))
    os.utime(rollout, (expired, expired))
    with mock.patch.object(mod.time, "time", return_value=mod.REGISTRATION_TTL_SECONDS + 10):
        assert mod.registrations(str(repo)) == []
    assert not state_path.exists()


def test_manager_starts_one_daemon_worker_per_registration_and_stops(tmp_path):
    mod = _load()
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    registrations = [
        {"thread_id": "019f825b-f25f-70c3-8ee8-071f79fa1c42", "rollout": "/one", "offset": 11},
        {"thread_id": "019f82a6-ce64-75a3-b01d-92f7b0b4fe6f", "rollout": "/two", "offset": 22},
    ]
    calls = []

    def fake_watch(repo_root, thread_id, rollout, offset, *, stop_event, **_kwargs):
        calls.append((repo_root, thread_id, rollout, offset))
        stop_event.wait(0.05)
        return 0

    manager = mod.WatcherManager(str(repo), scan_seconds=0.01)
    with mock.patch.object(mod, "registrations", return_value=registrations), \
         mock.patch.object(mod, "watch", side_effect=fake_watch):
        assert manager.scan_once() == 2
        assert manager.scan_once() == 0
        manager.stop()
    assert {call[1] for call in calls} == {item["thread_id"] for item in registrations}
    assert all(worker.daemon for worker in manager.workers.values())


def test_manager_caps_simultaneous_workers(tmp_path):
    mod = _load()
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    items = [
        {"thread_id": f"019f825b-f25f-70c3-8ee8-071f79fa1c4{i}", "rollout": f"/{i}", "offset": i}
        for i in range(3)
    ]

    def fake_watch(*_args, stop_event, **_kwargs):
        stop_event.wait(1)
        return 0

    manager = mod.WatcherManager(str(repo), max_workers=2)
    with mock.patch.object(mod, "registrations", return_value=items), \
         mock.patch.object(mod, "watch", side_effect=fake_watch):
        assert manager.scan_once() == 2
        manager.stop()
    assert len(manager.workers) == 2


def test_manager_restart_replays_immutable_registration_offset(tmp_path):
    mod = _load()
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    registration = {
        "thread_id": "019f825b-f25f-70c3-8ee8-071f79fa1c42",
        "rollout": "/root-rollout", "offset": 777,
    }
    calls = []

    def fake_watch(_repo, _thread, _rollout, offset, *, stop_event, **_kwargs):
        calls.append(offset)
        return 0

    with mock.patch.object(mod, "registrations", return_value=[registration]), \
         mock.patch.object(mod, "watch", side_effect=fake_watch):
        first = mod.WatcherManager(str(repo))
        second = mod.WatcherManager(str(repo))
        assert first.scan_once() == 1
        first.workers[registration["thread_id"]].join()
        assert second.scan_once() == 1
        second.workers[registration["thread_id"]].join()
    assert calls == [777, 777]


def test_manager_restarts_failed_worker_in_same_manager(tmp_path):
    mod = _load()
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    registration = {
        "thread_id": "019f825b-f25f-70c3-8ee8-071f79fa1c42",
        "rollout": "/root-rollout",
        "offset": 777,
    }
    calls = []

    def fake_watch(_repo, _thread, _rollout, offset, *, stop_event, **_kwargs):
        calls.append(offset)
        if len(calls) == 1:
            raise RuntimeError("late terminal receipt rejected")
        return 0

    manager = mod.WatcherManager(str(repo))
    with mock.patch.object(mod, "registrations", return_value=[registration]), \
         mock.patch.object(mod, "watch", side_effect=fake_watch):
        assert manager.scan_once() == 1
        manager.workers[registration["thread_id"]].join()
        assert manager.worker_results[registration["thread_id"]] == 4
        assert manager.scan_once() == 1
        manager.workers[registration["thread_id"]].join()

    assert calls == [777, 777]
    assert manager.worker_results[registration["thread_id"]] == 0


def test_managers_use_cross_process_lease_for_same_registration(tmp_path):
    mod = _load()
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    registration = {
        "thread_id": "019f825b-f25f-70c3-8ee8-071f79fa1c42",
        "rollout": "/root-rollout", "offset": 777,
    }
    entered = mod.threading.Event()

    def fake_watch(*_args, stop_event, **_kwargs):
        entered.set()
        stop_event.wait(1)
        return 0

    with mock.patch.object(mod, "registrations", return_value=[registration]), \
         mock.patch.object(mod, "watch", side_effect=fake_watch):
        first = mod.WatcherManager(str(repo))
        second = mod.WatcherManager(str(repo))
        assert first.scan_once() == 1
        assert entered.wait(1)
        assert second.scan_once() == 0
        first.stop()
        assert second.scan_once() == 1
        second.stop()


def test_watch_inherits_rollout_idle_age_instead_of_resetting_lifetime(tmp_path, monkeypatch):
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
    os.utime(rollout, (1, 1))
    with mock.patch.object(mod.time, "time", return_value=1000):
        assert mod.watch(
            str(repo), root_id, str(rollout), rollout.stat().st_size,
            stop_event=mod.threading.Event(), idle_seconds=10,
        ) == 0


def test_main_retries_bounded_rollout_creation_race():
    mod = _load()
    root_id = "019f825b-f25f-70c3-8ee8-071f79fa1c42"
    with mock.patch.object(mod, "ensure", side_effect=[False, False, True]) as ensure, \
         mock.patch.object(mod.time, "monotonic", return_value=0.0), \
         mock.patch.object(mod.time, "sleep"):
        assert mod.main([
            "--ensure", "--repo-root", "/repo", "--thread-id", root_id,
            "--retry-seconds", "1.0",
        ]) == 0
    assert ensure.call_count == 3


def test_active_task_requires_exact_session_marker_and_state(tmp_path):
    mod = _load()
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    root_id = "019f825b-f25f-70c3-8ee8-071f79fa1c42"
    tasks = repo / "doc/harness/tasks"
    task = tasks / "TASK__active"
    task.mkdir(parents=True)
    _write_task_control(task)
    import _lib
    control = _lib.read_task_control(task)
    (tasks / _lib.ACTIVE_SESSIONS_DIRNAME).mkdir(parents=True)
    marker = tasks / _lib.ACTIVE_SESSIONS_DIRNAME / f"{root_id}.json"
    marker.write_text(json.dumps({
        "session_id": root_id, "task_dir": str(task), "task_id": task.name,
        "run_id": control["run_id"], "updated": _lib.now_iso(),
    }) + "\n", encoding="utf-8")
    (tasks / ".active").write_text(str(task) + "\n", encoding="utf-8")
    with mock.patch.object(mod, "resolve_active_task_dir", return_value=str(task)):
        assert mod._active_task_for_session(str(repo), root_id) == str(task.resolve())
        assert mod._active_task_binding_for_session(str(repo), root_id)["run_id"]
        control["run_id"] = _lib.new_uuid7()
        (task / "TASK.json").write_text(
            json.dumps(control, indent=2, sort_keys=True) + "\n", encoding="utf-8",
        )
        assert mod._active_task_binding_for_session(str(repo), root_id) == {}
    marker.write_text(json.dumps({
        "session_id": "other", "task_dir": str(task), "task_id": "TASK__active",
    }))
    with mock.patch.object(mod, "resolve_active_task_dir", return_value=str(task)):
        assert mod._active_task_for_session(str(repo), root_id) == ""


def test_validated_task_dir_rejects_symlinked_tasks_root(tmp_path):
    mod = _load()
    attacker = tmp_path / "attacker"
    victim = tmp_path / "victim"
    (attacker / ".git").mkdir(parents=True)
    victim_task = victim / "doc/harness/tasks/TASK__victim"
    victim_task.mkdir(parents=True)
    _write_task_control(victim_task)
    attacker_harness = attacker / "doc/harness"
    attacker_harness.mkdir(parents=True)
    (attacker_harness / "tasks").symlink_to(victim / "doc/harness/tasks", target_is_directory=True)

    assert mod._validated_task_dir(str(attacker), "TASK__victim") == ""


def test_validated_task_dir_accepts_root_owned_workspace_ancestors(tmp_path):
    mod = _load()
    repo = tmp_path / "repo"
    task = repo / "doc/harness/tasks/TASK__root-workspace"
    task.mkdir(parents=True)
    (repo / ".git").mkdir()
    _write_task_control(task)
    root_owned = {
        repo / "doc",
        repo / "doc/harness",
        repo / "doc/harness/tasks",
    }
    original_lstat = mod.os.lstat

    class RootOwnedStat:
        def __init__(self, wrapped):
            self._wrapped = wrapped
            self.st_uid = 0

        def __getattr__(self, name):
            return getattr(self._wrapped, name)

    def root_owned_ancestors(path):
        result = original_lstat(path)
        if Path(path) in root_owned:
            return RootOwnedStat(result)
        return result

    with mock.patch.object(mod.os, "lstat", side_effect=root_owned_ancestors):
        assert mod._validated_task_dir(
            str(repo), "TASK__root-workspace",
        ) == str(task.resolve())


def test_validated_task_dir_rejects_writable_root_owned_ancestor(tmp_path):
    mod = _load()
    repo = tmp_path / "repo"
    task = repo / "doc/harness/tasks/TASK__unsafe-root-workspace"
    task.mkdir(parents=True)
    (repo / ".git").mkdir()
    _write_task_control(task)
    unsafe = repo / "doc/harness"
    original_lstat = mod.os.lstat

    class WritableRootStat:
        def __init__(self, wrapped):
            self._wrapped = wrapped
            self.st_uid = 0
            self.st_mode = wrapped.st_mode | 0o022

        def __getattr__(self, name):
            return getattr(self._wrapped, name)

    def writable_root_ancestor(path):
        result = original_lstat(path)
        if Path(path) == unsafe:
            return WritableRootStat(result)
        return result

    with mock.patch.object(mod.os, "lstat", side_effect=writable_root_ancestor):
        assert mod._validated_task_dir(
            str(repo), "TASK__unsafe-root-workspace",
        ) == ""


def test_duplicate_identical_root_delivery_is_idempotent(tmp_path, monkeypatch):
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
        entry = dict(receipt)
        receipts.append(entry)
        return entry
    watcher = mod.Watcher(str(repo), root_id)
    final = "VERDICT: PASS\nFINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0"
    with mock.patch.object(mod, "_active_task_binding_for_session", return_value=_active_binding(task_dir)), \
         mock.patch.object(mod, "record_subagent_receipt", side_effect=record), \
         mock.patch.object(mod, "receipt_snapshot", side_effect=lambda _td: _snapshot(receipts)):
        for event in _spawn_events(root_id, child_id, "code_review", agent_path):
            watcher.feed(event)
        _write_jsonl(child, _child_events(root_id, child_id, agent_path, str(repo), final))
        delivery = _delivery(agent_path, final)
        watcher.feed(delivery)
        watcher.feed(delivery)
        assert [item.get("verdict") for item in receipts] == [None, "PASS"]
        watcher.feed(_delivery(
            agent_path,
            "VERDICT: FAIL\nFINDING_COUNTS: FIX_NOW=1 INVESTIGATE=0 OPTIONAL=0",
        ))
    assert [item.get("verdict") for item in receipts] == [None, "PASS", "PENDING"]
