#!/usr/bin/env python3
"""Codex UserPromptSubmit wrapper: one hook file per event type."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS_DIR)
try:
    from codex_hook_registration import restore_watcher_registration  # type: ignore
except Exception:  # pragma: no cover - hook must fail open
    restore_watcher_registration = None

HOOK_TIMEOUT_SECONDS = 8.0
TOTAL_BUDGET_SECONDS = 7.0
REGISTRATION_BUDGET_SECONDS = 0.5
CHILD_TIMEOUT_SECONDS = 6.0
DEADLINE_MARGIN_SECONDS = 0.1
CODEX_ROUTE = (
    "[harness-route] Repository mutation: invoke $harness:run before edits; "
    "read-only requests: answer directly."
)


def _payload_cwd(payload: bytes) -> str | None:
    try:
        data = json.loads(payload.decode("utf-8") or "{}")
    except Exception:
        return None
    cwd = data.get("cwd")
    return cwd if isinstance(cwd, str) and os.path.isdir(cwd) else None


def _is_harness_repo(cwd: str | None) -> bool:
    """Detect setup from the filesystem only; never invoke Git in a prompt hook."""
    if not cwd:
        return False
    current = os.path.abspath(cwd)
    while True:
        if os.path.isfile(os.path.join(current, "doc", "harness", "manifest.yaml")):
            return True
        parent = os.path.dirname(current)
        if parent == current:
            return False
        current = parent


def main() -> int:
    deadline = time.monotonic() + TOTAL_BUDGET_SECONDS
    payload = sys.stdin.buffer.read()
    if restore_watcher_registration is not None:
        restore_watcher_registration(payload, budget_seconds=REGISTRATION_BUDGET_SECONDS)
    cwd = _payload_cwd(payload)
    harness_repo = _is_harness_repo(cwd)
    context = ""
    remaining = deadline - time.monotonic() - DEADLINE_MARGIN_SECONDS
    try:
        if remaining <= 0:
            raise subprocess.TimeoutExpired("prompt_memory.py", 0)
        proc = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS_DIR, "prompt_memory.py")],
            input=payload,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=min(CHILD_TIMEOUT_SECONDS, remaining),
            cwd=cwd,
            env={**os.environ, "HARNESS_RUNTIME": "codex"},
        )
        context = (proc.stdout or b"").decode("utf-8", errors="replace").strip()
    except Exception:
        pass
    if context or harness_repo:
        context = CODEX_ROUTE + ("\n" + context if context else "")
        sys.stdout.write(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": context,
            }
        }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
