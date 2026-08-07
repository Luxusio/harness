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


def test_current_raw_spawn_output_requires_explicit_agent_id():
    mod = _load()
    child_id = "019f82a6-ce64-75a3-b01d-92f7b0b4fe6f"

    def event(output):
        return {
            "type": "response_item",
            "payload": {
                "type": "function_call_output",
                "call_id": "call_runtime_123456",
                "output": output,
            },
        }

    assert mod._spawn_output(
        event("agent_name: '/root/display_only'"), require_agent_id=True,
    ) is None
    assert mod._spawn_output(
        event(f"agent_id: '{child_id}'"), require_agent_id=True,
    ) == ("call_runtime_123456", child_id)
    for alias in (
        "not_agent_id", "parent_agent_id", "display_agent_id",
        "display-agent_id", "parent.agent_id", "display/agent_id",
    ):
        assert mod._spawn_output(
            event(f"{alias}: '{child_id}'"), require_agent_id=True,
        ) is None
    assert mod._spawn_output(
        event(f"{{ agent_id: '{child_id}' }}"), require_agent_id=True,
    ) == ("call_runtime_123456", child_id)
    assert mod._spawn_output(event("agent_name: '/root/legacy_display'")) == (
        "call_runtime_123456", "/root/legacy_display",
    )
    for alias in ("display-agent_name", "parent.agent_name", "display/agent_name"):
        assert mod._spawn_output(event(f"{alias}: '/root/display_only'")) is None


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


def _exec_spawn_events(child_id: str, task_name: str):
    call_id = "call_exec_runtime_123456"
    return [
        {"type": "response_item", "payload": {
            "type": "custom_tool_call", "name": "exec", "call_id": call_id,
            "input": (
                "const result = await tools.multi_agent_v1__spawn_agent("
                f'{{task_name: "{task_name}", message: "encrypted"}}); text(result);'
            ),
        }},
        {"type": "event_msg", "payload": {
            "type": "sub_agent_activity", "kind": "started", "event_id": call_id,
            "agent_thread_id": child_id, "agent_path": child_id,
        }},
        {"type": "response_item", "payload": {
            "type": "custom_tool_call_output", "call_id": call_id,
            "output": json.dumps({"agent_id": child_id}),
        }},
    ]


def _current_spawn_events(child_id: str, task_name: str):
    call_id = "call_current_runtime_123456"
    return [
        {"type": "response_item", "payload": {
            "type": "function_call", "namespace": "multi_agent_v1", "name": "spawn_agent",
            "call_id": call_id,
            "arguments": json.dumps({
                "message": f"task_name: {task_name}\nReview the final snapshot.",
                "items": [],
            }),
        }},
        {"type": "response_item", "payload": {
            "type": "function_call_output", "call_id": call_id,
            "output": json.dumps({"agent_id": child_id, "nickname": "DisplayOnly"}),
        }},
    ]


def _current_completion_events(kind: str, child_id: str, final: str):
    call_id = f"call_{kind}_runtime_123456"
    arguments = {"agent_ids": [child_id]}
    if kind == "close_agent":
        arguments = {"agent_id": child_id}
    output = {"status": {child_id: {"completed": final}}}
    if kind == "close_agent":
        output = {"previous_status": {"completed": final}}
    return [
        {"type": "response_item", "payload": {
            "type": "function_call", "name": f"multi_agent_v1__{kind}",
            "call_id": call_id, "arguments": json.dumps(arguments),
        }},
        {"type": "response_item", "payload": {
            "type": "function_call_output", "call_id": call_id,
            "output": json.dumps(output),
        }},
    ]


def test_exec_wrapped_current_completion_calls_normalize():
    mod = _load()
    child_id = "019f82a6-ce64-75a3-b01d-92f7b0b4fe6f"
    wait_event = {"type": "response_item", "payload": {
        "type": "custom_tool_call", "name": "exec", "call_id": "call_wait_wrapped_123",
        "input": "const r = await tools.multi_agent_v1__wait_agent({}); text(r);",
    }}
    close_event = {"type": "response_item", "payload": {
        "type": "custom_tool_call", "name": "exec", "call_id": "call_close_wrapped_123",
        "input": (
            "const r = await tools.multi_agent_v1__close_agent("
            f'{{agent_id: "{child_id}"}}); text(r);'
        ),
    }}

    assert mod._completion_call(wait_event) == (
        "call_wait_wrapped_123", "wait_agent", "",
    )
    assert mod._completion_call(close_event) == (
        "call_close_wrapped_123", "close_agent", child_id,
    )


def test_current_completion_without_recorded_start_is_ignored(tmp_path):
    mod = _load()
    child_id = "019f82a6-ce64-75a3-b01d-92f7b0b4fe6f"
    watcher = mod.Watcher(
        str(tmp_path), "019f825b-f25f-70c3-8ee8-071f79fa1c42",
    )

    with mock.patch.object(mod, "record_subagent_receipt") as record:
        for event in _current_completion_events(
            "wait_agent", child_id, "VERDICT: PASS\nQA passed",
        ):
            watcher.feed(event)

    record.assert_not_called()


def _status_notification(agent_id: str, final: str):
    return {"type": "response_item", "payload": {
        "type": "message", "role": "user",
        "content": [{"type": "input_text", "text": (
            "<subagent_notification>\n"
            + json.dumps({"agent_path": agent_id, "status": {"completed": final}})
            + "\n</subagent_notification>"
        )}],
    }}


def _status_child_events(
    root_id: str,
    child_id: str,
    task_name: str,
    cwd: str,
    final: str | None = None,
    *,
    agent_path: str | None = None,
):
    agent_path = agent_path or child_id
    events = [{
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
    }, {
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": f"task_name: {task_name}\nReview the task."}],
        },
    }]
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


def _task_context_binding(task_id: str, task_dir: str, *, ok: bool = True):
    return {"type": "event_msg", "payload": {
        "type": "mcp_tool_call_end",
        "invocation": {
            "server": "harness", "tool": "task_context",
            "arguments": {"task_id": task_id},
        },
        "result": {"Ok" if ok else "Err": {
            "structuredContent": {
                "task_id": task_id,
                "task_dir": task_dir,
                "task_context": {"task_id": task_id},
            },
        }},
    }}


def _task_start_binding(slug: str, task_id: str, task_dir: str):
    event = _task_context_binding(task_id, task_dir)
    event["payload"]["invocation"]["tool"] = "task_start"
    event["payload"]["invocation"]["arguments"] = {"slug": slug}
    return event


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


def test_watcher_records_exec_wrapped_spawn_and_status_notification(tmp_path, monkeypatch):
    mod = _load()
    codex_home = tmp_path / ".codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    repo = tmp_path / "repo"
    task_dir = repo / "doc/harness/tasks/TASK__watcher-exec"
    task_dir.mkdir(parents=True)
    root_id = "019f825b-f25f-70c3-8ee8-071f79fa1c42"
    child_id = "019f82a6-ce64-75a3-b01d-92f7b0b4fe6f"
    task_name = "code_review_exec_runtime"
    child = codex_home / "sessions/day" / f"rollout-{child_id}.jsonl"
    _write_jsonl(child, _status_child_events(root_id, child_id, task_name, str(repo)))
    receipts = []

    def record(_task_dir, receipt):
        entry = {
            **receipt,
            "head_sha": receipt.get("head_sha") or "a" * 40,
            "base_sha": receipt.get("base_sha") or "a" * 40,
            "diff_fingerprint": receipt.get("diff_fingerprint") or "sha256:before",
        }
        receipts.append(entry)
        return entry

    watcher = mod.Watcher(str(repo), root_id)
    final = "VERDICT: PASS\nFINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0\nClean"
    with mock.patch.object(mod, "_active_task_for_session", return_value=str(task_dir)), \
         mock.patch.object(mod, "review_diff_fingerprint", return_value="sha256:before"), \
         mock.patch.object(mod, "record_subagent_receipt", side_effect=record), \
         mock.patch.object(mod, "list_review_receipts", side_effect=lambda _td: receipts), \
         mock.patch.object(mod, "list_subagent_receipts", return_value=[]):
        for event in _exec_spawn_events(child_id, task_name):
            watcher.feed(event)
        _write_jsonl(
            child,
            _status_child_events(root_id, child_id, task_name, str(repo), final),
        )
        watcher.feed(_status_notification(child_id, final))

    assert [(item["status"], item.get("verdict", "")) for item in receipts] == [
        ("started", ""), ("completed", "PASS"),
    ]
    assert receipts[-1]["agent_id"] == child_id


def test_current_spawn_requires_one_strict_first_line_task_marker():
    mod = _load()
    base = {
        "type": "response_item",
        "payload": {
            "type": "function_call", "namespace": "multi_agent_v1", "name": "spawn_agent",
            "call_id": "call_current_runtime_123456",
        },
    }

    def parsed(arguments):
        event = json.loads(json.dumps(base))
        event["payload"]["arguments"] = json.dumps(arguments)
        return mod._spawn_call(event)

    assert parsed({"message": "task_name: qa_cli_current\nRun QA."}) == (
        "call_current_runtime_123456", "qa_cli_current", True,
    )
    assert parsed({"message": "Run QA.\ntask_name: qa_cli_late"}) is None
    assert parsed({"message": "Run QA without a marker."}) is None
    assert parsed({
        "message": "task_name: qa_cli_first\nRun QA.",
        "items": [{"text": "task_name: qa_cli_second\nRun QA."}],
    }) is None
    assert parsed({"message": " task_name: qa_cli_indented\nRun QA."}) is None

    exec_event = {"type": "response_item", "payload": {
        "type": "custom_tool_call", "name": "exec",
        "call_id": "call_exec_prompt_123456",
        "input": (
            "const r = await tools.multi_agent_v1__spawn_agent({message: "
            + json.dumps("task_name: qa_cli_exec_prompt\nRun QA.")
            + "}); text(r);"
        ),
    }}
    assert mod._spawn_call(exec_event) == (
        "call_exec_prompt_123456", "qa_cli_exec_prompt", True,
    )


def test_valid_non_receipt_worker_spawn_does_not_report_adapter_failure():
    mod = _load()
    structured = {
        "type": "response_item",
        "payload": {
            "type": "function_call",
            "namespace": "multi_agent_v1",
            "name": "spawn_agent",
            "call_id": "call_worker_structured_123",
            "arguments": json.dumps({"task_name": "worker_parse_protocol"}),
        },
    }
    marker = {
        "type": "response_item",
        "payload": {
            "type": "function_call",
            "namespace": "multi_agent_v1",
            "name": "spawn_agent",
            "call_id": "call_worker_marker_123456",
            "arguments": json.dumps({
                "message": "task_name: worker_parse_protocol\nImplement the parser.",
            }),
        },
    }

    assert mod._spawn_call(structured) is None
    assert mod._unsupported_current_spawn(structured) is None
    assert mod._spawn_call(marker) is None
    assert mod._unsupported_current_spawn(marker) is None


def test_malformed_current_spawn_records_actionable_adapter_diagnostic(tmp_path, monkeypatch):
    mod = _load()
    repo = tmp_path / "repo"
    task_dir = repo / "doc/harness/tasks/TASK__adapter-diagnostic"
    task_dir.mkdir(parents=True)
    watcher = mod.Watcher(
        str(repo), "019f825b-f25f-70c3-8ee8-071f79fa1c42",
    )
    receipts = []

    event = {
        "type": "response_item",
        "payload": {
            "type": "function_call",
            "namespace": "multi_agent_v1",
            "name": "spawn_agent",
            "call_id": "call_bad_adapter_123456",
            "arguments": json.dumps({
                "message": "Run QA.\ntask_name: qa_cli_too_late",
            }),
        },
    }
    with mock.patch.object(
        mod, "_active_task_for_session", return_value=str(task_dir),
    ), mock.patch.object(
        mod, "record_subagent_receipt",
        side_effect=lambda _td, receipt: receipts.append(receipt) or receipt,
    ), mock.patch.object(mod, "list_review_receipts", return_value=[]), \
         mock.patch.object(mod, "list_subagent_receipts", return_value=[]):
        watcher.feed(event)

    assert len(receipts) == 1
    assert receipts[0]["status"] == "adapter_unsupported"
    assert receipts[0]["runtime_event_id"] == "call_bad_adapter_123456:adapter"
    assert receipts[0]["summary"].startswith(
        "Receipt adapter unsupported: observed=multi_agent_v1__spawn_agent"
    )


def test_current_spawn_invalid_output_records_adapter_diagnostic(tmp_path):
    mod = _load()
    repo = tmp_path / "repo"
    task_dir = repo / "doc/harness/tasks/TASK__adapter-output"
    task_dir.mkdir(parents=True)
    watcher = mod.Watcher(
        str(repo), "019f825b-f25f-70c3-8ee8-071f79fa1c42",
    )
    receipts = []
    spawn = _current_spawn_events(
        "019f82a6-ce64-75a3-b01d-92f7b0b4fe6f", "qa_cli_output",
    )[0]
    bad_output = {
        "type": "response_item",
        "payload": {
            "type": "function_call_output",
            "call_id": "call_current_runtime_123456",
            "output": json.dumps({
                "agent_name": "/root/display_only",
                "nickname": "DisplayOnly",
            }),
        },
    }
    with mock.patch.object(
        mod, "_active_task_for_session", return_value=str(task_dir),
    ), mock.patch.object(
        mod, "record_subagent_receipt",
        side_effect=lambda _td, receipt: receipts.append(receipt) or receipt,
    ), mock.patch.object(mod, "list_review_receipts", return_value=[]), \
         mock.patch.object(mod, "list_subagent_receipts", return_value=[]):
        watcher.feed(spawn)
        watcher.feed(bad_output)

    assert [item["status"] for item in receipts] == ["adapter_unsupported"]
    assert "valid agent_id" in receipts[0]["summary"]


def test_current_spawn_and_completion_sources_use_agent_id_once(tmp_path, monkeypatch):
    mod = _load()
    codex_home = tmp_path / ".codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    repo = tmp_path / "repo"
    task_dir = repo / "doc/harness/tasks/TASK__watcher-current"
    task_dir.mkdir(parents=True)
    root_id = "019f825b-f25f-70c3-8ee8-071f79fa1c42"
    completions = (
        ("wait_agent", "PASS"),
        ("notification", "FAIL"),
        ("close_agent", "BLOCKED_ENV"),
    )

    for index, (completion, expected_verdict) in enumerate(completions):
        child_id = f"019f82a6-ce64-75a3-b01d-92f7b0b4fe{70 + index:02d}"
        task_name = f"qa_cli_current_{completion}"
        activity_path = "/root/qa_cli_current_wait" if completion == "wait_agent" else child_id
        child = codex_home / "sessions/day" / f"rollout-{child_id}.jsonl"
        _write_jsonl(child, _status_child_events(
            root_id, child_id, task_name, str(repo), agent_path=activity_path,
        ))
        receipts = []

        def record(_task_dir, receipt):
            entry = {
                **receipt,
                "head_sha": receipt.get("head_sha") or "a" * 40,
                "base_sha": receipt.get("base_sha") or "a" * 40,
                "diff_fingerprint": receipt.get("diff_fingerprint") or "sha256:before",
            }
            receipts.append(entry)
            return entry

        watcher = mod.Watcher(str(repo), root_id)
        final = f"VERDICT: {expected_verdict}\nQA completed"
        with mock.patch.object(mod, "_active_task_for_session", return_value=str(task_dir)), \
             mock.patch.object(mod, "review_diff_fingerprint", return_value="sha256:before"), \
             mock.patch.object(mod, "record_subagent_receipt", side_effect=record), \
             mock.patch.object(mod, "list_review_receipts", return_value=[]), \
             mock.patch.object(mod, "list_subagent_receipts", side_effect=lambda _td: receipts):
            spawn_events = _current_spawn_events(child_id, task_name)
            if activity_path != child_id:
                spawn_events.insert(1, {"type": "event_msg", "payload": {
                    "type": "sub_agent_activity", "kind": "started",
                    "event_id": "call_current_runtime_123456",
                    "agent_thread_id": child_id, "agent_path": activity_path,
                }})
            for event in spawn_events:
                watcher.feed(event)
            assert [(item["status"], item["agent_id"]) for item in receipts] == [
                ("started", child_id),
            ]
            _write_jsonl(
                child, _status_child_events(
                    root_id, child_id, task_name, str(repo), final,
                    agent_path=activity_path,
                ),
            )
            if completion == "notification":
                delivery_events = [_status_notification(child_id, final)]
            else:
                delivery_events = _current_completion_events(completion, child_id, final)
            for event in delivery_events:
                watcher.feed(event)
            for event in delivery_events:
                watcher.feed(event)

        assert [(item["status"], item.get("verdict", "")) for item in receipts] == [
            ("started", ""), ("completed", expected_verdict),
        ]
        assert receipts[-1]["agent_id"] == child_id
        assert receipts[-1]["runtime_agent_path"] == child_id


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
        entry = {
            **receipt,
            "head_sha": receipt.get("head_sha") or "a" * 40,
            "base_sha": receipt.get("base_sha") or "a" * 40,
            "diff_fingerprint": receipt.get("diff_fingerprint") or "sha256:before",
        }
        receipts.append(entry)
        return entry

    watcher = mod.Watcher(str(repo), root_id)
    with mock.patch.object(mod, "_active_task_for_session", return_value=str(task_dir)), \
         mock.patch.object(mod, "review_diff_fingerprint", return_value="sha256:before"), \
         mock.patch.object(mod, "record_subagent_receipt", side_effect=record), \
         mock.patch.object(mod, "list_review_receipts", return_value=[]), \
         mock.patch.object(mod, "list_subagent_receipts", side_effect=lambda _td: receipts):
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

    completed = [item for item in receipts if item["status"] == "completed"]
    assert [item["lens"] for item in completed] == ["qa-cli", "qa-cli"]
    assert [item["verdict"] for item in completed] == ["PASS", "PASS"]
    assert len({item["runtime_agent_path"] for item in completed}) == 2


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
        entry = {
            **receipt,
            "head_sha": receipt.get("head_sha") or "a" * 40,
            "base_sha": receipt.get("base_sha") or "a" * 40,
            "diff_fingerprint": receipt.get("diff_fingerprint") or "sha256:before",
        }
        receipts.append(entry)
        return entry

    watcher = mod.Watcher(str(repo), root_id)
    final = "VERDICT: PASS\nQA passed"
    with mock.patch.object(mod, "_active_task_for_session", return_value=str(task_dir)), \
         mock.patch.object(mod, "review_diff_fingerprint", return_value="sha256:before"), \
         mock.patch.object(mod, "record_subagent_receipt", side_effect=record), \
         mock.patch.object(mod, "list_review_receipts", return_value=[]), \
         mock.patch.object(mod, "list_subagent_receipts", side_effect=lambda _td: receipts):
        for event in _spawn_events(root_id, child_id, "qa_cli_status_r1", agent_path):
            watcher.feed(event)
        watcher.feed(_intermediate_message(agent_path))
        assert watcher.by_path[agent_path].get("root_final") is None

        _write_jsonl(child, _child_events(root_id, child_id, agent_path, str(repo), final))
        watcher.feed(_delivery(agent_path, final))

    assert [(item["status"], item.get("verdict", "")) for item in receipts] == [
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
    with mock.patch.object(mod, "_active_task_for_session", return_value="/task"), \
         mock.patch.object(mod, "record_subagent_receipt", side_effect=lambda td, item: receipts.append(item)), \
         mock.patch.object(mod, "list_review_receipts", return_value=[]), \
         mock.patch.object(mod, "list_subagent_receipts", return_value=[]):
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
    event_id = f"{root_id}:{call_id}:{child_id}"
    final = "VERDICT: PASS\nFINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0"
    child = codex_home / "sessions/day" / f"rollout-{child_id}.jsonl"
    _write_jsonl(child, _child_events(root_id, child_id, agent_path, str(repo), final))
    receipts = [{
        "status": "started", "agent_id": agent_path, "lens": "review-code",
        "runtime_event_id": event_id, "head_sha": "a" * 40,
        "base_sha": "a" * 40, "diff_fingerprint": "sha256:before",
        "runtime_session_id": root_id, "runtime_thread_id": child_id,
        "runtime_agent_path": agent_path,
    }]

    def record(_task_dir, receipt):
        receipts.append(receipt)
        return receipt

    watcher = mod.Watcher(str(repo), root_id)
    with mock.patch.object(mod, "_active_task_for_session", return_value=str(task_dir)), \
         mock.patch.object(mod, "review_diff_fingerprint", return_value="sha256:before"), \
         mock.patch.object(mod, "record_subagent_receipt", side_effect=record), \
         mock.patch.object(mod, "list_review_receipts", side_effect=lambda _td: receipts), \
         mock.patch.object(mod, "list_subagent_receipts", return_value=[]):
        for event in _spawn_events(root_id, child_id, "code_review", agent_path):
            watcher.feed(event)
        watcher.feed(_delivery(agent_path, final))

    assert [(item["status"], item.get("verdict")) for item in receipts] == [
        ("started", None), ("completed", "PASS"),
    ]


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
        entry = {
            **receipt,
            "head_sha": receipt.get("head_sha") or "a" * 40,
            "base_sha": receipt.get("base_sha") or "a" * 40,
            "diff_fingerprint": receipt.get("diff_fingerprint") or "sha256:stable",
        }
        receipts.append(entry)
        return entry

    watcher = mod.Watcher(
        str(control_root), root_id, session_cwd=str(session_cwd)
    )
    with mock.patch.object(
        mod, "_active_task_for_session", return_value=str(task_dir)
    ), mock.patch.object(
        mod, "review_diff_fingerprint", return_value="sha256:stable"
    ), mock.patch.object(
        mod, "record_subagent_receipt", side_effect=record
    ), mock.patch.object(
        mod, "list_review_receipts", return_value=[]
    ), mock.patch.object(
        mod, "list_subagent_receipts", side_effect=lambda _td: receipts
    ):
        for event in _spawn_events(root_id, child_id, "qa_cli", agent_path):
            watcher.feed(event)
        _write_jsonl(
            child,
            _child_events(root_id, child_id, agent_path, str(session_cwd), final),
        )
        watcher.feed(_delivery(agent_path, final))

    assert [(item["status"], item.get("verdict")) for item in receipts] == [
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


def test_ensure_migrates_valid_v2_registration_without_changing_offset(tmp_path, monkeypatch):
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
    original_offset = rollout.stat().st_size
    with rollout.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"type": "event_msg", "payload": {"type": "later"}}) + "\n")
    state_path = mod._state_path(str(repo), root_id)
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps({
        "version": 2,
        "owner": "session_start_hook",
        "thread_id": root_id,
        "repo_root": str(repo.resolve()),
        "rollout": str(rollout),
        "offset": original_offset,
        "registered_at": 1234.5,
    }))

    assert mod.ensure(str(repo), root_id)
    state = json.loads(state_path.read_text())
    assert state["version"] == mod.REGISTRATION_VERSION
    assert state["owner"] == mod.REGISTRATION_OWNER
    assert state["offset"] == original_offset
    assert state["registered_at"] == 1234.5


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


def test_validated_task_dir_rejects_symlinked_tasks_root(tmp_path):
    mod = _load()
    attacker = tmp_path / "attacker"
    victim = tmp_path / "victim"
    (attacker / ".git").mkdir(parents=True)
    victim_task = victim / "doc/harness/tasks/TASK__victim"
    victim_task.mkdir(parents=True)
    (victim_task / "TASK_STATE.yaml").write_text(
        "task_id: TASK__victim\nstatus: created\nruntime_verdict: pending\n"
        "touched_paths: []\nplan_session_state: closed\nclosed_at: null\nupdated: now\n"
    )
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
    (task / "TASK_STATE.yaml").write_text(
        "task_id: TASK__root-workspace\nstatus: created\nruntime_verdict: pending\n"
        "touched_paths: []\nplan_session_state: closed\nclosed_at: null\nupdated: now\n"
    )
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
    (task / "TASK_STATE.yaml").write_text(
        "task_id: TASK__unsafe-root-workspace\nstatus: created\n"
        "runtime_verdict: pending\ntouched_paths: []\nplan_session_state: closed\n"
        "closed_at: null\nupdated: now\n"
    )
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


def test_watcher_binds_task_from_successful_root_mcp_context_without_session_marker(tmp_path, monkeypatch):
    mod = _load()
    codex_home = tmp_path / ".codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    task_id = "TASK__watcher"
    task_dir = repo / "doc/harness/tasks" / task_id
    task_dir.mkdir(parents=True)
    (task_dir / "TASK_STATE.yaml").write_text(
        f"task_id: {task_id}\nstatus: created\nruntime_verdict: pending\n"
        "touched_paths: []\nplan_session_state: closed\nclosed_at: null\nupdated: now\n"
    )
    root_id = "019f825b-f25f-70c3-8ee8-071f79fa1c42"
    child_id = "019f82a6-ce64-75a3-b01d-92f7b0b4fe6f"
    agent_path = "/root/code_review"
    child = codex_home / "sessions/day" / f"rollout-{child_id}.jsonl"
    _write_jsonl(child, _child_events(root_id, child_id, agent_path, str(repo)))
    receipts = []

    def record(_task_dir, receipt):
        entry = {**receipt, "head_sha": "a" * 40, "base_sha": "a" * 40,
                 "diff_fingerprint": "sha256:before"}
        receipts.append(entry)
        return entry

    watcher = mod.Watcher(str(repo), root_id)
    with mock.patch.object(mod, "record_subagent_receipt", side_effect=record), \
         mock.patch.object(mod, "list_review_receipts", side_effect=lambda _td: receipts), \
         mock.patch.object(mod, "list_subagent_receipts", return_value=[]):
        watcher.feed(_task_context_binding(task_id, str(task_dir)))
        for event in _spawn_events(root_id, child_id, "code_review", agent_path):
            watcher.feed(event)

    assert watcher.task_dir == str(task_dir.resolve())
    assert [(item["status"], item["lens"]) for item in receipts] == [("started", "review-code")]


def test_watcher_rejects_failed_or_invalid_root_mcp_task_binding(tmp_path):
    mod = _load()
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    task_id = "TASK__watcher"
    task_dir = repo / "doc/harness/tasks" / task_id
    task_dir.mkdir(parents=True)
    (task_dir / "TASK_STATE.yaml").write_text(
        f"task_id: {task_id}\nstatus: created\nruntime_verdict: pending\n"
        "touched_paths: []\nplan_session_state: closed\nclosed_at: null\nupdated: now\n"
    )
    watcher = mod.Watcher(str(repo), "019f825b-f25f-70c3-8ee8-071f79fa1c42")
    watcher.feed(_task_context_binding(task_id, str(task_dir), ok=False))
    assert watcher.task_dir == ""
    wrong_server = _task_context_binding(task_id, str(task_dir))
    wrong_server["payload"]["invocation"]["server"] = "other"
    watcher.feed(wrong_server)
    assert watcher.task_dir == ""
    watcher.feed(_task_context_binding("../TASK__watcher", str(task_dir)))
    assert watcher.task_dir == ""


def test_watcher_binds_real_task_start_slug_from_success_result(tmp_path):
    mod = _load()
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    task_id = "TASK__watcher"
    task_dir = repo / "doc/harness/tasks" / task_id
    task_dir.mkdir(parents=True)
    (task_dir / "TASK_STATE.yaml").write_text(
        f"task_id: {task_id}\nstatus: created\nruntime_verdict: pending\n"
        "touched_paths: []\nplan_session_state: closed\nclosed_at: null\nupdated: now\n"
    )
    watcher = mod.Watcher(str(repo), "019f825b-f25f-70c3-8ee8-071f79fa1c42")
    watcher.feed(_task_start_binding("watcher", task_id, str(task_dir)))
    assert watcher.task_dir == str(task_dir.resolve())

    conflicting = _task_context_binding(task_id, str(task_dir))
    conflicting["payload"]["invocation"]["arguments"]["task_id"] = "TASK__other"
    watcher.task_dir = ""
    watcher.feed(conflicting)
    assert watcher.task_dir == ""


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
        assert [item.get("verdict") for item in receipts] == [None, "PASS"]
        watcher.feed(_delivery(
            agent_path,
            "VERDICT: FAIL\nFINDING_COUNTS: FIX_NOW=1 INVESTIGATE=0 OPTIONAL=0",
        ))
    assert [item.get("verdict") for item in receipts] == [None, "PASS", "PENDING"]
