#!/usr/bin/env python3
"""Codex PostToolUse wrapper: one hook file per event type."""
from __future__ import annotations

import json
import os
import subprocess
import sys

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))


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


def main() -> int:
    payload = sys.stdin.buffer.read()
    if _tool_name(payload) != "Bash":
        return 0
    try:
        proc = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS_DIR, "tool_routing.py")],
            input=payload,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=3,
            cwd=_payload_cwd(payload),
        )
        hint = (proc.stdout or b"").decode("utf-8", errors="replace").strip()
        if hint:
            sys.stdout.write(json.dumps({
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "additionalContext": hint,
                }
            }))
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
