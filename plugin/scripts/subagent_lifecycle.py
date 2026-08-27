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
        _bind_runtime_receipt_adapter,
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

    def _bind_runtime_receipt_adapter(source: str, function: Any) -> None:
        return None


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


def _reject(diagnostics: dict[str, Any] | None, reason: str) -> tuple[str, str]:
    """Record why provenance rejected a stop, then reject.

    Provenance has many independent hard-fail paths and rejects by returning an
    empty tuple, so at the call site every failure looks identical. That opacity
    is what made the 2026-08-25 completion-receipt outage expensive to diagnose.

    The reason travels through a caller-supplied dict rather than module state:
    `record_subagent_receipt`'s adapter pins this module's globals as an
    integrity check, so any mutable module-level variable invalidates the
    binding on first write and every later receipt append fails with
    PermissionError. Reason codes are short fixed strings — never transcript
    content or assistant text.
    """
    if diagnostics is not None:
        diagnostics["provenance_reason"] = reason
    return "", ""


def _trusted_stop_provenance(
    payload: dict[str, Any], sid: str, aid: str, run_id: str,
    diagnostics: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """Return transcript path/type only when runtime start and final text prove the stop."""
    raw_path = payload.get("agent_transcript_path")
    final_message = payload.get("last_assistant_message")
    if not isinstance(raw_path, str) or not isinstance(final_message, str) or not final_message:
        return _reject(diagnostics, "missing-transcript-path-or-final-message")
    path = os.path.abspath(raw_path)
    claude_root = os.path.abspath(
        os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    )
    projects_root = os.path.join(claude_root, "projects")
    opened: list[tuple[int, str, os.stat_result]] = []
    try:
        if os.path.commonpath((projects_root, path)) != projects_root:
            return _reject(diagnostics, "path-outside-projects-root")
        rel = os.path.relpath(path, projects_root).split(os.sep)
        if (
            len(rel) < 4 or any(part in {"", ".", ".."} for part in rel)
            or rel[-3:] != [sid, "subagents", f"agent-{aid}.jsonl"]
        ):
            return _reject(diagnostics, "unexpected-transcript-path-shape")
        dir_flags = (
            os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        current_fd = os.open(projects_root, dir_flags)
        root_stat = os.fstat(current_fd)
        opened.append((current_fd, projects_root, root_stat))
        if root_stat.st_uid != os.getuid() or root_stat.st_mode & 0o022:
            return _reject(diagnostics, "projects-root-ownership")
        for part in rel[:-1]:
            child_fd = os.open(part, dir_flags, dir_fd=current_fd)
            child_stat = os.fstat(child_fd)
            opened.append((child_fd, part, child_stat))
            if (
                not stat.S_ISDIR(child_stat.st_mode)
                or child_stat.st_uid != os.getuid() or child_stat.st_mode & 0o022
            ):
                return _reject(diagnostics, "transcript-dir-ownership")
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
            return _reject(diagnostics, "transcript-file-attributes")
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
            return _reject(diagnostics, "transcript-changed-during-read")
        for index, (fd, name, expected) in enumerate(opened):
            actual = (
                os.stat(name, dir_fd=opened[index - 1][0], follow_symlinks=False)
                if index else os.stat(projects_root, follow_symlinks=False)
            )
            if (actual.st_dev, actual.st_ino, actual.st_mode) != (
                expected.st_dev, expected.st_ino, expected.st_mode,
            ):
                return _reject(diagnostics, "transcript-path-swapped-during-read")
        run_time = datetime.fromisoformat(task_run_started_at({"run_id": run_id}).replace("Z", "+00:00"))

        def _start_is_within_run(entry, started_at):
            """True when the entry carries a tz-aware time at/after the run start."""
            raw = str(entry.get("timestamp") or "").replace("Z", "+00:00")
            try:
                event_time = datetime.fromisoformat(raw)
            except ValueError:
                return False
            if not event_time.tzinfo:
                return False
            return event_time.astimezone(timezone.utc) >= started_at

        items = [json.loads(line) for line in lines if line.strip()]
        # Two start shapes bind, because two runtimes emit different ones.
        #
        # The canonical shape is an identity banner: hookName "SubagentStart",
        # content ["Agent <type> started (<agentId>)"]. The matcher-qualified
        # shape is a hook-*execution* record: hookName "SubagentStart:<type>",
        # content "" (an empty string, not a list), alongside command/stdout/
        # exitCode/durationMs. This code previously skipped the second as a
        # "duplicate alongside the canonical attachment" — but on builds that
        # emit it, it is the ONLY start attachment in the transcript. The scan
        # skipped the sole start line and rejected at
        # `no-canonical-start-attachment`, declining ~1 in 5 completion
        # receipts on transcripts that existed and were valid. Because
        # `task_verify` derives PASS from ordered start/completion pairs, that
        # silently made PASS unreachable for every task.
        #
        # Accepting the second shape does not weaken identity. The line still
        # has to carry this exact agentId, sit in this session, and post-date
        # the task run; the agent type comes from the hookName suffix, which is
        # what the runtime dispatched on. What it does not carry is a *restated*
        # identity banner, and requiring a restatement was never the check doing
        # the work.
        canonical_types: list[str] = []
        qualified: list[tuple[str, dict]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("agentId") not in {None, aid} or item.get("sessionId") not in {None, sid}:
                return _reject(diagnostics, "foreign-agent-or-session-in-transcript")
            attachment = item.get("attachment")
            if not isinstance(attachment, dict) or attachment.get("hookEvent") != "SubagentStart":
                continue
            hook_name = attachment.get("hookName")
            if hook_name != "SubagentStart":
                if not (isinstance(hook_name, str)
                        and hook_name.startswith("SubagentStart:")):
                    return _reject(diagnostics, "unrecognized-start-hook-name")
                suffix = hook_name[len("SubagentStart:"):]
                if not re.fullmatch(r"[A-Za-z0-9_.:-]+", suffix):
                    return _reject(diagnostics, "unrecognized-start-hook-name")
                # Identity is demanded only if this line ends up being the
                # binding one. When a canonical attachment is present the
                # qualified line is ignored exactly as before — requiring its
                # agentId there would re-couple receipts to an undocumented
                # field of a line the validator otherwise never reads, which is
                # a previously-fixed outage shape.
                qualified.append((suffix, item))
                continue
            if item.get("agentId") != aid:
                return _reject(diagnostics, "canonical-start-agent-id-mismatch")
            if not _start_is_within_run(item, run_time):
                return _reject(diagnostics, "start-precedes-task-run")
            content = attachment.get("content")
            if not isinstance(content, list) or len(content) != 1 or not isinstance(content[0], str):
                return _reject(diagnostics, "start-content-shape")
            match = re.fullmatch(
                rf"Agent ([A-Za-z0-9_.:-]+) started \({re.escape(aid)}\)", content[0],
            )
            if not match:
                return _reject(diagnostics, "start-identity-mismatch")
            canonical_types.append(match.group(1))
        if len(set(canonical_types)) > 1:
            # Two *different* agent types claiming one agentId is the conflict.
            # Counting instead of de-duplicating declined honest stops: the
            # runtime writes one start pair per registered SubagentStart hook,
            # so a second hook produces a second identical banner, and two
            # transcripts in the observed session were rejected for repeating
            # themselves. The qualified branch below already used this rule;
            # the two are now consistent.
            return _reject(diagnostics, "duplicate-canonical-start")
        if canonical_types:
            transcript_agent_type = canonical_types[0]
        elif qualified:
            # Falling back to the qualified line, so now it must prove identity.
            if not any(entry.get("agentId") == aid for _, entry in qualified):
                return _reject(diagnostics, "canonical-start-agent-id-mismatch")
            bound = [
                suffix for suffix, entry in qualified
                if entry.get("agentId") == aid and _start_is_within_run(entry, run_time)
            ]
            if not bound:
                return _reject(diagnostics, "start-precedes-task-run")
            if len(set(bound)) > 1:
                # Two different agent types claiming one agentId is a conflict,
                # not a duplicate of a single start.
                return _reject(diagnostics, "duplicate-canonical-start")
            transcript_agent_type = bound[0]
        else:
            return _reject(diagnostics, "no-canonical-start-attachment")
        # The transcript proves a real subagent of this type started in this run.
        # It deliberately does NOT cross-check the final assistant text against
        # the payload: the runtime appends that text around the same instant
        # SubagentStop fires, so the check rejected genuine stops as often as it
        # passed them, and the only thing it added was resistance to a
        # deliberate multi-step forgery (spawn a decoy agent, then replay a
        # hand-written payload against its session). That is not the failure
        # mode receipts exist for — confabulation is — and an agent that can
        # write files could fabricate the transcript anyway. The verdict comes
        # from the runtime-supplied payload.
        return path, transcript_agent_type
    except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return _reject(diagnostics, f"exception:{type(exc).__name__}")
    finally:
        for fd, _name, _expected in reversed(opened):
            try:
                os.close(fd)
            except OSError:
                pass


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


def mark_subagent_stop(
    repo_root: str, payload: dict[str, Any],
    diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Append a trusted completion, or an atomic pair in stop-only runtimes.

    ``diagnostics`` is an optional caller-owned dict that receives a reason code
    when no receipt is produced. It exists so the hook can say *why* a stop was
    refused; see :func:`_reject`.
    """
    sid, aid = _official_stop_identity(payload)
    stop_message = _payload_value(payload, "last_assistant_message")
    task_dir, run_id = _binding(repo_root, sid) if sid and aid and stop_message else ("", "")
    if not task_dir and diagnostics is not None:
        diagnostics["provenance_reason"] = (
            "stop-identity-incomplete" if not (sid and aid and stop_message)
            else "session-task-binding-unresolved"
        )
    if diagnostics is not None:
        # Best-effort: a corrupt receipt stream must not escape here, or the
        # hook crashes instead of leaving the breadcrumb this exists to enable.
        try:
            diagnostics["expected_receipt"] = bool(task_dir) and any(
                item.get("source") == SOURCE
                and item.get("task_run_id") == run_id
                and item.get("runtime_id") == _runtime_id(sid, aid)
                and item.get("event") == "started"
                for item in receipt_snapshot(task_dir).entries
            )
        except Exception:
            diagnostics["expected_receipt"] = bool(task_dir)
    trusted_transcript, transcript_agent_type = (
        _trusted_stop_provenance(payload, sid, aid, run_id, diagnostics)
        if task_dir else ("", "")
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
    return result


def handle_subagent_hook(
    repo_root: str, payload: dict[str, Any], *, forced_event: str = "",
    diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    event = (forced_event or _event_name(payload)).lower()
    if event in ("start", "subagentstart", "subagent_start"):
        return register_subagent_start(repo_root, payload)
    if event in ("stop", "subagentstop", "subagent_stop"):
        return mark_subagent_stop(repo_root, payload, diagnostics)
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


_bind_runtime_receipt_adapter(SOURCE, register_subagent_start)
_bind_runtime_receipt_adapter(SOURCE, mark_subagent_stop)
del _bind_runtime_receipt_adapter
