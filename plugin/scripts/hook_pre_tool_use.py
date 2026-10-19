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
        _registration_identity,
    )
except Exception:  # pragma: no cover - registration recovery is best effort
    restore_watcher_registration = None
    _registration_identity = None
    NOT_APPLICABLE = "not_applicable"
    REGISTRATION_FAILED = "failed"

try:
    from codex_lifecycle_watcher import registration_host_live, workspace_roots  # type: ignore
except Exception:  # pragma: no cover - live-host check is fail-safe below
    registration_host_live = None
    workspace_roots = None

try:
    from _lib import (  # type: ignore
        find_harness_root,
        resolve_session_task_binding,
        _infer_receipt_lens,
        emit_permission_decision,
        now_iso,
        read_json_diagnostics,
        write_json_diagnostics,
    )
except Exception:  # pragma: no cover - diagnostics must never break the hook
    find_harness_root = None
    resolve_session_task_binding = None
    now_iso = None
    read_json_diagnostics = None
    write_json_diagnostics = None
    _infer_receipt_lens = None
    emit_permission_decision = None


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


def _is_subagent_spawn_tool(tool_name: str) -> bool:
    return (tool_name or "").lower() == "collaboration.spawn_agent"


def _spawn_task_name(payload: bytes) -> str:
    try:
        data = json.loads(payload.decode("utf-8", errors="surrogateescape") or "{}")
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
        data = json.loads(payload.decode("utf-8", errors="surrogateescape") or "{}")
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
        "in RECEIPTS.jsonl, so task_verify cannot reach PASS from it. Continue "
        "substantive review and QA, label unreceipted results non-attesting, "
        "then call wbs(action='verify') once and use wbs(action='block') if required evidence is missing; "
        "do not hand-author receipts.\n"
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
    if any(helper is None for helper in (
        registration_host_live, workspace_roots, resolve_session_task_binding,
        _registration_identity,
    )):
        return False
    root = _harness_root(payload)
    _, thread_id = _registration_identity(payload)
    if not root or not thread_id:
        return False
    try:
        bound = [workspace for workspace in workspace_roots(root)
                 if resolve_session_task_binding(workspace, thread_id)]
        return len(bound) == 1 and bool(registration_host_live(bound[0], thread_id))
    except Exception:
        return False


def _clear_registration_failure(payload: bytes) -> None:
    _update_diagnostics(payload, {
        "registration_present": True,
        "last_registration_error": "",
        "last_registration_note": "",
    })
# The prewrite gate child's budget. The rest of HOOK_TIMEOUT_SECONDS covers
# interpreter start, this wrapper's imports, the kill, and the in-process
# protected-artifact fallback below. HOOK_TIMEOUT_SECONDS itself is set by
# install.py and is part of the Codex hook trust hash.
CHILD_TIMEOUT_SECONDS = 3.0


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
    tool_name = _tool_name(payload)
    if _is_subagent_spawn_tool(tool_name):
        invalid_name = _invalid_review_spawn_name(payload)
        if invalid_name and emit_permission_decision is not None:
            emit_permission_decision("deny", invalid_name)
            return 0
        # Registration stays best-effort — per C-12 this hook must never block
        # the session. Failure is surfaced, but substantive review and QA still
        # run; only attested close remains unavailable.
        if restore_watcher_registration is None:
            reason = "codex_hook_registration is unavailable in this hook tree"
            _report_registration_failure(payload, reason)
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
            return 0
        reason = str(status.get("reason") or "")
        if registered:
            if not _watcher_host_live(payload):
                reason = (
                    "watcher registration exists but no live MCP-hosted watcher "
                    "holds its lease"
                )
                _report_registration_failure(payload, reason)
                return 0
            _clear_registration_failure(payload)
        elif status.get("status") == REGISTRATION_FAILED:
            reason = reason or (
                "watcher registration did not complete within "
                f"{REGISTRATION_BUDGET_SECONDS}s"
            )
            _report_registration_failure(payload, reason)
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

    out, finished = _run(script, payload)
    if not out and not finished:
        out = _protected_artifact_fallback(payload)
    if out:
        sys.stdout.buffer.write(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
