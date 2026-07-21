#!/usr/bin/env python3
"""Best-effort Codex root-rollout registration from any installed root hook."""
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


THREAD_RE = re.compile(r"^[0-9a-fA-F-]{16,80}$")


class _RegistrationTimeout(Exception):
    pass


def _ensure_with_deadline(cwd: str, thread_id: str, deadline: float) -> bool:
    """Interrupt the complete registration attempt at its wall-clock budget."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return False
    if not hasattr(signal, "setitimer"):
        return ensure(cwd, thread_id, deadline=deadline)

    def _timeout(_signum, _frame):
        raise _RegistrationTimeout()

    try:
        current_timer = signal.getitimer(signal.ITIMER_REAL)
        previous_handler = signal.signal(signal.SIGALRM, _timeout)
    except (AttributeError, ValueError):
        return ensure(cwd, thread_id, deadline=deadline)
    effective_remaining = min(
        remaining,
        current_timer[0] if current_timer[0] > 0 else remaining,
    )
    timer_started = time.monotonic()
    try:
        previous_timer = signal.setitimer(signal.ITIMER_REAL, effective_remaining)
    except (AttributeError, ValueError):
        signal.signal(signal.SIGALRM, previous_handler)
        return ensure(cwd, thread_id, deadline=deadline)
    try:
        return ensure(cwd, thread_id, deadline=deadline)
    except _RegistrationTimeout:
        return False
    except Exception:
        return False
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


def restore_watcher_registration(
    payload: bytes,
    *,
    retry_seconds: float = 0.0,
    budget_seconds: float = 0.5,
    ensure_fn: Callable[[str, str], bool] = ensure,
) -> bool:
    """Restore registration without changing an existing immutable offset.

    A late recovery starts at the then-current rollout offset, so it can attest
    only future subagent starts. It never reconstructs already-finished work.
    """
    cwd, thread_id = _registration_identity(payload)
    if not cwd or not thread_id:
        return False
    started = time.monotonic()
    deadline = started + max(0.0, float(budget_seconds))
    retry_deadline = started + min(
        max(0.0, float(retry_seconds)), max(0.0, float(budget_seconds))
    )
    while True:
        if ensure_fn is ensure:
            restored = _ensure_with_deadline(cwd, thread_id, deadline)
        else:
            try:
                restored = ensure_fn(cwd, thread_id)
            except Exception:
                restored = False
        if restored:
            return True
        if time.monotonic() >= retry_deadline:
            return False
        time.sleep(min(0.05, max(0.0, retry_deadline - time.monotonic())))
