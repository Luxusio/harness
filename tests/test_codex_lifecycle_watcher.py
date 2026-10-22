from __future__ import annotations

import importlib.util
import contextlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
from datetime import datetime, timezone
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


def test_replaced_static_watcher_identity_helper_invalidates_binding(tmp_path):
    task = tmp_path / "doc/harness/tasks/TASK__replaced-static-helper"
    task.mkdir(parents=True)
    _write_task_control(task)
    probe = f'''\
import sys
sys.path.insert(0, {str(SCRIPT.parent)!r})
import codex_lifecycle_watcher as mod

mod.Watcher._receipt_source = staticmethod(lambda _item: "codex_session_watcher:collaboration")
try:
    mod.record_subagent_receipt({str(task)!r}, {{
        "event": "started",
        "source": "codex_session_watcher:collaboration",
        "runtime_id": "codex:019f825b-f25f-70c3-8ee8-071f79fa1c42:call_staticHelper123:019f825b-f25f-70c3-8ee8-071f79fa1c43",
        "agent_id": "/root/qa_cli_static",
        "agent_type": "qa-cli",
        "lens": "qa-cli",
        "verdict": "",
        "summary": "",
    }})
except PermissionError as exc:
    assert "runtime-owned" in str(exc)
else:
    raise AssertionError("replaced static helper did not invalidate binding")
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


def _write_exact_session_binding(repo: Path, root_id: str) -> tuple[str, str]:
    task = repo / "doc/harness/tasks/TASK__registration"
    task.mkdir(parents=True, exist_ok=True)
    _write_task_control(task)
    sessions = repo / "doc/harness/tasks/.active_sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    (sessions / f"{root_id}.json").write_text(json.dumps({
        "session_id": root_id,
        "task_dir": str(task),
        "task_id": task.name,
        "run_id": RUN_ID,
        "updated": "2026-08-11T05:00:00Z",
    }) + "\n", encoding="utf-8")
    return task.name, RUN_ID


def _write_jsonl(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")


def _rollout_path(codex_home: Path, thread_id: str) -> Path:
    created = datetime.fromtimestamp(
        int(thread_id.replace("-", "")[:12], 16) / 1000,
        tz=timezone.utc,
    )
    return (
        codex_home / "sessions" / f"{created:%Y}" / f"{created:%m}" / f"{created:%d}"
        / f"rollout-{thread_id}.jsonl"
    )


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


def _spawn_events(
    root_id: str,
    child_id: str,
    task_name: str,
    agent_path: str,
    call_id: str = "call_runtime_123456",
):
    return [
        {"timestamp": "2026-08-11T05:00:00Z", "type": "response_item", "payload": {
            "type": "function_call", "namespace": "collaboration", "name": "spawn_agent",
            "call_id": call_id, "arguments": json.dumps({"task_name": task_name, "message": "encrypted"}),
        }},
        {"type": "event_msg", "payload": {
            "type": "item_completed", "item": {
                "type": "SubAgentActivity", "kind": "started", "id": call_id,
                "agent_thread_id": child_id, "agent_path": agent_path,
            },
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


def test_spawn_call_accepts_symmetric_review_name_aliases():
    mod = _load()
    for task_name in (
        "code_review_final", "review_code_final",
        "security_review_final", "review_security_final",
    ):
        event = {
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "namespace": "collaboration",
                "name": "spawn_agent",
                "call_id": "call_runtime_123456",
                "arguments": json.dumps({"task_name": task_name, "message": "review"}),
            },
        }
        assert mod._spawn_call(event) == ("call_runtime_123456", task_name)


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
    agent_path = "/root/review_code_final"
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
        for event in _spawn_events(root_id, child_id, "review_code_final", agent_path):
            watcher.feed(event)
        assert [(item["event"], item["lens"]) for item in receipts] == [("started", "review-code")]

        final = (
            "VERDICT: PASS\n"
            "FINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0\n"
            "Clean.\n  "
        )
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
    assert receipts[1]["summary"] == final


def test_watcher_preserves_exact_verdictless_review_final_for_pending_detail(tmp_path):
    mod = _load()
    task_dir = tmp_path / "doc/harness/tasks/TASK__watcher"
    task_dir.mkdir(parents=True)
    watcher = mod.Watcher(
        str(tmp_path), "019f825b-f25f-70c3-8ee8-071f79fa1c42",
    )
    malformed_final = "Review completed, but the verdict envelope is missing."
    item = {
        "task_name": "code_review_verdictless",
        "task_dir": str(task_dir),
        "task_run_id": RUN_ID,
        "agent_path": "/root/code_review_verdictless",
        "child_id": "019f82a6-ce64-75a3-b01d-92f7b0b4fe6f",
        "runtime_id": (
            "codex:019f825b-f25f-70c3-8ee8-071f79fa1c42:"
            "call_verdictless_123456:019f82a6-ce64-75a3-b01d-92f7b0b4fe6f"
        ),
        "started": True,
        "root_final": malformed_final,
    }
    receipts = []
    with mock.patch.object(
        mod, "_active_task_binding_for_session", return_value=_active_binding(task_dir),
    ), mock.patch.object(
        mod, "_child_status",
        return_value=("complete", tmp_path / "child.jsonl", malformed_final),
    ), mock.patch.object(
        mod, "record_subagent_receipt",
        side_effect=lambda _task, receipt: receipts.append(dict(receipt)) or receipt,
    ), mock.patch.object(
        mod, "receipt_snapshot", side_effect=lambda _task: _snapshot(receipts),
    ):
        watcher._maybe_complete(item)

    assert item["invalid"] is True
    assert len(receipts) == 1
    assert receipts[0]["verdict"] == "PENDING"
    assert receipts[0]["summary"] == malformed_final


def test_child_status_accepts_a_formal_final_at_the_detail_size_limit(
    tmp_path, monkeypatch,
):
    mod = _load()
    codex_home = tmp_path / ".codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    root_id = "019f825b-f25f-70c3-8ee8-071f79fa1c42"
    child_id = "019f82a6-ce64-75a3-b01d-92f7b0b4fe6f"
    agent_path = "/root/code_review_size_limit"
    prefix = (
        "VERDICT: PASS\n"
        "FINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0\n"
    )
    final = prefix + "x" * (mod._REVIEW_DETAIL_MAX_BYTES - len(prefix))
    child = _rollout_path(codex_home, child_id)
    _write_jsonl(
        child,
        _child_events(root_id, child_id, agent_path, str(tmp_path), final),
    )

    status, path, observed = mod._child_status(
        child_id, root_id, agent_path, str(tmp_path),
    )

    assert status == "complete"
    assert path == child
    assert observed == final


def test_deeply_nested_root_record_is_quarantined_without_an_exception():
    mod = _load()
    raw = ("[" * 10000 + "]" * 10000 + "\n").encode()
    assert mod._load_json_line(raw) is None


def test_deeply_nested_child_record_is_rejected_without_an_exception(
    tmp_path, monkeypatch,
):
    mod = _load()
    codex_home = tmp_path / ".codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    root_id = "019f825b-f25f-70c3-8ee8-071f79fa1c42"
    child_id = "019f82a6-ce64-75a3-b01d-92f7b0b4fe6f"
    agent_path = "/root/code_review_nested_record"
    child = _rollout_path(codex_home, child_id)
    _write_jsonl(
        child,
        _child_events(root_id, child_id, agent_path, str(tmp_path)),
    )
    with child.open("ab") as handle:
        handle.write(("[" * 10000 + "]" * 10000 + "\n").encode())

    status, path, final = mod._child_status(
        child_id, root_id, agent_path, str(tmp_path),
    )

    assert status == "invalid"
    assert path == child
    assert final == ""


def test_watcher_requires_activity_when_runtime_emits_only_spawn_output(tmp_path, monkeypatch):
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
        assert receipts == []
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
    assert receipts == []


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


def test_activity_rejects_legacy_and_direct_envelopes():
    mod = _load()
    canonical = _spawn_events(
        "019f825b-f25f-70c3-8ee8-071f79fa1c42",
        "019f82a6-ce64-75a3-b01d-92f7b0b4fe6f",
        "qa_cli_activity",
        "/root/qa_cli_activity",
    )[1]
    direct = {"type": "event_msg", "payload": dict(canonical["payload"]["item"])}
    legacy = json.loads(json.dumps(canonical))
    legacy["payload"]["item"].update({
        "type": "sub_agent_activity",
        "event_id": legacy["payload"]["item"].pop("id"),
    })
    assert mod._spawn_activity(direct) is None
    assert mod._spawn_activity(legacy) is None


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
         mock.patch.object(mod, "_child_status", return_value=("running", None, "")):
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
            child = _rollout_path(codex_home, child_id)
            _write_jsonl(child, _child_events(root_id, child_id, agent_path, str(repo)))
            events = _spawn_events(root_id, child_id, task_name, agent_path, call_id)
            for event in events:
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
    child = _rollout_path(codex_home, child_id)
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


def test_watcher_replays_child_that_completed_after_registered_spawn(tmp_path, monkeypatch):
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
        watcher.feed(_delivery(agent_path, final))
    assert [(item["event"], item.get("verdict")) for item in receipts] == [
        ("started", None), ("completed", "PASS"),
    ]


def test_activity_and_structured_output_identity_must_match(tmp_path, monkeypatch):
    mod = _load()
    task_dir = tmp_path / "doc/harness/tasks/TASK__watcher"
    task_dir.mkdir(parents=True)
    root_id = "019f825b-f25f-70c3-8ee8-071f79fa1c42"
    child_id = "019f82a6-ce64-75a3-b01d-92f7b0b4fe6f"
    events = _spawn_events(root_id, child_id, "qa_cli", "/root/qa_cli")
    events[-1]["payload"]["output"] = json.dumps({"task_name": "/root/qa_cli_other"})
    watcher = mod.Watcher(str(tmp_path), root_id)
    receipts = []
    with mock.patch.object(mod, "_active_task_binding_for_session", return_value=_active_binding(task_dir)), \
         mock.patch.object(mod, "record_subagent_receipt", side_effect=lambda _td, row: receipts.append(row)), \
         mock.patch.object(mod, "receipt_snapshot", return_value=_snapshot([])):
        for event in events:
            watcher.feed(event)
    assert receipts == []
    assert watcher.calls["call_runtime_123456"]["invalid"] is True


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
    child = _rollout_path(codex_home, child_id)
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
    child = _rollout_path(codex_home, child_id)
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
    child = _rollout_path(codex_home, child_id)
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
    child = _rollout_path(codex_home, child_id)
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
    child = _rollout_path(codex_home, child_id)
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
    child = _rollout_path(codex_home, child_id)
    _write_jsonl(child, _child_events(root_id, child_id, "/root/qa_cli", "/repo"))
    with child.open("ab") as handle:
        handle.write(b'{"type":"event_msg","payload":{"type":"agent_message"')
    assert mod._child_status(child_id, root_id, "/root/qa_cli", "/repo")[0] == "pending"


def test_child_status_retries_before_child_metadata_is_written(tmp_path, monkeypatch):
    mod = _load()
    codex_home = tmp_path / ".codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    child_id = "019f82a6-ce64-75a3-b01d-92f7b0b4fe6f"
    child = _rollout_path(codex_home, child_id)
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
    child = _rollout_path(codex_home, child_id)
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
    child = _rollout_path(codex_home, child_id)
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
    link = _rollout_path(codex_home, child_id)
    link.parent.mkdir(parents=True)
    link.symlink_to(real)
    assert mod._find_rollout(child_id) is None


def test_find_rollout_uses_uuid_local_day_without_recursive_fallback(tmp_path, monkeypatch):
    mod = _load()
    codex_home = tmp_path / ".codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    child_id = "019ff6e0-b765-7aa3-b9cb-e6d4f5c8b1b7"
    created = mod.datetime.fromtimestamp(mod.uuid7_timestamp_ms(child_id) / 1000).astimezone()
    rollout = (
        codex_home / "sessions" / f"{created:%Y}" / f"{created:%m}" / f"{created:%d}"
        / f"rollout-now-{child_id}.jsonl"
    )
    _write_jsonl(rollout, [{"type": "session_meta", "payload": {"id": child_id}}])
    assert mod._find_rollout(child_id) == rollout
    assert "walk" not in mod._find_rollout.__code__.co_names


def test_find_rollout_honors_local_date_at_utc_boundary(tmp_path, monkeypatch):
    mod = _load()
    codex_home = tmp_path / ".codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("TZ", "Asia/Seoul")
    if hasattr(mod.time, "tzset"):
        mod.time.tzset()
    try:
        thread_id = "019ff16c-e8de-78c3-8f18-879fd945e8bc"
        local = codex_home / "sessions/2026/08/12" / f"rollout-local-{thread_id}.jsonl"
        utc = codex_home / "sessions/2026/08/11" / f"rollout-utc-{thread_id}.jsonl"
        _write_jsonl(local, [{"type": "session_meta", "payload": {"id": thread_id}}])
        _write_jsonl(utc, [{"type": "session_meta", "payload": {"id": thread_id}}])
        assert mod._find_rollout(thread_id) == local
    finally:
        monkeypatch.delenv("TZ", raising=False)
        if hasattr(mod.time, "tzset"):
            mod.time.tzset()


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


def test_ensure_refreshes_offset_when_task_generation_changes(tmp_path, monkeypatch):
    mod = _load()
    codex_home = tmp_path / ".codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    root_id = "019f825b-f25f-70c3-8ee8-071f79fa1c42"
    rollout = _rollout_path(codex_home, root_id)
    _write_jsonl(rollout, [{"type": "session_meta", "payload": {
        "session_id": root_id, "id": root_id, "cwd": str(repo),
        "thread_source": "user",
    }}])
    first_run = RUN_ID
    second_run = "019f825b-f25f-70c3-8ee8-071f79fa1c99"
    second_task_id = "TASK__" + "x" * 180
    with mock.patch.object(mod, "_registration_binding_matches", return_value=True):
        assert mod.ensure(
            str(repo), root_id, task_id="TASK__first", run_id=first_run,
        )
        state_path = mod._state_path(str(repo), root_id)
        first = json.loads(state_path.read_text())
        with rollout.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "type": "event_msg", "payload": {"type": "later"},
            }) + "\n")
        assert mod.ensure(
            str(repo), root_id, task_id="TASK__first", run_id=first_run,
        )
        same = json.loads(state_path.read_text())
        assert same["offset"] == first["offset"]
        assert mod.ensure(
            str(repo), root_id, task_id=second_task_id, run_id=second_run,
        )
    second = json.loads(state_path.read_text())
    assert second["offset"] == rollout.stat().st_size
    assert (second["task_id"], second["run_id"]) == (second_task_id, second_run)


def test_invalidated_registration_rebinds_at_fresh_rollout_offset(tmp_path, monkeypatch):
    mod = _load()
    codex_home = tmp_path / ".codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    root_id = "019f825b-f25f-70c3-8ee8-071f79fa1c42"
    rollout = _rollout_path(codex_home, root_id)
    _write_jsonl(rollout, [{"type": "session_meta", "payload": {
        "session_id": root_id, "id": root_id, "cwd": str(repo),
        "thread_source": "user",
    }}])
    task_id, run_id = _write_exact_session_binding(repo, root_id)

    assert mod.ensure(str(repo), root_id, task_id=task_id, run_id=run_id)
    state_path = mod._state_path(str(repo), root_id)
    first = json.loads(state_path.read_text())

    marker = repo / "doc/harness/tasks/.active_sessions" / f"{root_id}.json"
    marker.unlink()
    assert mod.invalidate_registration(str(repo), root_id)
    assert not state_path.exists()
    with rollout.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "type": "event_msg", "payload": {"type": "during-ambiguity"},
        }) + "\n")

    _write_exact_session_binding(repo, root_id)
    assert mod.ensure(str(repo), root_id, task_id=task_id, run_id=run_id)
    rebound = json.loads(state_path.read_text())
    assert rebound["offset"] == rollout.stat().st_size
    assert rebound["offset"] > first["offset"]
    assert mod._registration_generation(rebound) != mod._registration_generation(first)


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
    rollout = _rollout_path(codex_home, root_id)
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
    rollout = _rollout_path(codex_home, root_id)
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
    rollout = _rollout_path(codex_home, root_id)
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
    rollout = _rollout_path(codex_home, root_id)
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
    rollout = _rollout_path(codex_home, child_id)
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
    rollout = _rollout_path(codex_home, root_id)
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
    root = _rollout_path(codex_home, root_id)
    child = _rollout_path(codex_home, child_id)
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
    rollout = _rollout_path(codex_home, root_id)
    _write_jsonl(rollout, [{"type": "session_meta", "payload": {
        "session_id": root_id, "id": root_id, "cwd": str(repo), "thread_source": "user",
    }}])
    task_id, run_id = _write_exact_session_binding(repo, root_id)
    assert mod.ensure(str(repo), root_id, task_id=task_id, run_id=run_id)
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
    rollout = _rollout_path(codex_home, root_id)
    _write_jsonl(rollout, [{"type": "session_meta", "payload": {
        "session_id": root_id, "id": root_id, "cwd": str(repo), "thread_source": "user",
    }}])
    task_id, run_id = _write_exact_session_binding(repo, root_id)
    assert mod.ensure(str(repo), root_id, task_id=task_id, run_id=run_id)
    state_path = mod._state_path(str(repo), root_id)
    state = json.loads(state_path.read_text())
    expired = 1.0
    state["registered_at"] = expired
    state_path.write_text(json.dumps(state))
    os.utime(rollout, (expired, expired))
    with mock.patch.object(mod.time, "time", return_value=mod.REGISTRATION_TTL_SECONDS + 10):
        assert mod.registrations(str(repo)) == []
    assert not state_path.exists()


def test_nonfinite_registration_timestamp_is_rejected_and_replaced(tmp_path, monkeypatch):
    mod = _load()
    codex_home = tmp_path / ".codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    root_id = "019f825b-f25f-70c3-8ee8-071f79fa1c42"
    rollout = _rollout_path(codex_home, root_id)
    _write_jsonl(rollout, [{"type": "session_meta", "payload": {
        "session_id": root_id, "id": root_id, "cwd": str(repo),
        "thread_source": "user",
    }}])
    task_id, run_id = _write_exact_session_binding(repo, root_id)

    for invalid in (float("nan"), float("inf"), float("-inf")):
        assert mod.ensure(str(repo), root_id, task_id=task_id, run_id=run_id)
        state_path = mod._state_path(str(repo), root_id)
        state = json.loads(state_path.read_text())
        state["registered_at"] = invalid
        state_path.write_text(json.dumps(state))

        assert mod.registrations(str(repo)) == []
        assert mod.ensure(str(repo), root_id, task_id=task_id, run_id=run_id)
        replacement = json.loads(state_path.read_text())
        assert math.isfinite(replacement["registered_at"])
        assert len(mod.registrations(str(repo))) == 1


def test_manager_starts_one_daemon_worker_per_registration_and_stops(tmp_path):
    mod = _load()
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    registrations = [
        {
            "thread_id": "019f825b-f25f-70c3-8ee8-071f79fa1c42",
            "rollout": "/one", "offset": 11, "registered_at": 1.0,
        },
        {
            "thread_id": "019f82a6-ce64-75a3-b01d-92f7b0b4fe6f",
            "rollout": "/two", "offset": 22, "registered_at": 1.0,
        },
    ]
    calls = []

    def fake_watch(repo_root, thread_id, rollout, offset, *, stop_event, **_kwargs):
        calls.append((repo_root, thread_id, rollout, offset))
        stop_event.wait(1)
        return 0

    manager = mod.WatcherManager(str(repo), scan_seconds=0.01)
    with mock.patch.object(mod, "registrations", return_value=registrations), \
         mock.patch.object(mod, "watch", side_effect=fake_watch):
        assert manager.scan_once() == 2
        assert manager.scan_once() == 0
        status = manager.worker_status(registrations[0]["thread_id"])
        assert status["generation"] == mod._registration_generation(registrations[0])
        assert status["alive"] is True
        assert status["cursor"] is None  # A fake watch never observed a trusted handle.
        assert manager.worker_status("external-or-absent") is None
        manager.stop()
        assert manager.worker_status(registrations[0]["thread_id"])["alive"] is False
    assert {call[1] for call in calls} == {item["thread_id"] for item in registrations}
    assert all(worker.daemon for worker in manager.workers.values())


def test_manager_caps_simultaneous_workers(tmp_path):
    mod = _load()
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    items = [
        {
            "thread_id": f"019f825b-f25f-70c3-8ee8-071f79fa1c4{i}",
            "rollout": f"/{i}", "offset": i, "registered_at": 1.0,
        }
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
        "rollout": "/root-rollout", "offset": 777, "registered_at": 1.0,
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
        "registered_at": 1.0,
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


def test_manager_retains_bounded_worker_error_diagnostic(tmp_path):
    mod = _load()
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    registration = {
        "thread_id": "019f825b-f25f-70c3-8ee8-071f79fa1c42",
        "rollout": "/root-rollout", "offset": 777, "registered_at": 1.0,
    }

    def fake_watch(*_args, on_error, **_kwargs):
        on_error("x" * 1000)
        return 0

    manager = mod.WatcherManager(str(repo))
    with mock.patch.object(mod, "registrations", return_value=[registration]), \
         mock.patch.object(mod, "watch", side_effect=fake_watch):
        assert manager.scan_once() == 1
        manager.workers[registration["thread_id"]].join()

    assert manager.worker_error(registration["thread_id"]) == "x" * 320


def test_manager_records_nonzero_exit_and_clears_only_after_recovery(tmp_path):
    mod = _load()
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    thread_id = "019f825b-f25f-70c3-8ee8-071f79fa1c42"
    registration = {
        "thread_id": thread_id, "rollout": "/root-rollout", "offset": 777,
        "registered_at": 1.0,
    }
    calls = 0
    error_seen_at_retry = []

    def fake_watch(*_args, on_error, recovering, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            assert recovering is False
            return 3
        error_seen_at_retry.append(manager.worker_error(thread_id))
        assert recovering is True
        on_error("")
        return 0

    manager = mod.WatcherManager(str(repo))
    with mock.patch.object(mod, "registrations", return_value=[registration]), \
         mock.patch.object(mod, "watch", side_effect=fake_watch):
        assert manager.scan_once() == 1
        manager.workers[thread_id].join()
        assert manager.worker_error(thread_id) == "watcher exited with status 3"
        assert manager.scan_once() == 1
        manager.workers[thread_id].join()

    assert error_seen_at_retry == ["watcher exited with status 3"]
    assert manager.worker_error(thread_id) == ""


def test_manager_running_status_tracks_manager_thread(tmp_path):
    mod = _load()
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    manager = mod.WatcherManager(str(repo), scan_seconds=0.01)
    with mock.patch.object(mod, "registrations", return_value=[]):
        assert manager.is_running() is False
        manager.start()
        assert manager.is_running() is True
        manager.stop()
    assert manager.is_running() is False


def test_managers_use_cross_process_lease_for_same_registration(tmp_path):
    mod = _load()
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    registration = {
        "thread_id": "019f825b-f25f-70c3-8ee8-071f79fa1c42",
        "rollout": "/root-rollout", "offset": 777, "registered_at": 1.0,
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


def test_manager_restarts_successful_worker_for_refreshed_registration_generation(tmp_path):
    mod = _load()
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    thread_id = "019f825b-f25f-70c3-8ee8-071f79fa1c42"
    current = [{
        "thread_id": thread_id,
        "rollout": "/root-rollout-a",
        "offset": 100,
        "registered_at": 1.0,
    }]
    calls = []

    def fake_watch(_repo, _thread, rollout, offset, **_kwargs):
        calls.append((rollout, offset))
        return 0

    manager = mod.WatcherManager(str(repo))
    with mock.patch.object(mod, "registrations", side_effect=lambda _repo: current), \
         mock.patch.object(mod, "watch", side_effect=fake_watch):
        assert manager.scan_once() == 1
        manager.workers[thread_id].join()
        assert manager.scan_once() == 0

        for field, value in (
            ("rollout", "/root-rollout-b"),
            ("offset", 900),
            ("registered_at", 2.0),
        ):
            current[0] = {**current[0], field: value}
            assert manager.scan_once() == 1
            manager.workers[thread_id].join()
            assert manager.scan_once() == 0

    assert calls == [
        ("/root-rollout-a", 100),
        ("/root-rollout-b", 100),
        ("/root-rollout-b", 900),
        ("/root-rollout-b", 900),
    ]
    assert len(manager.seen) == 1


def test_manager_waits_for_old_generation_before_starting_refreshed_registration(tmp_path):
    mod = _load()
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    thread_id = "019f825b-f25f-70c3-8ee8-071f79fa1c42"
    current = [{
        "thread_id": thread_id,
        "rollout": "/root-rollout",
        "offset": 100,
        "registered_at": 1.0,
    }]
    lease_released = mod.threading.Event()
    allow_old_worker_to_finish = mod.threading.Event()
    calls = []

    class BlockingCloseLease:
        def close(self):
            lease_released.set()
            allow_old_worker_to_finish.wait(1)

    def fake_watch(_repo, _thread, _rollout, offset, **_kwargs):
        calls.append(offset)
        return 0

    manager = mod.WatcherManager(str(repo))
    with mock.patch.object(mod, "registrations", side_effect=lambda _repo: current), \
         mock.patch.object(
             mod, "_acquire_registration_lease",
             side_effect=[BlockingCloseLease(), mock.Mock()],
         ) as acquire_lease, \
         mock.patch.object(mod, "watch", side_effect=fake_watch):
        assert manager.scan_once() == 1
        assert lease_released.wait(1)
        first_worker = manager.workers[thread_id]
        current[0] = {**current[0], "offset": 900, "registered_at": 2.0}
        assert manager.scan_once() == 0
        assert manager.workers[thread_id] is first_worker
        assert acquire_lease.call_count == 1

        allow_old_worker_to_finish.set()
        first_worker.join()
        assert manager.scan_once() == 1
        manager.workers[thread_id].join()

    assert calls == [100, 900]


def test_manager_failed_cleanup_does_not_clear_newer_generation(tmp_path):
    mod = _load()
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    thread_id = "019f825b-f25f-70c3-8ee8-071f79fa1c42"
    old_registration = {
        "thread_id": thread_id,
        "rollout": "/root-rollout",
        "offset": 100,
        "registered_at": 1.0,
    }
    new_registration = {
        **old_registration,
        "offset": 900,
        "registered_at": 2.0,
    }
    old_generation = mod._registration_generation(old_registration)
    new_generation = mod._registration_generation(new_registration)
    manager = mod.WatcherManager(str(repo))
    manager.workers[thread_id] = mod.threading.Thread()
    manager.worker_generations[thread_id] = old_generation
    manager.worker_results[thread_id] = 3
    manager.seen[thread_id] = new_generation

    with mock.patch.object(mod, "registrations", return_value=[new_registration]):
        assert manager.scan_once() == 0

    assert manager.seen[thread_id] == new_generation


def test_watch_inherits_rollout_idle_age_instead_of_resetting_lifetime(tmp_path, monkeypatch):
    mod = _load()
    codex_home = tmp_path / ".codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    root_id = "019f825b-f25f-70c3-8ee8-071f79fa1c42"
    rollout = _rollout_path(codex_home, root_id)
    _write_jsonl(rollout, [{"type": "session_meta", "payload": {
        "session_id": root_id, "id": root_id, "cwd": str(repo), "thread_source": "user",
    }}])
    os.utime(rollout, (1, 1))
    with mock.patch.object(mod.time, "time", return_value=1000):
        assert mod.watch(
            str(repo), root_id, str(rollout), rollout.stat().st_size,
            stop_event=mod.threading.Event(), idle_seconds=10,
        ) == 0


def test_watch_drains_oversized_non_evidence_record_and_reads_next_line(
    tmp_path, monkeypatch,
):
    mod = _load()
    codex_home = tmp_path / ".codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    root_id = "019f825b-f25f-70c3-8ee8-071f79fa1c42"
    rollout = _rollout_path(codex_home, root_id)
    session_meta = json.dumps({"type": "session_meta", "payload": {
        "session_id": root_id, "id": root_id, "cwd": str(repo),
        "thread_source": "user",
    }}).encode()
    monkeypatch.setattr(mod, "MAX_LINE_BYTES", 1024)
    oversized = json.dumps({
        "type": "event_msg",
        "payload": {"type": "item_completed", "stdout": "x" * (mod.MAX_LINE_BYTES + 1)},
    }).encode()
    rollout.parent.mkdir(parents=True, exist_ok=True)
    rollout.write_bytes(session_meta + b"\n" + oversized + b"\n" + b"not-json\n")

    # Neither record is lifecycle evidence, so both are rejected locally and
    # the root tail remains available for later independent lifecycle events.
    # Neither may become a manager worker error: task_context treats that
    # channel as a proven receipt failure and would suppress the first review.
    errors = []
    stop = mod.threading.Event()
    parsed = []
    original_load = mod._load_json_line

    def observe_next_line(raw):
        if raw == b"not-json\n":
            parsed.append(raw)
            stop.set()
        return original_load(raw)

    monkeypatch.setattr(mod, "_load_json_line", observe_next_line)
    assert mod.watch(
                str(repo), root_id, str(rollout), len(session_meta) + 1,
                stop_event=stop, idle_seconds=1.0,
                on_error=errors.append,
    ) == 0
    assert parsed == [b"not-json\n"]
    assert errors == []


def test_watch_continues_after_malformed_record_and_transient_feed_error(
    tmp_path, monkeypatch,
):
    mod = _load()
    codex_home = tmp_path / ".codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    root_id = "019f825b-f25f-70c3-8ee8-071f79fa1c42"
    rollout = _rollout_path(codex_home, root_id)
    session_meta = json.dumps({"type": "session_meta", "payload": {
        "session_id": root_id, "id": root_id, "cwd": str(repo),
        "thread_source": "user",
    }}).encode()
    first = json.dumps({"type": "first", "payload": {}}).encode()
    second = json.dumps({"type": "second", "payload": {}}).encode()
    rollout.parent.mkdir(parents=True, exist_ok=True)
    rollout.write_bytes(
        session_meta + b"\n" + b"not-json\n" + first + b"\n" + second + b"\n"
    )

    class FakeWatcher:
        def __init__(self, *_args, **_kwargs):
            self.seen = []
            self.failed = False
            self.calls = {}
            self.by_agent = {}
            self.receipt_progress = 0

        def feed(self, event):
            self.seen.append(event["type"])
            if event["type"] == "first" and not self.failed:
                self.failed = True
                raise RuntimeError("temporary receipt lock failure")
            if event["type"] == "second":
                self.receipt_progress += 1

        def retry(self):
            return None

    errors = []
    fake = FakeWatcher()
    with mock.patch.object(mod, "Watcher", return_value=fake):
        assert mod.watch(
            str(repo), root_id, str(rollout), len(session_meta) + 1,
            stop_event=mod.threading.Event(), idle_seconds=0.5,
            on_error=errors.append,
        ) == 0

    assert fake.seen == ["first", "first", "second"]
    # Malformed non-evidence is quarantined locally. Publishing it as a live
    # worker error would make task_context suppress the first review spawn,
    # leaving no receipt progress capable of clearing the error.
    assert "temporary receipt lock failure" in errors[0]
    assert all("invalid rollout record" not in error for error in errors)
    assert errors[-1] == ""


def test_watch_quarantines_persistent_feed_error_and_reads_next_line(
    tmp_path, monkeypatch,
):
    mod = _load()
    codex_home = tmp_path / ".codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    root_id = "019f825b-f25f-70c3-8ee8-071f79fa1c42"
    rollout = _rollout_path(codex_home, root_id)
    session_meta = json.dumps({"type": "session_meta", "payload": {
        "session_id": root_id, "id": root_id, "cwd": str(repo),
        "thread_source": "user",
    }}).encode()
    first = json.dumps({"type": "first", "payload": {}}).encode()
    second = json.dumps({"type": "second", "payload": {}}).encode()
    rollout.parent.mkdir(parents=True, exist_ok=True)
    rollout.write_bytes(session_meta + b"\n" + first + b"\n" + second + b"\n")

    class FakeWatcher:
        def __init__(self, *_args, **_kwargs):
            self.seen = []
            self.calls = {}
            self.by_agent = {}
            self.receipt_progress = 0

        def feed(self, event):
            self.seen.append(event["type"])
            if event["type"] == "first":
                raise RuntimeError("permanent receipt integrity failure")

        def retry(self):
            return None

    errors = []
    fake = FakeWatcher()
    with mock.patch.object(mod, "Watcher", return_value=fake):
        assert mod.watch(
            str(repo), root_id, str(rollout), len(session_meta) + 1,
            stop_event=mod.threading.Event(), idle_seconds=1.0,
            on_error=errors.append,
        ) == 0

    assert fake.seen == [
        "first",
        "first",
        "first",
        "second",
    ]
    assert sum("permanent receipt integrity failure" in error for error in errors) == 3
    assert errors[-1] != ""


def test_watch_keeps_discarded_lifecycle_error_after_later_receipt_progress(
    tmp_path, monkeypatch,
):
    mod = _load()
    codex_home = tmp_path / ".codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    root_id = "019f825b-f25f-70c3-8ee8-071f79fa1c42"
    rollout = _rollout_path(codex_home, root_id)
    session_meta = json.dumps({"type": "session_meta", "payload": {
        "session_id": root_id, "id": root_id, "cwd": str(repo),
        "thread_source": "user",
    }}).encode()
    first = json.dumps({"type": "first", "payload": {}}).encode()
    second = json.dumps({"type": "receipt-progress", "payload": {}}).encode()
    rollout.parent.mkdir(parents=True, exist_ok=True)
    rollout.write_bytes(session_meta + b"\n" + first + b"\n" + second + b"\n")

    class FakeWatcher:
        def __init__(self, *_args, **_kwargs):
            self.calls = {}
            self.by_agent = {}
            self.receipt_progress = 0

        def feed(self, event):
            if event["type"] == "first":
                raise RuntimeError("permanent receipt integrity failure")
            if event["type"] == "receipt-progress":
                self.receipt_progress += 1

        def retry(self):
            return None

    errors = []
    with mock.patch.object(mod, "Watcher", return_value=FakeWatcher()):
        assert mod.watch(
            str(repo), root_id, str(rollout), len(session_meta) + 1,
            stop_event=mod.threading.Event(), idle_seconds=1.0,
            on_error=errors.append,
        ) == 0

    assert sum("permanent receipt integrity failure" in error for error in errors) == 3
    assert errors[-1] != ""


def test_existing_receipts_rebuild_state_without_write_progress(tmp_path):
    mod = _load()
    task_dir = tmp_path / "doc/harness/tasks/TASK__watcher"
    task_dir.mkdir(parents=True)
    root_id = "019f825b-f25f-70c3-8ee8-071f79fa1c42"
    child_id = "019f82a6-ce64-75a3-b01d-92f7b0b4fe6f"
    agent_path = "/root/code_review_existing"
    call_id = "call_existingReceipt123"
    watcher = mod.Watcher(str(tmp_path), root_id)
    watcher.calls[call_id] = {
        "task_name": "code_review_existing",
        "task_dir": str(task_dir),
        "task_run_id": RUN_ID,
        "output_path": agent_path,
        "agent_path": agent_path,
        "child_id": child_id,
    }

    with mock.patch.object(
        mod, "_active_task_binding_for_session", return_value=_active_binding(task_dir),
    ), mock.patch.object(
        mod, "_exact_receipt", return_value={"event": "started"},
    ), mock.patch.object(
        mod, "record_subagent_receipt",
        side_effect=AssertionError("existing receipt must not be rewritten"),
    ):
        watcher._maybe_start(call_id)

    item = watcher.calls[call_id]
    assert item["started"] is True
    assert watcher.receipt_progress == 0
    assert watcher.replay_recovery_progress == 1

    item["root_final"] = (
        "VERDICT: PASS\nFINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0"
    )
    with mock.patch.object(
        mod, "_active_task_binding_for_session", return_value=_active_binding(task_dir),
    ), mock.patch.object(
        mod, "_child_status",
        return_value=("complete", tmp_path / "child.jsonl", item["root_final"]),
    ), mock.patch.object(
        mod, "_exact_receipt", return_value={"event": "completed"},
    ), mock.patch.object(
        mod, "record_subagent_receipt",
        side_effect=AssertionError("existing receipt must not be rewritten"),
    ):
        watcher._maybe_complete(item)

    assert item["completed"] is True
    assert watcher.receipt_progress == 0
    assert watcher.replay_recovery_progress == 2


def test_recovering_watch_clears_prior_error_after_exact_receipt_replay(
    tmp_path, monkeypatch,
):
    mod = _load()
    codex_home = tmp_path / ".codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    repo = tmp_path / "repo"
    task_dir = repo / "doc/harness/tasks/TASK__watcher"
    task_dir.mkdir(parents=True)
    (repo / ".git").mkdir()
    _write_task_control(task_dir)
    binding = _active_binding(task_dir)
    root_id = "019f825b-f25f-70c3-8ee8-071f79fa1c42"
    child_id = "019f82a6-ce64-75a3-b01d-92f7b0b4fe6f"
    agent_path = "/root/code_review_replay"
    task_name = "code_review_replay"
    call_id = "call_replay_123456"
    final = "VERDICT: PASS\nFINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0"
    runtime_id = mod._codex_runtime_id(root_id, call_id, child_id)
    receipts = [{
        "source": "codex_session_watcher:collaboration",
        "event": "started",
        "agent_id": agent_path,
        "agent_type": task_name,
        "lens": "review-code",
        "task_run_id": binding["run_id"],
        "summary": "existing start",
        "runtime_id": runtime_id,
    }, {
        "source": "codex_session_watcher:collaboration",
        "event": "completed",
        "agent_id": agent_path,
        "agent_type": task_name,
        "lens": "review-code",
        "task_run_id": binding["run_id"],
        "verdict": "PASS",
        "summary": final,
        "runtime_id": runtime_id,
    }]

    _write_jsonl(
        _rollout_path(codex_home, child_id),
        _child_events(root_id, child_id, agent_path, str(repo), final),
    )
    rollout = _rollout_path(codex_home, root_id)
    meta = {"type": "session_meta", "payload": {
        "session_id": root_id, "id": root_id, "cwd": str(repo),
        "thread_source": "user",
    }}
    _write_jsonl(rollout, [meta])
    offset = rollout.stat().st_size
    with rollout.open("a", encoding="utf-8") as handle:
        for event in _spawn_events(root_id, child_id, task_name, agent_path, call_id):
            handle.write(json.dumps(event) + "\n")
        handle.write(json.dumps(_delivery(agent_path, final)) + "\n")

    watcher = mod.Watcher(str(repo), root_id)
    errors = []
    with mock.patch.object(mod, "Watcher", return_value=watcher), \
         mock.patch.object(mod, "_active_task_binding_for_session", return_value=binding), \
         mock.patch.object(mod, "receipt_snapshot", side_effect=lambda _td: _snapshot(receipts)), \
         mock.patch.object(
             mod, "record_subagent_receipt",
             side_effect=AssertionError("exact replay must not rewrite receipts"),
         ):
        assert mod.watch(
            str(repo), root_id, str(rollout), offset,
            session_cwd=str(repo), stop_event=mod.threading.Event(),
            idle_seconds=1.0, on_error=errors.append, recovering=True,
        ) == 0

    assert watcher.by_agent[agent_path]["completed"] is True
    assert watcher.receipt_progress == 0
    assert watcher.replay_recovery_progress == 2
    assert errors == [""]


def test_watch_replays_real_spawn_after_transient_binding_failure(
    tmp_path, monkeypatch,
):
    mod = _load()
    codex_home = tmp_path / ".codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    repo = tmp_path / "repo"
    task_dir = repo / "doc/harness/tasks/TASK__watcher"
    task_dir.mkdir(parents=True)
    (repo / ".git").mkdir()
    root_id = "019f825b-f25f-70c3-8ee8-071f79fa1c42"
    child_id = "019f82a6-ce64-75a3-b01d-92f7b0b4fe6f"
    agent_path = "/root/code_review_retry"
    rollout = _rollout_path(codex_home, root_id)
    meta = {"type": "session_meta", "payload": {
        "session_id": root_id, "id": root_id, "cwd": str(repo),
        "thread_source": "user",
    }}
    _write_jsonl(rollout, [meta])
    offset = rollout.stat().st_size
    with rollout.open("a", encoding="utf-8") as handle:
        for event in _spawn_events(
            root_id, child_id, "code_review_retry", agent_path,
        ):
            handle.write(json.dumps(event) + "\n")
    child = _rollout_path(codex_home, child_id)
    _write_jsonl(child, _child_events(root_id, child_id, agent_path, str(repo)))

    receipts = []
    attempts = 0

    def binding(*_args):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary task binding failure")
        return _active_binding(task_dir)

    errors = []
    with mock.patch.object(
        mod, "_active_task_binding_for_session", side_effect=binding,
    ), mock.patch.object(
        mod, "record_subagent_receipt",
        side_effect=lambda _td, item: receipts.append(dict(item)) or item,
    ), mock.patch.object(
        mod, "receipt_snapshot", side_effect=lambda _td: _snapshot(receipts),
    ):
        assert mod.watch(
            str(repo), root_id, str(rollout), offset,
            stop_event=mod.threading.Event(), idle_seconds=0.5,
            on_error=errors.append,
        ) == 0

    assert attempts >= 2
    assert [(item["event"], item["lens"]) for item in receipts] == [
        ("started", "review-code"),
    ]
    assert "temporary task binding failure" in errors[0]
    assert errors[-1] == ""


def test_watch_bounds_incomplete_oversized_tail_by_idle_deadline(tmp_path, monkeypatch):
    mod = _load()
    codex_home = tmp_path / ".codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    root_id = "019f825b-f25f-70c3-8ee8-071f79fa1c42"
    rollout = _rollout_path(codex_home, root_id)
    session_meta = json.dumps({"type": "session_meta", "payload": {
        "session_id": root_id, "id": root_id, "cwd": str(repo),
        "thread_source": "user",
    }}).encode()
    rollout.parent.mkdir(parents=True, exist_ok=True)
    rollout.write_bytes(session_meta + b"\n" + b"x" * (mod.MAX_LINE_BYTES + 1))

    started = time.monotonic()
    assert mod.watch(
        str(repo), root_id, str(rollout), len(session_meta) + 1,
        stop_event=mod.threading.Event(), idle_seconds=0.05,
    ) == 3
    assert time.monotonic() - started < 0.5


def test_main_retries_bounded_rollout_creation_race():
    mod = _load()
    root_id = "019f825b-f25f-70c3-8ee8-071f79fa1c42"
    with mock.patch.object(mod, "ensure", side_effect=[False, False, True]) as ensure, \
         mock.patch.object(mod, "_active_task_binding_for_session", return_value={
             "task_dir": "/repo/doc/harness/tasks/TASK__main",
             "run_id": RUN_ID,
         }), \
         mock.patch.object(mod.time, "monotonic", return_value=0.0), \
         mock.patch.object(mod.time, "sleep"):
        assert mod.main([
            "--ensure", "--repo-root", "/repo", "--thread-id", root_id,
            "--retry-seconds", "1.0",
        ]) == 0
    assert ensure.call_count == 3
    ensure.assert_called_with(
        "/repo", root_id, task_id="TASK__main", run_id=RUN_ID,
    )


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
    child = _rollout_path(codex_home, child_id)
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


def test_conflicting_delivery_retries_transient_pending_receipt_failure(
    tmp_path, monkeypatch,
):
    mod = _load()
    codex_home = tmp_path / ".codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    repo = tmp_path / "repo"
    task_dir = repo / "doc/harness/tasks/TASK__watcher"
    task_dir.mkdir(parents=True)
    root_id = "019f825b-f25f-70c3-8ee8-071f79fa1c42"
    child_id = "019f82a6-ce64-75a3-b01d-92f7b0b4fe6f"
    agent_path = "/root/code_review"
    child = _rollout_path(codex_home, child_id)
    _write_jsonl(child, _child_events(root_id, child_id, agent_path, str(repo)))
    receipts = []
    pending_attempts = 0

    def record(_task_dir, receipt):
        nonlocal pending_attempts
        entry = dict(receipt)
        if entry.get("verdict") == "PENDING":
            pending_attempts += 1
            if pending_attempts == 1:
                raise RuntimeError("temporary receipt lock failure")
        receipts.append(entry)
        return entry

    watcher = mod.Watcher(str(repo), root_id)
    final = "VERDICT: PASS\nFINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0"
    conflict = _delivery(
        agent_path,
        "VERDICT: FAIL\nFINDING_COUNTS: FIX_NOW=1 INVESTIGATE=0 OPTIONAL=0",
    )
    with mock.patch.object(
        mod, "_active_task_binding_for_session", return_value=_active_binding(task_dir),
    ), mock.patch.object(
        mod, "record_subagent_receipt", side_effect=record,
    ), mock.patch.object(
        mod, "receipt_snapshot", side_effect=lambda _td: _snapshot(receipts),
    ):
        for event in _spawn_events(root_id, child_id, "code_review", agent_path):
            watcher.feed(event)
        _write_jsonl(child, _child_events(root_id, child_id, agent_path, str(repo), final))
        watcher.feed(_delivery(agent_path, final))
        try:
            watcher.feed(conflict)
        except RuntimeError as exc:
            assert "temporary receipt lock failure" in str(exc)
        else:
            raise AssertionError("transient invalidation write did not fail")
        assert watcher.by_agent[agent_path].get("invalid") is not True
        watcher.feed(conflict)

    assert pending_attempts == 2
    assert [item.get("verdict") for item in receipts] == [None, "PASS", "PENDING"]
    assert watcher.by_agent[agent_path]["invalid"] is True


def test_watch_keeps_failed_invalidation_sticky_after_independent_receipt(
    tmp_path, monkeypatch,
):
    """Later PASS progress cannot repair a discarded conflicting terminal."""
    mod = _load()
    codex_home = tmp_path / ".codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    repo = tmp_path / "repo"
    task_dir = repo / "doc/harness/tasks/TASK__watcher"
    task_dir.mkdir(parents=True)
    (repo / ".git").mkdir()
    _write_task_control(task_dir)
    binding = _active_binding(task_dir)
    run_id = binding["run_id"]
    root_id = "019f825b-f25f-70c3-8ee8-071f79fa1c42"
    first_child = "019f82a6-ce64-75a3-b01d-92f7b0b4fe6f"
    second_child = "019f82a6-ce64-75a3-b01d-92f7b0b4fe70"
    first_path = "/root/code_review_first"
    second_path = "/root/security_review_second"
    first_final = "VERDICT: PASS\nFINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0"
    conflict_final = "VERDICT: FAIL\nFINDING_COUNTS: FIX_NOW=1 INVESTIGATE=0 OPTIONAL=0"
    second_final = "VERDICT: PASS\nFINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0"
    first_runtime = mod._codex_runtime_id(root_id, "call_first_123456", first_child)
    second_runtime = mod._codex_runtime_id(root_id, "call_second_123456", second_child)

    receipts = [{
        "source": "codex_session_watcher:collaboration",
        "event": "completed",
        "agent_id": first_path,
        "agent_type": "code_review_first",
        "lens": "review-code",
        "task_run_id": run_id,
        "verdict": "PASS",
        "summary": first_final,
        "runtime_id": first_runtime,
    }]
    pending_attempts = 0

    def record(_task_dir, receipt):
        nonlocal pending_attempts
        entry = dict(receipt)
        if entry.get("verdict") == "PENDING":
            pending_attempts += 1
            raise RuntimeError("persistent pending receipt failure")
        receipts.append(entry)
        return entry

    watcher = mod.Watcher(str(repo), root_id)
    first_item = {
        "task_name": "code_review_first",
        "task_dir": str(task_dir),
        "task_run_id": run_id,
        "agent_path": first_path,
        "child_id": first_child,
        "runtime_id": first_runtime,
        "started": True,
        "completed": True,
        "root_final": first_final,
    }
    second_item = {
        "task_name": "security_review_second",
        "task_dir": str(task_dir),
        "task_run_id": run_id,
        "agent_path": second_path,
        "child_id": second_child,
        "runtime_id": second_runtime,
        "started": True,
    }
    watcher.calls = {"call_first_123456": first_item, "call_second_123456": second_item}
    watcher.by_agent = {first_path: first_item, second_path: second_item}

    _write_jsonl(
        _rollout_path(codex_home, second_child),
        _child_events(root_id, second_child, second_path, str(repo), second_final),
    )
    root_rollout = _rollout_path(codex_home, root_id)
    root_meta = {"type": "session_meta", "payload": {
        "session_id": root_id, "id": root_id, "cwd": str(repo),
        "thread_source": "user",
    }}
    _write_jsonl(root_rollout, [root_meta])
    offset = root_rollout.stat().st_size
    with root_rollout.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(_delivery(first_path, conflict_final)) + "\n")
        handle.write(json.dumps(_delivery(second_path, second_final)) + "\n")

    errors = []
    with mock.patch.object(mod, "Watcher", return_value=watcher), \
         mock.patch.object(mod, "_active_task_binding_for_session", return_value=binding), \
         mock.patch.object(mod, "record_subagent_receipt", side_effect=record), \
         mock.patch.object(mod, "receipt_snapshot", side_effect=lambda _td: _snapshot(receipts)):
        assert mod.watch(
            str(repo), root_id, str(root_rollout), offset,
            session_cwd=str(repo), stop_event=mod.threading.Event(),
            idle_seconds=1.0, on_error=errors.append,
        ) == 0

    assert pending_attempts == mod.MAX_RECORD_OBSERVATION_ATTEMPTS
    assert any(
        item.get("agent_id") == second_path and item.get("verdict") == "PASS"
        for item in receipts
    )
    assert not any(item.get("verdict") == "PENDING" for item in receipts)
    assert errors[-1].endswith("persistent pending receipt failure")


def _bound_lifecycle_fixture(tmp_path, monkeypatch):
    mod = _load()
    codex_home = tmp_path / '.codex'
    monkeypatch.setenv('CODEX_HOME', str(codex_home))
    repo = tmp_path / 'repo'
    (repo / '.git').mkdir(parents=True)
    root_id = '019f825b-f25f-70c3-8ee8-071f79fa1c42'
    child_id = '019f82a6-ce64-75a3-b01d-92f7b0b4fe6f'
    agent_path = '/root/code_review_binding'
    rollout = _rollout_path(codex_home, root_id)
    _write_jsonl(rollout, [{'type': 'session_meta', 'payload': {
        'session_id': root_id, 'id': root_id, 'cwd': str(repo), 'thread_source': 'user',
    }}])
    task_id, run_id = _write_exact_session_binding(repo, root_id)
    assert mod.ensure(str(repo), root_id, task_id=task_id, run_id=run_id)
    registration = json.loads(mod._state_path(str(repo), root_id).read_text())
    final = 'VERDICT: PASS\nFINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0'
    _write_jsonl(_rollout_path(codex_home, child_id),
                 _child_events(root_id, child_id, agent_path, str(repo), final))
    with rollout.open('a', encoding='utf-8') as handle:
        for event in [*_spawn_events(root_id, child_id, 'code_review_binding', agent_path),
                      _delivery(agent_path, final)]:
            handle.write(json.dumps(event) + '\n')
    monkeypatch.setattr(mod, 'POLL_SECONDS', 0.005)
    return mod, repo, root_id, agent_path, registration


def _binding_read_stage():
    # Identify existing lifecycle entry points, allowing a shared binding reader
    # to be inserted between the entry point and the injected filesystem fault.
    frame = sys._getframe(1)
    while frame:
        if frame.f_code.co_name in {'feed', '_maybe_start', '_maybe_complete', 'watch'}:
            return frame.f_code.co_name
        frame = frame.f_back
    return ''


def _assert_manager_recovers_empty_binding(tmp_path, monkeypatch, stage, occurrence, empty_reads=1):
    mod, repo, root_id, agent_path, registration = _bound_lifecycle_fixture(tmp_path, monkeypatch)
    original_binding = mod._active_task_binding_for_session
    receipts = []
    reads = 0
    injected = False
    last_read_available = True
    uncertain_writes = []
    manager = mod.WatcherManager(str(repo), scan_seconds=0.05)

    def binding(*args):
        nonlocal reads, injected, last_read_available
        if _binding_read_stage() == stage:
            reads += 1
            if occurrence <= reads < occurrence + empty_reads:
                injected = True
                last_read_available = False
                return {}
        result = original_binding(*args)
        last_read_available = bool(result)
        return result

    def record(task_dir, item):
        if not last_read_available:
            uncertain_writes.append(dict(item))
        assert last_read_available, 'uncertain binding must not authorize a receipt'
        assert Path(task_dir).name == registration['task_id']
        receipts.append(dict(item))
        if item['event'] == 'completed':
            manager.stop_event.set()
        return item

    with mock.patch.object(mod, '_active_task_binding_for_session', side_effect=binding), \
         mock.patch.object(mod, 'receipt_snapshot', side_effect=lambda _td: _snapshot(receipts)), \
         mock.patch.object(mod, 'record_subagent_receipt', side_effect=record):
        manager.start()
        try:
            manager.thread.join(timeout=1.5)
        finally:
            manager.stop()
        assert injected, (stage, occurrence, reads)
        assert uncertain_writes == []
        assert [(r['event'], r.get('verdict', '')) for r in receipts] == [
            ('started', ''), ('completed', 'PASS'),
        ]
        assert all(r['task_run_id'] == registration['run_id'] for r in receipts)
        assert all(r['agent_id'] == agent_path for r in receipts)
        assert all(r['source'] == 'codex_session_watcher:collaboration' for r in receipts)
        assert receipts[0]['runtime_id'] == receipts[1]['runtime_id']
        assert manager.worker_error(root_id) == ''
        # Replaying exactly the persisted checkpoint must recover the same pair
        # without appending either lifecycle record a second time.
        with mock.patch.object(mod, 'record_subagent_receipt', side_effect=AssertionError('duplicate receipt')) as replay_record:
            assert mod.watch(str(repo), root_id, registration['rollout'], registration['offset'],
                             task_id=registration['task_id'], run_id=registration['run_id'],
                             idle_seconds=0.03, stop_event=mod.threading.Event(), recovering=True) == 0
            replay_record.assert_not_called()
        assert len(receipts) == 2


def test_manager_recovers_one_empty_outer_binding(tmp_path, monkeypatch):
    _assert_manager_recovers_empty_binding(tmp_path, monkeypatch, 'watch', 1)


def test_manager_recovers_one_empty_spawn_binding(tmp_path, monkeypatch):
    _assert_manager_recovers_empty_binding(tmp_path, monkeypatch, 'feed', 1)


def test_manager_recovers_one_empty_start_binding(tmp_path, monkeypatch):
    _assert_manager_recovers_empty_binding(tmp_path, monkeypatch, '_maybe_start', 1)


def test_manager_recovers_one_empty_start_transaction_binding(tmp_path, monkeypatch):
    _assert_manager_recovers_empty_binding(tmp_path, monkeypatch, '_maybe_start', 2)


def test_manager_recovers_one_empty_start_publication_binding(tmp_path, monkeypatch):
    _assert_manager_recovers_empty_binding(tmp_path, monkeypatch, '_maybe_start', 3)


def test_manager_recovers_one_empty_completion_binding(tmp_path, monkeypatch):
    _assert_manager_recovers_empty_binding(tmp_path, monkeypatch, '_maybe_complete', 1)


def test_manager_recovers_one_empty_completion_publication_binding(tmp_path, monkeypatch):
    _assert_manager_recovers_empty_binding(tmp_path, monkeypatch, '_maybe_complete', 2)


def test_bound_watch_sustained_empty_binding_fails_boundedly_without_receipts(tmp_path, monkeypatch):
    mod, repo, root_id, _, registration = _bound_lifecycle_fixture(tmp_path, monkeypatch)
    errors = []
    started = time.monotonic()
    with mock.patch.object(mod, '_active_task_binding_for_session', return_value={}) as binding, \
         mock.patch.object(mod, 'record_subagent_receipt', side_effect=AssertionError('uncertain receipt')):
        result = mod.watch(str(repo), root_id, registration['rollout'], registration['offset'],
                           task_id=registration['task_id'], run_id=registration['run_id'],
                           idle_seconds=0.1, on_error=errors.append)
    assert result != 0
    assert time.monotonic() - started < 1.0
    assert 1 <= binding.call_count <= 30
    assert errors and any('binding' in e.lower() and 'unavailable' in e.lower() for e in errors)
    assert all('corrupt' not in e.lower() for e in errors)


def test_bound_watch_unknown_preserves_stop_and_inherited_idle_deadline(tmp_path, monkeypatch):
    mod, repo, root_id, _, registration = _bound_lifecycle_fixture(tmp_path, monkeypatch)
    with mock.patch.object(mod, '_active_task_binding_for_session', return_value={}) as binding, \
         mock.patch.object(mod, 'record_subagent_receipt', side_effect=AssertionError('uncertain receipt')):
        stop = mod.threading.Event()
        stop.set()
        started = time.monotonic()
        mod.watch(str(repo), root_id, registration['rollout'], registration['offset'],
                  task_id=registration['task_id'], run_id=registration['run_id'], stop_event=stop)
        assert time.monotonic() - started < 0.5
        os.utime(registration['rollout'], (time.time() - 10, time.time() - 10))
        mod.watch(str(repo), root_id, registration['rollout'], registration['offset'],
                  task_id=registration['task_id'], run_id=registration['run_id'], idle_seconds=0.1)
        assert binding.call_count == 0


def test_bound_watch_validated_different_task_or_run_retires_normally(tmp_path, monkeypatch):
    mod, repo, root_id, _, registration = _bound_lifecycle_fixture(tmp_path, monkeypatch)
    task = repo / 'doc/harness/tasks' / registration['task_id']
    for different in ({'task_dir': str(task), 'run_id': PRIOR_RUN_ID},
                      {'task_dir': str(task.with_name('TASK__different')), 'run_id': RUN_ID}):
        with mock.patch.object(mod, '_active_task_binding_for_session', return_value=different), \
             mock.patch.object(mod, 'record_subagent_receipt', side_effect=AssertionError('wrong generation')):
            assert mod.watch(str(repo), root_id, registration['rollout'], registration['offset'],
                             task_id=registration['task_id'], run_id=registration['run_id'], idle_seconds=0.1) == 0


def test_task_control_path_replacement_rejects_binding_then_recovers(tmp_path, monkeypatch):
    mod, repo, root_id, _, registration = _bound_lifecycle_fixture(tmp_path, monkeypatch)
    import _lib
    task = repo / 'doc/harness/tasks' / registration['task_id']
    control = task / 'TASK.json'
    original_open = _lib.os.open
    replaced = False

    def replace_between_stat_and_open(path, flags, *args, **kwargs):
        nonlocal replaced
        if os.fspath(path) == str(control) and not replaced:
            replaced = True
            replacement = control.with_suffix('.replacement')
            replacement.write_bytes(control.read_bytes())
            replacement.replace(control)
        return original_open(path, flags, *args, **kwargs)

    with mock.patch.object(_lib.os, 'open', side_effect=replace_between_stat_and_open):
        assert mod._active_task_binding_for_session(str(repo), root_id) == {}
    assert replaced
    assert mod._active_task_binding_for_session(str(repo), root_id) == _active_binding(task)


def test_manager_revalidates_before_recovering_unknown_worker(tmp_path, monkeypatch):
    mod, repo, root_id, _, registration = _bound_lifecycle_fixture(tmp_path, monkeypatch)
    original_binding = mod._active_task_binding_for_session
    unknown = True
    receipts = []
    manager = mod.WatcherManager(str(repo), scan_seconds=0.05)

    def binding(*args):
        if unknown and _binding_read_stage():
            return {}
        return original_binding(*args)

    def record(_td, item):
        assert not unknown
        receipts.append(dict(item))
        if item['event'] == 'completed':
            manager.stop_event.set()
        return item

    marker = repo / 'doc/harness/tasks/.active_sessions' / f'{root_id}.json'
    original_marker = marker.read_bytes()
    with mock.patch.object(mod, '_active_task_binding_for_session', side_effect=binding), \
         mock.patch.object(mod, 'receipt_snapshot', side_effect=lambda _td: _snapshot(receipts)), \
         mock.patch.object(mod, 'record_subagent_receipt', side_effect=record):
        try:
            assert manager.scan_once() == 1
            worker = manager.workers[root_id]
            worker.join(timeout=1)
            assert not worker.is_alive(), 'sustained uncertainty must release the lease boundedly'
            assert manager.worker_results[root_id] != 0
            diagnostic = manager.worker_error(root_id)
            assert diagnostic
            assert receipts == []
            # Recovery must independently validate persisted authority, even
            # after its previous worker failed rather than retired normally.
            marker.unlink()
            unknown = False
            assert manager.scan_once() == 0
            assert manager.worker_error(root_id) == diagnostic
            marker.write_bytes(original_marker)
            assert manager.scan_once() == 1
            manager.workers[root_id].join(timeout=1)
            assert [r['event'] for r in receipts] == ['started', 'completed']
            assert manager.worker_error(root_id) == ''
            assert all(r['task_run_id'] == registration['run_id'] for r in receipts)
        finally:
            manager.stop()


def test_conflict_fence_new_registration_does_not_replay_ambiguous_lifecycle(tmp_path, monkeypatch):
    mod, repo, root_id, _, registration = _bound_lifecycle_fixture(tmp_path, monkeypatch)
    # Fixture lifecycle is already appended after the old immutable offset.
    # A known conflict invalidates that registration before recovery can use it.
    marker = repo / 'doc/harness/tasks/.active_sessions' / f'{root_id}.json'
    marker.unlink()
    assert mod.invalidate_registration(str(repo), root_id)
    manager = mod.WatcherManager(str(repo))
    assert manager.scan_once() == 0
    _write_exact_session_binding(repo, root_id)
    assert mod.ensure(str(repo), root_id, task_id=registration['task_id'], run_id=registration['run_id'])
    rebound = json.loads(mod._state_path(str(repo), root_id).read_text())
    assert rebound['offset'] > registration['offset']
    assert rebound['offset'] == Path(registration['rollout']).stat().st_size
    with mock.patch.object(mod, 'record_subagent_receipt', side_effect=AssertionError('ambiguous replay')) as record:
        assert mod.watch(str(repo), root_id, rebound['rollout'], rebound['offset'],
                         task_id=rebound['task_id'], run_id=rebound['run_id'], idle_seconds=0.03) == 0
        record.assert_not_called()


def test_manager_replays_spawn_after_exhausted_binding_reads(tmp_path, monkeypatch):
    _assert_manager_recovers_empty_binding(tmp_path, monkeypatch, 'feed', 1, empty_reads=3)


def test_manager_replays_start_publication_after_exhausted_binding_reads(tmp_path, monkeypatch):
    _assert_manager_recovers_empty_binding(tmp_path, monkeypatch, '_maybe_start', 3, empty_reads=3)


def test_manager_replays_completion_publication_after_exhausted_binding_reads(tmp_path, monkeypatch):
    _assert_manager_recovers_empty_binding(tmp_path, monkeypatch, '_maybe_complete', 2, empty_reads=3)


def test_manager_sustained_unknown_keeps_scan_cadence_and_stops(tmp_path, monkeypatch):
    mod, repo, root_id, _, _ = _bound_lifecycle_fixture(tmp_path, monkeypatch)
    original_binding = mod._active_task_binding_for_session
    observed = mod.threading.Event()
    worker_threads = set()
    manager = mod.WatcherManager(str(repo), scan_seconds=0.05)

    def binding(*args):
        if _binding_read_stage():
            worker_threads.add(mod.threading.current_thread())
            observed.set()
            return {}
        return original_binding(*args)

    with mock.patch.object(mod, '_active_task_binding_for_session', side_effect=binding), \
         mock.patch.object(mod, 'record_subagent_receipt', side_effect=AssertionError('uncertain receipt')):
        manager.start()
        try:
            assert observed.wait(timeout=1)
            # Real failed watch workers must wait for the manager scan cadence;
            # failure must not turn into an unbounded immediate restart loop.
            mod.threading.Event().wait(0.16)
        finally:
            manager.stop()
    assert 1 <= len(worker_threads) <= 5
    assert not manager.is_running()
    assert all(not worker.is_alive() for worker in worker_threads)
    assert manager.worker_error(root_id)


def _assert_live_worker_respects_registration_fence(tmp_path, monkeypatch, stage, empty_read):
    mod, repo, root_id, old_agent, registration = _bound_lifecycle_fixture(tmp_path, monkeypatch)
    original_binding = mod._active_task_binding_for_session
    original_child_status = mod._child_status
    original_watch = mod.watch
    marker = repo / 'doc/harness/tasks/.active_sessions' / f'{root_id}.json'
    marker_bytes = marker.read_bytes()
    receipts = []
    fenced_at = None
    restored = False
    mutating = False
    armed = stage == 'watch'
    manager = mod.WatcherManager(str(repo))

    def restore_registration():
        nonlocal restored, mutating
        mutating = True
        try:
            marker.write_bytes(marker_bytes)
            assert mod.ensure(str(repo), root_id, task_id=registration['task_id'],
                              run_id=registration['run_id'])
            restored = True
        finally:
            mutating = False

    def binding(*args):
        nonlocal fenced_at, mutating
        if mutating:
            return original_binding(*args)
        if fenced_at is not None and not restored:
            restore_registration()
        if armed and fenced_at is None and _binding_read_stage() == stage:
            fenced_at = len(receipts)
            mutating = True
            try:
                if empty_read:
                    marker.unlink()
                assert mod.invalidate_registration(str(repo), root_id)
            finally:
                mutating = False
            if empty_read:
                return {}
            # Same exact task and run, but a positively replaced registration.
            restore_registration()
        return original_binding(*args)

    def child_status(*args, **kwargs):
        nonlocal armed
        result = original_child_status(*args, **kwargs)
        if _binding_read_stage() == stage:
            armed = True
        return result

    def bounded_watch(*args, **kwargs):
        return original_watch(*args, **kwargs, idle_seconds=0.08)

    with mock.patch.object(mod, '_active_task_binding_for_session', side_effect=binding), \
         mock.patch.object(mod, '_child_status', side_effect=child_status), \
         mock.patch.object(mod, 'watch', side_effect=bounded_watch), \
         mock.patch.object(mod, 'receipt_snapshot', side_effect=lambda _td: _snapshot(receipts)), \
         mock.patch.object(mod, 'record_subagent_receipt',
                           side_effect=lambda _td, item: receipts.append(dict(item)) or item):
        try:
            assert manager.scan_once() == 1
            manager.workers[root_id].join(timeout=1)
            assert not manager.workers[root_id].is_alive()
            assert fenced_at is not None, 'the old live worker must reach the injected fence'
            # No started, completed, or invalidation/PENDING receipt may cross
            # the fence, even when exact task/run authority becomes readable.
            assert len(receipts) == fenced_at
            assert [r['event'] for r in receipts] == (
                ['started'] if stage == '_maybe_complete' else []
            )
            if not restored:
                restore_registration()
            rebound = json.loads(mod._state_path(str(repo), root_id).read_text())
            assert rebound['offset'] > registration['offset']
            assert rebound['offset'] == Path(registration['rollout']).stat().st_size

            fresh_child = '019f82a6-ce64-75a3-b01d-92f7b0b4fe70'
            fresh_agent = '/root/code_review_after_fence'
            final = 'VERDICT: PASS\nFINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0'
            codex_home = Path(os.environ['CODEX_HOME'])
            _write_jsonl(_rollout_path(codex_home, fresh_child),
                         _child_events(root_id, fresh_child, fresh_agent, str(repo), final))
            with Path(registration['rollout']).open('a', encoding='utf-8') as handle:
                for event in [*_spawn_events(root_id, fresh_child, 'code_review_after_fence',
                                             fresh_agent, 'call_after_fence_123456'),
                              _delivery(fresh_agent, final)]:
                    handle.write(json.dumps(event) + '\n')
            assert manager.scan_once() == 1
            manager.workers[root_id].join(timeout=1)
            assert not manager.workers[root_id].is_alive()
            fresh_receipts = receipts[fenced_at:]
            assert [(r['agent_id'], r['event'], r.get('verdict', '')) for r in fresh_receipts] == [
                (fresh_agent, 'started', ''), (fresh_agent, 'completed', 'PASS'),
            ]
            assert all(r['task_run_id'] == registration['run_id'] for r in fresh_receipts)
            assert all(r['agent_id'] != old_agent for r in fresh_receipts)
        finally:
            manager.stop()


def test_live_worker_empty_outer_binding_cannot_bridge_registration_fence(tmp_path, monkeypatch):
    _assert_live_worker_respects_registration_fence(tmp_path, monkeypatch, 'watch', True)


def test_live_worker_empty_start_publication_cannot_bridge_registration_fence(tmp_path, monkeypatch):
    _assert_live_worker_respects_registration_fence(tmp_path, monkeypatch, '_maybe_start', True)


def test_live_worker_empty_completion_publication_cannot_bridge_registration_fence(tmp_path, monkeypatch):
    _assert_live_worker_respects_registration_fence(tmp_path, monkeypatch, '_maybe_complete', True)


def test_live_worker_same_binding_replacement_fences_start_publication(tmp_path, monkeypatch):
    _assert_live_worker_respects_registration_fence(tmp_path, monkeypatch, '_maybe_start', False)


def test_live_worker_same_binding_replacement_fences_completion_publication(tmp_path, monkeypatch):
    _assert_live_worker_respects_registration_fence(tmp_path, monkeypatch, '_maybe_complete', False)


def test_positive_run_switch_at_completion_publication_terminates_without_append(tmp_path, monkeypatch):
    mod, repo, root_id, _, registration = _bound_lifecycle_fixture(tmp_path, monkeypatch)
    original_binding = mod._active_task_binding_for_session
    original_child_status = mod._child_status
    armed = False
    switched = False
    receipts = []
    manager = mod.WatcherManager(str(repo))

    def child_status(*args, **kwargs):
        nonlocal armed
        result = original_child_status(*args, **kwargs)
        if _binding_read_stage() == '_maybe_complete':
            armed = True
        return result

    def binding(*args):
        nonlocal switched
        if armed and not switched and _binding_read_stage() == '_maybe_complete':
            switched = True
            task = repo / 'doc/harness/tasks' / registration['task_id']
            _write_task_control(task, run_id=PRIOR_RUN_ID)
            marker = repo / 'doc/harness/tasks/.active_sessions' / f'{root_id}.json'
            data = json.loads(marker.read_text())
            data['run_id'] = PRIOR_RUN_ID
            marker.write_text(json.dumps(data) + '\n')
        return original_binding(*args)

    with mock.patch.object(mod, '_active_task_binding_for_session', side_effect=binding), \
         mock.patch.object(mod, '_child_status', side_effect=child_status), \
         mock.patch.object(mod, 'receipt_snapshot', side_effect=lambda _td: _snapshot(receipts)), \
         mock.patch.object(mod, 'record_subagent_receipt',
                           side_effect=lambda _td, item: receipts.append(dict(item)) or item):
        try:
            assert manager.scan_once() == 1
            manager.workers[root_id].join(timeout=1)
            assert switched
            assert not manager.workers[root_id].is_alive(), 'publication must not acquire a nested lock'
            assert manager.worker_results[root_id] == 0
            assert [r['event'] for r in receipts] == ['started']
        finally:
            manager.stop()


def test_live_worker_rejects_untrusted_registration_before_completion(tmp_path, monkeypatch):
    cases = [
        ('missing', None), ('unsafe_mode', None), ('owner', 'untrusted'),
        ('version', -1), ('repo_root', '/different-root'),
        ('thread_id', '019f82a6-ce64-75a3-b01d-92f7b0b4fe70'),
        ('session_cwd', '/different-workspace'),
    ]
    for field, value in cases:
        mod, repo, root_id, _, _ = _bound_lifecycle_fixture(tmp_path / field, monkeypatch)
        original_child_status = mod._child_status
        state_path = mod._state_path(str(repo), root_id)
        receipts = []
        changed = False
        manager = mod.WatcherManager(str(repo))

        def child_status(*args, **kwargs):
            nonlocal changed
            result = original_child_status(*args, **kwargs)
            if not changed and _binding_read_stage() == '_maybe_complete':
                changed = True
                if field == 'missing':
                    state_path.unlink()
                elif field == 'unsafe_mode':
                    state_path.chmod(0o666)
                else:
                    state = json.loads(state_path.read_text())
                    state[field] = value
                    state_path.write_text(json.dumps(state) + '\n')
            return result

        with mock.patch.object(mod, '_child_status', side_effect=child_status), \
             mock.patch.object(mod, 'receipt_snapshot', side_effect=lambda _td: _snapshot(receipts)), \
             mock.patch.object(mod, 'record_subagent_receipt',
                               side_effect=lambda _td, item: receipts.append(dict(item)) or item):
            try:
                assert manager.scan_once() == 1
                manager.workers[root_id].join(timeout=1)
                assert changed, field
                assert not manager.workers[root_id].is_alive(), field
                assert manager.worker_results[root_id] != 0, field
                assert [r['event'] for r in receipts] == ['started'], field
            finally:
                manager.stop()


def test_direct_bound_watch_rejects_missing_or_mismatched_initial_registration(tmp_path, monkeypatch):
    for field, value in [('missing', None), ('offset', 0),
                         ('task_id', 'TASK__different'), ('run_id', PRIOR_RUN_ID)]:
        mod, repo, root_id, _, registration = _bound_lifecycle_fixture(tmp_path / field, monkeypatch)
        state_path = mod._state_path(str(repo), root_id)
        if field == 'missing':
            state_path.unlink()
        else:
            state = json.loads(state_path.read_text())
            state[field] = value
            state_path.write_text(json.dumps(state) + '\n')
        with mock.patch.object(mod, 'record_subagent_receipt') as record:
            result = mod.watch(str(repo), root_id, registration['rollout'], registration['offset'],
                               task_id=registration['task_id'], run_id=registration['run_id'],
                               idle_seconds=0.03)
        assert result != 0, field
        record.assert_not_called()


def _followup_events(child_id, agent_path, target=None, call_id="call_followup_123456"):
    events = _spawn_events("unused", child_id, "unused", agent_path, call_id)
    events[0]["payload"].update(name="followup_task", arguments=json.dumps({
        "target": target or agent_path, "message": "encrypted",
    }))
    events[1]["payload"]["item"]["kind"] = "interacted"
    events[2]["payload"]["output"] = ""
    return events


def _assert_native_followup(tmp_path, monkeypatch, target, *, delayed_child_completion=False,
                            partial_terminal=False, failed_followup=False,
                            accepted_followup_pending=False):
    mod = _load()
    codex_home = tmp_path / ".codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    repo = tmp_path / "repo"
    task_dir = repo / "doc/harness/tasks/TASK__watcher"
    task_dir.mkdir(parents=True)
    root_id = "019f825b-f25f-70c3-8ee8-071f79fa1c42"
    child_id = "019f82a6-ce64-75a3-b01d-92f7b0b4fe6f"
    agent_path = "/root/code_review"
    first = "VERDICT: PASS\nFINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0"
    second = "VERDICT: FAIL\nFINDING_COUNTS: FIX_NOW=1 INVESTIGATE=0 OPTIONAL=0"
    child = _rollout_path(codex_home, child_id)
    initial = _child_events(root_id, child_id, agent_path, str(repo), first)
    _write_jsonl(child, initial)
    receipts = []
    watcher = mod.Watcher(str(repo), root_id)
    with mock.patch.object(mod, "_active_task_binding_for_session", return_value=_active_binding(task_dir)), \
         mock.patch.object(mod, "record_subagent_receipt", side_effect=lambda _td, row: receipts.append(dict(row)) or row), \
         mock.patch.object(mod, "receipt_snapshot", side_effect=lambda _td: _snapshot(receipts)):
        for event in _spawn_events(root_id, child_id, "code_review", agent_path):
            watcher.feed(event)
        watcher.feed(_delivery(agent_path, first))
        if failed_followup:
            failed = _followup_events(child_id, agent_path, target, call_id="call_failed_123456")
            watcher.feed(failed[0])
            watcher.feed(failed[2])
            watcher.feed(_delivery(agent_path, first))
            assert watcher.calls["call_runtime_123456"].get("completed")
            assert not watcher.calls["call_runtime_123456"].get("invalid")
            assert "root_final" not in watcher.calls["call_failed_123456"]
        # A real idle follow-up emits a second NEW_TASK in the same child,
        # an interacted activity and potentially empty tool output.
        next_boundary = _child_events(root_id, child_id, agent_path, str(repo))[-1:]
        _write_jsonl(child, initial + next_boundary)
        with mock.patch.object(mod, "_coordinator_identity", side_effect=mod._AncestryPending()) if accepted_followup_pending else contextlib.nullcontext():
            for event in _followup_events(child_id, agent_path, target):
                watcher.feed(event)
        if accepted_followup_pending:
            watcher.feed(_delivery(agent_path, second))
            assert not watcher.calls["call_runtime_123456"].get("invalid")
            assert watcher.calls["call_followup_123456"]["root_final"] == second
            watcher.retry()
        assert [row.get("event") for row in receipts] == ["started", "completed", "started"]
        assert receipts[0]["runtime_id"] != receipts[2]["runtime_id"]
        assert receipts[1]["verdict"] == "PASS"
        if delayed_child_completion:
            if partial_terminal:
                partial = _child_events(root_id, child_id, agent_path, str(repo), second)[-3:-1]
                _write_jsonl(child, initial + partial)
            watcher.feed(_delivery(agent_path, second))
            assert len(receipts) == 3
            assert not watcher.by_agent[agent_path].get("invalid")
        _write_jsonl(child, initial + _child_events(root_id, child_id, agent_path, str(repo), second)[-3:])
        if delayed_child_completion or accepted_followup_pending:
            watcher.retry()
        else:
            watcher.feed(_delivery(agent_path, second))
        watcher.feed(_delivery(agent_path, second))
        assert [row.get("verdict") for row in receipts] == [None, "PASS", None, "FAIL"]
        assert mod._child_status(child_id, root_id, agent_path, str(repo))[2] == first
        assert mod._child_status(child_id, root_id, agent_path, str(repo), generation=2)[2] == second
        watcher.feed(_delivery(agent_path, first))  # Conflict inside generation2.
    assert [row.get("verdict") for row in receipts] == [None, "PASS", None, "FAIL", "PENDING"]


def test_native_followup_has_distinct_receipts_and_selected_child_turn(tmp_path, monkeypatch):
    _assert_native_followup(tmp_path, monkeypatch, "/root/code_review")


def test_native_followup_accepts_exact_relative_child_name(tmp_path, monkeypatch):
    _assert_native_followup(tmp_path, monkeypatch, "code_review")


def test_root_completion_waits_for_child_generation_flush(tmp_path, monkeypatch):
    _assert_native_followup(tmp_path, monkeypatch, "code_review", delayed_child_completion=True)


def test_partial_terminal_waits_for_task_complete(tmp_path, monkeypatch):
    _assert_native_followup(tmp_path, monkeypatch, "code_review",
                            delayed_child_completion=True, partial_terminal=True)


def test_failed_followup_does_not_consume_generation_or_delivery(tmp_path, monkeypatch):
    _assert_native_followup(tmp_path, monkeypatch, "code_review", failed_followup=True)


def test_accepted_followup_delivery_waits_for_pending_start(tmp_path, monkeypatch):
    _assert_native_followup(tmp_path, monkeypatch, "code_review", accepted_followup_pending=True)


def _assert_accepted_followup_reservations(tmp_path, monkeypatch, *, identical_finals=False,
                                          invalid_before_acceptance=False):
    mod = _load()
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / ".codex"))
    task_dir = tmp_path / "repo/doc/harness/tasks/TASK__watcher"
    task_dir.mkdir(parents=True)
    root_id = "019f825b-f25f-70c3-8ee8-071f79fa1c42"
    child_id = "019f82a6-ce64-75a3-b01d-92f7b0b4fe6f"
    agent_path = "/root/code_review"
    watcher = mod.Watcher(str(tmp_path / "repo"), root_id)
    receipts = []
    with mock.patch.object(mod, "_active_task_binding_for_session", return_value=_active_binding(task_dir)), \
         mock.patch.object(mod, "_coordinator_identity", return_value={"agent_path": "/root"}), \
         mock.patch.object(mod, "_child_status", return_value=("pending", None, "")), \
         mock.patch.object(mod, "record_subagent_receipt", side_effect=lambda _td, row: receipts.append(dict(row)) or row), \
         mock.patch.object(mod, "receipt_snapshot", side_effect=lambda _td: _snapshot(receipts)):
        for event in _spawn_events(root_id, child_id, "code_review", agent_path):
            watcher.feed(event)
        watcher.feed(_delivery(agent_path, "first"))
        second = _followup_events(child_id, agent_path, call_id="call_second_123456")
        third = _followup_events(child_id, agent_path, call_id="call_third_123456")
        if invalid_before_acceptance:
            watcher.feed(second[0])
            bad_output = _spawn_events(root_id, child_id, "code_review", "/root/other_review",
                                       call_id="call_second_123456")[2]
            watcher.feed(bad_output)
            assert watcher.calls["call_second_123456"].get("invalid")
            assert "generation" not in watcher.calls["call_second_123456"]
            assert "child_id" not in watcher.calls["call_second_123456"]
        for event in (second[1:] if invalid_before_acceptance else second) + third:
            watcher.feed(event)
        assert watcher.calls["call_second_123456"]["generation"] == 2
        assert watcher.calls["call_third_123456"]["generation"] == 3
        assert not watcher.calls["call_second_123456"].get("started")
        watcher.feed(second[1])  # Delayed duplicate cannot renumber generation2.
        assert watcher.calls["call_second_123456"]["generation"] == 2
        assert watcher.calls["call_second_123456"].get("invalid")
        second_final = "VERDICT: PASS" if identical_finals else "second"
        third_final = "VERDICT: PASS" if identical_finals else "third"
        watcher.feed(_delivery(agent_path, second_final))
        assert watcher.calls["call_second_123456"]["root_final"] == second_final
        assert "root_final" not in watcher.calls["call_third_123456"]
        watcher.feed(_delivery(agent_path, third_final))
        assert watcher.calls["call_third_123456"]["root_final"] == third_final
        for event in _followup_events(child_id, agent_path, call_id="call_fourth_123456"):
            watcher.feed(event)
        assert watcher.calls["call_fourth_123456"]["generation"] == 4
        assert not receipts


def test_accepted_followup_generations_stay_reserved_before_start(tmp_path, monkeypatch):
    _assert_accepted_followup_reservations(tmp_path, monkeypatch)


def test_accepted_followup_identical_finals_keep_delivery_positions(tmp_path, monkeypatch):
    _assert_accepted_followup_reservations(tmp_path, monkeypatch, identical_finals=True)


def test_accepted_followup_invalid_before_acceptance_reserves_turn(tmp_path, monkeypatch):
    _assert_accepted_followup_reservations(tmp_path, monkeypatch, invalid_before_acceptance=True)


def test_accepted_followup_invalid_before_acceptance_identical_finals(tmp_path, monkeypatch):
    _assert_accepted_followup_reservations(tmp_path, monkeypatch, invalid_before_acceptance=True,
                                          identical_finals=True)


def test_interacted_activity_without_followup_call_is_not_authority(tmp_path, monkeypatch):
    mod = _load()
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / ".codex"))
    watcher = mod.Watcher(str(tmp_path), "019f825b-f25f-70c3-8ee8-071f79fa1c42")
    activity = _followup_events("019f82a6-ce64-75a3-b01d-92f7b0b4fe6f", "/root/code_review")[1]
    with mock.patch.object(mod, "record_subagent_receipt", side_effect=AssertionError("unauthenticated start")):
        watcher.feed(activity)
    assert not watcher.calls


def test_active_binding_must_match_registration_generation(tmp_path, monkeypatch):
    mod, repo, root_id, _, registration = _bound_lifecycle_fixture(tmp_path, monkeypatch)
    generation = mod._registration_generation(registration)
    task = repo / 'doc/harness/tasks' / registration['task_id']
    for binding in ({'task_dir': str(task), 'run_id': PRIOR_RUN_ID},
                    {'task_dir': str(task.with_name('TASK__other')), 'run_id': RUN_ID}):
        with mock.patch.object(mod, '_active_task_binding_for_session', return_value=binding), \
             mock.patch.object(mod, '_current_registration_generation', return_value=generation):
            try:
                mod._require_task_binding(str(repo), root_id, generation, str(repo))
            except mod._RegistrationChanged:
                pass
            else:
                raise AssertionError('unchanged registration cannot authorize a different active task/run')


def test_invalidation_requires_locked_binding_to_match_item(tmp_path, monkeypatch):
    mod, repo, root_id, _, registration = _bound_lifecycle_fixture(tmp_path, monkeypatch)
    task = repo / 'doc/harness/tasks' / registration['task_id']
    watcher = mod.Watcher(str(repo), root_id)
    for task_dir, run_id in ((str(task), PRIOR_RUN_ID),
                             (str(task.with_name('TASK__other')), RUN_ID)):
        item = {'completed': True, 'task_dir': task_dir, 'task_run_id': run_id,
                'task_name': 'code_review_binding', 'agent_path': '/root/code_review_binding',
                'runtime_id': 'old-runtime'}
        with mock.patch.object(mod, 'record_subagent_receipt') as record, \
             mock.patch.object(mod, 'receipt_snapshot') as snapshot:
            try:
                watcher._invalidate(item, 'duplicate terminal')
            except mod._BindingUnavailable:
                pass
            else:
                raise AssertionError('invalidation must reject another item task/run')
        record.assert_not_called()
        snapshot.assert_not_called()
        assert not item.get('invalid'), 'failed publication must remain retryable'


def test_irrelevant_records_use_completion_based_binding_deadline_and_keep_lifecycle(tmp_path, monkeypatch):
    mod, repo, root_id, agent_path, registration = _bound_lifecycle_fixture(tmp_path, monkeypatch)
    rollout = Path(registration['rollout'])
    data = rollout.read_bytes()
    offset = registration['offset']
    irrelevant = (json.dumps({'type': 'event_msg', 'payload': {'type': 'token_count'}}) + '\n').encode()
    rollout.write_bytes(data[:offset] + irrelevant * 250 + data[offset:])
    original_require = mod._require_task_binding
    original_feed = mod.Watcher.feed
    clock = [100.0]
    unrelated_count = [0]
    outer_checks = []
    receipts = []
    cursors = []
    stop = mod.threading.Event()

    def binding(*args, **kwargs):
        if _binding_read_stage() == 'watch':
            outer_checks.append(clock[0])
            clock[0] += mod.BINDING_CHECK_SECONDS * 2
        return original_require(*args, **kwargs)

    def feed(self, event):
        if event.get('payload', {}).get('type') == 'token_count':
            unrelated_count[0] += 1
            if unrelated_count[0] == 125:
                clock[0] += mod.BINDING_CHECK_SECONDS * 2
        return original_feed(self, event)

    def record(_task, item):
        receipts.append(dict(item))
        if item['event'] == 'completed':
            stop.set()
        return item

    with mock.patch.object(mod.time, 'monotonic', side_effect=lambda: clock[0]), \
         mock.patch.object(mod.Watcher, 'feed', new=feed), \
         mock.patch.object(mod, '_require_task_binding', side_effect=binding), \
         mock.patch.object(mod, 'record_subagent_receipt', side_effect=record), \
         mock.patch.object(mod, 'receipt_snapshot', side_effect=lambda _td: _snapshot(receipts)):
        assert mod.watch(str(repo), root_id, str(rollout), offset,
                         task_id=registration['task_id'], run_id=registration['run_id'],
                         idle_seconds=30, stop_event=stop, on_cursor=cursors.append) == 0
    assert outer_checks == [100.0, 104.0], 'check again after the interval, never per unrelated record'
    assert [(r['event'], r.get('verdict', '')) for r in receipts] == [('started', ''), ('completed', 'PASS')]
    assert all(r['agent_id'] == agent_path for r in receipts)
    assert cursors[0] == offset and cursors[-1] == rollout.stat().st_size
    assert cursors == sorted(cursors)


def test_retry_and_invalidation_reject_changed_active_generation(tmp_path, monkeypatch):
    mod, repo, root_id, agent_path, registration = _bound_lifecycle_fixture(tmp_path, monkeypatch)
    task = repo / 'doc/harness/tasks' / registration['task_id']
    generation = mod._registration_generation(registration)
    watcher = mod.Watcher(str(repo), root_id)
    watcher.expected_generation = generation
    item = {'started': True, 'completed': True, 'root_final': 'VERDICT: PASS',
            'task_dir': str(task), 'task_run_id': RUN_ID,
            'task_name': 'code_review_binding', 'agent_path': agent_path,
            'child_id': '019f82a6-ce64-75a3-b01d-92f7b0b4fe6f', 'runtime_id': 'old-runtime'}
    watcher.calls['call_binding_123456'] = item
    wrong_binding = {'task_dir': str(task), 'run_id': PRIOR_RUN_ID}
    with mock.patch.object(mod, '_active_task_binding_for_session', return_value=wrong_binding), \
         mock.patch.object(mod, '_current_registration_generation', return_value=generation), \
         mock.patch.object(mod, 'record_subagent_receipt') as record:
        for action in (lambda: watcher._invalidate(item, 'duplicate terminal'), watcher.retry):
            if action == watcher.retry:
                item['completed'] = False
            try:
                action()
            except mod._RegistrationChanged:
                pass
            else:
                raise AssertionError('periodic liveness cannot authorize stale publication')
        record.assert_not_called()
