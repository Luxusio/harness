#!/usr/bin/env python3
"""Codex SessionStart wrapper: one hook file per event type."""
from __future__ import annotations

import os
import json
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


def _run(args: list[str], payload: bytes) -> bytes:
    try:
        proc = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS_DIR, args[0]), *args[1:]],
            input=payload,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=8,
            cwd=_payload_cwd(payload),
        )
        return proc.stdout or b""
    except Exception:
        return b""


def main() -> int:
    payload = sys.stdin.buffer.read()
    chunks: list[str] = []
    commands = [
        ["note_freshness.py", "--from-git", "1", "--quiet"],
        ["verification_gap_check.py"],
    ]
    for command in commands:
        out = _run(command, payload)
        if out:
            chunks.append(out.decode("utf-8", errors="replace").strip())
    context = "\n".join(chunk for chunk in chunks if chunk)
    if context:
        sys.stdout.write(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": context,
            }
        }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
