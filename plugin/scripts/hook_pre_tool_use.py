#!/usr/bin/env python3
"""Codex PreToolUse wrapper: one hook file per event type."""
from __future__ import annotations

import json
import os
import subprocess
import sys

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS_DIR)

try:
    from codex_hook_registration import restore_watcher_registration  # type: ignore
except Exception:  # pragma: no cover - registration recovery is best effort
    restore_watcher_registration = None

try:
    from _lib import find_repo_root, resolve_active_task_dir  # type: ignore
    from background_registry import register_subagent_start  # type: ignore
except Exception:  # pragma: no cover - hook must fail open
    find_repo_root = None
    resolve_active_task_dir = None
    register_subagent_start = None


def _payload_cwd(payload: bytes) -> str | None:
    try:
        data = json.loads(payload.decode("utf-8") or "{}")
    except Exception:
        return None
    cwd = data.get("cwd")
    return cwd if isinstance(cwd, str) and os.path.isdir(cwd) else None


def _tool_name(payload: bytes) -> str:
    try:
        data = json.loads(payload.decode("utf-8") or "{}")
    except Exception:
        return ""
    return str(data.get("tool_name") or data.get("tool") or "")


def _json_payload(payload: bytes) -> dict:
    try:
        data = json.loads(payload.decode("utf-8") or "{}")
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _is_subagent_spawn_tool(tool_name: str) -> bool:
    name = (tool_name or "").lower().replace("-", "_")
    return (
        name == "spawn_agent"
        or name.endswith(".spawn_agent")
        or name.endswith("__spawn_agent")
        or "multi_agent" in name and "spawn_agent" in name
    )


def _structured_task_id(tool_input: dict) -> str:
    task_id = tool_input.get("task_id") or tool_input.get("taskId")
    return str(task_id).strip() if task_id else ""


def _record_codex_subagent_start(payload: bytes) -> None:
    if register_subagent_start is None or find_repo_root is None or resolve_active_task_dir is None:
        return
    data = _json_payload(payload)
    if not _is_subagent_spawn_tool(str(data.get("tool_name") or data.get("tool") or "")):
        return
    try:
        repo_root = find_repo_root(_payload_cwd(payload) or os.getcwd())
        tool_input = data.get("tool_input") or data.get("input") or data.get("arguments") or {}
        if isinstance(tool_input, dict):
            expected_task_id = _structured_task_id(tool_input)
            if expected_task_id:
                active_task_dir = resolve_active_task_dir(repo_root)
                if os.path.basename(os.path.normpath(active_task_dir or "")) != expected_task_id:
                    return
            payload_for_registry = dict(data)
            payload_for_registry["agent_type"] = str(
                tool_input.get("agent_type")
                or tool_input.get("type")
                or tool_input.get("task_name")
                or "default"
            )
            payload_for_registry["agent_id"] = str(
                data.get("tool_call_id")
                or data.get("call_id")
                or data.get("id")
                or ""
            )
            payload_for_registry["hook_event_name"] = "CodexSubagentStart"
        else:
            payload_for_registry = dict(data)
            payload_for_registry["hook_event_name"] = "CodexSubagentStart"
        register_subagent_start(repo_root, payload_for_registry, allow_generated_id=True)
    except Exception:
        pass


def _run(script: str, payload: bytes) -> bytes:
    try:
        proc = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS_DIR, script)],
            input=payload,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=3,
            cwd=_payload_cwd(payload),
        )
        return proc.stdout or b""
    except Exception:
        return b""


def main() -> int:
    payload = sys.stdin.buffer.read()
    if restore_watcher_registration is not None:
        restore_watcher_registration(payload)
    # Codex exposes the durable runtime agent id only in the spawn result. The
    # post-tool hook records that correlated start; registering the pre-call's
    # tool_call_id here would create an active record that no completion event
    # can safely close.
    scripts = ["prewrite_gate.py", "qa_delegation_gate.py"]
    if _tool_name(payload) == "Bash":
        scripts.append("mcp_bash_guard.py")
    for script in scripts:
        out = _run(script, payload)
        if out:
            sys.stdout.buffer.write(out)
            return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
