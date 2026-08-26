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

WATCHER_DIAGNOSTICS_RELPATH = "doc/harness/.watcher-diagnostics.json"


def _diagnostics_path(payload: bytes) -> str:
    cwd = _payload_cwd(payload)
    if not cwd:
        return ""
    root = cwd
    while True:
        if os.path.isdir(os.path.join(root, "doc", "harness")):
            return os.path.join(root, WATCHER_DIAGNOSTICS_RELPATH)
        parent = os.path.dirname(root)
        if parent == root:
            return ""
        root = parent


def _update_diagnostics(payload: bytes, updates: dict) -> None:
    """Leave the registration result where the MCP control plane can read it.

    Diagnostic only. Nothing written here can authorize a PASS; the close gate
    still reads only hook-owned RECEIPTS.jsonl entries.
    """
    path = _diagnostics_path(payload)
    if not path:
        return
    data = {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            loaded = json.load(handle)
        if isinstance(loaded, dict):
            data = loaded
    except Exception:
        data = {}
    data.update(updates)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp, path)
    except Exception:
        pass


def _report_registration_failure(payload: bytes, reason: str) -> None:
    _update_diagnostics(payload, {
        "registration_present": False,
        "last_registration_error": reason,
    })
    sys.stderr.write(
        "[harness] receipt watcher registration failed: "
        f"{reason}. This subagent's start and completion will NOT be recorded "
        "in RECEIPTS.jsonl, so task_verify cannot reach PASS from it. Repair "
        "receipt capability and re-run the lens; do not hand-author receipts.\n"
    )


def _clear_registration_failure(payload: bytes) -> None:
    _update_diagnostics(payload, {
        "registration_present": True,
        "last_registration_error": "",
    })
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
        # Registration stays best-effort — per C-12 this hook must never block
        # the session. What must not stay best-effort is the *result*: an
        # unregistered spawn produces no receipt, and discovering that after
        # review and QA have finished wastes the whole verification pass.
        if restore_watcher_registration is None:
            _report_registration_failure(
                payload, "codex_hook_registration is unavailable in this hook tree",
            )
            return 0
        try:
            registered = restore_watcher_registration(
                payload, budget_seconds=REGISTRATION_BUDGET_SECONDS,
            )
        except Exception as exc:
            _report_registration_failure(payload, f"{type(exc).__name__}: {exc}")
            return 0
        if registered:
            _clear_registration_failure(payload)
        else:
            _report_registration_failure(
                payload,
                "watcher registration did not complete within "
                f"{REGISTRATION_BUDGET_SECONDS}s",
            )
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
