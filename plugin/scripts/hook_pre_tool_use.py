#!/usr/bin/env python3
"""Codex PreToolUse wrapper: one hook file per event type."""
from __future__ import annotations

import json
import os
import subprocess
import sys

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))


def _tool_name(payload: bytes) -> str:
    try:
        data = json.loads(payload.decode("utf-8") or "{}")
    except Exception:
        return ""
    return str(data.get("tool_name") or data.get("tool") or "")


def _run(script: str, payload: bytes) -> bytes:
    try:
        proc = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS_DIR, script)],
            input=payload,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=3,
        )
        return proc.stdout or b""
    except Exception:
        return b""


def main() -> int:
    payload = sys.stdin.buffer.read()
    scripts = ["prewrite_gate.py", "qa_delegation_gate.py"]
    if _tool_name(payload) == "Bash":
        scripts.append("mcp_bash_guard.py")
    for script in scripts:
        out = _run(script, payload)
        if out:
            sys.stdout.buffer.write(out)
            return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
