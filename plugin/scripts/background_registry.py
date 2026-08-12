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
import re
import tempfile
import threading
import time
from datetime import datetime, timezone
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
        receipt_stream_transaction,
        extract_qa_verdict,
        resolve_active_task_dir,
        resolve_session_task_binding,
        read_task_control,
        task_run_started_at,
    )
except Exception:  # pragma: no cover - imported only inside harness scripts
    TASK_DIR = "doc/harness/tasks"

    def current_session_id(default: str = "default") -> str:
        return default

    def now_iso() -> str:
        return "unknown"

    def resolve_active_task_dir(repo_root: str | None = None, session_id: str | None = None) -> str:
        return ""

    def resolve_session_task_binding(repo_root: str, session_id: str) -> dict[str, str]:
        return {}

    def read_task_control(task_dir: str) -> dict[str, Any]:
        return {}

    def task_run_started_at(control: dict[str, Any]) -> str:
        return ""

    def record_subagent_receipt(task_dir: str, receipt: dict[str, Any]) -> dict[str, Any]:
        return {}

    class _NullTransaction:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def receipt_stream_transaction(task_dir: str):
        return _NullTransaction()

    def extract_qa_verdict(value: str) -> str:
        return ""

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


def _official_stop_identity(payload: dict[str, Any]) -> tuple[str, str]:
    sid = payload.get("session_id")
    aid = payload.get("agent_id")
    if not isinstance(sid, str) or not isinstance(aid, str):
        return "", ""
    sid = sid.strip()
    aid = aid.strip()
    if (
        not sid or not aid or sid == "default"
        or any(ch not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-" for ch in sid)
        or any(ch not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.:-" for ch in aid)
    ):
        return "", ""
    return sid, aid


def _trusted_stop_provenance(
    payload: dict[str, Any], sid: str, aid: str, run_id: str,
) -> tuple[str, str]:
    """Return transcript path/type only when runtime start and final text prove the stop."""
    raw_path = payload.get("agent_transcript_path")
    final_message = payload.get("last_assistant_message")
    if not isinstance(raw_path, str) or not isinstance(final_message, str) or not final_message:
        return "", ""
    path = os.path.realpath(raw_path)
    claude_root = os.path.realpath(
        os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    )
    projects_root = os.path.join(claude_root, "projects")
    try:
        if os.path.commonpath((projects_root, path)) != projects_root:
            return "", ""
        rel = os.path.relpath(path, projects_root).split(os.sep)
        if len(rel) < 4 or rel[-3:] != [sid, "subagents", f"agent-{aid}.jsonl"]:
            return "", ""
        before = os.lstat(path)
        if (
            os.path.islink(path) or not os.path.isfile(path)
            or before.st_uid != os.getuid() or before.st_nlink != 1
            or before.st_mode & 0o022 or before.st_size > 64 * 1024 * 1024
        ):
            return "", ""
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags)
        with os.fdopen(fd, encoding="utf-8") as handle:
            lines = handle.readlines()
        after = os.lstat(path)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns,
        ):
            return "", ""
        run_time = datetime.fromisoformat(task_run_started_at({"run_id": run_id}).replace("Z", "+00:00"))
        items = [json.loads(line) for line in lines if line.strip()]
        transcript_agent_type = ""
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("agentId") not in {None, aid} or item.get("sessionId") not in {None, sid}:
                return "", ""
            attachment = item.get("attachment")
            if not isinstance(attachment, dict) or attachment.get("hookEvent") != "SubagentStart":
                continue
            if attachment.get("hookName") != "SubagentStart" or item.get("agentId") != aid:
                return "", ""
            event_time = datetime.fromisoformat(
                str(item.get("timestamp") or "").replace("Z", "+00:00")
            )
            if not event_time.tzinfo or event_time.astimezone(timezone.utc) < run_time:
                return "", ""
            content = attachment.get("content")
            if not isinstance(content, list) or len(content) != 1 or not isinstance(content[0], str):
                return "", ""
            match = re.fullmatch(
                rf"Agent ([A-Za-z0-9_.:-]+) started \({re.escape(aid)}\)", content[0],
            )
            if not match or transcript_agent_type:
                return "", ""
            transcript_agent_type = match.group(1)
        if not transcript_agent_type:
            return "", ""
        for item in reversed(items):
            message = item.get("message") if isinstance(item, dict) else None
            if not isinstance(message, dict) or message.get("role") != "assistant":
                continue
            text = "".join(
                str(part.get("text") or "")
                for part in message.get("content", [])
                if isinstance(part, dict) and part.get("type") == "text"
            )
            if text == final_message:
                return path, transcript_agent_type
            return "", ""
    except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
        return "", ""
    return "", ""


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
    control = read_task_control(task_dir)
    run_id = str(control.get("run_id") or "")
    if not run_id:
        return {}
    record = {
        "id": aid,
        "kind": "subagent",
        "status": "active",
        "session_id": sid,
        "task_id": _task_id_from_dir(task_dir),
        "task_dir": task_dir,
        "run_id": run_id,
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
                "event": "started",
                "agent_id": aid,
                "agent_type": _agent_type(payload),
                "summary": "subagent start hook observed",
                "transcript_path": _payload_value(payload, "agent_transcript_path", "transcript_path"),
                "runtime_session_id": sid,
                "runtime_thread_id": aid,
                "task_run_id": run_id,
            },
        )
        if receipt.get("receipt_id"):
            result["subagent_receipt_id"] = receipt["receipt_id"]
    except Exception:
        pass
    return result


def mark_subagent_stop(repo_root: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Mark a subagent done by official agent_id/session_id fields."""
    sid, aid = _official_stop_identity(payload)
    stop_message = _payload_value(payload, "last_assistant_message")
    stop_verdict = extract_qa_verdict(stop_message)
    fallback_binding = resolve_session_task_binding(repo_root, sid) if aid and stop_verdict else {}
    fallback_task_dir = str(fallback_binding.get("task_dir") or "")
    fallback_run_id = str(fallback_binding.get("run_id") or "")
    trusted_transcript, transcript_agent_type = (
        _trusted_stop_provenance(payload, sid, aid, fallback_run_id)
        if fallback_task_dir else ("", "")
    )
    if not trusted_transcript:
        fallback_task_dir = ""
        fallback_run_id = ""
    def mark(data: dict[str, Any]):
        candidates: list[dict[str, Any]] = []
        for record in data["records"]:
            if (
                record.get("kind") == "subagent"
                and record.get("status") == "done"
                and aid and record.get("id") == aid
                and record.get("session_id") == sid
                and record.get("run_id") == fallback_run_id
            ):
                diag = _diagnostic_record(
                    payload,
                    status="duplicate_stop",
                    reason="SubagentStop identity was already completed.",
                )
                data["records"].append(diag)
                return diag
            if record.get("kind") != "subagent" or record.get("status") != "active":
                continue
            if aid and record.get("id") == aid and record.get("session_id") == sid:
                candidates.append(record)
        if not candidates:
            if fallback_task_dir:
                ts = _now()
                transcript = _payload_value(
                    payload, "agent_transcript_path", "transcript_path"
                )
                last = stop_message
                inferred = {
                    "id": aid,
                    "kind": "subagent",
                    "status": "done",
                    "reason": (
                        "SubagentStop supplied the authoritative lifecycle event; "
                        "this runtime emitted no matching SubagentStart."
                    ),
                    "session_id": sid,
                    "task_id": _task_id_from_dir(fallback_task_dir),
                    "task_dir": fallback_task_dir,
                    "run_id": fallback_run_id,
                    "agent_id": aid,
                    "agent_type": transcript_agent_type,
                    "event": _event_name(payload) or "SubagentStop",
                    "started_from_stop": True,
                    "updated_at": now_iso(),
                    "updated_ts": ts,
                    "stop_hook_active": bool(payload.get("stop_hook_active")),
                }
                inferred["transcript_path"] = trusted_transcript
                if last:
                    inferred["last_assistant_message"] = last[:500]
                data["records"].append(inferred)
                return inferred
            diag = _diagnostic_record(
                payload,
                status="unmatched_stop",
                reason="SubagentStop did not match any active record by official agent_id/session_id fields.",
            )
            data["records"].append(diag)
            return diag
        target = sorted(candidates, key=lambda r: float(r.get("updated_ts") or 0))[-1]
        if stop_verdict and (
            not fallback_binding
            or fallback_binding.get("task_dir") != target.get("task_dir")
            or fallback_binding.get("run_id") != target.get("run_id")
            or not trusted_transcript
            or transcript_agent_type != target.get("agent_type")
        ):
            diag = _diagnostic_record(
                payload,
                status="untrusted_stop",
                reason=(
                    "SubagentStop verdict was not bound to the exact active session, "
                    "run, transcript start attachment, and final assistant text."
                ),
            )
            data["records"].append(diag)
            return diag
        target["status"] = "done"
        target["updated_at"] = now_iso()
        target["updated_ts"] = _now()
        if transcript_agent_type:
            target["agent_type"] = transcript_agent_type
        transcript = trusted_transcript or _payload_value(
            payload, "agent_transcript_path", "transcript_path"
        )
        if transcript:
            target["transcript_path"] = transcript
        last = _payload_value(payload, "last_assistant_message")
        if last:
            target["last_assistant_message"] = last[:500]
        target["stop_hook_active"] = bool(payload.get("stop_hook_active"))
        return target

    result = _with_registry_lock(repo_root, mark)
    try:
        final_message = result.get("last_assistant_message") or ""
        verdict = extract_qa_verdict(final_message)
        if result.get("status") == "done" and result.get("task_dir"):
            task_dir = result.get("task_dir") or ""
            bound_run_id = str(result.get("run_id") or "")
            identity = {
                "source": "subagent_stop_hook",
                "agent_id": result.get("id") or aid,
                "agent_type": result.get("agent_type") or _agent_type(payload),
                "transcript_path": result.get("transcript_path") or "",
                "runtime_session_id": sid,
                "runtime_thread_id": result.get("id") or aid,
                "task_run_id": bound_run_id,
            }
            with receipt_stream_transaction(task_dir):
                binding = resolve_session_task_binding(repo_root, sid)
                if (
                    not bound_run_id
                    or binding.get("task_dir") != task_dir
                    or binding.get("run_id") != bound_run_id
                ):
                    return result
                if result.get("started_from_stop"):
                    started = record_subagent_receipt(
                        task_dir,
                        {
                            **identity,
                            "event": "started",
                            "summary": "subagent start inferred from authoritative stop hook",
                        },
                    )
                    if started.get("receipt_id"):
                        result["subagent_receipt_id"] = started["receipt_id"]
                completion = record_subagent_receipt(
                    task_dir,
                    {
                        **identity,
                        "event": "completed",
                        "verdict": verdict or "UNKNOWN",
                        "summary": final_message,
                    },
                )
            if completion.get("receipt_id"):
                result["completion_receipt_id"] = completion["receipt_id"]
    except Exception:
        pass
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
