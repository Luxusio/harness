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

from codex_lifecycle_watcher import ensure
from _lib import (
    find_harness_root,
    read_task_control,
    resolve_active_task_dir,
    task_control_status,
    write_active_marker,
    _bind_control_writer,
)


THREAD_RE = re.compile(r"^[0-9a-fA-F-]{16,80}$")


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
) -> bool:
    """Interrupt the complete registration attempt at its wall-clock budget."""
    return bool(_call_with_deadline(
        lambda: ensure(
            control_root, thread_id,
            session_cwd=session_cwd or control_root,
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
        return cwd, payload_id
    return (cwd, env_id) if THREAD_RE.fullmatch(env_id) else ("", "")


def _bind_active_task_to_root_session(control_root: str, thread_id: str) -> bool:
    """Bind the live default task to the trusted root rollout identity.

    ``task_start`` can run in an MCP host that does not receive the Codex root
    thread id, so it writes the conservative ``default`` marker.  The
    pre-spawn hook does receive that identity.  Promote only a canonical live
    task; never revive a terminal legacy marker.
    """
    task_dir = resolve_active_task_dir(control_root, session_id="default")
    if not task_dir:
        return False
    control = read_task_control(task_dir)
    if task_control_status(task_dir, control) != "open":
        return False
    write_active_marker(control_root, task_dir, session_id=thread_id)
    return True


def restore_watcher_registration(
    payload: bytes,
    *,
    retry_seconds: float = 0.0,
    budget_seconds: float = 0.5,
    ensure_fn: Callable[[str, str], bool] = ensure,
    bind_fn: Callable[[str, str], bool] | None = None,
) -> bool:
    """Restore registration without changing an existing immutable offset.

    A late recovery starts at the then-current rollout offset, so it can attest
    only future subagent starts. It never reconstructs already-finished work.
    """
    started = time.monotonic()
    deadline = started + max(0.0, float(budget_seconds))
    cwd, thread_id = _registration_identity(payload)
    control_root = (
        _call_with_deadline(lambda: find_harness_root(cwd), deadline, "")
        if cwd else ""
    )
    if not cwd or not thread_id or not control_root:
        return False
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
        return False
    retry_deadline = started + min(
        max(0.0, float(retry_seconds)), max(0.0, float(budget_seconds))
    )
    while True:
        if ensure_fn is ensure:
            restored = _ensure_with_deadline(
                control_root, thread_id, deadline, session_cwd=cwd
            )
        else:
            try:
                restored = ensure_fn(control_root, thread_id)
            except Exception:
                restored = False
        if restored:
            return True
        if time.monotonic() >= retry_deadline:
            return False
        time.sleep(min(0.05, max(0.0, retry_deadline - time.monotonic())))


_bind_control_writer(restore_watcher_registration)
del _bind_control_writer
