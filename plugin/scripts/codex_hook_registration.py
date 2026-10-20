#!/usr/bin/env python3
"""Best-effort Codex root-rollout registration at startup and before spawn."""
from __future__ import annotations

import json
import os
import re
import signal
import sys
import time
from contextlib import ExitStack
from typing import Callable

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS_DIR)

from codex_lifecycle_watcher import (
    ensure, invalidate_registration, workspace_roots,
    _find_rollout, _open_trusted_file, _sessions_root, _native_meta,
    _path_matches_handle, _coordinator_identity, MAX_CHILD_BYTES,
)
from _lib import (
    active_session_transaction,
    MAX_BINDING_CONFLICTS, MAX_CONFLICT_BYTES,
    _binding_conflicts_overflow as _conflicts_overflow,
    _live_binding_conflicts, _BindingConflictOverflow,
    _has_binding_conflict_fence, clear_binding_conflict_fence,
    find_harness_root,
    is_codex_task_binding_tool,
    read_active_session_marker,
    read_task_control,
    receipt_stream_transaction,
    resolve_session_task_binding,
    resolve_registered_worktree,
    task_control_status,
    write_active_marker,
    write_binding_conflict_fence,
    _bind_control_writer,
)


THREAD_RE = re.compile(r"^[0-9a-fA-F-]{16,80}$")
TASK_RE = re.compile(r"^TASK__[A-Za-z0-9_.-]{1,180}$")
REGISTRATION_BUDGET_SECONDS = 2.0


class _RegistrationTimeout(BaseException):
    pass


def _harness_enabled_cwd(cwd: str) -> bool:
    """Return whether ``cwd`` belongs to an explicitly enabled workspace."""
    return bool(find_harness_root(cwd))


def _call_with_deadline(callback, deadline: float, fallback=False, *, status_out=None, stage="registration"):
    """Run in-process registration work inside the remaining hard budget."""
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
        return failed(f"validation unavailable ({type(exc).__name__})")
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


def _ensure_with_deadline(
    control_root: str,
    thread_id: str,
    deadline: float,
    *,
    session_cwd: str | None = None,
    task_id: str = "",
    run_id: str = "",
    status_out: dict | None = None,
) -> bool:
    """Interrupt the complete registration attempt at its wall-clock budget."""
    return bool(_call_with_deadline(
        lambda: ensure(
            control_root, thread_id,
            session_cwd=session_cwd or control_root,
            task_id=task_id,
            run_id=run_id,
            deadline=deadline,
        ),
        deadline,
        False,
        status_out=status_out, stage="registration publication",
    ))


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
    payload_ids = {
        str(data.get(key) or "")
        for key in ("session_id", "thread_id")
        if data.get(key)
    }
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


def authenticate_native_session(thread_id: str, control_root: str) -> str:
    """Return a process-owned actor's trusted native cwd in this repository.

    The environment supplies only a candidate ID. Native descriptor metadata
    and every parent spawn edge must authenticate it before fresh authority.
    """
    if not THREAD_RE.fullmatch(thread_id) or os.environ.get("CODEX_THREAD_ID") != thread_id:
        return ""
    try:
        path = _find_rollout(thread_id)
        opened = _open_trusted_file(path, _sessions_root(), max_size=MAX_CHILD_BYTES) if path else None
        if opened is None:
            return ""
        handle, _ = opened
        try:
            meta = _native_meta(handle)
            raw_cwd = meta.get("cwd")
            if not isinstance(raw_cwd, str) or not os.path.isabs(raw_cwd):
                return ""
            cwd = os.path.realpath(raw_cwd)
            roots = workspace_roots(control_root)
            native_root = find_harness_root(cwd)
            if not roots or not native_root or os.path.realpath(native_root) not in roots:
                return ""
            if not _coordinator_identity(thread_id, cwd):
                return ""
            return cwd if _path_matches_handle(path, _sessions_root(), handle) else ""
        finally:
            handle.close()
    except Exception:
        return ""


def _bound_workspace_roots(control_root: str, thread_id: str) -> list[str] | None:
    roots = workspace_roots(control_root)
    if not roots:
        raise RuntimeError("workspace registry unavailable")
    bound = []
    for root in roots:
        marker = read_active_session_marker(root, thread_id)
        if _has_binding_conflict_fence(marker):
            return None
        if marker and resolve_session_task_binding(root, thread_id):
            bound.append(root)
    return bound


def _live_conflicts(control_root: str, marker: dict) -> list[dict]:
    return _live_binding_conflicts(marker, workspace_roots(control_root))


def _conflict_task_dirs(control_root: str, marker: dict) -> list[str]:
    """Return structurally valid task directories named by a conflict fence."""
    tasks_roots = {os.path.realpath(os.path.join(root, "doc", "harness", "tasks"))
                   for root in workspace_roots(control_root)}
    found = []
    if _conflicts_overflow(marker):
        raise _BindingConflictOverflow("coordinator conflict fence exceeds its bound")
    raw = marker.get("conflicts")
    if not isinstance(raw, list) or not 2 <= len(raw) <= MAX_BINDING_CONFLICTS:
        return found
    for item in raw:
        if not isinstance(item, dict):
            continue
        task_dir = os.path.realpath(str(item.get("task_dir") or ""))
        task_id = str(item.get("task_id") or "")
        if (
            TASK_RE.fullmatch(task_id)
            and os.path.dirname(task_dir) in tasks_roots
            and os.path.basename(task_dir) == task_id
        ):
            found.append(task_dir)
    return found


REGISTERED = "registered"
NOT_APPLICABLE = "not_applicable"
REGISTRATION_FAILED = "failed"


def _bind_active_task_to_root_session(control_root: str, thread_id: str) -> bool:
    """Accept only an already exact session binding.

    The shared default/legacy marker is compatibility state, not provenance.
    Exact binding is published by PostToolUse, where the hook has both the
    current root-session identity and the successful MCP task result.
    """
    return bool(resolve_session_task_binding(control_root, thread_id))



def _task_arguments(data: dict) -> dict | None:
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


def _task_result(payload: bytes) -> tuple[str, str, str]:
    """Extract one successful task result from known Codex hook envelopes."""
    data = _payload_data(payload)
    tool_name = str(data.get("tool_name") or data.get("tool") or "")
    arguments = _task_arguments(data)
    if arguments is None or not is_codex_task_binding_tool(tool_name, arguments):
        return "", "", ""
    response = data.get("tool_response", data.get("tool_result", data.get("toolResult")))
    if (
        not isinstance(response, dict)
        or response.get("isError") is True
        or response.get("success") is False
        or str(response.get("status") or "").lower() in {"error", "failed"}
    ):
        return "", "", ""
    candidate = response.get("structuredContent", response)
    if not isinstance(candidate, dict) or candidate.get("error"):
        return "", "", ""
    task_dir = candidate.get("task_dir")
    task_id = candidate.get("task_id")
    run_id = candidate.get("run_id")
    if not isinstance(run_id, str):
        context = candidate.get("task_context")
        run_id = context.get("active_run_id") if isinstance(context, dict) else ""
    return (
        task_dir if isinstance(task_dir, str) else "",
        task_id if isinstance(task_id, str) else "",
        run_id if isinstance(run_id, str) else "",
    )


def register_task_result(
    payload: bytes,
    *,
    budget_seconds: float = REGISTRATION_BUDGET_SECONDS,
    status_out: dict | None = None,
) -> bool:
    """Bind a successful Harness task result to this exact Codex session."""
    deadline = time.monotonic() + max(0.0, float(budget_seconds))
    result = bool(_call_with_deadline(
        lambda: _register_task_result(payload, deadline, status_out), deadline,
        status_out=status_out, stage="task binding",
    ))
    if not result and time.monotonic() >= deadline and status_out is not None and status_out.get("status") != REGISTRATION_FAILED:
        status_out.update({
            "status": REGISTRATION_FAILED,
            "reason": "task binding did not complete within its registration budget",
        })
    return result


def _register_task_result(payload: bytes, deadline: float, status_out: dict | None) -> bool:
    cwd, thread_id = _registration_identity(payload)
    task_dir, task_id, run_id = _task_result(payload)
    if status_out is not None:
        status_out.update({"status": NOT_APPLICABLE, "reason": "task result was not bindable"})
        if thread_id:
            status_out["thread_id"] = thread_id
    if (
        not cwd or not thread_id or not task_dir or not task_id or not run_id
    ):
        return False
    control_root = find_harness_root(cwd) or ""
    roots = workspace_roots(control_root) if control_root else []
    if not roots:
        return False
    data = _payload_data(payload)
    tool_input = _task_arguments(data)
    if tool_input is None:
        return False
    requested_workspace = tool_input.get("workspace")
    if requested_workspace is not None:
        response = data.get("tool_response", data.get("tool_result", data.get("toolResult")))
        candidate = response.get("structuredContent", response)
        if candidate.get("workspace") != requested_workspace:
            return False
        try:
            control_root = resolve_registered_worktree(roots[0], requested_workspace) or roots[0]
        except (ValueError, RuntimeError):
            return False
    return _bind_task_session(cwd, thread_id, control_root, task_dir, task_id, run_id, deadline, status_out)


def register_native_task(
    session_cwd: str, thread_id: str, control_root: str,
    task_dir: str, task_id: str, run_id: str,
    *, budget_seconds: float = REGISTRATION_BUDGET_SECONDS, status_out: dict | None = None,
) -> bool:
    """Bind an authenticated process-owned native actor through the hook owner."""
    # Native ancestry discovery is separately bounded by the trusted rollout
    # reader. It can exceed the watcher budget for a long-running coordinator;
    # authenticate before starting that budget, and always before publication.
    if authenticate_native_session(thread_id, control_root) != session_cwd:
        if status_out is not None:
            status_out.update({"status": NOT_APPLICABLE, "reason": "native session proof was unavailable"})
        return False
    deadline = time.monotonic() + max(0.0, float(budget_seconds))
    return bool(_call_with_deadline(
        lambda: _bind_task_session(
            session_cwd, thread_id, control_root, task_dir, task_id, run_id, deadline, status_out,
        ), deadline, status_out=status_out, stage="task binding",
    ))


def _bind_task_session(
    cwd: str, thread_id: str, control_root: str, task_dir: str,
    task_id: str, run_id: str, deadline: float, status_out: dict | None,
) -> bool:
    """One cross-workspace lock/fence/marker owner for hooks and native MCP."""
    roots = workspace_roots(control_root)
    if not roots:
        return False
    expected_parent = os.path.realpath(os.path.join(control_root, "doc", "harness", "tasks"))
    canonical_task = os.path.realpath(task_dir)
    if (
        not control_root
        or not TASK_RE.fullmatch(task_id)
        or os.path.dirname(canonical_task) != expected_parent
        or os.path.basename(canonical_task) != task_id
        or canonical_task != os.path.abspath(task_dir)
    ):
        return False
    try:
        with active_session_transaction(roots[0]), ExitStack() as workspace_locks:
            roots = workspace_roots(roots[0])
            if control_root not in roots:
                return False
            for root in sorted(roots[1:]):
                workspace_locks.enter_context(active_session_transaction(root))
            # One shared transaction serializes binding and conflict fencing
            # across every validated checkout of this repository.
            markers = {root: read_active_session_marker(root, thread_id) for root in roots}
            if any(_conflicts_overflow(marker) for marker in markers.values()):
                for root in roots:
                    if root == control_root or markers[root]:
                        write_binding_conflict_fence(root, thread_id, [], overflow=True)
                        invalidate_registration(root, thread_id)
                if status_out is not None:
                    status_out.update({"status": NOT_APPLICABLE,
                                       "reason": "coordinator conflict fence overflow requires a new coordinator"})
                return False
            bindings = {root: resolve_session_task_binding(root, thread_id) for root in roots}
            lock_dirs = {canonical_task}
            for root in roots:
                lock_dirs.update(_conflict_task_dirs(root, markers[root]))
                if bindings[root]:
                    lock_dirs.add(bindings[root]["task_dir"])
            with ExitStack() as locks:
                for locked_task in sorted(lock_dirs):
                    locks.enter_context(receipt_stream_transaction(locked_task))
                control = read_task_control(canonical_task)
                if (
                    task_control_status(canonical_task, control) != "open"
                    or control.get("run_id") != run_id
                ):
                    return False
                candidate_binding = {
                    "task_dir": canonical_task, "task_id": task_id, "run_id": run_id,
                }
                live = []
                for root in roots:
                    live.extend(_live_conflicts(root, markers[root]))
                    binding = resolve_session_task_binding(root, thread_id)
                    if binding:
                        live.append({"task_dir": binding["task_dir"],
                                     "task_id": os.path.basename(binding["task_dir"]),
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
                            invalidate_registration(root, thread_id)
                    if status_out is not None:
                        status_out.update({
                            "status": NOT_APPLICABLE,
                            "reason": "conflicting open tasks invalidated exact session binding",
                        })
                    return False
                if any(_has_binding_conflict_fence(marker) for marker in markers.values()):
                    # No fence is acknowledged until every old offset is gone.
                    # Interruption during either loop must never revive a survivor.
                    for root in roots:
                        if not invalidate_registration(root, thread_id):
                            return False
                    for root in roots:
                        if _has_binding_conflict_fence(markers[root]):
                            clear_binding_conflict_fence(root, thread_id)
                existing = resolve_session_task_binding(control_root, thread_id)
                if not existing and not invalidate_registration(control_root, thread_id):
                    return False
                write_active_marker(
                    control_root,
                    canonical_task,
                    session_id=thread_id,
                    publish_legacy=False,
                )
                return restore_watcher_registration(
                    json.dumps({"cwd": cwd, "session_id": thread_id}).encode("utf-8"),
                    budget_seconds=max(0.0, deadline - time.monotonic()),
                    status_out=status_out,
                )
    except Exception as exc:
        if status_out is not None and status_out.get("status") != REGISTRATION_FAILED:
            status_out.update({"status": REGISTRATION_FAILED, "stage": "task binding",
                               "reason": f"task binding unavailable ({type(exc).__name__})"})
        return False
def restore_watcher_registration(
    payload: bytes,
    *,
    retry_seconds: float = 0.0,
    budget_seconds: float = REGISTRATION_BUDGET_SECONDS,
    ensure_fn: Callable[[str, str], bool] = ensure,
    bind_fn: Callable[[str, str], bool] | None = None,
    status_out: dict | None = None,
) -> bool:
    """Restore registration without changing an existing immutable offset.

    A late recovery starts at the then-current rollout offset, so it can attest
    only future subagent starts. It never reconstructs already-finished work.

    The bool return is unchanged for existing callers, but False conflates two
    very different situations. Registration is *not applicable* when this is not
    a Codex rollout, when no thread identity is present, or when there is no
    open task to bind — none of which is a failure, and none of which should be
    reported to the user as one. It has genuinely *failed* only when an attempt
    was made and did not succeed. Callers that report the result pass
    ``status_out`` and receive ``{"status": ..., "reason": ...}``; reporting
    every False as a timeout sends the user to repair a timeout that never
    happened.
    """
    def _record(status: str, reason: str) -> None:
        if status_out is not None:
            status_out["status"] = status
            status_out["reason"] = reason

    _record(NOT_APPLICABLE, "registration was not attempted")
    if status_out is not None:
        status_out.pop("stage", None)
    started = time.monotonic()
    deadline = started + max(0.0, float(budget_seconds))
    cwd, thread_id = _registration_identity(payload)
    if status_out is not None and thread_id:
        status_out["thread_id"] = thread_id
    control_root = (
        _call_with_deadline(lambda: find_harness_root(cwd), deadline, "",
                            status_out=status_out, stage="harness root discovery")
        if cwd else ""
    )
    if not cwd or not thread_id or not control_root:
        if status_out is not None and status_out.get("status") == REGISTRATION_FAILED:
            return False
        # Report against the payload, not the collapsed identity tuple:
        # `_registration_identity` returns ("", "") when the thread id is
        # missing, discarding a perfectly good cwd. Blaming the cwd sends
        # someone to debug a field that was correct.
        payload_cwd = ""
        try:
            payload_cwd = str((json.loads(payload.decode("utf-8")) or {}).get("cwd") or "")
        except Exception:
            payload_cwd = ""
        missing = (
            "no working directory in the payload" if not payload_cwd
            else "no Codex thread identity for this session" if not thread_id
            else "no harness root above the session directory"
        )
        _record(NOT_APPLICABLE, missing)
        return False
    # An extra binding validator must not erase the production generation.
    # The MCP eager path supplies exact_bind, but the manager still requires
    # the same task/run as the ordinary PostToolUse registration path.
    generation_bound = ensure_fn is ensure
    bound_roots = (_call_with_deadline(
        lambda: _bound_workspace_roots(control_root, thread_id), deadline, None,
        status_out=status_out, stage="workspace binding discovery",
    ) if generation_bound else [])
    if bound_roots is None or len(bound_roots) > 1:
        if status_out is not None and status_out.get("status") == REGISTRATION_FAILED:
            return False
        _record(NOT_APPLICABLE, "conflicting workspace bindings")
        return False
    if bound_roots:
        control_root = bound_roots[0]
    if bind_fn is None:
        bind_fn = (
            _bind_active_task_to_root_session
            if ensure_fn is ensure
            else lambda _root, _thread: True
        )
    # Publish the task binding before potentially expensive rollout discovery.
    # The watcher cannot attest a spawn without this marker, while a missing
    # registration can be retried safely from the next pre-spawn hook.
    if not bool(_call_with_deadline(
        lambda: bind_fn(control_root, thread_id), deadline, False,
        status_out=status_out, stage="current task binding",
    )):
        if status_out is not None and status_out.get("status") == REGISTRATION_FAILED:
            return False
        # No open task to bind to. Spawning a subagent before task_start is
        # ordinary, not broken, so this is not a registration failure.
        _record(NOT_APPLICABLE, "no open task is bound to this session")
        return False
    task_id = ""
    run_id = ""
    if generation_bound:
        binding = _call_with_deadline(
            lambda: resolve_session_task_binding(control_root, thread_id), deadline, {},
            status_out=status_out, stage="current run resolution",
        )
        if status_out is not None and status_out.get("status") == REGISTRATION_FAILED:
            return False
        task_dir = str(binding.get("task_dir") or "")
        task_id = os.path.basename(os.path.normpath(task_dir)) if task_dir else ""
        run_id = str(binding.get("run_id") or "")
        if not task_id or not run_id:
            _record(NOT_APPLICABLE, "no open task is bound to this session")
            return False
    retry_deadline = started + min(
        max(0.0, float(retry_seconds)), max(0.0, float(budget_seconds))
    )
    while True:
        if ensure_fn is ensure:
            restored = _ensure_with_deadline(
                control_root, thread_id, deadline, session_cwd=cwd,
                task_id=task_id, run_id=run_id, status_out=status_out,
            )
        else:
            try:
                restored = ensure_fn(control_root, thread_id)
            except Exception as exc:
                restored = False
                _record(REGISTRATION_FAILED, f"registration publication unavailable ({type(exc).__name__})")
                if status_out is not None:
                    status_out["stage"] = "registration publication"
        if restored:
            _record(REGISTERED, "")
            return True
        if time.monotonic() >= retry_deadline:
            if status_out is not None and status_out.get("status") == REGISTRATION_FAILED:
                return False
            # An attempt was made against a real identity and a real root and
            # did not succeed. This one is a genuine failure.
            _record(
                REGISTRATION_FAILED,
                "registration validation or publication was refused",
            )
            if status_out is not None:
                status_out["stage"] = "registration publication"
            return False
        time.sleep(min(0.05, max(0.0, retry_deadline - time.monotonic())))


_bind_control_writer(restore_watcher_registration)
_bind_control_writer(register_task_result)
del _bind_control_writer
