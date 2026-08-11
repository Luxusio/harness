#!/usr/bin/env python3
"""Codex UserPromptSubmit wrapper: one hook file per event type."""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import time

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))

HOOK_TIMEOUT_SECONDS = 8.0
TOTAL_BUDGET_SECONDS = 7.0
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
    """Detect an ancestor manifest without importing lifecycle or Git code."""
    if not cwd:
        return False
    current = os.path.realpath(cwd)
    nearest_git = ""
    while True:
        probe = current
        try:
            for component in ("doc", "harness", "manifest.yaml"):
                probe = os.path.join(probe, component)
                info = os.lstat(probe)
                if stat.S_ISLNK(info.st_mode):
                    return False
            if stat.S_ISREG(info.st_mode) and (not nearest_git or nearest_git == current):
                return True
        except OSError:
            pass
        if not nearest_git and os.path.lexists(os.path.join(current, ".git")):
            nearest_git = current
        parent = os.path.dirname(current)
        if parent == current:
            return False
        current = parent


def main() -> int:
    deadline = time.monotonic() + TOTAL_BUDGET_SECONDS
    payload = sys.stdin.buffer.read()
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
