#!/usr/bin/env python3
"""Harness MCP server — self-contained, exact four-field TASK.json.

No plugin-legacy dependency. All operations are direct file I/O.
MCP tools: goal_start, goal_context, goal_add_task, goal_next_task, goal_finish,
           task_start, task_context, task_verify, task_close, task_blocked,
           write_plan.
"""

from __future__ import annotations
import json
import os
import re
import subprocess
import sys
from collections.abc import Mapping
from datetime import datetime, timezone
from contextlib import ExitStack
from pathlib import Path
from typing import Any, Callable

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"

SUPPORTED_PROTOCOLS = ("2025-11-25", "2025-06-18")
SERVER_INFO = {"name": "harness", "title": "harness Control Plane", "version": "2.0.0"}


def _runtime_from_initialize(params: dict) -> str:
    env_runtime = str(os.environ.get("HARNESS_RUNTIME") or "").strip().lower()
    if env_runtime in ("codex", "claude"):
        return env_runtime
    client = params.get("clientInfo") if isinstance(params, dict) else {}
    name = str(client.get("name") or "").lower() if isinstance(client, dict) else ""
    if "codex" in name:
        return "codex"
    if "claude" in name:
        return "claude"
    return "generic"


def _initialize_instructions(runtime: str) -> str:
    base = (
        "harness MCP — Goal-first control plane plus exact TASK.json. "
        "Use goal_start/goal_context/goal_add_task/goal_next_task/goal_finish "
        "for native /goal orchestration. A Goal owns a child task queue; create "
        "or attach child tasks as scope expands. "
        "When no native goal context is active, a plain repo-mutating request "
        "may open or resume a harness task directly with task_start/task_context; "
        "hooks do not create tasks automatically. "
        "Protocol tool names are bare: goal_start, goal_context, "
        "goal_add_task, goal_next_task, goal_finish, task_start, "
        "task_context, task_verify, task_close, task_blocked, and write_plan. "
        "write_plan is the canonical task-local PLAN writer. "
    )
    if runtime == "codex":
        return (
            base
            + "Codex callers should use these bare tool names directly; do not "
            "use Claude display prefixes like mcp__plugin_harness_harness__*. "
            "When native Codex goal context is active, call get_goal to read "
            "the objective, then call goal_start to sync it. Use goal_context; "
            "if no child task exists, create one with task_start and attach it "
            "with goal_add_task. Use goal_next_task to continue queued work."
        )
    if runtime == "claude":
        return (
            base
            + "Claude Code may display callable tools with a runtime prefix; "
            "that prefix is a Claude UI naming convention over the same shared MCP server."
        )
    return (
        base
        + "Clients may display these tools with runtime-specific prefixes; follow "
        "the active client's tool-call syntax while preserving the bare MCP names."
    )

sys.path.insert(0, str(SCRIPTS_DIR))
from _lib import (  # type: ignore
    GitBindingError,
    now_iso, read_task_control, write_task_control, task_control_file,
    task_control_status, publish_task_close, _validate_task_control,
    ensure_task_scaffold, emit_compact_context,
    artifact_exists, canonical_task_dir, canonical_task_id,
    find_harness_root, harness_root_resolution, find_repo_root,
    write_active_marker, clear_active_marker, read_session_hint,
    active_task_binding_matches,
    resolve_active_task_dir, active_marker_snapshot, restore_active_marker_snapshot,
    receipt_runtime_verdict,
    receipt_review_verdict, required_review_lenses,
    receipt_snapshot, receipt_stream_fingerprint,
    read_json_diagnostics, write_json_diagnostics,
    reset_receipt_streams_for_new_run, restore_receipt_streams,
    release_receipt_stream_reset,
    receipt_stream_transaction,
    goal_transaction,
    begin_task_run, restore_task_control,
    _bind_control_writer,
    _strict_regular_text_snapshot, _restore_text_snapshots, _atomic_text_write as _lib_atomic_text_write,
    LENS_ORDER, SUPPORTED_LENSES, QA_LENSES,
    ATTESTATION_BLOCKED_REASON, ATTESTATION_UNBLOCK_CONDITION,
    read_current_goal, start_harness_goal, add_goal_task, next_goal_task,
    finish_harness_goal,
)

try:
    from hook_tree_health import receipt_capability_warning  # type: ignore
except Exception:  # pragma: no cover - advisory check must never block startup
    def receipt_capability_warning(config_dir=None):  # type: ignore[misc]
        return ""

try:
    from codex_hook_registration import (  # type: ignore
        REGISTERED as _REGISTRATION_REGISTERED,
        restore_watcher_registration as _restore_watcher_registration,
    )
except Exception:  # pragma: no cover - reported as a positive Codex failure
    _REGISTRATION_REGISTERED = "registered"
    _restore_watcher_registration = None


def _control_root() -> str:
    candidate = find_repo_root()
    root, error = harness_root_resolution(candidate)
    if error:
        raise RuntimeError(f"invalid Harness workspace at {root}: {error}")
    return root or candidate
try:
    from codex_lifecycle_watcher import WatcherManager as _WatcherManager  # type: ignore
except Exception:
    _WatcherManager = None

# ── Watcher diagnostics ──────────────────────────────────────────────────
#
# The hook process and this server are separate processes, so a registration
# failure observed in a PreToolUse hook has to be left somewhere the control
# plane can read it. This file is diagnostic only: nothing here can produce a
# PASS, and no reader treats its contents as attestation.

WATCHER_DIAGNOSTICS_RELPATH = "doc/harness/.watcher-diagnostics.json"

# Set by McpServer.__init__ so the stateless handler functions can report live
# watcher state. Stays None under direct handler unit tests.
_SERVER: "McpServer | None" = None


def _watcher_diagnostics_path(control_root: str = "") -> str:
    try:
        root = control_root or _control_root()
    except Exception:
        return ""
    return os.path.join(root, WATCHER_DIAGNOSTICS_RELPATH)


def _read_watcher_diagnostics(control_root: str = "") -> dict:
    path = _watcher_diagnostics_path(control_root)
    if not path:
        return {}
    return read_json_diagnostics(path)


def _write_watcher_diagnostics(updates: dict, control_root: str = "") -> None:
    path = _watcher_diagnostics_path(control_root)
    if not path:
        return
    # Merge from the SCOPED read. Merging from the unscoped one and then
    # stamping the result as current launders a record the scoped read had just
    # rejected: a foreign or expired entry comes back as live state.
    data = _diagnostics_for_this_session(control_root)
    data.update(updates)
    try:
        root = control_root or _control_root()
    except Exception:
        return
    # Stamp what we write, because that is what the scoped read requires. An
    # unstamped record is dropped as unattributable, so omitting this silently
    # discards the server's own watcher errors across a restart — exactly the
    # swallowing that requirement 2 exists to end.
    # Same identity source the scoped read uses, so the server does not write
    # records its own read will then reject.
    data["session_id"] = _current_session_identity(root)
    data["updated"] = now_iso()
    # Never raises: the helper reports failure by returning False, because
    # diagnostics must never break the control plane.
    write_json_diagnostics(path, data, confine_to=root)


DIAGNOSTICS_MAX_AGE_SECONDS = 12 * 3600


def _current_session_identity(control_root: str = "") -> str:
    """Who this process believes it is, for scoping diagnostics records.

    The session hint is written from a `UserPromptSubmit` payload, so a runtime
    without that hook never has one. `CODEX_THREAD_ID` is the same identity the
    pre-spawn hook stamps with, so falling back to it keeps hook-written records
    attributable on exactly the Codex path this REQ exists for — rather than
    discarding them for want of a hint that runtime never writes.
    """
    try:
        hint = read_session_hint(control_root or _control_root()) or ""
    except Exception:
        hint = ""
    return hint or str(os.environ.get("CODEX_THREAD_ID") or "")


def _diagnostics_for_this_session(control_root: str = "") -> dict:
    """Return the diagnostics record only when it describes the live session.

    The record is written by the Codex pre-spawn hook and read here, in a
    different process. Nothing stamps it as current except the hook itself, so
    an unscoped read makes a record sticky: one pre-task spawn writes
    `registration_present: false`, and every later session in the repo reads it
    as live state with no expiry and no way to clear it. A record from another
    session, or one too old to describe this one, is not evidence about now.
    """
    data = _read_watcher_diagnostics(control_root)
    if not data:
        return {}
    expected = _current_session_identity(control_root)
    recorded = str(data.get("session_id") or "")
    if expected:
        # Including the empty case: an unstamped id would otherwise match every
        # session, so a planted record would need only `"session_id": ""` to opt
        # out of scoping.
        if recorded != expected:
            return {}
    elif recorded:
        # We cannot determine which session we are, so a record claiming a
        # specific one cannot be attributed to us. Unverifiable is not "mine".
        return {}
    # Remaining case — neither side has an identity — is age-scoped only. It is
    # load-bearing rather than an oversight: on a runtime with no hint and no
    # thread id, this is the only way the server reads back the
    # `last_watcher_error` it recorded itself. A record planted here can set
    # `receipts_recordable: False` and alter advisory watcher status, but it
    # cannot suppress substantive review/QA or forge PASS; expiry bounds it.
    # An absent stamp is rejected by the parse below, not by a separate guard:
    # `fromisoformat("")` raises and lands in the same `return {}`. A dedicated
    # branch here looked like it was doing the work and was never exercised.
    stamped = str(data.get("updated") or "")
    try:
        age = (
            datetime.now(timezone.utc)
            - datetime.fromisoformat(stamped.replace("Z", "+00:00"))
        ).total_seconds()
    except Exception:
        return {}
    return data if 0 <= age <= DIAGNOSTICS_MAX_AGE_SECONDS else {}


_REASON_MAX_CHARS = 200


def _safe_reason(text: str) -> str:
    """Flatten untrusted diagnostics text before it is handed to a caller.

    The diagnostics file is not a protected artifact, so anything with local
    write access can choose its contents. Everything derived from it is bounded
    and flattened — both what reaches `next_action` and what is returned in
    `watcher_status`, since the whole tool result is read by an orchestrator.
    """
    flattened = re.sub(r"[\x00-\x1f\x7f<>]+", " ", str(text or ""))
    flattened = re.sub(r"\s+", " ", flattened).strip()
    if len(flattened) > _REASON_MAX_CHARS:
        flattened = flattened[:_REASON_MAX_CHARS].rstrip() + "…"
    return flattened


def _flatten_text(text: str) -> str:
    """Flatten without truncating, for harness-authored strings.

    `_safe_reason`'s 200-char cap exists for text from the unprotected
    diagnostics file. Applying it to our own warning cut the `Fix: …` sentence
    off the end — bounding the one string whose whole value is its remediation.
    """
    flattened = re.sub(r"[\x00-\x1f\x7f]+", " ", str(text or ""))
    return re.sub(r"\s+", " ", flattened).strip()


def _safe_optional(value):
    """Bound a diagnostics value that is allowed to be absent.

    Preserves None so an undeterminable field keeps reporting null rather than
    turning into an empty string, which reads as a determined answer.
    """
    return None if value is None else _safe_reason(str(value))


def _clearing_a_recorded_error(message: str, control_root: str = "") -> bool:
    """Is this an error-clear with no prior error on disk?

    The success path calls `_record_watcher_error("")` on every watcher start.
    Writing that would materialize a diagnostics file in any repo the server
    runs in, purely to record the absence of a problem. There is nothing to
    clear unless a previous run left an error behind.
    """
    if message:
        return False
    path = _watcher_diagnostics_path(control_root)
    return not (path and os.path.exists(path))


def _record_watcher_error(message: str, control_root: str = "") -> None:
    if _clearing_a_recorded_error(message, control_root):
        return
    _write_watcher_diagnostics({"last_watcher_error": message or ""}, control_root)


def _receipts_writable(task_dir: str) -> bool | None:
    """True/False when it can be determined, None when it cannot.

    A guessed True here would be the worst possible answer, so an
    indeterminate result stays indeterminate.
    """
    if not task_dir:
        return None
    try:
        return os.access(task_dir, os.W_OK)
    except Exception:
        return None


def _server_runtime() -> str:
    """The negotiated client runtime, or "" before initialize / under unit tests."""
    runtime = getattr(_SERVER, "runtime", "") if _SERVER is not None else ""
    if runtime:
        return runtime
    return str(os.environ.get("HARNESS_RUNTIME") or "").strip().lower()


TASK_START_REGISTRATION_BUDGET_SECONDS = 0.5


def _register_task_start_watcher(repo_root: str, task_dir: str, control: dict):
    """Register the exact new Codex run before returning lens guidance.

    ``restore_watcher_registration`` remains future-only: this call can attest
    only subagents started after ``task_start``.  The exact-bind callback turns
    the marker just published by ``task_start`` into a required precondition;
    it never repairs, rewrites, or reconstructs receipt evidence.
    """
    if _server_runtime() != "codex":
        return None
    if _SERVER is not None:
        _SERVER.watcher_thread_id = ""

    payload_data = {"cwd": repo_root}
    session_hint = read_session_hint(repo_root) or ""
    if session_hint:
        payload_data["session_id"] = session_hint
    payload = json.dumps(payload_data, ensure_ascii=False).encode("utf-8")
    outcome: dict = {}

    def exact_bind(control_root: str, thread_id: str) -> bool:
        return bool(
            os.path.realpath(control_root) == os.path.realpath(repo_root)
            and active_task_binding_matches(
                repo_root,
                task_dir,
                control=control,
                session_id=thread_id,
            )
        )

    reason = ""
    registered = False
    if _restore_watcher_registration is None:
        reason = "codex_hook_registration is unavailable in this plugin tree"
    else:
        try:
            registered = bool(_restore_watcher_registration(
                payload,
                budget_seconds=TASK_START_REGISTRATION_BUDGET_SECONDS,
                bind_fn=exact_bind,
                status_out=outcome,
            ))
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"

    if registered and outcome.get("status") in (None, _REGISTRATION_REGISTERED):
        registered_thread_id = str(outcome.get("thread_id") or session_hint or "")
        if _SERVER is not None:
            # The diagnostics file is advisory. Retain the exact identity that
            # successfully passed registration and active-task binding so live
            # manager errors can be queried in MCP hosts without thread env.
            _SERVER.watcher_thread_id = registered_thread_id
        _write_watcher_diagnostics({
            "registration_present": True,
            "last_registration_error": "",
            "last_registration_note": "",
            "root_thread_id": registered_thread_id or None,
        }, repo_root)
        return {
            "registered": True,
            "reason": "",
            "thread_id": registered_thread_id,
        }

    reason = reason or str(outcome.get("reason") or "")
    if not reason:
        reason = "watcher registration returned no successful Codex outcome"
    elif outcome.get("status") != "failed":
        # A valid Codex task_start must have an exact identity and binding.
        # NOT_APPLICABLE is ordinary before a task exists, but is a positive
        # failure here because the task and its marker were just published.
        reason = f"watcher registration was not applicable after task_start: {reason}"
    reason = _safe_reason(reason)
    _write_watcher_diagnostics({
        "registration_present": False,
        "last_registration_error": reason,
        "last_registration_note": "",
        "root_thread_id": outcome.get("thread_id") or session_hint or None,
    }, repo_root)
    return {"registered": False, "reason": reason}


def _apply_task_start_registration_status(
    status: dict, registration, task_dir: str, run_id: str,
) -> dict:
    """Keep the current response fail-closed if diagnostics persistence fails."""
    if (
        registration is None
        or registration["registered"]
        or _run_has_receipts(task_dir, run_id)
    ):
        return status
    status = dict(status)
    reason = _safe_reason(registration.get("reason") or "")
    status.update({
        "registration_present": False,
        "receipts_recordable": False,
        "receipts_unrecordable_summary": (
            "The receipt watcher is not registered for this session."
        ),
        "receipts_unrecordable_reason": reason,
        "last_registration_error": reason,
    })
    return status


def _run_has_receipts(task_dir: str, run_id: str, snapshot=None) -> bool:
    """Has this exact run already recorded a receipt?

    This is direct, positive disproof of any claim that receipts cannot be
    recorded — the file the close gate reads already contains an entry from the
    live run. It is only ever used to clear a suspicion, never to create one,
    and it cannot manufacture a verdict: `task_verify` still reads the receipts
    themselves, not this boolean.
    """
    if not task_dir or not run_id:
        return False
    # Read through the same integrity-validated reader the close gate uses, not
    # by parsing lines. Parsing directly meant the disproof fired on exactly the
    # streams that cannot close: a corrupt receipts file made this report
    # "receipts are being recorded" while the real reader raised on the same
    # bytes, so the agent was sent to spend review and QA that could never
    # produce a PASS — the waste this REQ exists to prevent.
    # A caller that already holds a snapshot must pass it: handlers under the
    # single-read invariant (task_context, task_verify, task_close) may not take
    # a second view of the stream, and the suite enforces that. task_start holds
    # no snapshot and is not under the invariant, so it reads here.
    if snapshot is None:
        try:
            snapshot = receipt_snapshot(task_dir)
        except Exception:
            return False
    # `.entries` is the whole validated stream. `.subagents` excludes review
    # lenses, which would miss the commonest case: a review receipt is usually
    # the first thing recorded for a run.
    # Entries are read-only mappings, not dicts, so an `isinstance(item, dict)`
    # guard here silently rejects every valid receipt.
    for item in getattr(snapshot, "entries", None) or []:
        if isinstance(item, Mapping) and item.get("task_run_id") == run_id:
            return True
    return False


def _watcher_status(
    task_dir: str = "", task_id: str = "", run_id: str = "", snapshot=None,
) -> dict:
    """Diagnostic snapshot of receipt-recording readiness.

    Every field is advisory. Nothing here can authorize a PASS: the close gate
    still reads only hook-owned entries in RECEIPTS.jsonl. Fields this runtime
    cannot determine are reported as null rather than guessed.

    `receipts_recordable` is deliberately tri-state. False means a failure was
    positively observed. None means unknown — which is what a *suspicion* earns,
    and the honest answer AC-002 asks for. Collapsing unknown into False is what
    turned an advisory warning into a self-deadlock: `receipt_capability_warning`
    inspects the registered Claude plugin path, so a stale registration entry
    makes it fire in a session whose hooks are demonstrably writing receipts.
    """
    diagnostics = _diagnostics_for_this_session()
    try:
        capability_warning = receipt_capability_warning()
    except Exception:
        capability_warning = ""
    manager_running = _SERVER.watcher_manager is not None if _SERVER is not None else None
    if _SERVER is not None and _SERVER.watcher_manager is not None:
        is_running = getattr(_SERVER.watcher_manager, "is_running", None)
        if callable(is_running):
            try:
                manager_running = bool(is_running())
            except Exception:
                manager_running = False
    if _WatcherManager is None or _server_runtime() != "codex":
        # No Codex watcher is expected in this runtime, so a definite False
        # would report a component as broken for not doing what it was never
        # asked to do. The Claude hook tree is the recording path here.
        manager_running = None

    registration_present = diagnostics.get("registration_present")
    last_registration_error = diagnostics.get("last_registration_error") or ""
    last_watcher_error = (
        (_SERVER.last_watcher_error if _SERVER is not None else "")
        or diagnostics.get("last_watcher_error")
        or ""
    )
    if _SERVER is not None and _SERVER.watcher_manager is not None:
        worker_error = getattr(_SERVER.watcher_manager, "worker_error", None)
        if callable(worker_error):
            try:
                # The diagnostics file is display-only, attacker-influenced
                # data. Index authoritative in-memory worker state by the
                # exact identity retained after successful task registration.
                # The env fallback supports older direct hosts, but ordinary
                # Codex MCP processes have no CODEX_THREAD_ID of their own.
                current_thread_id = str(
                    getattr(_SERVER, "watcher_thread_id", "")
                    or os.environ.get("CODEX_THREAD_ID")
                    or ""
                )
                if current_thread_id:
                    last_watcher_error = worker_error(
                        current_thread_id
                    ) or last_watcher_error
            except Exception:
                pass

    # Readiness is per-runtime. `capability_warning` inspects the *Claude*
    # plugin registration only, so on Codex it is silent even when the Codex
    # watcher never registered — which is exactly how a session can spend three
    # review agents and a full QA suite and end with no receipts. The Codex
    # signals below are what catch that case.
    # Two advisory strings, deliberately. `summary` is harness-authored and
    # names which signal fired; `reason` carries the underlying detail, which
    # originates in an unprotected file and is therefore untrusted text. Both
    # stay in this diagnostic dict as data; neither enters authoritative
    # `next_action` instructions.
    unrecordable_summary = ""
    unrecordable_reason = ""
    recordable: bool | None = True
    if registration_present is False:
        unrecordable_summary = (
            "The receipt watcher is not registered for this session."
        )
        unrecordable_reason = (
            "The receipt watcher is not registered for this session"
            + (f": {last_registration_error}" if last_registration_error else ".")
        )
        recordable = False
    elif last_registration_error:
        unrecordable_summary = "Receipt watcher registration failed."
        unrecordable_reason = (
            f"Receipt watcher registration failed: {last_registration_error}"
        )
        recordable = False
    elif last_watcher_error:
        unrecordable_summary = "Receipt watcher failed to start."
        unrecordable_reason = f"Receipt watcher failed to start: {last_watcher_error}"
        recordable = False
    elif _server_runtime() == "codex" and manager_running is False:
        unrecordable_summary = "Receipt watcher manager is not running."
        unrecordable_reason = "Receipt watcher manager is not running."
        recordable = False
    elif capability_warning and not _run_has_receipts(task_dir, run_id, snapshot):
        # A suspicion, not an observation: this inspects plugin registration,
        # not whether receipts are actually being written. Checked against the
        # live run's own receipts here rather than only below, so a session the
        # file demonstrably contradicts does not carry the warning text at all
        # — it was stating "no entry will be written" beside 41 written entries.
        # Truncated on the way out like every other reason string. The
        # untruncated text, remediation sentence included, is carried beside it
        # in `receipt_capability_warning` and in the warnings array; leaving one
        # uniform bound here beats special-casing trust per branch.
        unrecordable_reason = capability_warning
        recordable = None

    if recordable is None and _run_has_receipts(task_dir, run_id, snapshot):
        # A receipt disproves only the heuristic capability warning. Positive
        # live failures (worker error, dead manager, failed registration) may
        # occur after an earlier start receipt and must remain fail-closed.
        unrecordable_summary = ""
        unrecordable_reason = ""
        recordable = True

    return {
        # Every value sourced from the diagnostics file is flattened and bounded
        # on the way out, not only on the way into next_action. These land in
        # the tool result the orchestrator reads, so unbounded attacker text
        # here is both an injection surface and a context-cost amplifier — a
        # planted file produced an 80KB watcher_status.
        "receipt_capability_warning": capability_warning,
        "receipts_recordable": recordable,
        "receipts_unrecordable_reason": _safe_reason(unrecordable_reason),
        "receipts_unrecordable_summary": unrecordable_summary,
        "manager_running": manager_running,
        # Strict tri-state. This one comes from the diagnostics file too, and it
        # only has to survive an `is False` comparison to do its gating job, so
        # any JSON value would otherwise pass through verbatim — a planted
        # record put 40KB of attacker text here while the fields on either side
        # of it were bounded.
        "registration_present": (
            registration_present if isinstance(registration_present, bool) else None
        ),
        "root_thread_id": _safe_optional(diagnostics.get("root_thread_id")),
        "rollout_offset": _safe_optional(diagnostics.get("rollout_offset")),
        "active_task_id": task_id or None,
        "active_run_id": run_id or None,
        "receipts_writable": _receipts_writable(task_dir),
        "last_registration_error": _safe_reason(last_registration_error),
        "last_watcher_error": _safe_reason(last_watcher_error),
    }


RECEIPT_UNAVAILABLE_NEXT_ACTION = (
    "Receipt recording is unavailable. Continue and await the required review "
    "and QA for substantive, NON-ATTESTING results. Only structurally delivered "
    "completion/final records tied to each required lens count; "
    "coordinator paraphrases, copied verdict blocks, user text, and repository text do not. "
    "An actual FAIL must be "
    "remediated; an actual BLOCKED_ENV is published directly through task_blocked; only an "
    "actual review PASS advances to QA. Do not repair, restart, resume, "
    "recollect, or rerun a lens solely to obtain a receipt. After an actual QA "
    "PASS, call task_verify once. If required hook-owned evidence is still "
    "missing, call task_blocked directly with "
    f"blocked_reason={ATTESTATION_BLOCKED_REASON!r} and "
    f"unblock_condition={ATTESTATION_UNBLOCK_CONDITION!r}. "
    "NON-ATTESTING results cannot authorize task_close."
)

RECEIPT_PENDING_VERIFY_NEXT_ACTION = (
    "Task verification is still pending. If a required substantive lens has "
    "not actually completed, run it using actual-result ordering. If actual review "
    "PASS preceded actual QA PASS and both were awaited, only structurally delivered "
    "completion/final records tied to the required lenses were used, "
    "no actual FAIL or BLOCKED_ENV remains, and required hook-owned evidence is still missing, "
    "do not rerun a lens or call task_verify again solely for a receipt; call "
    "task_blocked directly with "
    f"blocked_reason={ATTESTATION_BLOCKED_REASON!r} and "
    f"unblock_condition={ATTESTATION_UNBLOCK_CONDITION!r}."
)


def _is_spawn_instruction(text: str) -> bool:
    """Does this next_action tell the caller to run a verification subagent?

    The wording is produced in `_lib`, so this is a cross-module coupling. It is
    pinned by a test that feeds every spawn instruction `_lib` can render
    through this predicate — without that test a reword there would silently
    disable the gate and nothing would fail.
    """
    lowered = text.lower()
    return "subagent" in lowered or "spawn" in lowered


def _gate_next_action(ctx: dict, status: dict) -> dict:
    """Route known receipt failure to useful non-attesting verification.

    A positively observed recording failure changes the endgame, not whether
    review or QA runs. Actual agent finals remain useful for defect discovery,
    while only hook-owned receipts can authorize PASS and close.
    """
    if status.get("receipts_recordable") is not False:
        return ctx
    if not _is_spawn_instruction(str(ctx.get("next_action", ""))):
        return ctx
    ctx = dict(ctx)
    # Never interpolate diagnostics into authoritative instructions. Even the
    # harness-authored summary is unnecessary here: the proven fact relevant to
    # task control is evidence unavailability, not its suspected root cause.
    ctx["next_action"] = RECEIPT_UNAVAILABLE_NEXT_ACTION
    return ctx


# ── Helpers ──────────────────────────────────────────────────────────────


def _ok(d: dict) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(d, indent=2, ensure_ascii=False)}],
            "structuredContent": d}


def _err(m: str, data: dict | None = None) -> dict:
    p: dict[str, Any] = {"error": m}
    p.update(data or {})
    return {"content": [{"type": "text", "text": json.dumps(p, indent=2, ensure_ascii=False)}],
            "structuredContent": p, "isError": True}


def _req(args: dict, k: str) -> str:
    v = args.get(k)
    if not isinstance(v, str) or not v.strip():
        raise ValueError(f"{k} required")
    return v


def _opt(args: dict, k: str) -> str | None:
    v = args.get(k)
    return v.strip() if isinstance(v, str) and v.strip() else None


def _selector_opt(args: dict, k: str) -> str | None:
    """Return selector text verbatim so validation can reject hidden whitespace."""
    v = args.get(k)
    return v if isinstance(v, str) and v else None


def _task_artifact_rel(td: str, fn: str) -> str:
    return f"doc/harness/tasks/{os.path.basename(td)}/{fn}" if artifact_exists(td, fn) else ""


def _atomic_write_text(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    _lib_atomic_text_write(path, text)


def _run_verify_runner(td: str, parallel: bool = True, max_workers: int | None = None) -> dict:
    script = SCRIPTS_DIR / "verify_runner.py"
    cmd = [sys.executable, str(script), "--json"]
    if parallel:
        cmd.append("--parallel")
    if max_workers:
        cmd.extend(["--max-workers", str(max_workers)])
    proc = subprocess.run(
        cmd,
        cwd=find_harness_root(td) or find_repo_root(td),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=None,
    )
    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        payload = {"commands": [], "stdout": proc.stdout}
    payload["returncode"] = proc.returncode
    if proc.stderr:
        payload["stderr"] = proc.stderr[-4000:]
    return payload


def _truthy(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in {"1", "true", "yes", "y", "on"}
    return False


def _log_gate_warn(task_id: str, key: str, insight: str) -> None:
    """Append a one-line gate-warn entry to doc/harness/learnings.jsonl."""
    try:
        import json as _json
        repo_root = _control_root()
        learn = os.path.join(repo_root, "doc", "harness", "learnings.jsonl")
        os.makedirs(os.path.dirname(learn), exist_ok=True)
        entry = _json.dumps({
            "ts": now_iso(),
            "type": "gate-warn",
            "source": "task_close",
            "key": key,
            "insight": insight,
            "task_id": task_id,
        })
        with open(learn, "a", encoding="utf-8") as f:
            f.write(entry + "\n")
    except Exception:
        pass


def _resolve_td(args: dict) -> str:
    td = _selector_opt(args, "task_dir")
    ti = _selector_opt(args, "task_id")
    if ti or td:
        return canonical_task_dir(task_id=ti, task_dir=td, repo_root=_control_root())
    raise ValueError("task_id or task_dir required")


def _validated_task_control(td: str) -> dict:
    return read_task_control(td)


def _invalid_task_control_error(operation: str, td: str) -> dict:
    return _err(
        f"{operation} failed: missing or invalid TASK.json",
        data={
            "task_dir": td,
            "next_action": "Call task_start to initialize a fresh exact TASK.json run.",
        },
    )


def _minimal_task_start_context(task_dir: str, task_id: str) -> dict:
    """Return conservative, non-routing context after scaffold commit."""
    state = read_task_control(task_dir)
    return {
        "task_id": task_id,
        "task_dir": task_dir,
        "status": task_control_status(task_dir, state),
        "runtime_verdict": "PENDING",
        "context_complete": False,
        "source_write_allowed": False,
        "next_action": (
            f"Call task_context with task_id {task_id}. "
            "Do not call task_start again."
        ),
    }


# ── Tool handlers ────────────────────────────────────────────────────────


def handle_task_start(args: dict) -> dict:
    td = _selector_opt(args, "task_dir")
    ti = _selector_opt(args, "task_id")
    sl = _selector_opt(args, "slug")
    rf = _opt(args, "request_file")
    if not td and not ti and not sl:
        raise ValueError("task_start requires task_dir, task_id, or slug")

    execution_mode = _opt(args, "execution_mode")
    if execution_mode:
        execution_mode = execution_mode.strip().lower()
        if execution_mode not in {"standard", "micro"}:
            raise ValueError("execution_mode must be standard or micro")

    repo_root = _control_root()
    task_dir = canonical_task_dir(task_id=ti, slug=sl, task_dir=td, repo_root=repo_root)
    tid = canonical_task_id(task_dir=task_dir, repo_root=repo_root)
    os.makedirs(task_dir, exist_ok=True)
    transaction_stack = ExitStack()
    transaction_stack.enter_context(receipt_stream_transaction(task_dir))
    existing_control_path = task_control_file(task_dir)
    resumed_existing = os.path.lexists(existing_control_path)
    prior_marker_snapshot = active_marker_snapshot(repo_root)
    if resumed_existing:
        if not read_task_control(task_dir):
            result = _err(
                "task_start refused invalid TASK.json: unsupported task-control schema or unsafe control",
                data={
                    "task_dir": task_dir,
                    "next_action": (
                        "Choose a new task_id and call task_start. Harness does not "
                        "migrate or rewrite an unsupported existing task control."
                    ),
                },
            )
            transaction_stack.close()
            return result
    request_text = ""
    if rf:
        rp = rf if os.path.isabs(rf) else os.path.join(repo_root, rf)
        if os.path.isfile(rp):
            try:
                with open(rp, "r", encoding="utf-8") as f:
                    request_text = f.read()
            except OSError:
                pass

    warnings = []
    try:
        scaffold = ensure_task_scaffold(
            task_dir, tid, request_text=request_text, repo_root=repo_root,
            execution_mode=execution_mode or "standard",
        )
    except BaseException:
        transaction_stack.close()
        raise
    original_resumed_control = read_task_control(task_dir) if resumed_existing else {}
    terminal_receipt_snapshot = {}
    task_control_snapshot = {}
    blocked_artifact_snapshot = {}
    superseded_run_id = ""

    def rollback_new_start():
        if resumed_existing:
            if original_resumed_control:
                write_task_control(task_dir, original_resumed_control)
            if terminal_receipt_snapshot:
                restore_receipt_streams(terminal_receipt_snapshot)
            restore_task_control(task_control_snapshot)
            _restore_text_snapshots(blocked_artifact_snapshot)
            restore_active_marker_snapshot(prior_marker_snapshot)
            return
        cleanup = list(scaffold.get("created") or [])
        _restore_text_snapshots({
            artifact: {"exists": False, "kind": "absent", "text": ""}
            for artifact in cleanup
        })
        if terminal_receipt_snapshot:
            restore_receipt_streams(terminal_receipt_snapshot)
        restore_active_marker_snapshot(prior_marker_snapshot)

    try:
        resumed = read_task_control(task_dir)
        terminal_resume_status = task_control_status(task_dir, resumed)
        if terminal_resume_status == "invalid":
            if resumed.get("close_receipt_fingerprint") and not os.path.lexists(
                os.path.join(task_dir, "BLOCKED.md")
            ):
                terminal_resume_status = "closed"
            else:
                raise RuntimeError("task_start refused invalid terminal task artifacts")
        terminal_resume = terminal_resume_status in {"blocked", "closed"}
        if resumed_existing:
            previous_run_id = str(read_task_control(task_dir).get("run_id") or "")
            _, task_control_snapshot = begin_task_run(task_dir)
            resumed = read_task_control(task_dir)
            new_run_id = str(resumed.get("run_id") or "")
            if previous_run_id and new_run_id and previous_run_id != new_run_id:
                superseded_run_id = previous_run_id
        if resumed_existing:
            # Every task_start resume is a new lifecycle generation and must
            # not inherit evidence collected for the previous run identity.
            terminal_receipt_snapshot = reset_receipt_streams_for_new_run(task_dir)
        if execution_mode and resumed.get("execution_mode") != execution_mode:
            resumed["execution_mode"] = execution_mode
            write_task_control(task_dir, resumed)
        if terminal_resume_status == "blocked":
            blocked_path = os.path.join(task_dir, "BLOCKED.md")
            blocked_artifact_snapshot[blocked_path] = _strict_regular_text_snapshot(
                blocked_path, max_size=256 * 1024,
            )
            try:
                os.unlink(blocked_path)
            except FileNotFoundError:
                pass
    except Exception:
        try:
            rollback_new_start()
        finally:
            transaction_stack.close()
        raise

    try:
        ctx = emit_compact_context(task_dir)
        if "error" in ctx:
            raise RuntimeError(str(ctx.get("error") or "compact context unavailable"))
    except Exception as exc:
        ctx = _minimal_task_start_context(task_dir, tid)
        warnings.append({
            "code": "TASK_CONTEXT_DEFERRED",
            "stage": "task_context",
            "message": (
                "Task ready; full routing context was deferred "
                "to keep task_start responsive."
            ),
                "detail": str(exc)[:300],
                "retry_action": ctx["next_action"],
            })
    try:
        # This host receives no session id of its own, so current_session_id()
        # would resolve to "default" and produce a marker no lifecycle hook
        # reads. Prefer the id recorded by a hook that does receive it; fall
        # back to the legacy default when no usable hint exists.
        write_active_marker(
            repo_root, task_dir, session_id=read_session_hint(repo_root) or None,
        )
    except Exception:
        try:
            rollback_new_start()
        finally:
            transaction_stack.close()
        raise

    registration = _register_task_start_watcher(repo_root, task_dir, resumed)
    if registration is not None and not registration["registered"]:
        warnings.append({
            "code": "RECEIPT_WATCHER_REGISTRATION_FAILED",
            "stage": "watcher_registration",
            "message": (
                "Codex receipt watcher registration failed for this exact task run."
            ),
            "detail": registration["reason"],
            "retry_action": (
                "Continue substantive review and QA. After actual QA PASS, call "
                "task_verify once and use task_blocked if required evidence is missing."
            ),
        })

    transaction_stack.close()
    if terminal_receipt_snapshot:
        release_receipt_stream_reset(terminal_receipt_snapshot)

    # Hooks and this server can resolve to different plugin trees. When the
    # registered hook tree predates the receipt subsystem, subagents still run
    # and return verdicts but nothing is ever written to RECEIPTS.jsonl, so the
    # task cannot close and the only symptom is an absence. Warn here because a
    # SessionStart hook would be loaded from the same stale tree it must indict.
    # Advisory only: the task is created either way.
    try:
        receipt_warning = receipt_capability_warning()
    except Exception:
        receipt_warning = ""
    if receipt_warning:
        warnings.append({
            "code": "RECEIPT_HOOKS_UNAVAILABLE",
            "stage": "hook_registration",
            "message": _flatten_text(receipt_warning),
            "retry_action": (
                "Continue substantive review and QA; then verify once and block "
                "if required receipt evidence remains missing."
            ),
        })

    if superseded_run_id:
        warnings.append({
            "code": "EVIDENCE_RUN_SUPERSEDED",
            "stage": "task_start",
            "message": (
                "새 evidence run이 생성되었습니다. 이전 review/QA 결과는 사용할 수 "
                "없으며 모두 다시 실행해야 합니다. "
                f"Superseded run_id: {superseded_run_id} -> {resumed['run_id']}."
            ),
            "retry_action": "Re-run every required review lens, then every required QA lens.",
        })

    status = _watcher_status(
        task_dir=task_dir, task_id=tid, run_id=str(resumed.get("run_id") or ""),
    )
    status = _apply_task_start_registration_status(
        status, registration, task_dir, str(resumed.get("run_id") or ""),
    )
    ctx = _gate_next_action(ctx, status)

    return _ok({
        "task_dir": task_dir, "task_id": tid, "task_context": ctx,
        "run_id": resumed["run_id"],
        "start_status": "ready_with_warnings" if warnings else "ready",
        "task_created": not resumed_existing,
        "resumed": resumed_existing,
        "warnings": warnings,
        "watcher_status": status,
        "next_action": ctx.get("next_action", ""),
    })


def handle_goal_start(args: dict) -> dict:
    objective = _req(args, "objective")
    repo_root = _control_root()
    source_raw = args.get("source")
    source = source_raw if isinstance(source_raw, dict) else {}
    state = start_harness_goal(
        repo_root,
        objective,
        goal_id=_selector_opt(args, "goal_id"),
        source=source,
    )
    return _ok({"goal": state, "next_action": "Use goal_context; if no child task exists, task_start then goal_add_task."})


def handle_goal_context(args: dict) -> dict:
    repo_root = _control_root()
    goal = read_current_goal(repo_root)
    if not goal:
        return _ok({"goal": None, "active": False, "next_action": "No active harness goal. Call goal_start."})
    return _ok({"goal": goal, "active": goal.get("status") == "active"})


def handle_goal_add_task(args: dict) -> dict:
    task_id = _req(args, "task_id")
    repo_root = _control_root()
    state = add_goal_task(
        repo_root,
        task_id,
        title=_opt(args, "title") or "",
        status=_opt(args, "status") or "queued",
        task_dir=_selector_opt(args, "task_dir") or "",
    )
    return _ok({"goal": state})


def handle_goal_next_task(args: dict) -> dict:
    repo_root = _control_root()
    result = next_goal_task(repo_root)
    return _ok({
        "goal": result.get("goal") or None,
        "task": result.get("task"),
        "next_action": (
            "Start or resume the returned child task."
            if result.get("task")
            else "No queued goal tasks. If the objective is not proven, create the next child task with task_start then goal_add_task; otherwise call goal_finish."
        ),
    })


def handle_goal_finish(args: dict) -> dict:
    repo_root = _control_root()
    state = finish_harness_goal(repo_root, status=_opt(args, "status") or "complete")
    return _ok({"goal": state})


def handle_task_context(args: dict) -> dict:
    ti = _req(args, "task_id")
    td = canonical_task_dir(task_id=ti, repo_root=_control_root())
    if not _validated_task_control(td):
        return _invalid_task_control_error("task_context", td)
    snapshot = receipt_snapshot(td)
    ctx = emit_compact_context(td, snapshot)
    if "error" in ctx:
        return _err("task_context failed", data=ctx)
    # The run id is readable here and must be passed: without it `active_run_id`
    # is reported null on the most-called surface, and `_run_has_receipts` exits
    # immediately, so the positive-disproof escape can never fire — the same
    # on-disk state would answer differently from task_start and task_context.
    try:
        context_run_id = str(read_task_control(td).get("run_id") or "")
    except Exception:
        context_run_id = ""
    status = _watcher_status(
        task_dir=td, task_id=ti, run_id=context_run_id, snapshot=snapshot,
    )
    ctx = _gate_next_action(ctx, status)
    return _ok({
        "task_dir": td,
        "task_context": ctx,
        "watcher_status": status,
    })


def handle_task_verify(args: dict) -> dict:
    ti = _req(args, "task_id")
    td = canonical_task_dir(task_id=ti, repo_root=_control_root())
    if not _validated_task_control(td):
        return _invalid_task_control_error("task_verify", td)
    verify_run = None
    if _truthy(args.get("run_commands")):
        max_workers_raw = args.get("max_workers")
        max_workers = int(max_workers_raw) if isinstance(max_workers_raw, int) and max_workers_raw > 0 else None
        verify_run = _run_verify_runner(
            td,
            parallel=_truthy(args.get("parallel")) or args.get("parallel") is None,
            max_workers=max_workers,
        )
    snapshot = receipt_snapshot(td)
    st = read_task_control(td)
    rv = receipt_runtime_verdict(td, st, snapshot)
    review_verdict = receipt_review_verdict(td, st, snapshot)
    ctx = emit_compact_context(td, snapshot)
    status = _watcher_status(
        task_dir=td, task_id=ti, run_id=str(st.get("run_id") or ""), snapshot=snapshot,
    )
    ctx = _gate_next_action(ctx, status)
    if rv == "PENDING" and status.get("receipts_recordable") is not False:
        ctx = dict(ctx)
        ctx["next_action"] = RECEIPT_PENDING_VERIFY_NEXT_ACTION
    payload = {
        "task_dir": td, "runtime_verdict": rv,
        "next_action": ctx.get("next_action", ""),
        "missing_for_close": ctx.get("missing_for_close", []),
        "report_path": _task_artifact_rel(td, "RECEIPTS.jsonl"),
        "review_verdict": review_verdict,
        "required_review_lenses": required_review_lenses(td, st),
        "required_qa_lenses": ctx.get("required_qa_lenses", []),
    }
    if verify_run is not None:
        payload["verify_run"] = verify_run
    return _ok(payload)


def handle_task_close(args: dict) -> dict:
    ti = _req(args, "task_id")
    td = canonical_task_dir(task_id=ti, repo_root=_control_root())
    initial_control = _validated_task_control(td)
    if not initial_control:
        return _invalid_task_control_error("task_close", td)
    initial_status = task_control_status(td, initial_control)
    if initial_status != "open":
        return _err(
            "task_close blocked: task is not open",
            data={"task_dir": td, "status": initial_status,
                  "next_action": "Call task_start to begin a fresh task run."},
        )
    control_root = find_harness_root(td) or find_repo_root(td)
    with goal_transaction(control_root), receipt_stream_transaction(td):
        snapshot = receipt_snapshot(td)
        def close_error(message, data):
            return _err(message, data=dict(data))

        ctx = emit_compact_context(td, snapshot)
        missing = ctx.get("missing_for_close") or []
        if missing:
            data = {
                "task_dir": td, "missing_for_close": missing, "task_context": ctx,
            }
            return close_error("task_close blocked", data)

        try:
            receipt_fingerprint = receipt_stream_fingerprint(td, snapshot)
        except RuntimeError:
            return close_error("task_close blocked: receipt stream snapshot unavailable", {
                "task_dir": td, "receipt_snapshot_unavailable": True,
            })

        st = _validated_task_control(td)
        if not st:
            return _invalid_task_control_error("task_close", td)
        locked_status = task_control_status(td, st)
        if locked_status != "open":
            return close_error("task_close blocked: task changed terminal state", {
                "task_dir": td, "status": locked_status,
            })
        preclose_control = dict(st)
        preclose_marker_snapshot = active_marker_snapshot(control_root)
        try:
            publish_task_close(td, st, receipt_fingerprint=receipt_fingerprint)
            clear_active_marker(control_root, td, strict=True)
            if os.path.realpath(resolve_active_task_dir(control_root) or "") == os.path.realpath(td):
                raise RuntimeError("active task marker cleanup unavailable")

            goal = read_current_goal(control_root)
            if goal.get("status") == "active" and any(
                isinstance(task, dict) and task.get("task_id") == os.path.basename(td)
                for task in goal.get("tasks", [])
            ):
                add_goal_task(
                    control_root, os.path.basename(td), status="closed", task_dir=td,
                )
        except Exception as exc:
            write_task_control(td, preclose_control)
            restore_active_marker_snapshot(preclose_marker_snapshot)
            data = {"task_dir": td, "detail": str(exc)[:500]}
            if isinstance(exc, GitBindingError):
                data.update({
                    "code": exc.code,
                    "path": exc.path,
                    "invariant": exc.invariant,
                    "next_action": exc.next_action,
                })
            return close_error("task_close blocked: close publication failed", data)

        return _ok({
            "task_dir": td, "closed": True, "status": "closed",
            "gate_artifact": _task_artifact_rel(td, "PLAN.md"),
        })


def handle_task_blocked(args: dict) -> dict:
    td = canonical_task_dir(task_id=_req(args, "task_id"), repo_root=_control_root())
    st = _validated_task_control(td)
    if not st:
        return _invalid_task_control_error("task_blocked", td)
    status = task_control_status(td, st)
    if status != "open":
        return _err(
            "task_blocked refused: task is not open",
            data={"task_dir": td, "status": status,
                  "next_action": "Call task_start to begin a fresh task run."},
        )
    with receipt_stream_transaction(td):
        return _handle_task_blocked_locked(args, td)


def _handle_task_blocked_locked(args: dict, td: str) -> dict:
    reason = _req(args, "blocked_reason")
    unblock = _req(args, "unblock_condition")
    st = _validated_task_control(td)
    if not st:
        return _invalid_task_control_error("task_blocked", td)
    status = task_control_status(td, st)
    if status != "open":
        return _err(
            "task_blocked refused: task is not open",
            data={"task_dir": td, "status": status,
                  "next_action": "Call task_start to begin a fresh task run."},
        )
    blocked_md = (
        "# BLOCKED\n\n"
        f"## Blocked Reason\n{reason}\n\n"
        f"## Unblock Condition\n{unblock}\n\n"
        f"## Blocked At\n{now_iso()}\n"
    )
    blocked_path = os.path.join(td, "BLOCKED.md")
    marker_snapshot = active_marker_snapshot(_control_root())
    blocked_snapshot = {
        blocked_path: _strict_regular_text_snapshot(blocked_path, max_size=256 * 1024)
    }
    try:
        _atomic_write_text(blocked_path, blocked_md)
        clear_active_marker(_control_root(), td, strict=True)
    except Exception:
        _restore_text_snapshots(blocked_snapshot)
        restore_active_marker_snapshot(marker_snapshot)
        raise
    return _ok({
        "task_dir": td,
        "status": "blocked",
        "runtime_verdict": "BLOCKED_ENV",
        "blocked_artifact": _task_artifact_rel(td, "BLOCKED.md"),
    })


def _nonempty_artifact_content(value: str, *, artifact: str, filename: str) -> str | dict:
    if not value.strip():
        return _err(
            f"write_plan refused to write empty {filename}",
            data={
                "artifact": artifact,
                "filename": filename,
                "next_action": "Pass non-empty content, or omit the bundled artifact.",
            },
        )
    return value


def _record_write(path: str, text: str, written: list[str], bytes_written: dict[str, int]) -> None:
    _atomic_write_text(path, text)
    name = os.path.basename(path)
    written.append(name)
    bytes_written[name] = len(text.encode("utf-8"))


def handle_write_plan(args: dict) -> dict:
    allowed_args = {"task_id", "task_dir", "plan", "required_lenses"}
    unknown_args = sorted(set(args) - allowed_args)
    if unknown_args:
        return _err(
            "write_plan received unsupported fields",
            data={"unsupported": unknown_args, "allowed": sorted(allowed_args), "written": []},
        )
    td = _resolve_td(args)
    control = _validated_task_control(td)
    if not control:
        return _invalid_task_control_error("write_plan", td)
    status = task_control_status(td, control)
    if status != "open":
        return _err(
            "write_plan refused: task is not open",
            data={"task_dir": td, "status": status, "written": [],
                  "next_action": "Call task_start to begin a fresh task run."},
        )
    preflight = _prepare_write_plan(args, td, control)
    if isinstance(preflight, dict) and preflight.get("isError"):
        return preflight
    with receipt_stream_transaction(td):
        return _handle_write_plan_locked(args, td, preflight=preflight)


def _handle_write_plan_locked(args: dict, td: str, *, preflight=None) -> dict:
    """Write the minimal task-local planning artifacts in one MCP call."""
    control = _validated_task_control(td)
    if not control:
        return _invalid_task_control_error("write_plan", td)
    status = task_control_status(td, control)
    if status != "open":
        return _err(
            "write_plan refused: task is not open",
            data={"task_dir": td, "status": status, "written": [],
                  "next_action": "Call task_start to begin a fresh task run."},
        )
    return _publish_write_plan(args, td, control, preflight)


def _prepare_write_plan(args: dict, td: str, control: dict):
    raw_plan = args.get("plan")
    plan = raw_plan if isinstance(raw_plan, str) else ""
    candidate_control = dict(control)
    if "required_lenses" in args:
        requested = args["required_lenses"]
        if (
            isinstance(requested, list)
            and requested
            and all(isinstance(lens, str) and lens in SUPPORTED_LENSES for lens in requested)
            and len(requested) == len(set(requested))
            and "review-code" in requested
            and any(lens in QA_LENSES for lens in requested)
        ):
            selected = set(requested)
            candidate_control["required_lenses"] = [
                lens for lens in LENS_ORDER if lens in selected
            ]
        else:
            candidate_control["required_lenses"] = requested
    try:
        # Validate the exact lens declarations before changing any artifact.
        if not _validate_task_control(candidate_control):
            raise ValueError("invalid or empty lens declaration")
    except (TypeError, ValueError) as exc:
        return _err(
            f"write_plan refused invalid required_lenses: {exc}",
            data={"written": [], "allowed_required_lenses": [
                "review-code", "review-security", "qa-api", "qa-browser",
                "qa-cli", "qa-desktop",
            ]},
        )
    checked_plan = _nonempty_artifact_content(plan, artifact="plan", filename="PLAN.md")
    if isinstance(checked_plan, dict):
        return checked_plan
    return control, candidate_control, checked_plan


def _publish_write_plan(args, td, control, preflight):
    expected_control, candidate_control, checked_plan = preflight
    # Recheck the exact authority while holding the receipt transaction.
    current = _validated_task_control(td)
    if current != expected_control or current != control or task_control_status(td, current) != "open":
        return _err(
            "write_plan refused: task changed before publication",
            data={"task_dir": td, "written": [],
                  "next_action": "Call task_context and retry only if the task is open."},
        )
    written: list[str] = []
    bytes_written: dict[str, int] = {}
    plan_path = os.path.join(td, "PLAN.md")
    control_path = task_control_file(td)
    try:
        snapshots = {
            path: _strict_regular_text_snapshot(path, max_size=1024 * 1024)
            for path in (plan_path, control_path)
        }
        _record_write(plan_path, checked_plan, written, bytes_written)
        write_task_control(td, candidate_control)
        written.append("TASK.json")
        bytes_written["TASK.json"] = len(
            json.dumps(candidate_control, indent=2, sort_keys=True).encode("utf-8")
        ) + 1
    except Exception as exc:
        if "snapshots" in locals():
            _restore_text_snapshots(snapshots)
        return _err(
            f"write_plan failed without publishing a partial artifact bundle: {exc}",
            data={"written": []},
        )

    return _ok({
        "artifact": "plan",
        "task_dir": td,
        "written": written,
        "bytes_written": bytes_written,
    })


for _control_writer in (
    handle_task_start, handle_task_close, handle_task_blocked, handle_write_plan,
    handle_goal_start, handle_goal_add_task, handle_goal_finish,
):
    _bind_control_writer(_control_writer)
del _control_writer
del _bind_control_writer


# ── Tool definitions ─────────────────────────────────────────────────────

TOOL_DEFS: list[dict[str, Any]] = [
    {"name": "goal_start", "title": "Start or sync a native goal",
     "description": "Create or update the active harness Goal from a native /goal objective. Goal is the public orchestration container and owns child harness tasks.",
     "inputSchema": {"type": "object", "properties": {
         "objective": {"type": "string"},
         "goal_id": {"type": "string"},
         "source": {"type": "object"}},
         "required": ["objective"], "additionalProperties": False},
     "handler": handle_goal_start},
    {"name": "goal_context", "title": "Read active goal",
     "description": "Return the active harness Goal and its child task queue.",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
     "handler": handle_goal_context},
    {"name": "goal_add_task", "title": "Add or update a goal child task",
     "description": "Attach a harness task to the active Goal. Use this after task_start or when new scope is discovered and the Goal needs another child task.",
     "inputSchema": {"type": "object", "properties": {
         "task_id": {"type": "string"},
         "title": {"type": "string"},
         "status": {"type": "string", "enum": ["queued", "active", "closed", "blocked"]},
         "task_dir": {"type": "string"}},
         "required": ["task_id"], "additionalProperties": False},
     "handler": handle_goal_add_task},
    {"name": "goal_next_task", "title": "Return next goal child task",
     "description": "Return the next queued or active child task for the active Goal, or indicate that none remain.",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
     "handler": handle_goal_next_task},
    {"name": "goal_finish", "title": "Finish active goal",
     "description": "Mark the active Goal complete or blocked after child tasks prove the objective is satisfied or genuinely blocked.",
     "inputSchema": {"type": "object", "properties": {
         "status": {"type": "string", "enum": ["complete", "blocked"]}},
         "additionalProperties": False},
     "handler": handle_goal_finish},
    {"name": "task_start", "title": "Create or resume a task",
     "description": "Create exact TASK.json scaffolding and return fresh context. Use directly for plain repo-mutating requests when no native goal context is active. The public lifecycle is task start -> plan -> develop -> QA -> close; review and task_verify are internal close gates. Pass execution_mode='micro' for an explicitly shortened no-plan develop -> QA -> close path; internal verification remains mandatory.",
     "inputSchema": {"type": "object", "properties": {
         "task_dir": {"type": "string"}, "task_id": {"type": "string"},
         "slug": {"type": "string"}, "request_file": {"type": "string"},
         "execution_mode": {"type": "string", "enum": ["standard", "micro"]}},
         "additionalProperties": False},
     "handler": handle_task_start},
    {"name": "task_context", "title": "Read the task pack",
     "description": "Return compact task context with on-the-fly routing.",
     "inputSchema": {"type": "object", "properties": {
         "task_id": {"type": "string"}},
         "required": ["task_id"], "additionalProperties": False},
     "handler": handle_task_context},
    {"name": "task_verify", "title": "Run task verification",
     "description": "Compute verification state from ordered hook-owned review and QA completion receipts. After substantive QA, use one fresh call before closing or entering the blocked path.",
     "inputSchema": {"type": "object", "properties": {
         "task_id": {"type": "string"},
         "run_commands": {"type": "boolean"},
         "parallel": {"type": "boolean"},
         "max_workers": {"type": "integer"}},
         "required": ["task_id"], "additionalProperties": False},
     "handler": handle_task_verify},
    {"name": "task_close", "title": "Run the completion gate",
     "description": "Check all verdicts PASS, then close the task.",
     "inputSchema": {"type": "object", "properties": {
         "task_id": {"type": "string"}},
         "required": ["task_id"], "additionalProperties": False},
     "handler": handle_task_close},
    {"name": "task_blocked", "title": "Park a task on a real environment or attestation blocker",
     "description": "Record BLOCKED_ENV in BLOCKED.md and clear this session's active marker. This is not completion.",
     "inputSchema": {"type": "object", "properties": {
         "task_id": {"type": "string"},
         "blocked_reason": {"type": "string"},
         "unblock_condition": {"type": "string"}},
         "required": ["task_id", "blocked_reason", "unblock_condition"],
         "additionalProperties": False},
     "handler": handle_task_blocked},
    {"name": "write_plan", "title": "Write task plan",
     "description": "Write PLAN.md and the exact required_lenses declaration in TASK.json.",
     "inputSchema": {"type": "object", "properties": {
         "task_id": {"type": "string"}, "task_dir": {"type": "string"},
         "plan": {"type": "string"},
         "required_lenses": {"type": "array", "items": {"type": "string"}}},
         "required": ["plan"],
         "additionalProperties": False},
     "handler": handle_write_plan},
]

TOOLS = {t["name"]: t for t in TOOL_DEFS}


def list_tools() -> list[dict]:
    return [{k: v for k, v in t.items() if k != "handler"} for t in TOOL_DEFS]


def call_tool(name: str, args: dict | None) -> dict:
    if name not in TOOLS:
        return _err(f"Unknown tool: {name}")
    try:
        return TOOLS[name]["handler"](args or {})
    except ValueError as e:
        message = str(e)
        supplied = args or {}
        if "goal storage root" in message:
            field = "goal_storage_root"
            raw = "doc/harness/goals"
            expected = "a real, non-symlink doc/harness/goals directory inside the repository"
            next_action = "Restore doc/harness/goals and its existing parents as real repository directories, then retry."
        elif "canonical task root" in message:
            field = "task_storage_root"
            raw = "doc/harness/tasks"
            expected = "a real, non-symlink doc/harness/tasks directory inside the repository"
            next_action = "Restore doc/harness/tasks and its existing parents as real repository directories, then retry."
        else:
            field = next(
                (key for key in ("task_dir", "task_id", "slug", "goal_id") if key in message),
                next((key for key in ("goal_id", "task_dir", "task_id", "slug") if key in supplied), "selector"),
            )
            raw = supplied.get(field) if field != "selector" else None
            expected = (
                "GOAL__<safe-id>, or omit goal_id"
                if field == "goal_id"
                else "TASK__<safe-id> or doc/harness/tasks/TASK__<safe-id>"
            )
            next_action = "Correct the named selector to the canonical form and retry without changing repository state."
        rejected = repr(raw)
        if len(rejected) > 160:
            rejected = rejected[:157] + "..."
        return _err(message, data={
            "field": field,
            "rejected_value": rejected,
            "expected": expected,
            "next_action": next_action,
        })
    except GitBindingError as e:
        return _err(f"{name} failed: {e}", data={
            "error_code": e.code,
            "path": e.path,
            "invariant": e.invariant,
            "next_action": e.next_action,
        })
    except Exception as e:
        # The MCP server and its test/plugin loaders can import _lib under
        # distinct module identities. Preserve the structured recovery
        # contract for an equivalent GitBindingError from either identity.
        if all(hasattr(e, field) for field in (
            "code", "path", "invariant", "next_action",
        )):
            return _err(f"{name} failed: {e}", data={
                "error_code": e.code,
                "path": e.path,
                "invariant": e.invariant,
                "next_action": e.next_action,
            })
        return _err(f"{name} failed: {e}")


# ── MCP protocol ─────────────────────────────────────────────────────────


class McpServer:
    def __init__(self) -> None:
        self.initialized = False
        self.protocol_version = SUPPORTED_PROTOCOLS[0]
        self.framed_stdio = False
        self.watcher_manager = None
        self.watcher_thread_id = ""
        self.last_watcher_error = ""
        self.runtime = ""
        global _SERVER
        _SERVER = self

    def _start_codex_watchers(self) -> None:
        if self.watcher_manager is not None or _WatcherManager is None:
            return
        try:
            self.watcher_manager = _WatcherManager(_control_root()).start()
            self.last_watcher_error = ""
            _record_watcher_error("")
        except Exception as exc:
            # Lifecycle attestation is fail-closed.  A watcher failure must not
            # take down task_context or other MCP control-plane operations —
            # but it must not be silent either.  Without the cause recorded the
            # only symptom is an absent receipt, discovered after review and QA
            # have already been paid for.
            self.watcher_manager = None
            self.last_watcher_error = f"{type(exc).__name__}: {exc}"
            _record_watcher_error(self.last_watcher_error)

    def close(self) -> None:
        manager = self.watcher_manager
        self.watcher_manager = None
        self.watcher_thread_id = ""
        if manager is not None:
            try:
                manager.stop()
            except Exception:
                pass

    def _read(self) -> dict | None:
        """Read either MCP stdio frames or newline-delimited JSON.

        Codex speaks the standard MCP stdio transport, where every JSON-RPC
        message is framed with HTTP-like headers, most importantly
        ``Content-Length``. Older harness smoke tests used one JSON object per
        line, so this reader accepts both forms.
        """
        first = sys.stdin.buffer.readline()
        if not first:
            return None
        if first.strip().startswith(b"{"):
            self.framed_stdio = False
            return json.loads(first.strip().decode())

        self.framed_stdio = True
        headers: dict[str, str] = {}
        line = first
        while line and line.strip():
            key, sep, value = line.decode(errors="replace").partition(":")
            if sep:
                headers[key.strip().lower()] = value.strip()
            line = sys.stdin.buffer.readline()

        length_raw = headers.get("content-length")
        if not length_raw:
            return None
        body = sys.stdin.buffer.read(int(length_raw))
        if not body:
            return None
        return json.loads(body.decode())

    def _write(self, payload: dict) -> None:
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
        if self.framed_stdio:
            header = f"Content-Length: {len(body)}\r\n\r\n".encode()
            sys.stdout.buffer.write(header + body)
        else:
            sys.stdout.buffer.write(body + b"\n")
        sys.stdout.buffer.flush()

    def _reply(self, msg_id: Any, result: Any) -> None:
        self._write({"jsonrpc": "2.0", "id": msg_id, "result": result})

    def _error(self, msg_id: Any, code: int, message: str) -> None:
        self._write({"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}})

    def handle_request(self, req: dict) -> None:
        method = req.get("method")
        msg_id = req.get("id")
        params = req.get("params") or {}

        if method == "initialize":
            pv = params.get("protocolVersion")
            self.protocol_version = pv if isinstance(pv, str) and pv in SUPPORTED_PROTOCOLS else SUPPORTED_PROTOCOLS[0]
            runtime = _runtime_from_initialize(params)
            self.runtime = runtime
            if runtime == "codex":
                self._start_codex_watchers()
            self._reply(msg_id, {
                "protocolVersion": self.protocol_version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
                "instructions": _initialize_instructions(runtime),
            })
        elif method == "notifications/initialized":
            self.initialized = True
        elif method == "ping":
            self._reply(msg_id, {})
        elif method == "tools/list":
            self._reply(msg_id, {"tools": list_tools()})
        elif method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments") or {}
            if not isinstance(name, str):
                self._error(msg_id, -32602, "Tool name must be a string")
                return
            self._reply(msg_id, call_tool(name, arguments))
        else:
            self._error(msg_id, -32601, f"Method not found: {method}")

    def serve_forever(self) -> None:
        try:
            while True:
                req = self._read()
                if req is None:
                    return
                self.handle_request(req)
        finally:
            self.close()


def main() -> int:
    McpServer().serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
