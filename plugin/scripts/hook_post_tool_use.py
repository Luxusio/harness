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
    from codex_hook_registration import restore_watcher_registration  # type: ignore
except Exception:  # pragma: no cover - registration recovery is best effort
    restore_watcher_registration = None

try:
    from _lib import (  # type: ignore
        extract_qa_verdict,
        find_harness_root,
        find_repo_root,
        is_harness_enabled_repo,
        list_review_receipts,
        list_subagent_receipts,
        record_subagent_receipt,
        read_current_goal,
        resolve_active_task_dir,
    )
except Exception:  # pragma: no cover - hook must fail open
    extract_qa_verdict = None
    find_harness_root = None
    find_repo_root = None
    is_harness_enabled_repo = None
    record_subagent_receipt = None
    read_current_goal = None
    resolve_active_task_dir = None
    list_subagent_receipts = None
    list_review_receipts = None

HOOK_TIMEOUT_SECONDS = 3.0
TOTAL_BUDGET_SECONDS = 2.4
REGISTRATION_BUDGET_SECONDS = 0.4
CHILD_TIMEOUT_SECONDS = 1.5
DEADLINE_MARGIN_SECONDS = 0.1
# Production receipt ownership belongs to the lifecycle watcher. Tests may
# switch this module-local value to ``sync`` to exercise the parser helpers
# without making outer PostToolUse latency depend on Git.
RECEIPT_EVENT_MODE = "watcher"


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


def _is_wait_agent_tool(tool_name: str) -> bool:
    name = (tool_name or "").lower().replace("-", "_")
    return name == "wait_agent" or name.endswith(".wait_agent") or name.endswith("__wait_agent")


def _is_list_agents_tool(tool_name: str) -> bool:
    name = (tool_name or "").lower().replace("-", "_")
    return name == "list_agents" or name.endswith(".list_agents") or name.endswith("__list_agents")


def _is_spawn_agent_tool(tool_name: str) -> bool:
    name = (tool_name or "").lower().replace("-", "_")
    return name == "spawn_agent" or name.endswith(".spawn_agent") or name.endswith("__spawn_agent")


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


def _response_text(value) -> str:
    parts: list[str] = []

    def visit(item) -> None:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict):
            for key, child in item.items():
                if key not in {"image", "image_url", "data"}:
                    visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return "\n".join(parts)[:10000]


def _response_agent_id(value) -> str:
    if isinstance(value, dict):
        for key in (
            "agent_id", "agentId", "agent_name", "agentName",
            "task_name", "taskName", "target", "id",
        ):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        for child in value.values():
            found = _response_agent_id(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _response_agent_id(child)
            if found:
                return found
    elif isinstance(value, str):
        match = re.search(r"(?i)(?:agent[_ ]?id|spawned)\s*[:=]?\s*([A-Za-z0-9_./:-]{6,})", value)
        if match:
            return match.group(1)
    return ""


def _record_codex_spawn_result(payload: bytes) -> None:
    if None in (find_harness_root, record_subagent_receipt, resolve_active_task_dir):
        return
    data = _json_payload(payload)
    if not _is_spawn_agent_tool(str(data.get("tool_name") or data.get("tool") or "")):
        return
    try:
        repo_root = _receipt_control_root(_payload_cwd(payload) or os.getcwd())
        if not repo_root:
            return
        task_dir = resolve_active_task_dir(repo_root)
        if not task_dir:
            return
        tool_input = data.get("tool_input") or data.get("input") or data.get("arguments") or {}
        response = data.get("tool_response") or data.get("tool_result") or data.get("toolResult") or {}
        agent_id = _response_agent_id(response)
        if not agent_id:
            return
        agent_type = ""
        if isinstance(tool_input, dict):
            agent_type = str(
                tool_input.get("agent_type") or tool_input.get("type") or tool_input.get("task_name") or ""
            )
        record_subagent_receipt(
            task_dir,
            {
                "source": "codex_spawn_post_hook",
                "status": "started",
                "agent_id": agent_id,
                "agent_type": agent_type,
                "summary": "Codex spawn result correlated with runtime agent id",
            },
        )
    except Exception:
        pass


def _completed_agents(value) -> list[tuple[str, str]]:
    """Extract structurally identified completions from wait/list responses."""
    completed: list[tuple[str, str]] = []
    if isinstance(value, dict):
        name = value.get("agent_name")
        status = value.get("agent_status")
        if isinstance(name, str) and isinstance(status, dict):
            final = status.get("completed")
            if isinstance(final, str):
                completed.append((name, final))
        status_map = value.get("status")
        if isinstance(status_map, dict):
            for agent_id, agent_status in status_map.items():
                if not isinstance(agent_id, str) or not isinstance(agent_status, dict):
                    continue
                final = agent_status.get("completed")
                if isinstance(final, str):
                    completed.append((agent_id, final))
        for child in value.values():
            completed.extend(_completed_agents(child))
    elif isinstance(value, list):
        for child in value:
            completed.extend(_completed_agents(child))
    return completed


def _record_one_completion(task_dir, target: str, final_text: str) -> None:
    verdict = extract_qa_verdict(final_text)
    if not verdict:
        return
    receipts = sorted(
        list_subagent_receipts(task_dir) + list_review_receipts(task_dir),
        key=lambda item: str(item.get("ts") or ""),
    )
    open_starts = []
    for item in receipts:
        if str(item.get("agent_id") or "") != target:
            continue
        status = str(item.get("status") or "").lower()
        if (
            status == "started"
            and str(item.get("lens") or "").startswith(("qa-", "review-"))
            and item.get("agent_type")
        ):
            open_starts.append(item)
        elif status in {"completed", "done"} and open_starts:
            open_starts.pop(0)
    # A name can be reused by the runtime. Without a unique runtime ID in the
    # wait response, more than one unmatched start is ambiguous and must not
    # authorize a completion receipt.
    if len(open_starts) != 1:
        return
    started = open_starts[0]
    if any(
        str(item.get("agent_id") or "") == target
        and str(item.get("status") or "").lower() in {"completed", "done"}
        and item.get("summary") == final_text[:1000]
        for item in receipts
    ):
        return
    record_subagent_receipt(
        task_dir,
        {
            "source": "codex_agent_completion_post_hook",
            "status": "completed",
            "agent_id": target,
            "agent_type": str(started.get("agent_type") or ""),
            "verdict": verdict,
            "summary": final_text,
        },
    )


def _record_codex_subagent_completion(payload: bytes) -> None:
    if None in (
        extract_qa_verdict, find_harness_root, list_subagent_receipts, list_review_receipts,
        record_subagent_receipt, resolve_active_task_dir,
    ):
        return
    data = _json_payload(payload)
    tool_name = str(data.get("tool_name") or data.get("tool") or "")
    if not (_is_wait_agent_tool(tool_name) or _is_list_agents_tool(tool_name)):
        return
    try:
        repo_root = _receipt_control_root(_payload_cwd(payload) or os.getcwd())
        if not repo_root:
            return
        task_dir = resolve_active_task_dir(repo_root)
        if not task_dir:
            return
        response = data.get("tool_response") or data.get("tool_result") or data.get("toolResult") or {}
        structured = _completed_agents(response)
        if structured:
            seen = set()
            for target, final_text in structured:
                identity = (target, final_text)
                if identity in seen:
                    continue
                seen.add(identity)
                _record_one_completion(task_dir, target, final_text)
            return
        if _is_list_agents_tool(tool_name):
            return
        tool_input = data.get("tool_input") or data.get("input") or data.get("arguments") or {}
        target = str(tool_input.get("target") or tool_input.get("agent_id") or "") if isinstance(tool_input, dict) else ""
        if target:
            _record_one_completion(task_dir, target, _response_text(response))
    except Exception:
        pass


def main() -> int:
    deadline = time.monotonic() + TOTAL_BUDGET_SECONDS
    payload = sys.stdin.buffer.read()
    if restore_watcher_registration is not None:
        restore_watcher_registration(payload, budget_seconds=REGISTRATION_BUDGET_SECONDS)
    tool_name = _tool_name(payload)
    if RECEIPT_EVENT_MODE == "sync" and (
        _is_spawn_agent_tool(tool_name)
        or _is_wait_agent_tool(tool_name)
        or _is_list_agents_tool(tool_name)
    ):
        _record_codex_spawn_result(payload)
        _record_codex_subagent_completion(payload)
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
