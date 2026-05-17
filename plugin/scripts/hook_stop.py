#!/usr/bin/env python3
"""Codex Stop wrapper: one hook file per event type."""
from __future__ import annotations

import os
import subprocess
import sys

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))


def main() -> int:
    payload = sys.stdin.buffer.read()
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
        )
        _ = proc
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
