#!/usr/bin/env python3
"""Best-effort Codex root-rollout registration at startup and before spawn."""
from __future__ import annotations

import json
import os
import re
import signal
import sys
import time
from typing import Callable

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS_DIR)

from codex_lifecycle_watcher import ensure, invalidate_registration
from _lib import (
    active_session_transaction,
    find_harness_root,
    is_codex_task_binding_tool,
    read_active_session_marker,
    read_task_control,
    receipt_stream_transaction,
    resolve_session_task_binding,
    task_control_status,
    write_active_marker,
    write_binding_conflict_fence,
    _bind_control_writer,
)


THREAD_RE = re.compile(r"^[0-9a-fA-F-]{16,80}$")
TASK_RE = re.compile(r"^TASK__[A-Za-z0-9_.-]{1,180}$")


class _RegistrationTimeout(BaseException):
    pass


def _harness_enabled_cwd(cwd: str) -> bool:
    """Return whether ``cwd`` belongs to an explicitly enabled workspace."""
    return bool(find_harness_root(cwd))


def _call_with_deadline(callback, deadline: float, fallback=False):
    """Run in-process registration work inside the remaining hard budget."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return fallback
    if not hasattr(signal, "setitimer"):
        return fallback

    def _timeout(_signum, _frame):
        raise _RegistrationTimeout()

    try:
        current_timer = signal.getitimer(signal.ITIMER_REAL)
        previous_handler = signal.signal(signal.SIGALRM, _timeout)
    except (AttributeError, ValueError):
        return fallback
    effective_remaining = min(
        remaining,
        current_timer[0] if current_timer[0] > 0 else remaining,
    )
    timer_started = time.monotonic()
    try:
        previous_timer = signal.setitimer(signal.ITIMER_REAL, effective_remaining)
    except (AttributeError, ValueError):
        signal.signal(signal.SIGALRM, previous_handler)
        return fallback
    try:
        return callback()
    except _RegistrationTimeout:
        return fallback
    except Exception:
        return fallback
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


def _live_conflicts(control_root: str, marker: dict) -> list[dict]:
    """Return canonical still-open generations from a conflict fence."""
    tasks_root = os.path.realpath(os.path.join(control_root, "doc", "harness", "tasks"))
    found = []
    raw = marker.get("conflicts")
    if not isinstance(raw, list) or len(raw) != 2:
        return found
    for item in raw:
        if not isinstance(item, dict):
            continue
        task_dir = os.path.realpath(str(item.get("task_dir") or ""))
        task_id = str(item.get("task_id") or "")
        run_id = str(item.get("run_id") or "")
        if (
            not TASK_RE.fullmatch(task_id)
            or os.path.dirname(task_dir) != tasks_root
            or os.path.basename(task_dir) != task_id
        ):
            continue
        control = read_task_control(task_dir)
        if task_control_status(task_dir, control) == "open" and control.get("run_id") == run_id:
            found.append({"task_dir": task_dir, "task_id": task_id, "run_id": run_id})
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


def _task_result(payload: bytes) -> tuple[str, str, str]:
    """Extract one successful task result from known Codex hook envelopes."""
    data = _payload_data(payload)
    tool_name = str(data.get("tool_name") or data.get("tool") or "")
    if not is_codex_task_binding_tool(tool_name):
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
    budget_seconds: float = 0.5,
    status_out: dict | None = None,
) -> bool:
    """Bind a successful Harness task result to this exact Codex session."""
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
        with active_session_transaction(control_root):
            with receipt_stream_transaction(canonical_task):
                control = read_task_control(canonical_task)
                if (
                    task_control_status(canonical_task, control) != "open"
                    or control.get("run_id") != run_id
                ):
                    return False
                marker = read_active_session_marker(control_root, thread_id)
                live_conflicts = _live_conflicts(control_root, marker)
                candidate_binding = {
                    "task_dir": canonical_task, "task_id": task_id, "run_id": run_id,
                }
                if len(live_conflicts) >= 2:
                    return False
                if len(live_conflicts) == 1:
                    live = live_conflicts[0]
                    if live != candidate_binding:
                        write_binding_conflict_fence(
                            control_root, thread_id, [live, candidate_binding],
                        )
                        invalidate_registration(control_root, thread_id)
                        return False
                existing = resolve_session_task_binding(control_root, thread_id)
                if existing and os.path.realpath(existing["task_dir"]) != canonical_task:
                    write_binding_conflict_fence(control_root, thread_id, [{
                        "task_dir": os.path.realpath(existing["task_dir"]),
                        "task_id": os.path.basename(existing["task_dir"]),
                        "run_id": existing["run_id"],
                    }, candidate_binding])
                    invalidate_registration(control_root, thread_id)
                    if status_out is not None:
                        status_out.update({
                            "status": NOT_APPLICABLE,
                            "reason": "conflicting open tasks invalidated exact session binding",
                        })
                    return False
                if not existing and not invalidate_registration(control_root, thread_id):
                    return False
                write_active_marker(
                    control_root,
                    canonical_task,
                    session_id=thread_id,
                    publish_legacy=False,
                )
                return restore_watcher_registration(
                    payload,
                    budget_seconds=budget_seconds,
                    status_out=status_out,
                )
    except Exception:
        if status_out is not None:
            status_out.update({"status": REGISTRATION_FAILED, "reason": "exact task binding failed"})
        return False
def restore_watcher_registration(
    payload: bytes,
    *,
    retry_seconds: float = 0.0,
    budget_seconds: float = 0.5,
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
    started = time.monotonic()
    deadline = started + max(0.0, float(budget_seconds))
    cwd, thread_id = _registration_identity(payload)
    if status_out is not None and thread_id:
        status_out["thread_id"] = thread_id
    control_root = (
        _call_with_deadline(lambda: find_harness_root(cwd), deadline, "")
        if cwd else ""
    )
    if not cwd or not thread_id or not control_root:
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
    generation_bound = bind_fn is None and ensure_fn is ensure
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
        lambda: bind_fn(control_root, thread_id), deadline, False
    )):
        # No open task to bind to. Spawning a subagent before task_start is
        # ordinary, not broken, so this is not a registration failure.
        _record(NOT_APPLICABLE, "no open task is bound to this session")
        return False
    task_id = ""
    run_id = ""
    if generation_bound:
        binding = resolve_session_task_binding(control_root, thread_id)
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
                task_id=task_id, run_id=run_id,
            )
        else:
            try:
                restored = ensure_fn(control_root, thread_id)
            except Exception:
                restored = False
        if restored:
            _record(REGISTERED, "")
            return True
        if time.monotonic() >= retry_deadline:
            # An attempt was made against a real identity and a real root and
            # did not succeed. This one is a genuine failure.
            _record(
                REGISTRATION_FAILED,
                "watcher registration did not complete within "
                f"{max(0.0, float(budget_seconds))}s",
            )
            return False
        time.sleep(min(0.05, max(0.0, retry_deadline - time.monotonic())))


_bind_control_writer(restore_watcher_registration)
_bind_control_writer(register_task_result)
del _bind_control_writer
