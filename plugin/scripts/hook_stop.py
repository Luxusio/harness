#!/usr/bin/env python3
"""Codex Stop wrapper: one hook file per event type."""
from __future__ import annotations

import os
import json
import subprocess
import sys

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS_DIR)
try:
    from codex_hook_registration import restore_watcher_registration  # type: ignore
except Exception:  # pragma: no cover - hook must fail open
    restore_watcher_registration = None


def _payload_cwd(payload: bytes) -> str | None:
    try:
        data = json.loads(payload.decode("utf-8") or "{}")
    except Exception:
        return None
    cwd = data.get("cwd")
    return cwd if isinstance(cwd, str) and os.path.isdir(cwd) else None


def main() -> int:
    payload = sys.stdin.buffer.read()
    if restore_watcher_registration is not None:
        restore_watcher_registration(payload)
    try:
        # Codex does not auto-resume after a Stop block. If we forward
        # stop_gate.py's block decision, Codex turns it into a hook_prompt that
        # the user must manually answer. Keep the diagnostic run, but fail open.
        proc = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS_DIR, "stop_gate.py")],
            input=payload,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=8,
            cwd=_payload_cwd(payload),
        )
        _ = proc
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
