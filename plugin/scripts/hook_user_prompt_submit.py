#!/usr/bin/env python3
"""Codex UserPromptSubmit wrapper: one hook file per event type."""
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


def main() -> int:
    payload = sys.stdin.buffer.read()
    try:
        proc = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS_DIR, "prompt_memory.py")],
            input=payload,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=3,
            cwd=_payload_cwd(payload),
        )
        context = (proc.stdout or b"").decode("utf-8", errors="replace").strip()
        if context:
            sys.stdout.write(json.dumps({
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": context,
                }
            }))
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
