#!/usr/bin/env python3
"""Best-effort registry for Claude background subagent lifecycle hooks.

The Stop hook uses this module to wait briefly for active background work
without asking Claude to run a separate wait command. All operations are
fail-open: registry corruption or IO errors must not trap the session.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from typing import Any

try:
    from _lib import TASK_DIR, current_session_id, now_iso, resolve_active_task_dir  # type: ignore
except Exception:  # pragma: no cover - imported only inside harness scripts
    TASK_DIR = "doc/harness/tasks"

    def current_session_id(default: str = "default") -> str:
        return default

    def now_iso() -> str:
        return "unknown"

    def resolve_active_task_dir(repo_root: str | None = None, session_id: str | None = None) -> str:
        return ""


RUNTIME_DIR = "doc/harness/runtime"
REGISTRY_NAME = "background.json"
DEFAULT_STALE_SECS = 30 * 60
DEFAULT_WAIT_SECS = 6.0
POLL_SECS = 0.25


def registry_path(repo_root: str) -> str:
    return os.path.join(repo_root, RUNTIME_DIR, REGISTRY_NAME)


def _now() -> float:
    return time.time()


def _read(repo_root: str) -> dict[str, Any]:
    path = registry_path(repo_root)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            records = data.get("records")
            if isinstance(records, list):
                return {"version": 1, "records": [r for r in records if isinstance(r, dict)]}
    except Exception:
        pass
    return {"version": 1, "records": []}


def _write(repo_root: str, data: dict[str, Any]) -> None:
    path = registry_path(repo_root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".background.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _payload_value(payload: dict[str, Any], *keys: str) -> str:
    for key in keys:
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _agent_id(payload: dict[str, Any]) -> str:
    direct = _payload_value(payload, "agent_id", "agentId", "subagent_id", "subagentId")
    if direct:
        return direct
    nested = payload.get("subagent") or payload.get("agent")
    if isinstance(nested, dict):
        nested_id = _payload_value(nested, "id", "agent_id", "agentId")
        if nested_id:
            return nested_id
    return ""


def _agent_type(payload: dict[str, Any]) -> str:
    direct = _payload_value(payload, "agent_type", "agentType", "subagent_type", "subagentType")
    if direct:
        return direct
    nested = payload.get("subagent") or payload.get("agent")
    if isinstance(nested, dict):
        return _payload_value(nested, "type", "agent_type", "agentType")
    return ""


def _session_id(payload: dict[str, Any]) -> str:
    return _payload_value(payload, "session_id", "sessionId") or current_session_id()


def _task_id_from_dir(task_dir: str) -> str:
    return os.path.basename(os.path.normpath(task_dir)) if task_dir else ""


def _event_name(payload: dict[str, Any]) -> str:
    return _payload_value(payload, "hook_event_name", "hookEventName", "event", "event_name")


def register_subagent_start(repo_root: str, payload: dict[str, Any], *, task_dir: str | None = None) -> dict[str, Any]:
    """Record an active subagent. Returns the record or {} on no active task."""
    task_dir = task_dir or resolve_active_task_dir(repo_root)
    if not task_dir:
        return {}
    data = _read(repo_root)
    sid = _session_id(payload)
    aid = _agent_id(payload) or f"{sid}:{int(_now() * 1000)}"
    ts = _now()
    record = {
        "id": aid,
        "kind": "subagent",
        "status": "active",
        "session_id": sid,
        "task_id": _task_id_from_dir(task_dir),
        "task_dir": task_dir,
        "agent_type": _agent_type(payload),
        "started_at": now_iso(),
        "updated_at": now_iso(),
        "updated_ts": ts,
    }
    records = [
        r for r in data["records"]
        if not (r.get("kind") == "subagent" and r.get("id") == aid and r.get("session_id") == sid)
    ]
    records.append(record)
    data["records"] = records[-200:]
    _write(repo_root, data)
    return record


def mark_subagent_stop(repo_root: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Mark a subagent done. Matches by agent id, falling back to latest active record."""
    data = _read(repo_root)
    sid = _session_id(payload)
    aid = _agent_id(payload)
    candidates: list[dict[str, Any]] = []
    for record in data["records"]:
        if record.get("kind") != "subagent" or record.get("status") != "active":
            continue
        if aid and record.get("id") == aid:
            candidates.append(record)
        elif not aid and record.get("session_id") == sid:
            candidates.append(record)
    if not candidates:
        return {}
    target = sorted(candidates, key=lambda r: float(r.get("updated_ts") or 0))[-1]
    target["status"] = "done"
    target["updated_at"] = now_iso()
    target["updated_ts"] = _now()
    transcript = _payload_value(payload, "agent_transcript_path", "transcript_path")
    if transcript:
        target["transcript_path"] = transcript
    last = _payload_value(payload, "last_assistant_message")
    if last:
        target["last_assistant_message"] = last[:500]
    _write(repo_root, data)
    return target


def handle_subagent_hook(repo_root: str, payload: dict[str, Any], *, forced_event: str = "") -> dict[str, Any]:
    event = (forced_event or _event_name(payload)).lower()
    if event in ("start", "subagentstart", "subagent_start"):
        return register_subagent_start(repo_root, payload)
    if event in ("stop", "subagentstop", "subagent_stop"):
        return mark_subagent_stop(repo_root, payload)
    return {}


def active_records(
    repo_root: str,
    *,
    task_id: str = "",
    session_id: str = "",
    stale_secs: float = DEFAULT_STALE_SECS,
) -> list[dict[str, Any]]:
    """Return active, non-stale records. Stale records are marked and ignored."""
    data = _read(repo_root)
    now = _now()
    changed = False
    active: list[dict[str, Any]] = []
    for record in data["records"]:
        if record.get("status") != "active":
            continue
        age = now - float(record.get("updated_ts") or 0)
        if age > stale_secs:
            record["status"] = "stale"
            record["updated_at"] = now_iso()
            record["updated_ts"] = now
            changed = True
            continue
        if task_id and record.get("task_id") != task_id:
            continue
        if session_id and record.get("session_id") not in (session_id, "default"):
            continue
        active.append(dict(record))
    if changed:
        try:
            _write(repo_root, data)
        except Exception:
            pass
    return active


def wait_for_clear(
    repo_root: str,
    *,
    task_id: str,
    session_id: str = "",
    timeout_secs: float = DEFAULT_WAIT_SECS,
    stale_secs: float = DEFAULT_STALE_SECS,
    poll_secs: float = POLL_SECS,
) -> dict[str, Any]:
    """Poll until no active records remain, or timeout expires."""
    deadline = _now() + max(0.0, timeout_secs)
    last = active_records(repo_root, task_id=task_id, session_id=session_id, stale_secs=stale_secs)
    while last and _now() < deadline:
        time.sleep(max(0.01, poll_secs))
        last = active_records(repo_root, task_id=task_id, session_id=session_id, stale_secs=stale_secs)
    return {
        "cleared": not last,
        "active": last,
        "waited_secs": max(0.0, timeout_secs - max(0.0, deadline - _now())),
    }
