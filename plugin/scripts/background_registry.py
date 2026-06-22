#!/usr/bin/env python3
"""Best-effort registry for Claude background subagent lifecycle hooks.

The Stop hook uses this module to wait briefly for active background work
without asking Claude to run a separate wait command. All operations are
fail-open: registry corruption or IO errors must not trap the session.
"""
from __future__ import annotations

import json
import os
import hashlib
import tempfile
import threading
import time
from typing import Any

try:
    import fcntl  # type: ignore
except Exception:  # pragma: no cover - non-POSIX fallback
    fcntl = None

try:
    from _lib import (  # type: ignore
        TASK_DIR,
        append_conversation_entry,
        current_session_id,
        now_iso,
        record_subagent_receipt,
        resolve_active_task_dir,
    )
except Exception:  # pragma: no cover - imported only inside harness scripts
    TASK_DIR = "doc/harness/tasks"

    def current_session_id(default: str = "default") -> str:
        return default

    def now_iso() -> str:
        return "unknown"

    def resolve_active_task_dir(repo_root: str | None = None, session_id: str | None = None) -> str:
        return ""

    def record_subagent_receipt(task_dir: str, receipt: dict[str, Any]) -> dict[str, Any]:
        return {}

    def append_conversation_entry(task_dir: str, **kwargs: Any) -> bool:
        return False


RUNTIME_DIR = "doc/harness/runtime"
REGISTRY_NAME = "background.json"
DEFAULT_STALE_SECS = 30 * 60
DEFAULT_WAIT_SECS = 6.0
POLL_SECS = 0.25
MAX_RECORDS = 200
_THREAD_LOCK = threading.Lock()


def registry_path(repo_root: str) -> str:
    return os.path.join(repo_root, RUNTIME_DIR, REGISTRY_NAME)


def _lock_path(repo_root: str) -> str:
    return os.path.join(repo_root, RUNTIME_DIR, REGISTRY_NAME + ".lock")


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


def _with_registry_lock(repo_root: str, mutator, *, write: bool = True):
    """Serialize registry read-modify-write across concurrent hook processes."""
    os.makedirs(os.path.join(repo_root, RUNTIME_DIR), exist_ok=True)
    with _THREAD_LOCK:
        with open(_lock_path(repo_root), "a+", encoding="utf-8") as lock:
            if fcntl is not None:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                data = _read(repo_root)
                result = mutator(data)
                data["records"] = data.get("records", [])[-MAX_RECORDS:]
                dirty = bool(data.pop("_dirty", False))
                if write or dirty:
                    _write(repo_root, data)
                return result
            finally:
                if fcntl is not None:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


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


def _diagnostic_record(
    payload: dict[str, Any],
    *,
    status: str,
    reason: str,
    task_dir: str = "",
) -> dict[str, Any]:
    ts = _now()
    try:
        keys = sorted(str(k) for k in payload.keys())[:40]
    except Exception:
        keys = []
    record = {
        "id": f"diag:{status}:{int(ts * 1000)}",
        "kind": "subagent",
        "status": status,
        "reason": reason,
        "session_id": _session_id(payload),
        "task_id": _task_id_from_dir(task_dir),
        "task_dir": task_dir,
        "agent_id": _agent_id(payload),
        "agent_type": _agent_type(payload),
        "event": _event_name(payload),
        "payload_keys": keys,
        "updated_at": now_iso(),
        "updated_ts": ts,
    }
    transcript = _payload_value(payload, "agent_transcript_path", "transcript_path")
    if transcript:
        record["transcript_path"] = transcript
    return record


def _generated_agent_id(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return "hook-spawn-" + hashlib.sha256(f"{now_iso()}|{raw}".encode("utf-8")).hexdigest()[:16]


def register_subagent_start(
    repo_root: str,
    payload: dict[str, Any],
    *,
    task_dir: str | None = None,
    allow_generated_id: bool = False,
) -> dict[str, Any]:
    """Record an active subagent. Returns the record or {} on no active task."""
    task_dir = task_dir or resolve_active_task_dir(repo_root)
    if not task_dir:
        return {}
    sid = _session_id(payload)
    aid = _agent_id(payload)
    if not aid:
        if allow_generated_id:
            aid = _generated_agent_id(payload)
        else:
            # Official Claude Code SubagentStart input includes agent_id. Without it,
            # any generated fallback id would be impossible for SubagentStop to match
            # reliably and would create a durable false-active record.
            record = _diagnostic_record(
                payload,
                status="ignored_start_missing_agent_id",
                reason="SubagentStart payload did not include official agent_id field.",
                task_dir=task_dir,
            )

            def add_diag(data: dict[str, Any]):
                data["records"].append(record)
                return record

            return _with_registry_lock(repo_root, add_diag)
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

    def upsert(data: dict[str, Any]):
        records = [
            r for r in data["records"]
            if not (r.get("kind") == "subagent" and r.get("id") == aid and r.get("session_id") == sid)
        ]
        records.append(record)
        data["records"] = records
        return record

    result = _with_registry_lock(repo_root, upsert)
    try:
        receipt = record_subagent_receipt(
            task_dir,
            {
                "source": "subagent_start_hook",
                "status": "started",
                "agent_id": aid,
                "agent_type": _agent_type(payload),
                "summary": "subagent start hook observed",
                "transcript_path": _payload_value(payload, "agent_transcript_path", "transcript_path"),
            },
        )
        if receipt.get("receipt_id"):
            result["subagent_receipt_id"] = receipt["receipt_id"]
    except Exception:
        pass
    return result


def mark_subagent_stop(repo_root: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Mark a subagent done by official agent_id/session_id fields."""
    sid = _session_id(payload)
    aid = _agent_id(payload)
    def mark(data: dict[str, Any]):
        candidates: list[dict[str, Any]] = []
        for record in data["records"]:
            if record.get("kind") != "subagent" or record.get("status") != "active":
                continue
            if aid and record.get("id") == aid and record.get("session_id") == sid:
                candidates.append(record)
        if not candidates:
            diag = _diagnostic_record(
                payload,
                status="unmatched_stop",
                reason="SubagentStop did not match any active record by official agent_id/session_id fields.",
            )
            data["records"].append(diag)
            return diag
        target = sorted(candidates, key=lambda r: float(r.get("updated_ts") or 0))[-1]
        target["status"] = "done"
        target["updated_at"] = now_iso()
        target["updated_ts"] = _now()
        agent_type = _agent_type(payload)
        if agent_type:
            target["agent_type"] = agent_type
        transcript = _payload_value(payload, "agent_transcript_path", "transcript_path")
        if transcript:
            target["transcript_path"] = transcript
        last = _payload_value(payload, "last_assistant_message")
        if last:
            target["last_assistant_message"] = last[:500]
        target["stop_hook_active"] = bool(payload.get("stop_hook_active"))
        return target

    result = _with_registry_lock(repo_root, mark)
    try:
        if result.get("status") == "done" and result.get("last_assistant_message"):
            append_conversation_entry(
                result.get("task_dir") or "",
                role="subagent",
                text=result.get("last_assistant_message") or "",
                source="subagent_stop_hook",
                event_id=result.get("id") or "",
                agent_type=result.get("agent_type") or "",
            )
    except Exception:
        pass
    return result


def prune(repo_root: str, *, keep: int = MAX_RECORDS, stale_secs: float = DEFAULT_STALE_SECS) -> None:
    """Mark stale active records and keep the registry bounded."""
    def do_prune(data: dict[str, Any]):
        now = _now()
        records = data["records"]
        for record in records:
            if record.get("status") != "active":
                continue
            try:
                age = now - float(record.get("updated_ts") or 0)
            except Exception:
                age = stale_secs + 1
            if age > stale_secs:
                record["status"] = "stale"
                record["updated_at"] = now_iso()
                record["updated_ts"] = now
        data["records"] = records[-keep:]
        return None

    _with_registry_lock(repo_root, do_prune)


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
    def collect(data: dict[str, Any]):
        now = _now()
        active: list[dict[str, Any]] = []
        for record in data["records"]:
            if record.get("status") != "active":
                continue
            try:
                age = now - float(record.get("updated_ts") or 0)
            except Exception:
                age = stale_secs + 1
            if age > stale_secs:
                record["status"] = "stale"
                record["updated_at"] = now_iso()
                record["updated_ts"] = now
                data["_dirty"] = True
                continue
            if task_id and record.get("task_id") != task_id:
                continue
            if session_id and record.get("session_id") != session_id:
                continue
            active.append(dict(record))
        return active

    return _with_registry_lock(repo_root, collect, write=False)


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
