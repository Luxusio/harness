#!/usr/bin/env python3
"""Codex PostToolUse wrapper: one hook file per event type."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time

_IMPORT_STARTED_AT = time.monotonic()
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS_DIR)

try:
    from _lib import (  # type: ignore
        find_harness_root,
        find_repo_root,
        is_codex_task_binding_tool,
        is_harness_enabled_repo,
        resolve_active_task_dir,
    )
except Exception:  # pragma: no cover - hook must fail open
    find_harness_root = None
    find_repo_root = None
    is_codex_task_binding_tool = None
    is_harness_enabled_repo = None
    resolve_active_task_dir = None

_IMPORT_SECONDS = time.monotonic() - _IMPORT_STARTED_AT
register_task_result = None


def _register_task_result(payload: bytes, *, deadline: float) -> None:
    """Lazy import avoids loading protected writer authority for unrelated hooks."""
    global register_task_result
    status = {}
    registered = False
    try:
        from codex_hook_registration import (
            REGISTRATION_BUDGET_SECONDS,
        )
        if register_task_result is None:
            from codex_hook_registration import register_task_result as callback  # type: ignore
            register_task_result = callback
        registered = register_task_result(
            payload, budget_seconds=min(REGISTRATION_BUDGET_SECONDS,
                                        max(0.0, deadline - time.monotonic() - DEADLINE_MARGIN_SECONDS)),
            status_out=status,
        )
    except Exception as exc:
        registered = False
        status.update(status="failed", stage="task binding",
                      reason=f"task binding unavailable ({type(exc).__name__})")
    reason = " ".join(str(status.get("reason") or "task binding failed").split())[:200]
    if not registered and status.get("status") == "failed":
        sys.stdout.write(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": f"[harness] Task focus binding failed: {reason}. Resume the intended task before source edits.",
        }}))
        sys.stdout.flush()

HOOK_TIMEOUT_SECONDS = 10.0
TOTAL_BUDGET_SECONDS = 9.4
CHILD_TIMEOUT_SECONDS = 1.5
DEADLINE_MARGIN_SECONDS = 0.4  # Leave time to publish binding guidance.
def _task_control_root(cwd: str) -> str:
    root = find_harness_root(cwd) if find_harness_root is not None else ""
    if root:
        return root
    if find_repo_root is not None and resolve_active_task_dir is not None:
        candidate = find_repo_root(cwd)
        if resolve_active_task_dir(candidate):
            return candidate
    return ""


def _payload_cwd(payload: bytes) -> str | None:
    data = _json_payload(payload)
    cwd = data.get("cwd")
    return cwd if isinstance(cwd, str) and os.path.isdir(cwd) else None


def _tool_name(payload: bytes) -> str:
    data = _json_payload(payload)
    return str(data.get("tool_name") or data.get("tool") or "")


def _json_payload(payload: bytes) -> dict:
    try:
        data = json.loads(payload.decode("utf-8") or "{}")
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _is_create_goal_tool(tool_name: str) -> bool:
    name = (tool_name or "").lower().replace("-", "_")
    return name == "create_goal" or name.endswith(".create_goal") or name.endswith("__create_goal")


def _is_task_binding_tool(tool_name: str, arguments=None) -> bool:
    return bool(
        is_codex_task_binding_tool is not None
        and is_codex_task_binding_tool(tool_name, arguments)
    )


def _goal_routing_hint(payload: bytes) -> str:
    """Return the native-Goal → harness synchronization reminder.

    ``create_goal`` is an agent-only Codex tool, so this hook cannot invoke
    ``get_goal`` itself. PostToolUse is the first reliable point at which the
    goal exists and the agent can be told to synchronize it through harness.
    """
    if find_harness_root is None or is_harness_enabled_repo is None:
        return ""
    data = _json_payload(payload)
    if not _is_create_goal_tool(str(data.get("tool_name") or data.get("tool") or "")):
        return ""
    try:
        repo_root = _task_control_root(_payload_cwd(payload) or os.getcwd())
        if not repo_root:
            return ""
        if not is_harness_enabled_repo(repo_root):
            return ""
        response = data.get("tool_response", data.get("tool_result", data.get("toolResult")))
        if isinstance(response, dict):
            status = str(response.get("status") or "").strip().lower()
            if response.get("success") is False or response.get("error") or status in {"error", "failed"}:
                return ""
        elif isinstance(response, str) and re.match(r"(?is)^\s*(?:error|failed)\b", response):
            return ""

        tool_input = data.get("tool_input") or data.get("input") or data.get("arguments") or {}
        if isinstance(tool_input, str):
            tool_input = json.loads(tool_input)
        objective = ""
        if isinstance(tool_input, dict):
            objective = " ".join(str(tool_input.get("objective") or "").split())
        from _lib import native_wbs_is_synced
        if native_wbs_is_synced(repo_root, objective):
            return ""
    except Exception:
        return ""
    return (
        "[harness-goal] Native Goal was created. Invoke $harness:run; before implementation call "
        "get_goal, then wbs(action=open, objective=...) to open or resume the same outcome tree. "
        "Use wbs context/plan/check/dispatch/verify/close; do not create a separate Goal/task planning ledger. "
        "Do not treat create_goal alone as harness activation."
    )


def main() -> int:
    deadline = time.monotonic() + max(0.0, TOTAL_BUDGET_SECONDS - _IMPORT_SECONDS)
    payload = sys.stdin.buffer.read()
    data = _json_payload(payload)
    tool_name = _tool_name(payload)
    if _is_task_binding_tool(tool_name, data.get("tool_input") or data.get("input") or data.get("arguments")):
        _register_task_result(payload, deadline=deadline)
        return 0
    goal_hint = ""
    if _is_create_goal_tool(tool_name):
        remaining = deadline - time.monotonic() - DEADLINE_MARGIN_SECONDS
        if remaining > 0:
            try:
                proc = subprocess.run(
                    [
                        sys.executable,
                        os.path.join(SCRIPTS_DIR, "tool_routing.py"),
                        "--goal-hint-worker",
                    ],
                    input=payload,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    timeout=min(CHILD_TIMEOUT_SECONDS, remaining),
                    cwd=_payload_cwd(payload),
                )
                goal_hint = (proc.stdout or b"").decode(
                    "utf-8", errors="replace"
                ).strip()
            except Exception:
                goal_hint = ""
    if goal_hint:
        sys.stdout.write(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": goal_hint,
            }
        }))
        return 0
    if tool_name != "Bash":
        return 0
    remaining = deadline - time.monotonic() - DEADLINE_MARGIN_SECONDS
    if remaining <= 0:
        return 0
    try:
        proc = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS_DIR, "tool_routing.py")],
            input=payload,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=min(CHILD_TIMEOUT_SECONDS, remaining),
            cwd=_payload_cwd(payload),
        )
        hint = (proc.stdout or b"").decode("utf-8", errors="replace").strip()
        if hint:
            sys.stdout.write(json.dumps({
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "additionalContext": hint,
                }
            }))
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
