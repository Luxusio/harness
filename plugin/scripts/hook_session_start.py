#!/usr/bin/env python3
"""Codex SessionStart wrapper: one hook file per event type."""
from __future__ import annotations

import os
import json
import subprocess
import sys
import time

_IMPORT_STARTED_AT = time.monotonic()
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS_DIR)

_IMPORT_SECONDS = time.monotonic() - _IMPORT_STARTED_AT
HOOK_TIMEOUT_SECONDS = 10.0

def _payload_data(payload: bytes) -> dict | None:
    try:
        data = json.loads(payload.decode("utf-8") or "{}")
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _payload_cwd(payload: bytes) -> str | None:
    data = _payload_data(payload)
    cwd = data.get("cwd") if data is not None else None
    return cwd if isinstance(cwd, str) and os.path.isdir(cwd) else None


def _run(args: list[str], payload: bytes, *, deadline: float) -> bytes:
    remaining = deadline - time.monotonic() - 0.2
    if remaining <= 0:
        return b""
    try:
        proc = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS_DIR, args[0]), *args[1:]],
            input=payload,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=min(8.0, remaining),
            cwd=_payload_cwd(payload),
        )
        return proc.stdout or b""
    except Exception:
        return b""


def main() -> int:
    deadline = time.monotonic() + max(0.0, HOOK_TIMEOUT_SECONDS - _IMPORT_SECONDS)
    payload = sys.stdin.buffer.read()
    data = _payload_data(payload)
    if data is None:
        return 0
    cwd = _payload_cwd(payload)
    if "cwd" in data and cwd is None:
        return 0
    if "--claude-banner" in sys.argv[1:]:
        try:
            from _lib import find_harness_root, is_harness_enabled_repo
            root = find_harness_root(cwd or os.getcwd())
            if root and is_harness_enabled_repo(root):
                print(
                    f"harness — ready.\nProject: {os.path.basename(root)}\n"
                    "Loop: task start → plan → develop → QA → close\n"
                    "Review and task_verify run as internal close gates.\n"
                    "Auto-routing on. Just describe what you want.\n"
                    "REQ: capture requirements as doc/<area>/REQ__<slug>.md when behavior changes."
                )
        except Exception:
            pass
        return 0
    chunks: list[str] = []
    commands = [
        ["verification_gap_check.py"],
        ["project_format_check.py"],
    ]
    for command in commands:
        out = _run(command, payload, deadline=deadline)
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
