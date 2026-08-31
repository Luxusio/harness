#!/usr/bin/env python3
"""Codex PreToolUse wrapper: one hook file per event type."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS_DIR)

try:
    from codex_hook_registration import (  # type: ignore
        NOT_APPLICABLE,
        REGISTRATION_FAILED,
        restore_watcher_registration,
    )
except Exception:  # pragma: no cover - registration recovery is best effort
    restore_watcher_registration = None
    NOT_APPLICABLE = "not_applicable"
    REGISTRATION_FAILED = "failed"

try:
    from codex_lifecycle_watcher import registration_host_live  # type: ignore
except Exception:  # pragma: no cover - live-host check is fail-safe below
    registration_host_live = None

try:
    from _lib import (  # type: ignore
        find_harness_root,
        _infer_receipt_lens,
        emit_permission_decision,
        now_iso,
        read_json_diagnostics,
        write_json_diagnostics,
    )
except Exception:  # pragma: no cover - diagnostics must never break the hook
    find_harness_root = None
    now_iso = None
    read_json_diagnostics = None
    write_json_diagnostics = None
    _infer_receipt_lens = None
    emit_permission_decision = None


def _payload_cwd(payload: bytes) -> str | None:
    try:
        data = json.loads(payload.decode("utf-8") or "{}")
    except Exception:
        return None
    if not isinstance(data, dict):
        # See _payload_session_id: independent parse, independent shape check.
        return None
    cwd = data.get("cwd")
    return cwd if isinstance(cwd, str) and os.path.isdir(cwd) else None


def _tool_name(payload: bytes) -> str:
    try:
        data = json.loads(payload.decode("utf-8") or "{}")
    except Exception:
        return ""
    if not isinstance(data, dict):
        # A payload of `null` or `[1,2,3]` parses fine and then raises
        # AttributeError on `.get`, taking the hook's exit code with it.
        return ""
    return str(data.get("tool_name") or data.get("tool") or "")


def _is_subagent_spawn_tool(tool_name: str) -> bool:
    return (tool_name or "").lower() == "collaboration.spawn_agent"


def _spawn_task_name(payload: bytes) -> str:
    try:
        data = json.loads(payload.decode("utf-8") or "{}")
    except Exception:
        return ""
    if not isinstance(data, dict):
        return ""
    for key in ("tool_input", "input", "arguments"):
        value = data.get(key)
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except Exception:
                continue
        if isinstance(value, dict):
            task_name = value.get("task_name")
            if isinstance(task_name, str):
                return task_name
    return ""


def _invalid_review_spawn_name(payload: bytes) -> str:
    """Return guidance when a review-looking spawn cannot bind a receipt."""
    task_name = _spawn_task_name(payload)
    if not task_name or _infer_receipt_lens is None:
        return ""
    tokens = {
        token for token in re.split(r"[:/_\-\s]+", task_name.lower()) if token
    }
    review_like = bool(tokens & {"review", "reviewer"})
    if not review_like:
        return ""
    if _infer_receipt_lens(task_name) in {"review-code", "review-security"}:
        return ""
    return (
        f"Harness cannot bind review spawn task_name={task_name!r} to a receipt lens. "
        "Use code_review_<suffix> or review_code_<suffix> for review-code; "
        "use security_review_<suffix> or review_security_<suffix> for "
        "review-security. The agent was not started; rename and retry."
    )


def _receipt_lens_spawn(payload: bytes) -> bool:
    """Return whether this spawn is expected to produce close-gate evidence."""
    if _infer_receipt_lens is None:
        return False
    lens = _infer_receipt_lens(_spawn_task_name(payload))
    return lens.startswith(("review-", "qa-", "ux-"))


HOOK_TIMEOUT_SECONDS = 5.0
REGISTRATION_BUDGET_SECONDS = 0.5

WATCHER_DIAGNOSTICS_RELPATH = "doc/harness/.watcher-diagnostics.json"

# Bound the error text carried across a size-cap retry, so the value that made
# the record oversize cannot make the retry oversize too.
_REASON_CARRY_CHARS = 500


def _harness_root(payload: bytes) -> str:
    """Resolve the harness root the same way the rest of the runtime does.

    An earlier version walked ancestors looking for any `doc/harness` directory.
    That selects a parent repository when the session runs in a nested project
    that never ran setup, and writes this session's state into someone else's
    tree — the stale-install pollution class. `find_harness_root` applies the
    real marker check.
    """
    cwd = _payload_cwd(payload)
    if not cwd or find_harness_root is None:
        return ""
    try:
        return find_harness_root(cwd) or ""
    except Exception:
        return ""


def _payload_session_id(payload: bytes) -> str:
    try:
        data = json.loads(payload.decode("utf-8") or "{}")
    except Exception:
        return ""
    if not isinstance(data, dict):
        # Defense in depth. `_tool_name` is the guard that actually fires on a
        # scalar payload — main() returns before reaching here — but each of
        # these parses stdin independently, so none of them should assume a
        # caller already checked the shape.
        return ""
    for key in ("session_id", "thread_id"):
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
    # Same fallback `_registration_identity` uses. Without it the documented
    # env-only configuration writes an unattributable record that degrades to
    # age-only scoping.
    return str(os.environ.get("CODEX_THREAD_ID") or "")


def _update_diagnostics(payload: bytes, updates: dict) -> None:
    """Leave the registration result where the MCP control plane can read it.

    Diagnostic only. Nothing written here can authorize a PASS; the close gate
    still reads only hook-owned RECEIPTS.jsonl entries.

    Every record is stamped with the session it describes and when it was
    written. Without that stamp the record is sticky and unscoped: one benign
    pre-task spawn writes `registration_present: false`, and every later session
    in the repo — including Claude sessions that never run this hook and so can
    never clear it — reads that stale record as the live state.
    """
    root = _harness_root(payload)
    if not root or write_json_diagnostics is None:
        return
    path = os.path.join(root, WATCHER_DIAGNOSTICS_RELPATH)
    session_id = _payload_session_id(payload)
    existing = read_json_diagnostics(path) if read_json_diagnostics else {}
    # Carry forward only a record this session already owns. Merging a foreign
    # or unattributable one and then stamping the result as current would
    # relaunder it as live state.
    data = existing if str(existing.get("session_id") or "") == session_id else {}
    data.update(updates)
    data["session_id"] = session_id
    data["updated"] = now_iso() if now_iso is not None else ""
    if write_json_diagnostics(path, data, confine_to=root):
        return
    # A carried-forward record can push the payload past the size cap, and a
    # planted oversize record would then suppress persistence of a genuinely
    # observed failure. The update itself is small; keep it, drop the rest —
    # but carry an observed failure across, or this fallback becomes the very
    # thing `_observed_registration_failure` exists to prevent: a later benign
    # spawn clearing a failure an earlier spawn really saw.
    fresh = {}
    if str(existing.get("session_id") or "") == session_id and (
        existing.get("registration_present") is False
    ):
        fresh["registration_present"] = False
        fresh["last_registration_error"] = str(
            existing.get("last_registration_error") or ""
        )[:_REASON_CARRY_CHARS]
    fresh.update(updates)
    fresh["session_id"] = session_id
    fresh["updated"] = now_iso() if now_iso is not None else ""
    write_json_diagnostics(path, fresh, confine_to=root)


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


def _deny_unrecordable_spawn(reason: str) -> None:
    if emit_permission_decision is None:
        return
    emit_permission_decision(
        "deny",
        "Harness did not start this review/QA agent because its verdict could "
        f"not be recorded: {reason}. Restart or repair the Harness MCP server, "
        "run python3 plugin/scripts/hook_tree_health.py, then retry the lens.",
    )


def _report_registration_not_applicable(payload: bytes, reason: str) -> None:
    """Record 'unknown', not 'failed'.

    Nothing was attempted, so asserting a failure here would fabricate the
    confident-but-unfounded value AC-002 exists to prevent. Equally, nothing
    was attempted means nothing was *disproved*: a later benign spawn must not
    clear a failure an earlier spawn actually observed. Only a successful
    registration clears one.
    """
    updates = {"last_registration_note": reason}
    if not _observed_registration_failure(payload):
        updates["registration_present"] = None
        updates["last_registration_error"] = ""
    _update_diagnostics(payload, updates)


def _observed_registration_failure(payload: bytes) -> bool:
    """Does this session already hold a positively observed failure?"""
    root = _harness_root(payload)
    if not root or read_json_diagnostics is None:
        return False
    record = read_json_diagnostics(os.path.join(root, WATCHER_DIAGNOSTICS_RELPATH))
    if str(record.get("session_id") or "") != _payload_session_id(payload):
        return False
    return record.get("registration_present") is False


def _watcher_host_live(payload: bytes) -> bool:
    if registration_host_live is None:
        return False
    root = _harness_root(payload)
    thread_id = _payload_session_id(payload)
    if not root or not thread_id:
        return False
    try:
        return bool(registration_host_live(root, thread_id))
    except Exception:
        return False


def _clear_registration_failure(payload: bytes) -> None:
    _update_diagnostics(payload, {
        "registration_present": True,
        "last_registration_error": "",
        "last_registration_note": "",
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
        invalid_name = _invalid_review_spawn_name(payload)
        if invalid_name and emit_permission_decision is not None:
            emit_permission_decision("deny", invalid_name)
            return 0
        receipt_lens = _receipt_lens_spawn(payload)
        # Registration stays best-effort — per C-12 this hook must never block
        # the session. What must not stay best-effort is the *result*: an
        # unregistered spawn produces no receipt, and discovering that after
        # review and QA have finished wastes the whole verification pass.
        if restore_watcher_registration is None:
            reason = "codex_hook_registration is unavailable in this hook tree"
            _report_registration_failure(payload, reason)
            if receipt_lens:
                _deny_unrecordable_spawn(reason)
            return 0
        status: dict = {}
        try:
            registered = restore_watcher_registration(
                payload,
                budget_seconds=REGISTRATION_BUDGET_SECONDS,
                status_out=status,
            )
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
            _report_registration_failure(payload, reason)
            if receipt_lens:
                _deny_unrecordable_spawn(reason)
            return 0
        reason = str(status.get("reason") or "")
        if registered:
            if not _watcher_host_live(payload):
                reason = (
                    "watcher registration exists but no live MCP-hosted watcher "
                    "holds its lease"
                )
                _report_registration_failure(payload, reason)
                if receipt_lens:
                    _deny_unrecordable_spawn(reason)
                return 0
            _clear_registration_failure(payload)
        elif status.get("status") == REGISTRATION_FAILED:
            reason = reason or (
                "watcher registration did not complete within "
                f"{REGISTRATION_BUDGET_SECONDS}s"
            )
            _report_registration_failure(payload, reason)
            if receipt_lens:
                _deny_unrecordable_spawn(reason)
        else:
            # NOT_APPLICABLE: not a Codex rollout, no thread identity, or no
            # open task yet. None of those is a fault to report or to gate on.
            _report_registration_not_applicable(payload, reason)
        return 0

    script = ""
    if tool_name in {"Write", "Edit", "MultiEdit", "apply_patch"}:
        script = "prewrite_gate.py"
    if not script:
        return 0

    out = _run(script, payload)
    if out:
        sys.stdout.buffer.write(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
