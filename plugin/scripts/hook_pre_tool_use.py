#!/usr/bin/env python3
"""Codex PreToolUse wrapper for task/source write ownership."""
from __future__ import annotations

import json
import os
import subprocess
import sys

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS_DIR)
HOOK_TIMEOUT_SECONDS = 10.0
CHILD_TIMEOUT_SECONDS = 6.0

def _payload_cwd(payload: bytes) -> str | None:
    try:
        data = json.loads(payload.decode("utf-8", errors="surrogateescape") or "{}")
    except Exception:
        return None
    if not isinstance(data, dict):
        # See _payload_session_id: independent parse, independent shape check.
        return None
    cwd = data.get("cwd")
    return cwd if isinstance(cwd, str) and os.path.isdir(cwd) else None


def _tool_name(payload: bytes) -> str:
    try:
        # Every payload parse in this wrapper decodes with surrogateescape, as
        # prewrite_gate does: one invalid byte in a Write's content must not
        # hide the tool name and skip the gate.
        data = json.loads(payload.decode("utf-8", errors="surrogateescape") or "{}")
    except Exception:
        return ""
    if not isinstance(data, dict):
        # A payload of `null` or `[1,2,3]` parses fine and then raises
        # AttributeError on `.get`, taking the hook's exit code with it.
        return ""
    return str(data.get("tool_name") or data.get("tool") or "")


def _run(script: str, payload: bytes) -> tuple[bytes, bool]:
    """Return the child's stdout and whether it exited 0.

    A timeout, a spawn failure, or any other error returns ``(b"", False)``.
    """
    try:
        proc = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS_DIR, script)],
            input=payload,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=CHILD_TIMEOUT_SECONDS,
            cwd=_payload_cwd(payload),
        )
        return proc.stdout or b"", proc.returncode == 0
    except Exception:
        return b"", False


def _protected_artifact_fallback(payload: bytes) -> bytes:
    """Deny C-05 protected-artifact targets after the gate child failed.

    A killed or crashed child prints nothing, which would allow the write. The
    gate's own classifiers decide which targets are protected; everything else
    stays allowed, and any error here allows too (C-12).
    """
    try:
        import prewrite_gate  # type: ignore

        decision = prewrite_gate.protected_artifact_decision(payload)
        return decision.encode("utf-8")
    except (Exception, SystemExit):
        # prewrite_gate calls sys.exit(0) at import when _lib is unavailable.
        return b""


def main() -> int:
    payload = sys.stdin.buffer.read()
    if _tool_name(payload) not in {"Write", "Edit", "MultiEdit", "apply_patch"}:
        return 0
    out, finished = _run("prewrite_gate.py", payload)
    if not out and not finished:
        out = _protected_artifact_fallback(payload)
    if out:
        sys.stdout.buffer.write(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
