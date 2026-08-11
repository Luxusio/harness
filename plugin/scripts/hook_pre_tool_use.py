#!/usr/bin/env python3
"""Codex PreToolUse wrapper: one hook file per event type."""
from __future__ import annotations

import json
import os
import subprocess
import sys

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS_DIR)

try:
    from codex_hook_registration import restore_watcher_registration  # type: ignore
except Exception:  # pragma: no cover - registration recovery is best effort
    restore_watcher_registration = None

def _payload_cwd(payload: bytes) -> str | None:
    try:
        data = json.loads(payload.decode("utf-8") or "{}")
    except Exception:
        return None
    cwd = data.get("cwd")
    return cwd if isinstance(cwd, str) and os.path.isdir(cwd) else None


def _tool_name(payload: bytes) -> str:
    try:
        data = json.loads(payload.decode("utf-8") or "{}")
    except Exception:
        return ""
    return str(data.get("tool_name") or data.get("tool") or "")


def _is_subagent_spawn_tool(tool_name: str) -> bool:
    return (tool_name or "").lower() == "collaboration.spawn_agent"


HOOK_TIMEOUT_SECONDS = 5.0
REGISTRATION_BUDGET_SECONDS = 0.5
CHILD_TIMEOUT_SECONDS = 1.5


def _run(script: str, payload: bytes) -> bytes:
    try:
        proc = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS_DIR, script)],
            input=payload,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=CHILD_TIMEOUT_SECONDS,
            cwd=_payload_cwd(payload),
        )
        return proc.stdout or b""
    except Exception:
        return b""


def main() -> int:
    payload = sys.stdin.buffer.read()
    tool_name = _tool_name(payload)
    if _is_subagent_spawn_tool(tool_name):
        if restore_watcher_registration is not None:
            restore_watcher_registration(payload, budget_seconds=REGISTRATION_BUDGET_SECONDS)
        return 0

    script = ""
    if tool_name in {"Write", "Edit", "MultiEdit", "apply_patch"}:
        script = "prewrite_gate.py"
    elif tool_name in {"Bash", "shell"}:
        script = "mcp_bash_guard.py"
    if not script:
        return 0

    out = _run(script, payload)
    if out:
        sys.stdout.buffer.write(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
