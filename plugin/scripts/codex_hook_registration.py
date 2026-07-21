#!/usr/bin/env python3
"""Best-effort Codex root-rollout registration from any installed root hook."""
from __future__ import annotations

import json
import os
import re
import sys
import time
from typing import Callable

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS_DIR)

from codex_lifecycle_watcher import ensure


THREAD_RE = re.compile(r"^[0-9a-fA-F-]{16,80}$")


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
    ensure_fn: Callable[[str, str], bool] = ensure,
) -> bool:
    """Restore registration without changing an existing immutable offset.

    A late recovery starts at the then-current rollout offset, so it can attest
    only future subagent starts. It never reconstructs already-finished work.
    """
    cwd, thread_id = _registration_identity(payload)
    if not cwd or not thread_id:
        return False
    deadline = time.monotonic() + max(0.0, float(retry_seconds))
    while True:
        try:
            if ensure_fn(cwd, thread_id):
                return True
        except Exception:
            pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
