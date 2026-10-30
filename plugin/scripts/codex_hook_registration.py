#!/usr/bin/env python3
"""Bind trusted Codex task results to exact session/task/run source focus."""
from __future__ import annotations

import json
import os
import re
import signal
import stat
from datetime import datetime
from itertools import islice
from pathlib import Path
import sys
import time
from contextlib import ExitStack

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS_DIR)

from _lib import (
    active_session_transaction,
    MAX_BINDING_CONFLICTS,
    _binding_conflicts_overflow as _conflicts_overflow,
    _live_binding_conflicts, _BindingConflictOverflow,
    _has_binding_conflict_fence, clear_binding_conflict_fence,
    find_harness_root,
    is_codex_wbs_binding_tool,
    read_active_session_marker,
    read_wbs_control,
    wbs_control_transaction,
    _read_regular_text_file,
    uuid7_timestamp_ms,
    resolve_session_wbs_binding,
    resolve_registered_worktree,
    wbs_control_status,
    write_active_marker,
    write_binding_conflict_fence,
    _bind_control_writer,
)


THREAD_RE = re.compile(r"^[0-9a-fA-F-]{16,80}$")
WBS_RE = re.compile(r"^WBS__[A-Za-z0-9_.-]{1,180}$")
REGISTRATION_BUDGET_SECONDS = 2.0
AUTHENTICATION_BUDGET_SECONDS = 6.0
NATIVE_METADATA_MAX_BYTES = 1024 * 1024


class _RegistrationTimeout(BaseException):
    pass


def _call_with_deadline(callback, deadline: float, fallback=False, *, status_out=None, stage="WBS binding"):
    """Run in-process WBS binding inside the remaining hard budget."""
    def failed(reason):
        if status_out is not None and status_out.get("status") != "failed":
            status_out.update(status="failed", stage=stage, reason=f"{stage}: {reason}")
        return fallback

    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return failed("deadline exhausted")
    if not hasattr(signal, "setitimer"):
        return failed("deadline enforcement unavailable")

    def _timeout(_signum, _frame):
        raise _RegistrationTimeout()

    try:
        current_timer = signal.getitimer(signal.ITIMER_REAL)
        previous_handler = signal.signal(signal.SIGALRM, _timeout)
    except (AttributeError, ValueError):
        return failed("deadline enforcement unavailable")
    effective_remaining = min(
        remaining,
        current_timer[0] if current_timer[0] > 0 else remaining,
    )
    timer_started = time.monotonic()
    try:
        previous_timer = signal.setitimer(signal.ITIMER_REAL, effective_remaining)
    except (AttributeError, ValueError):
        signal.signal(signal.SIGALRM, previous_handler)
        return failed("deadline enforcement unavailable")
    try:
        return callback()
    except _RegistrationTimeout:
        return failed("deadline exhausted")
    except Exception as exc:
        return failed(f"validation unavailable ({type(exc.__cause__ or exc).__name__})")
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0.0)
        signal.signal(signal.SIGALRM, previous_handler)
        previous_delay, previous_interval = previous_timer
        elapsed = time.monotonic() - timer_started
        previous_due = previous_delay > 0 and elapsed >= previous_delay
        if previous_delay > 0 and not previous_due:
            signal.setitimer(
                signal.ITIMER_REAL, previous_delay - elapsed, previous_interval
            )
        elif previous_due and previous_interval > 0:
            overdue = elapsed - previous_delay
            next_delay = previous_interval - (overdue % previous_interval)
            signal.setitimer(signal.ITIMER_REAL, next_delay, previous_interval)
        if previous_due:
            signal.raise_signal(signal.SIGALRM)


def _payload_data(payload: bytes) -> dict:
    try:
        value = json.loads(payload.decode("utf-8") or "{}")
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _registration_identity(payload: bytes) -> tuple[str, str]:
    data = _payload_data(payload)
    cwd = data.get("cwd")
    if not isinstance(cwd, str) or not os.path.isdir(cwd):
        return "", ""
    if any(key in data and data[key] is not None and not isinstance(data[key], str)
           for key in ("session_id", "thread_id")):
        return "", ""
    payload_ids = {data[key] for key in ("session_id", "thread_id") if data.get(key)}
    if len(payload_ids) > 1:
        return "", ""
    payload_id = next(iter(payload_ids), "")
    env_id = str(os.environ.get("CODEX_THREAD_ID") or "")
    if payload_id:
        if not THREAD_RE.fullmatch(payload_id):
            return "", ""
        if env_id and (not THREAD_RE.fullmatch(env_id) or env_id != payload_id):
            return "", ""
        thread_id = payload_id
    else:
        thread_id = env_id if THREAD_RE.fullmatch(env_id) else ""
    return (cwd, thread_id) if thread_id else ("", "")


def workspace_roots(control_root: str) -> list[str]:
    """Enumerate only revalidated linked worktrees with a bounded registry scan."""
    root = os.path.realpath(control_root)
    gitfile = Path(root) / ".git"
    if gitfile.is_file():
        try:
            target = _read_regular_text_file(str(gitfile), max_size=4096).strip().removeprefix("gitdir: ")
            common = (Path(root) / target).resolve().parent.parent
            main = str(common.parent)
            if resolve_registered_worktree(main, root) == root:
                root = main
        except (OSError, ValueError, RuntimeError):
            return []
    found = [root]
    registry = Path(root) / ".git" / "worktrees"
    if registry.is_dir() and not registry.is_symlink():
        entries = list(islice(registry.iterdir(), 257))
        if len(entries) > 256:
            return []
        for entry in entries:
            try:
                if entry.is_symlink():
                    continue
                target = str(Path(_read_regular_text_file(str(entry / "gitdir"), max_size=4096).strip()).parent)
                if target not in found and resolve_registered_worktree(root, target) == target:
                    found.append(target)
            except (OSError, ValueError, RuntimeError):
                continue
    return found


def _native_session_cwd(thread_id: str) -> str:
    """Read only native session metadata, never tool events or agent results."""
    if not THREAD_RE.fullmatch(thread_id) or os.environ.get("CODEX_THREAD_ID") != thread_id:
        return ""
    sessions = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "sessions"
    try:
        created = datetime.fromtimestamp(uuid7_timestamp_ms(thread_id) / 1000).astimezone()
        candidates = list((sessions / f"{created:%Y/%m/%d}").glob(f"rollout-*{thread_id}.jsonl"))
        if len(candidates) != 1:
            return ""
        path = candidates[0]
        cursor = sessions
        for component in (None, *path.relative_to(sessions).parts[:-1]):
            if component is not None:
                cursor = cursor / component
            info = os.lstat(cursor)
            if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
                return ""
        fd = None
        try:
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(fd, "rb") as handle:
                fd = None
                info = os.fstat(handle.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022 or info.st_nlink != 1:
                    return ""
                raw = handle.readline(NATIVE_METADATA_MAX_BYTES + 1)
                if not raw.endswith(b"\n") or len(raw) > NATIVE_METADATA_MAX_BYTES:
                    return ""
                event = json.loads(raw)
                meta = event.get("payload") if isinstance(event, dict) and event.get("type") == "session_meta" else None
                current = os.lstat(path)
                if stat.S_ISLNK(current.st_mode) or (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino):
                    return ""
        finally:
            if fd is not None:
                os.close(fd)
        if not isinstance(meta, dict) or meta.get("id") != thread_id:
            return ""
        cwd = meta.get("cwd")
        return os.path.realpath(cwd) if isinstance(cwd, str) and os.path.isabs(cwd) else ""
    except (OSError, ValueError, TypeError, OverflowError):
        return ""


def authenticate_native_session(thread_id: str, control_root: str) -> str:
    """Authenticate a process-owned native identity and its registered checkout."""
    def authenticate():
        cwd = _native_session_cwd(thread_id)
        root = find_harness_root(cwd) if cwd else ""
        return cwd if root and os.path.realpath(root) in workspace_roots(control_root) else ""
    return _call_with_deadline(authenticate, time.monotonic() + AUTHENTICATION_BUDGET_SECONDS, "", stage="native authentication")


def _conflict_roots(control_root: str, roots: list[str] | None) -> list[str]:
    roots = workspace_roots(control_root) if roots is None else roots
    if not roots:
        raise RuntimeError("workspace registry unavailable")
    return roots


def _live_conflicts(control_root: str, marker: dict, *, roots=None) -> list[dict]:
    return _live_binding_conflicts(marker, _conflict_roots(control_root, roots))


def _conflict_wbs_dirs(control_root: str, marker: dict, *, roots=None) -> list[str]:
    """Return structurally valid task directories named by a conflict fence."""
    tasks_roots = {os.path.realpath(os.path.join(root, "doc", "harness", "wbs"))
                   for root in _conflict_roots(control_root, roots)}
    found = []
    if _conflicts_overflow(marker):
        raise _BindingConflictOverflow("coordinator conflict fence exceeds its bound")
    raw = marker.get("conflicts")
    if not isinstance(raw, list) or not 2 <= len(raw) <= MAX_BINDING_CONFLICTS:
        return found
    for item in raw:
        if not isinstance(item, dict):
            continue
        wbs_dir = os.path.realpath(str(item.get("wbs_dir") or ""))
        wbs_id = str(item.get("wbs_id") or "")
        if (
            WBS_RE.fullmatch(wbs_id)
            and os.path.dirname(wbs_dir) in tasks_roots
            and os.path.basename(wbs_dir) == wbs_id
        ):
            found.append(wbs_dir)
    return found


REGISTERED = "registered"
NOT_APPLICABLE = "not_applicable"
REGISTRATION_FAILED = "failed"


def _wbs_arguments(data: dict) -> dict | None:
    """Decode supported aliases once; malformed or conflicting inputs fail closed."""
    values = []
    for key in ("tool_input", "input", "arguments"):
        if key not in data:
            continue
        value = data[key]
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (ValueError, RecursionError):
                return None
        if not isinstance(value, dict):
            return None
        values.append(value)
    if values and any(value != values[0] for value in values[1:]):
        return None
    return values[0] if values else {}


def _successful_wbs_response(response):
    """Decode the bounded structured/text envelope used by both MCP runtimes."""
    if not isinstance(response, dict) or response.get("isError") is True or response.get("success") is False:
        return None
    candidate = response.get("structuredContent", response)
    if "structuredContent" not in response and isinstance(response.get("content"), list):
        texts = [item.get("text") for item in response["content"]
                 if isinstance(item, dict) and item.get("type") == "text"]
        if len(texts) != 1 or not isinstance(texts[0], str) or len(texts[0]) > 1024 * 1024:
            return None
        try:
            candidate = json.loads(texts[0])
        except (ValueError, RecursionError):
            return None
    if not isinstance(candidate, dict) or candidate.get("error"):
        return None
    return candidate


def _wbs_result(payload: bytes) -> tuple[str, str, str]:
    """Extract one successful task result from known Codex hook envelopes."""
    data = _payload_data(payload)
    tool_name = str(data.get("tool_name") or data.get("tool") or "")
    arguments = _wbs_arguments(data)
    if arguments is None or not is_codex_wbs_binding_tool(tool_name, arguments):
        return "", "", ""
    response = data.get("tool_response", data.get("tool_result", data.get("toolResult")))
    if (
        not isinstance(response, dict)
        or response.get("isError") is True
        or response.get("success") is False
        or str(response.get("status") or "").lower() in {"error", "failed"}
    ):
        return "", "", ""
    candidate = _successful_wbs_response(response)
    if candidate is None:
        return "", "", ""
    wbs_dir = candidate.get("wbs_dir")
    wbs_id = candidate.get("wbs_id")
    run_id = candidate.get("run_id")
    if not isinstance(run_id, str):
        context = candidate.get("context")
        run_id = context.get("active_run_id") if isinstance(context, dict) else ""
    return (
        wbs_dir if isinstance(wbs_dir, str) else "",
        wbs_id if isinstance(wbs_id, str) else "",
        run_id if isinstance(run_id, str) else "",
    )


def register_wbs_result(
    payload: bytes,
    *,
    budget_seconds: float = REGISTRATION_BUDGET_SECONDS,
    status_out: dict | None = None,
) -> bool:
    """Bind a successful Harness WBS result to this exact Codex session."""
    deadline = time.monotonic() + max(0.0, float(budget_seconds))
    result = bool(_call_with_deadline(
        lambda: _register_wbs_result(payload, deadline, status_out,
                                     budget_seconds), deadline,
        status_out=status_out, stage="WBS binding",
    ))
    if not result and time.monotonic() >= deadline and status_out is not None and status_out.get("status") != REGISTRATION_FAILED:
        status_out.update({
            "status": REGISTRATION_FAILED,
            "reason": "WBS binding did not complete within its binding budget",
        })
    return result


def _wbs_result_binding(payload: bytes, status_out: dict | None):
    cwd, thread_id = _registration_identity(payload)
    wbs_dir, wbs_id, run_id = _wbs_result(payload)
    if status_out is not None:
        status_out.update({"status": NOT_APPLICABLE, "reason": "task result was not bindable"})
        if thread_id:
            status_out["thread_id"] = thread_id
    if (
        not cwd or not thread_id or not wbs_dir or not wbs_id or not run_id
    ):
        return None
    control_root = find_harness_root(cwd) or ""
    if not control_root:
        return None
    roots = workspace_roots(control_root)
    if not roots:
        if status_out is not None:
            status_out.update(status=REGISTRATION_FAILED, stage="workspace registry discovery",
                              reason="workspace registry discovery: registry unavailable")
        return None
    data = _payload_data(payload)
    tool_input = _wbs_arguments(data)
    if tool_input is None:
        return None
    requested_workspace = tool_input.get("workspace")
    if requested_workspace is not None:
        response = data.get("tool_response", data.get("tool_result", data.get("toolResult")))
        candidate = _successful_wbs_response(response)
        if candidate is None or candidate.get("workspace") != requested_workspace:
            return None
        try:
            control_root = resolve_registered_worktree(roots[0], requested_workspace) or roots[0]
        except (ValueError, RuntimeError):
            return None
    return cwd, thread_id, control_root, wbs_dir, wbs_id, run_id


def _register_wbs_result(
    payload: bytes, deadline: float, status_out: dict | None,
    budget_seconds: float = REGISTRATION_BUDGET_SECONDS,
) -> bool:
    binding = _call_with_deadline(
        lambda: _wbs_result_binding(payload, status_out),
        min(deadline, time.monotonic() + max(0.0, float(budget_seconds))), None,
        status_out=status_out, stage="WBS binding discovery",
    )
    if binding is None:
        return False
    cwd, thread_id, control_root, wbs_dir, wbs_id, run_id = binding
    return _bind_wbs_session(cwd, thread_id, control_root, wbs_dir, wbs_id, run_id, deadline, status_out)


def register_native_wbs(
    session_cwd: str, thread_id: str, control_root: str,
    wbs_dir: str, wbs_id: str, run_id: str,
    *, budget_seconds: float = REGISTRATION_BUDGET_SECONDS, status_out: dict | None = None,
) -> bool:
    """Bind an authenticated process-owned native actor through the hook owner."""
    if authenticate_native_session(thread_id, control_root) != os.path.realpath(session_cwd):
        return False
    deadline = time.monotonic() + max(0.0, float(budget_seconds))
    def publish():
        return _bind_wbs_session(session_cwd, thread_id, control_root, wbs_dir, wbs_id, run_id,
                                  deadline, status_out, native=True)
    return bool(_call_with_deadline(publish, deadline, status_out=status_out, stage="WBS binding"))


def _bind_wbs_session(
    cwd: str, thread_id: str, control_root: str, wbs_dir: str,
    wbs_id: str, run_id: str, deadline: float, status_out: dict | None, *, native: bool = False,
) -> bool:
    """One cross-workspace lock/fence/marker owner for hooks and native MCP."""
    roots = workspace_roots(control_root)
    if not roots:
        if status_out is not None:
            status_out.update(status=REGISTRATION_FAILED, stage="workspace registry discovery",
                              reason="workspace registry discovery: registry unavailable")
        return False
    expected_parent = os.path.realpath(os.path.join(control_root, "doc", "harness", "wbs"))
    canonical_wbs = os.path.realpath(wbs_dir)
    if (
        not control_root
        or not WBS_RE.fullmatch(wbs_id)
        or os.path.dirname(canonical_wbs) != expected_parent
        or os.path.basename(canonical_wbs) != wbs_id
        or canonical_wbs != os.path.abspath(wbs_dir)
    ):
        return False
    try:
        with active_session_transaction(roots[0]), ExitStack() as workspace_locks:
            roots = workspace_roots(roots[0])
            if not roots:
                if status_out is not None:
                    status_out.update(status=REGISTRATION_FAILED, stage="workspace registry discovery",
                                      reason="workspace registry discovery: registry unavailable")
                return False
            if control_root not in roots:
                return False
            for root in sorted(roots[1:]):
                workspace_locks.enter_context(active_session_transaction(root))
            # One shared transaction serializes binding and conflict fencing
            # across every validated checkout of this repository.
            markers = {root: read_active_session_marker(root, thread_id, strict=True) for root in roots}
            if any(_conflicts_overflow(marker) for marker in markers.values()):
                for root in roots:
                    if root == control_root or markers[root]:
                        write_binding_conflict_fence(root, thread_id, [], overflow=True)
                if status_out is not None:
                    status_out.update({"status": NOT_APPLICABLE,
                                       "reason": "coordinator conflict fence overflow requires a new coordinator"})
                return False
            bindings = {root: resolve_session_wbs_binding(root, thread_id, strict=True) for root in roots}
            lock_dirs = {canonical_wbs}
            for root in roots:
                lock_dirs.update(_conflict_wbs_dirs(root, markers[root], roots=roots))
                if bindings[root]:
                    lock_dirs.add(bindings[root]["wbs_dir"])
            with ExitStack() as locks:
                for locked_task in sorted(lock_dirs):
                    locks.enter_context(wbs_control_transaction(locked_task))
                if native and _native_session_cwd(thread_id) != os.path.realpath(cwd):
                    if status_out is not None:
                        status_out.update(status=REGISTRATION_FAILED, stage="WBS binding publication",
                                          reason="WBS binding publication: native identity unavailable")
                    return False
                control = read_wbs_control(canonical_wbs)
                if (
                    wbs_control_status(canonical_wbs, control) != "open"
                    or control.get("run_id") != run_id
                ):
                    return False
                candidate_binding = {
                    "wbs_dir": canonical_wbs, "wbs_id": wbs_id, "run_id": run_id,
                }
                live = []
                for root in roots:
                    live.extend(_live_conflicts(root, markers[root], roots=roots))
                    binding = resolve_session_wbs_binding(root, thread_id, strict=True)
                    if binding:
                        live.append({"wbs_dir": binding["wbs_dir"],
                                     "wbs_id": os.path.basename(binding["wbs_dir"]),
                                     "run_id": binding["run_id"]})
                distinct = {tuple(sorted(item.items())) for item in [*live, candidate_binding]}
                if len(distinct) >= 2:
                    conflicts = [dict(item) for item in sorted(distinct)]
                    overflow = _conflicts_overflow({"conflicts": conflicts})
                    for root in roots:
                        if root == control_root or markers[root]:
                            write_binding_conflict_fence(
                                root, thread_id, [] if overflow else conflicts, overflow=overflow,
                            )
                    if status_out is not None:
                        status_out.update({
                            "status": NOT_APPLICABLE,
                            "reason": "conflicting open WBSs invalidated exact session binding",
                        })
                    return False
                if any(_has_binding_conflict_fence(marker) for marker in markers.values()):
                    for root in roots:
                        if _has_binding_conflict_fence(markers[root]):
                            clear_binding_conflict_fence(root, thread_id)
                write_active_marker(
                    control_root,
                    canonical_wbs,
                    session_id=thread_id,
                )
                if status_out is not None:
                    status_out.update(status=REGISTERED, reason="")
                return True
    except Exception as exc:
        if status_out is not None and status_out.get("status") != REGISTRATION_FAILED:
            status_out.update({"status": REGISTRATION_FAILED, "stage": "WBS binding",
                               "reason": f"WBS binding unavailable ({type(exc).__name__})"})
        return False


_bind_control_writer(register_wbs_result)
_bind_control_writer(register_native_wbs)
del _bind_control_writer
