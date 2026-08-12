#!/usr/bin/env python3
"""Receipt-backed Claude subagent lifecycle hooks and Stop-gate queries."""
from __future__ import annotations

import json
import os
import re
import stat
import time
from datetime import datetime, timezone
from typing import Any

try:
    from _lib import (  # type: ignore
        append_conversation_entry,
        current_session_id,
        normalize_receipt_completion,
        record_subagent_receipt,
        receipt_snapshot,
        receipt_stream_savepoint,
        receipt_stream_transaction,
        extract_qa_verdict,
        resolve_session_task_binding,
        read_task_control,
        task_run_started_at,
    )
except Exception:  # pragma: no cover - imported only inside harness scripts
    def current_session_id(default: str = "default") -> str:
        return default

    def resolve_session_task_binding(repo_root: str, session_id: str) -> dict[str, str]:
        return {}

    def read_task_control(task_dir: str) -> dict[str, Any]:
        return {}

    def task_run_started_at(control: dict[str, Any]) -> str:
        return ""

    def record_subagent_receipt(task_dir: str, receipt: dict[str, Any]) -> dict[str, Any]:
        return {}

    def receipt_snapshot(task_dir: str):
        return type("EmptySnapshot", (), {"entries": ()})()

    class _NullTransaction:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def receipt_stream_transaction(task_dir: str):
        return _NullTransaction()

    def receipt_stream_savepoint(task_dir: str):
        return _NullTransaction()

    def extract_qa_verdict(value: str) -> str:
        return ""

    def normalize_receipt_completion(lens: str, value: str, supplied_verdict: str = ""):
        return "PENDING", ""

    def append_conversation_entry(task_dir: str, **kwargs: Any) -> bool:
        return False


DEFAULT_STALE_SECS = 30 * 60
DEFAULT_WAIT_SECS = 6.0
POLL_SECS = 0.25


def _now() -> float:
    return time.time()


def _payload_value(payload: dict[str, Any], *keys: str) -> str:
    for key in keys:
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _agent_type(payload: dict[str, Any]) -> str:
    direct = _payload_value(payload, "agent_type", "agentType", "subagent_type", "subagentType")
    if direct:
        return direct
    nested = payload.get("subagent") or payload.get("agent")
    if isinstance(nested, dict):
        return _payload_value(nested, "type", "agent_type", "agentType")
    return ""


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
        or any(ch not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-" for ch in aid)
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
    path = os.path.abspath(raw_path)
    claude_root = os.path.abspath(
        os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    )
    projects_root = os.path.join(claude_root, "projects")
    opened: list[tuple[int, str, os.stat_result]] = []
    try:
        if os.path.commonpath((projects_root, path)) != projects_root:
            return "", ""
        rel = os.path.relpath(path, projects_root).split(os.sep)
        if (
            len(rel) < 4 or any(part in {"", ".", ".."} for part in rel)
            or rel[-3:] != [sid, "subagents", f"agent-{aid}.jsonl"]
        ):
            return "", ""
        dir_flags = (
            os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        current_fd = os.open(projects_root, dir_flags)
        root_stat = os.fstat(current_fd)
        opened.append((current_fd, projects_root, root_stat))
        if root_stat.st_uid != os.getuid() or root_stat.st_mode & 0o022:
            return "", ""
        for part in rel[:-1]:
            child_fd = os.open(part, dir_flags, dir_fd=current_fd)
            child_stat = os.fstat(child_fd)
            opened.append((child_fd, part, child_stat))
            if (
                not stat.S_ISDIR(child_stat.st_mode)
                or child_stat.st_uid != os.getuid() or child_stat.st_mode & 0o022
            ):
                return "", ""
            current_fd = child_fd
        leaf_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        leaf_fd = os.open(rel[-1], leaf_flags, dir_fd=current_fd)
        before = os.fstat(leaf_fd)
        opened.append((leaf_fd, rel[-1], before))
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid() or before.st_nlink != 1
            or before.st_mode & 0o022 or before.st_size > 64 * 1024 * 1024
        ):
            return "", ""
        raw = bytearray()
        while True:
            chunk = os.read(leaf_fd, 64 * 1024)
            if not chunk:
                break
            raw.extend(chunk)
        lines = raw.decode("utf-8").splitlines()
        after = os.fstat(leaf_fd)
        if (
            before.st_dev, before.st_ino, before.st_size,
            before.st_mtime_ns, before.st_ctime_ns,
        ) != (
            after.st_dev, after.st_ino, after.st_size,
            after.st_mtime_ns, after.st_ctime_ns,
        ):
            return "", ""
        for index, (fd, name, expected) in enumerate(opened):
            actual = (
                os.stat(name, dir_fd=opened[index - 1][0], follow_symlinks=False)
                if index else os.stat(projects_root, follow_symlinks=False)
            )
            if (actual.st_dev, actual.st_ino, actual.st_mode) != (
                expected.st_dev, expected.st_ino, expected.st_mode,
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
    finally:
        for fd, _name, _expected in reversed(opened):
            try:
                os.close(fd)
            except OSError:
                pass
    return "", ""


def _task_id_from_dir(task_dir: str) -> str:
    return os.path.basename(os.path.normpath(task_dir)) if task_dir else ""


def _event_name(payload: dict[str, Any]) -> str:
    return _payload_value(payload, "hook_event_name", "hookEventName", "event", "event_name")


SOURCE = "claude_hook"


def _runtime_id(session_id: str, agent_id: str) -> str:
    return f"claude:{session_id}:{agent_id}"


def _identity_matches(item: dict[str, Any], identity: dict[str, str]) -> bool:
    return all(str(item.get(key) or "") == value for key, value in identity.items())


def _binding(repo_root: str, session_id: str) -> tuple[str, str]:
    bound = resolve_session_task_binding(repo_root, session_id)
    task_dir = str(bound.get("task_dir") or "")
    run_id = str(bound.get("run_id") or "")
    if not task_dir or not run_id:
        return "", ""
    control = read_task_control(task_dir)
    if str(control.get("run_id") or "") != run_id:
        return "", ""
    return task_dir, run_id


def _receipt_time(value: str) -> float | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if not parsed.tzinfo:
            return None
        return parsed.timestamp()
    except (TypeError, ValueError, OverflowError):
        return None


def _active_from_snapshot(
    task_dir: str,
    run_id: str,
    session_id: str,
    *,
    stale_secs: float,
) -> list[dict[str, Any]]:
    prefix = f"claude:{session_id}:"
    unmatched: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for item in receipt_snapshot(task_dir).entries:
        runtime_id = str(item.get("runtime_id") or "")
        if (
            item.get("source") != SOURCE
            or item.get("task_run_id") != run_id
            or not runtime_id.startswith(prefix)
        ):
            continue
        key = tuple(str(item.get(name) or "") for name in (
            "source", "task_run_id", "runtime_id", "agent_id", "agent_type", "lens",
        ))
        if item.get("event") == "started":
            unmatched.setdefault(key, []).append(item)
        elif item.get("event") == "completed" and unmatched.get(key):
            unmatched[key].pop(0)

    now = _now()
    active: list[dict[str, Any]] = []
    for starts in unmatched.values():
        for item in starts:
            started_ts = _receipt_time(str(item.get("ts") or ""))
            # Only a valid timestamp in the past can expire. Invalid and future
            # timestamps fail closed and remain active.
            if started_ts is not None and started_ts <= now and now - started_ts > stale_secs:
                continue
            active.append({
                "id": str(item.get("agent_id") or ""),
                "agent_id": str(item.get("agent_id") or ""),
                "agent_type": str(item.get("agent_type") or ""),
                "runtime_id": str(item.get("runtime_id") or ""),
                "session_id": session_id,
                "task_id": _task_id_from_dir(task_dir),
                "task_dir": task_dir,
                "run_id": run_id,
                "updated_ts": started_ts if started_ts is not None else now,
            })
    return active


def register_subagent_start(
    repo_root: str,
    payload: dict[str, Any],
    *,
    task_dir: str | None = None,
) -> dict[str, Any]:
    """Append one trusted Claude start directly to the task receipt stream."""
    sid, aid = _official_stop_identity(payload)
    if not sid or not aid:
        return {}
    bound_task_dir, run_id = _binding(repo_root, sid)
    if not bound_task_dir or (task_dir and os.path.realpath(task_dir) != os.path.realpath(bound_task_dir)):
        return {}
    task_dir = bound_task_dir
    runtime_id = _runtime_id(sid, aid)
    identity = {
        "source": SOURCE,
        "task_run_id": run_id,
        "runtime_id": runtime_id,
        "agent_id": aid,
        "agent_type": _agent_type(payload),
    }
    duplicate = False
    with receipt_stream_transaction(task_dir):
        runtime_existing = [
            item for item in receipt_snapshot(task_dir).entries
            if item.get("source") == SOURCE
            and item.get("task_run_id") == run_id
            and item.get("runtime_id") == runtime_id
        ]
        if runtime_existing:
            if (
                len(runtime_existing) == 1
                and runtime_existing[0].get("event") == "started"
                and _identity_matches(runtime_existing[0], identity)
            ):
                receipt = runtime_existing[0]
                duplicate = True
            else:
                raise RuntimeError("duplicate or conflicting Claude lifecycle start")
        else:
            receipt = record_subagent_receipt(
                task_dir,
                {
                    **identity,
                    "event": "started",
                    "summary": "subagent start hook observed",
                },
            )
    return {
        "status": "duplicate_start" if duplicate else "active", "id": aid, "agent_id": aid,
        "agent_type": receipt.get("agent_type") or "", "runtime_id": runtime_id,
        "session_id": sid, "task_id": _task_id_from_dir(task_dir),
        "task_dir": task_dir, "run_id": run_id,
    }


def mark_subagent_stop(repo_root: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Append a trusted completion, or an atomic pair in stop-only runtimes."""
    sid, aid = _official_stop_identity(payload)
    stop_message = _payload_value(payload, "last_assistant_message")
    task_dir, run_id = _binding(repo_root, sid) if sid and aid and stop_message else ("", "")
    trusted_transcript, transcript_agent_type = (
        _trusted_stop_provenance(payload, sid, aid, run_id) if task_dir else ("", "")
    )
    if not trusted_transcript or not transcript_agent_type:
        return {}
    runtime_id = _runtime_id(sid, aid)
    identity = {
        "source": SOURCE,
        "task_run_id": run_id,
        "runtime_id": runtime_id,
        "agent_id": aid,
        "agent_type": transcript_agent_type,
    }
    result = {
        "status": "done", "id": aid, "agent_id": aid,
        "agent_type": transcript_agent_type, "runtime_id": runtime_id,
        "session_id": sid, "task_id": _task_id_from_dir(task_dir),
        "task_dir": task_dir, "run_id": run_id,
        "last_assistant_message": stop_message,
    }
    try:
        with receipt_stream_transaction(task_dir):
            rebound_task_dir, rebound_run_id = _binding(repo_root, sid)
            if rebound_task_dir != task_dir or rebound_run_id != run_id:
                raise RuntimeError("Claude session task binding changed")
            snapshot = receipt_snapshot(task_dir)
            runtime_existing = [
                item for item in snapshot.entries
                if item.get("source") == SOURCE
                and item.get("task_run_id") == run_id
                and item.get("runtime_id") == runtime_id
            ]
            if runtime_existing:
                lenses = {str(item.get("lens") or "") for item in runtime_existing}
                if len(lenses) != 1:
                    raise RuntimeError("conflicting Claude lifecycle lens")
                identity["lens"] = lenses.pop()
            existing = [
                item for item in runtime_existing
                if _identity_matches(item, identity)
            ]
            if len(existing) != len(runtime_existing):
                raise RuntimeError("conflicting Claude lifecycle identity")
            starts = [item for item in existing if item.get("event") == "started"]
            completed = [item for item in existing if item.get("event") == "completed"]
            if len(starts) == 1 and len(completed) == 1 and len(existing) == 2:
                prior = completed[0]
                expected_verdict, expected_summary = normalize_receipt_completion(
                    str(prior.get("lens") or ""), stop_message,
                    extract_qa_verdict(stop_message) or "UNKNOWN",
                )
                if (
                    prior.get("verdict") != expected_verdict
                    or prior.get("summary") != expected_summary
                ):
                    raise RuntimeError("conflicting Claude lifecycle replay")
                result["status"] = "duplicate_stop"
            elif len(starts) == 1 and not completed and len(existing) == 1:
                with receipt_stream_savepoint(task_dir):
                    record_subagent_receipt(task_dir, {
                        **identity, "event": "completed",
                        "verdict": extract_qa_verdict(stop_message) or "UNKNOWN",
                        "summary": stop_message,
                    })
            elif not existing:
                result["started_from_stop"] = True
                with receipt_stream_savepoint(task_dir):
                    record_subagent_receipt(task_dir, {
                        **identity, "event": "started",
                        "summary": "subagent start inferred from authoritative stop hook",
                    })
                    record_subagent_receipt(task_dir, {
                        **identity, "event": "completed",
                        "verdict": extract_qa_verdict(stop_message) or "UNKNOWN",
                        "summary": stop_message,
                    })
            else:
                raise RuntimeError("existing Claude lifecycle identity is incomplete or duplicated")
    except Exception:
        result = dict(result)
        result["status"] = "receipt_pending"
        result["reason"] = "Receipt publication failed; the same stop may be retried."
    try:
        if result.get("status") in {"done", "duplicate_stop"}:
            append_conversation_entry(
                task_dir,
                role="subagent",
                text=stop_message,
                source=SOURCE,
                event_id=runtime_id,
                agent_type=transcript_agent_type,
            )
    except Exception:
        pass
    return result


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
    """Derive unmatched current-run/current-session starts from receipts."""
    sid = session_id or current_session_id()
    task_dir, run_id = _binding(repo_root, sid)
    if not task_dir or (task_id and _task_id_from_dir(task_dir) != task_id):
        return []
    return _active_from_snapshot(task_dir, run_id, sid, stale_secs=stale_secs)


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
