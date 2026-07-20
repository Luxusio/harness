#!/usr/bin/env python3
"""Codex PostToolUse wrapper: one hook file per event type."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS_DIR)

try:
    from _lib import (  # type: ignore
        extract_qa_verdict,
        find_repo_root,
        list_review_receipts,
        list_subagent_receipts,
        record_subagent_receipt,
        review_diff_fingerprint,
        resolve_active_task_dir,
    )
except Exception:  # pragma: no cover - hook must fail open
    extract_qa_verdict = None
    find_repo_root = None
    record_subagent_receipt = None
    resolve_active_task_dir = None
    list_subagent_receipts = None
    list_review_receipts = None
    review_diff_fingerprint = None


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
        for key in ("agent_id", "agentId", "task_name", "taskName", "target", "id"):
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
    if None in (find_repo_root, record_subagent_receipt, resolve_active_task_dir):
        return
    data = _json_payload(payload)
    if not _is_spawn_agent_tool(str(data.get("tool_name") or data.get("tool") or "")):
        return
    try:
        repo_root = find_repo_root(_payload_cwd(payload) or os.getcwd())
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
    """Extract only structurally identified completed agents from list_agents."""
    completed: list[tuple[str, str]] = []
    if isinstance(value, dict):
        name = value.get("agent_name")
        status = value.get("agent_status")
        if isinstance(name, str) and isinstance(status, dict):
            final = status.get("completed")
            if isinstance(final, str):
                completed.append((name, final))
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
    receipts = list_subagent_receipts(task_dir) + list_review_receipts(task_dir)
    started = next((
        item for item in reversed(receipts)
        if str(item.get("agent_id") or "") == target
        and str(item.get("status") or "").lower() == "started"
        and str(item.get("lens") or "").startswith(("qa-", "review-"))
        and item.get("agent_type")
    ), None)
    if not started:
        return
    if any(
        str(item.get("agent_id") or "") == target
        and str(item.get("status") or "").lower() in {"completed", "done"}
        and item.get("summary") == final_text[:1000]
        for item in receipts
    ):
        return
    started_fingerprint = str(started.get("diff_fingerprint") or "")
    if (
        str(started.get("lens") or "").startswith("review-")
        and review_diff_fingerprint is not None
        and started_fingerprint != review_diff_fingerprint(task_dir)
    ):
        verdict = "PENDING"
        final_text = (final_text + "\nReview invalidated: source changed while reviewer was running.").strip()
    record_subagent_receipt(
        task_dir,
        {
            "source": "codex_agent_completion_post_hook",
            "status": "completed",
            "agent_id": target,
            "agent_type": str(started.get("agent_type") or ""),
            "verdict": verdict,
            "summary": final_text,
            "head_sha": str(started.get("head_sha") or ""),
            "base_sha": str(started.get("base_sha") or ""),
            "diff_fingerprint": started_fingerprint,
        },
    )


def _record_codex_subagent_completion(payload: bytes) -> None:
    if None in (
        extract_qa_verdict, find_repo_root, list_subagent_receipts, list_review_receipts,
        record_subagent_receipt, resolve_active_task_dir,
    ):
        return
    data = _json_payload(payload)
    tool_name = str(data.get("tool_name") or data.get("tool") or "")
    if not (_is_wait_agent_tool(tool_name) or _is_list_agents_tool(tool_name)):
        return
    try:
        repo_root = find_repo_root(_payload_cwd(payload) or os.getcwd())
        task_dir = resolve_active_task_dir(repo_root)
        if not task_dir:
            return
        response = data.get("tool_response") or data.get("tool_result") or data.get("toolResult") or {}
        if _is_list_agents_tool(tool_name):
            for target, final_text in _completed_agents(response):
                _record_one_completion(task_dir, target, final_text)
            return
        tool_input = data.get("tool_input") or data.get("input") or data.get("arguments") or {}
        target = str(tool_input.get("target") or tool_input.get("agent_id") or "") if isinstance(tool_input, dict) else ""
        if target:
            _record_one_completion(task_dir, target, _response_text(response))
    except Exception:
        pass


def main() -> int:
    payload = sys.stdin.buffer.read()
    tool_name = _tool_name(payload)
    _record_codex_spawn_result(payload)
    _record_codex_subagent_completion(payload)
    if tool_name != "Bash":
        return 0
    try:
        proc = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS_DIR, "tool_routing.py")],
            input=payload,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=3,
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
