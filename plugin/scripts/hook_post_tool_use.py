#!/usr/bin/env python3
"""Codex PostToolUse wrapper: one hook file per event type."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS_DIR)

try:
    from _lib import (  # type: ignore
        find_harness_root,
        find_repo_root,
        is_harness_enabled_repo,
        read_current_goal,
        resolve_active_task_dir,
    )
except Exception:  # pragma: no cover - hook must fail open
    find_harness_root = None
    find_repo_root = None
    is_harness_enabled_repo = None
    read_current_goal = None
    resolve_active_task_dir = None

HOOK_TIMEOUT_SECONDS = 3.0
TOTAL_BUDGET_SECONDS = 2.4
CHILD_TIMEOUT_SECONDS = 1.5
DEADLINE_MARGIN_SECONDS = 0.1
def _receipt_control_root(cwd: str) -> str:
    root = find_harness_root(cwd) if find_harness_root is not None else ""
    if root:
        return root
    if find_repo_root is not None and resolve_active_task_dir is not None:
        candidate = find_repo_root(cwd)
        if resolve_active_task_dir(candidate):
            return candidate
    return ""


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


def _is_create_goal_tool(tool_name: str) -> bool:
    name = (tool_name or "").lower().replace("-", "_")
    return name == "create_goal" or name.endswith(".create_goal") or name.endswith("__create_goal")


def _goal_routing_hint(payload: bytes) -> str:
    """Return the native-Goal → harness synchronization reminder.

    ``create_goal`` is an agent-only Codex tool, so this hook cannot invoke
    ``get_goal`` itself. PostToolUse is the first reliable point at which the
    goal exists and the agent can be told to synchronize it through harness.
    """
    if find_harness_root is None or is_harness_enabled_repo is None or read_current_goal is None:
        return ""
    data = _json_payload(payload)
    if not _is_create_goal_tool(str(data.get("tool_name") or data.get("tool") or "")):
        return ""
    try:
        repo_root = _receipt_control_root(_payload_cwd(payload) or os.getcwd())
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
        objective = ""
        if isinstance(tool_input, dict):
            objective = " ".join(str(tool_input.get("objective") or "").split())
        current = read_current_goal(repo_root)
        current_objective = " ".join(str(current.get("objective") or "").split())
        if (
            objective
            and current.get("status") == "active"
            and objective == current_objective
        ):
            return ""
    except Exception:
        return ""
    return (
        "[harness-goal] Native Goal was created. Invoke $harness:run; before implementation call "
        "get_goal, then harness goal_start with that objective; call goal_context; "
        "if no child task exists, call task_start then goal_add_task. Continue with "
        "goal_next_task. Do not treat create_goal alone as harness activation."
    )


def main() -> int:
    deadline = time.monotonic() + TOTAL_BUDGET_SECONDS
    payload = sys.stdin.buffer.read()
    tool_name = _tool_name(payload)
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
