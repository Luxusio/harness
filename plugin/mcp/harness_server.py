#!/usr/bin/env python3
"""Harness MCP server — self-contained, exact four-field TASK.json.

No plugin-legacy dependency. All operations are direct file I/O.
MCP tool: wbs. Task execution handlers are private implementation details.
"""

from __future__ import annotations
import json
import os
import re
import stat
import subprocess
import sys
import time
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
        "harness MCP — WBS-first control plane. One WBS tree owns outcomes and progress for any request. "
        "Protocol tool names are bare: wbs. Use wbs(action=open|context|plan|check|next|dispatch|verify|close|block, wbs_id=...). "
        "Open a root for a plain repo-mutating request; hooks do not create tasks automatically. "
        "Start with coarse observable outcomes in PLAN's wbs fence, with optional check argv. "
        "Never prescribe edited files for progress or freeze implementation at intake. Refine as discovery requires. "
        "check runs one declared outcome assertion; verify and close measure current WBS checks and input applicability. Independent review and QA remain workflow-owned. "
        "dispatch owns prepare/bind/complete/resume/recover/status on the existing isolated-worktree pool; it creates worktrees and completes integration/disposal before parent completion. "
        "File ownership is supplied only at this actual concurrent execution handoff. "
        "Task open preserves the execution run; fresh_run=true deliberately creates a new run. "
        "Use wbs(action=migrate) to preview retired records; apply=true explicitly converts them. Retired tool names are unavailable. "
    )
    if runtime == "codex":
        return base + (
            "Codex callers should use these bare tool names directly; do not use Claude display prefixes like mcp__plugin_harness_harness__*. "
            "For a native Goal, read get_goal and use the same WBS root and objective, never a second Goal baseline."
        )
    if runtime == "claude":
        return base + "Claude Code may display callable tools with a runtime prefix over the same wbs tool."
    return base


sys.path.insert(0, str(SCRIPTS_DIR))
import wbs
from _lib import (  # type: ignore
    GitBindingError,
    now_iso, read_task_control, write_task_control, task_control_file,
    task_control_status, publish_task_close, _validate_task_control,
    ensure_task_scaffold, emit_compact_context,
    artifact_exists, canonical_task_dir, canonical_task_id,
    find_harness_root, harness_root_resolution, find_repo_root, is_harness_enabled_repo,
    resolve_registered_worktree, sanitize_session_id,
    write_active_marker, clear_active_marker,
    active_task_binding_matches,
    active_session_transaction, read_active_session_marker, resolve_session_task_binding,
    _has_binding_conflict_fence,
    resolve_active_task_dir, active_marker_snapshot, restore_active_marker_snapshot,
    required_review_lenses, _required_qa_lenses,
    task_control_transaction,
    begin_task_run, restore_task_control,
    _bind_control_writer,
    _strict_regular_text_snapshot, _restore_text_snapshots, _atomic_text_write as _lib_atomic_text_write,
    LENS_ORDER, SUPPORTED_LENSES, QA_LENSES,

)

try:
    from codex_hook_registration import (
        workspace_roots as _codex_workspace_roots,
        _live_conflicts as _codex_live_conflicts,
        authenticate_native_session as _authenticate_native_session,
        register_native_task as _register_native_task,
    )
except ImportError:
    _codex_workspace_roots = None
    _codex_live_conflicts = None
    _authenticate_native_session = None
    _register_native_task = None

_SERVER: "McpServer | None" = None


def _control_root() -> str:
    root, error = harness_root_resolution(find_repo_root())
    if error:
        raise RuntimeError(f"invalid Harness workspace at {root}: {error}")
    if not root or not is_harness_enabled_repo(root):
        raise _ToolArgumentError(
            "Harness is not initialized in this project; run harness:setup explicitly before using WBS.",
            error_code="HARNESS_NOT_INITIALIZED", field="project_setup", reason="setup_required",
            rejected_value="<not initialized>",
            expected="a project explicitly initialized with doc/harness/manifest.yaml",
            next_action="Run harness:setup explicitly in this project, then retry the same WBS request.",
        )
    return root
def _server_runtime() -> str:
    runtime = getattr(_SERVER, "runtime", "") if _SERVER is not None else ""
    return runtime or str(os.environ.get("HARNESS_RUNTIME") or "").strip().lower()


def _current_session_identity(control_root: str = "") -> str:
    if _server_runtime() == "codex":
        sid = str(os.environ.get("CODEX_THREAD_ID") or "")
    else:
        sid = str(os.environ.get("CLAUDE_SESSION_ID") or os.environ.get("HARNESS_SESSION_ID") or "")
    return sid if sid and sid != "default" and sanitize_session_id(sid) == sid else ""


def _ok(d: dict) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(d, indent=2, ensure_ascii=False)}],
            "structuredContent": d}


def _err(m: str, data: dict | None = None) -> dict:
    p: dict[str, Any] = {"error": m}
    p.update(data or {})
    return {"content": [{"type": "text", "text": json.dumps(p, indent=2, ensure_ascii=False)}],
            "structuredContent": p, "isError": True}


BLOCKER_FIELD_MAX_UTF8_BYTES = 120 * 1024
_BLOCKER_FIELDS = {"blocked_reason", "unblock_condition"}
_FIELD_EXPECTED = {
    "task_id": (
        "TASK__<safe-id> or <safe-id>, where safe-id is 1-180 ASCII letters, "
        "digits, dots, underscores, or hyphens"
    ),
    "task_dir": (
        "the exact canonical doc/harness/tasks/TASK__<safe-id> path or its "
        "absolute path inside the repository"
    ),
    "slug": (
        "TASK__<safe-id> or <safe-id>, where safe-id is 1-180 ASCII letters, "
        "digits, dots, underscores, or hyphens"
    ),
    "objective": "a nonblank string describing the goal",
    "blocked_reason": (
        f"a nonblank string no larger than {BLOCKER_FIELD_MAX_UTF8_BYTES} UTF-8 bytes"
    ),
    "unblock_condition": (
        f"a nonblank string no larger than {BLOCKER_FIELD_MAX_UTF8_BYTES} UTF-8 bytes"
    ),
    "fresh_run": "a boolean",
    "execution_mode": "standard or micro",
}


class _ToolArgumentError(ValueError):
    """Public argument failure with provenance that survives MCP serialization."""

    def __init__(
        self,
        message: str,
        *,
        field: str,
        reason: str,
        rejected_value: str,
        expected: str | None = None,
        next_action: str | None = None,
        error_code: str = "INVALID_ARGUMENT",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.data = {
            "error_code": error_code,
            "field": field,
            "reason": reason,
            "rejected_value": rejected_value,
            "expected": expected or _FIELD_EXPECTED.get(field, "a valid value"),
            "next_action": next_action or f"Correct {field} and retry.",
        }
        self.data.update(details or {})


def _argument_rejected_value(args: dict, field: str) -> tuple[str, str]:
    if field not in args:
        return "missing", "<missing>"
    value = args[field]
    if not isinstance(value, str):
        return "wrong_type", f"<non-string: {type(value).__name__}>"
    if not value.strip():
        return "blank", "<blank string>"
    if field in _BLOCKER_FIELDS:
        size = len(value.encode("utf-8"))
        return "invalid", f"<string: {size} UTF-8 bytes>"
    rejected = repr(value)
    if len(rejected) > 160:
        rejected = rejected[:157] + "..."
    return "invalid", rejected


def _req(args: dict, k: str) -> str:
    v = args.get(k)
    if not isinstance(v, str) or not v.strip():
        reason, rejected = _argument_rejected_value(args, k)
        raise _ToolArgumentError(
            f"{k} required",
            field=k,
            reason=reason,
            rejected_value=rejected,
            next_action=f"Provide {k} as {_FIELD_EXPECTED.get(k, 'a nonblank string')} and retry.",
        )
    return v


def _blocked_text(args: dict, field: str) -> str:
    value = _req(args, field)
    try:
        actual_bytes = len(value.encode("utf-8"))
    except UnicodeEncodeError:
        raise _ToolArgumentError(
            f"{field} must be valid UTF-8 text",
            field=field,
            reason="invalid_utf8",
            rejected_value="<string: invalid UTF-8>",
            next_action=f"Replace invalid Unicode code points in {field} and retry.",
        ) from None
    if actual_bytes > BLOCKER_FIELD_MAX_UTF8_BYTES:
        raise _ToolArgumentError(
            f"{field} exceeds the UTF-8 byte limit",
            field=field,
            reason="utf8_byte_limit_exceeded",
            rejected_value=f"<string: {actual_bytes} UTF-8 bytes>",
            next_action=f"Shorten {field} to the documented UTF-8 byte limit and retry.",
            error_code="ARGUMENT_TOO_LARGE",
            details={
                "actual_bytes": actual_bytes,
                "max_bytes": BLOCKER_FIELD_MAX_UTF8_BYTES,
            },
        )
    return value


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


def _task_roots(args: dict) -> tuple[str, str]:
    """Return ``(control_root, task_root)`` for a task tool call.

    The control root owns session identity and diagnostics. The task root owns
    task paths and focus markers: the control root itself, or a validated
    linked worktree named by ``workspace`` (a batch lead's checkout).
    """
    control_root = _control_root()
    workspace = args.get("workspace")
    if workspace is None:
        return control_root, control_root
    if not isinstance(workspace, str):
        raise _ToolArgumentError(
            "workspace must be a string",
            field="workspace",
            reason="wrong_type",
            rejected_value=repr(workspace)[:160],
            next_action="Pass workspace as an absolute worktree path, or omit it.",
        )
    # Codex PostToolUse binds the exact native coordinator to the echoed
    # workspace. Merely routing this call grants no source ownership.
    return control_root, resolve_registered_worktree(control_root, workspace) or control_root


def _resolve_td(args: dict) -> str:
    td = _selector_opt(args, "task_dir")
    ti = _selector_opt(args, "task_id")
    if ti or td:
        return canonical_task_dir(task_id=ti, task_dir=td, repo_root=_task_roots(args)[1])
    raise ValueError("task_id or task_dir required")


def _validated_task_control(td: str) -> dict:
    return read_task_control(td)


def _invalid_task_control_error(operation: str, td: str) -> dict:
    return _err(
        f"{operation} failed: missing or invalid TASK.json",
        data={
            "task_dir": td,
            "next_action": "Use wbs(action='migrate') preview/apply for old task controls, or wbs(action='open') to open a new task.",
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
            f"Call wbs(action='context', wbs_id={task_id!r}). "
            "Do not call wbs(action='open') again."
        ),
    }


def _task_resume_next_action(status: str) -> str:
    if status == "blocked":
        return (
            "Call wbs(action='open') with this wbs_id to resume the same run and preserve "
            "its WBS checks and history."
        )
    if status == "closed":
        return (
            "Call wbs(action='open', fresh_run=true) only to deliberately create a new "
            "execution run."
        )
    return "Choose a new wbs_id and call wbs(action='open')."


# ── Tool handlers ────────────────────────────────────────────────────────


def _with_codex_main_focus(args: dict, operation: Callable[..., dict]) -> dict:
    """Serialize exact process-owned focus with all worktree hook binds."""
    if _server_runtime() != "codex":
        with active_session_transaction(_task_roots(args)[1]):
            return operation(args)
    control_root, repo_root = _task_roots(args)
    session_id = _current_session_identity(control_root)
    native_cwd = ""
    workspace = args.get("workspace") is not None
    if workspace and session_id and _authenticate_native_session is not None:
        native_cwd = _authenticate_native_session(session_id, control_root)
    if not session_id or (workspace and not native_cwd):
        with active_session_transaction(repo_root):
            return operation(args)
    if _codex_workspace_roots is None or _codex_live_conflicts is None:
        return _err("Codex session focus validation is unavailable")
    roots = _codex_workspace_roots(repo_root)
    if not roots:
        return _err("Codex workspace registry exceeds the supported bound")
    with active_session_transaction(roots[0]), ExitStack() as locks:
        roots = _codex_workspace_roots(roots[0])
        if repo_root not in roots:
            return _err("Codex workspace registration changed")
        for root in sorted(roots[1:]):
            locks.enter_context(active_session_transaction(root))
        fenced = False
        for root in roots:
            marker = read_active_session_marker(root, session_id)
            fenced = fenced or _has_binding_conflict_fence(marker)
            try:
                conflicts = _codex_live_conflicts(root, marker, roots=roots)
            except RuntimeError:
                return _err("task focus refused: coordinator conflict fence overflow requires a new coordinator")
            unresolved = False
            if conflicts:
                requested = canonical_task_dir(
                    task_id=args.get("task_id"), slug=args.get("slug"),
                    task_dir=args.get("task_dir"), repo_root=repo_root,
                )
                unresolved = len(conflicts) != 1 or conflicts[0]["task_dir"] != requested
            if root == repo_root:
                if unresolved:
                    return _err("task focus refused: unresolved coordinator task conflicts")
                if workspace:
                    requested = canonical_task_dir(
                        task_id=args.get("task_id"), slug=args.get("slug"),
                        task_dir=args.get("task_dir"), repo_root=repo_root,
                    )
                    if not _session_resumes(repo_root, requested, session_id):
                        return _err(
                            "task focus refused: another open task owns this Codex coordinator",
                            data={"next_action": "Finish or park the focused task before activating another task."},
                        )
                continue
            if resolve_session_task_binding(root, session_id) or unresolved:
                return _err(
                    "task focus refused: another worktree owns this Codex coordinator",
                    data={"next_action": "Finish or park the bound worktree task before activating another task."},
                )
        # Workspace execution defers binding; main execution keeps its existing
        # focus admission and marker behavior. Both authenticate/register only
        # after these focus locks and the task control lock have been released.
        binding_handoff = {}
        if workspace:
            result = operation(args, defer_binding=True, binding_handoff=binding_handoff)
        else:
            result = operation(
                args, defer_binding=fenced, binding_handoff=binding_handoff,
            )
    if result.get("isError"):
        return result
    data = result.get("structuredContent", {})
    task_dir = str(data.get("task_dir") or "")
    control = read_task_control(task_dir)
    if (workspace and native_cwd and _register_native_task is not None
            and task_control_status(task_dir, control) == "open"):
        _register_native_task(native_cwd, session_id, repo_root, task_dir, os.path.basename(task_dir), control["run_id"])
    return result


def handle_task_start(args: dict) -> dict:
    # Reject an already invalid control before creating session lock storage.
    # The implementation rechecks under its task lock before any mutation.
    _, repo_root = _task_roots(args)
    task_dir = canonical_task_dir(
        task_id=_selector_opt(args, "task_id"), slug=_selector_opt(args, "slug"),
        task_dir=_selector_opt(args, "task_dir"), repo_root=repo_root,
    )
    if os.path.lexists(task_control_file(task_dir)) and not read_task_control(task_dir):
        return _err(
            "task_start refused invalid TASK.json: unsupported task-control schema or unsafe control",
            data={
                "task_dir": task_dir,
                "next_action": (
                    "Use wbs(action='migrate') preview/apply for an old task schema, or choose a new wbs_id. Harness does not "
                    "migrate or rewrite an unsupported existing task control."
                ),
            },
        )
    return _with_codex_main_focus(args, _handle_task_start)


def _handle_task_start(
    args: dict, *, defer_binding: bool = False, binding_handoff: dict | None = None,
) -> dict:
    td = _selector_opt(args, "task_dir")
    ti = _selector_opt(args, "task_id")
    sl = _selector_opt(args, "slug")
    rf = _opt(args, "request_file")
    if not td and not ti and not sl:
        raise ValueError("task_start requires task_dir, task_id, or slug")

    if "execution_mode" in args and not isinstance(args["execution_mode"], str):
        _, rejected = _argument_rejected_value(args, "execution_mode")
        raise _ToolArgumentError(
            "execution_mode must be standard or micro",
            field="execution_mode",
            reason="wrong_type",
            rejected_value=rejected,
            next_action="Pass execution_mode as standard or micro, or omit it.",
        )
    execution_mode = _opt(args, "execution_mode")
    if execution_mode:
        execution_mode = execution_mode.strip().lower()
        if execution_mode not in {"standard", "micro"}:
            _, rejected = _argument_rejected_value(args, "execution_mode")
            raise _ToolArgumentError(
                "execution_mode must be standard or micro",
                field="execution_mode",
                reason="invalid_choice",
                rejected_value=rejected,
                next_action="Pass execution_mode as standard or micro, or omit it.",
            )

    fresh_run_raw = args.get("fresh_run", False)
    if not isinstance(fresh_run_raw, bool):
        _, rejected = _argument_rejected_value(args, "fresh_run")
        raise _ToolArgumentError(
            "fresh_run must be a boolean",
            field="fresh_run",
            reason="wrong_type",
            rejected_value=rejected,
            next_action="Pass fresh_run as a boolean, or omit it to preserve the current run.",
        )
    fresh_run = fresh_run_raw

    control_root, repo_root = _task_roots(args)
    task_dir = canonical_task_dir(task_id=ti, slug=sl, task_dir=td, repo_root=repo_root)
    tid = canonical_task_id(task_dir=task_dir, repo_root=repo_root)
    wbs.require_integrated_for_task(repo_root, tid, measure=False)
    existing_control_path = task_control_file(task_dir)
    resumed_existing = os.path.lexists(existing_control_path)
    exact_session_id = _current_session_identity(control_root)
    codex_workspace = _server_runtime() == "codex" and args.get("workspace") is not None
    if codex_workspace or defer_binding:
        exact_session_id = ""
    session_id = exact_session_id
    defer_codex_binding = not exact_session_id
    if not defer_codex_binding and not resumed_existing and not _session_resumes(repo_root, task_dir, session_id):
        return _err(
            "task_start refused: another open task owns the resolvable session focus",
            data={
                "task_dir": task_dir,
                "status": "absent",
                "next_action": "Finish or park the currently focused task, then retry wbs(action='open').",
            },
        )
    if not resumed_existing:
        os.makedirs(task_dir, exist_ok=True)
    transaction_stack = ExitStack()
    transaction_stack.enter_context(task_control_transaction(task_dir))
    prior_marker_snapshot = active_marker_snapshot(repo_root, session_id=session_id)
    if resumed_existing:
        if not read_task_control(task_dir):
            result = _err(
                "task_start refused invalid TASK.json: unsupported task-control schema or unsafe control",
                data={
                    "task_dir": task_dir,
                    "next_action": (
                        "Use wbs(action='migrate') preview/apply for an old task schema, or choose a new wbs_id. Harness does not "
                        "migrate or rewrite an unsupported existing task control."
                    ),
                },
            )
            transaction_stack.close()
            return result
    preserved_context = None
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
    scaffold = {"created": []}
    if not resumed_existing:
        try:
            scaffold = ensure_task_scaffold(
                task_dir, tid, request_text=request_text, repo_root=repo_root,
                execution_mode=execution_mode or "standard",
            )
        except BaseException:
            transaction_stack.close()
            raise
    original_resumed_control = read_task_control(task_dir) if resumed_existing else {}
    task_control_snapshot = {}
    blocked_artifact_snapshot = {}
    superseded_run_id = ""
    run_action = "created"

    def rollback_new_start():
        if resumed_existing:
            if original_resumed_control:
                write_task_control(task_dir, original_resumed_control)
            restore_task_control(task_control_snapshot)
            _restore_text_snapshots(blocked_artifact_snapshot)
            restore_active_marker_snapshot(prior_marker_snapshot)
            return
        cleanup = list(scaffold.get("created") or [])
        _restore_text_snapshots({
            artifact: {"exists": False, "kind": "absent", "text": ""}
            for artifact in cleanup
        })
        restore_active_marker_snapshot(prior_marker_snapshot)

    try:
        resumed = read_task_control(task_dir)
        terminal_resume_status = task_control_status(task_dir, resumed)
        if terminal_resume_status == "invalid":
            transaction_stack.close()
            return _err(
                "task_start refused invalid terminal task artifacts",
                data={
                    "task_dir": task_dir,
                    "status": "invalid",
                    "next_action": (
                        "Inspect and repair the unsafe or inconsistent task artifacts without "
                        "changing task history, or choose a new task_id. fresh_run is not repair authority."
                    ),
                },
            )
        if resumed_existing and not fresh_run:
            if terminal_resume_status == "closed":
                transaction_stack.close()
                return _err(
                    "task_start refused: task is closed",
                    data={
                        "task_dir": task_dir,
                        "status": "closed",
                        "next_action": (
                            "Call wbs(action='open', fresh_run=true) only to deliberately "
                            "create a new evidence generation and discard the current "
                            "WBS checks and history."
                        ),
                    },
                )
            if terminal_resume_status not in {"open", "blocked"}:
                raise RuntimeError("task_start refused invalid terminal task artifacts")
        if resumed_existing:
            if rf:
                transaction_stack.close()
                return _err(
                    "task_start refused: request_file cannot replace an existing task request",
                    data={
                        "task_dir": task_dir,
                        "status": terminal_resume_status,
                        "next_action": (
                            "Omit request_file, or choose a new task_id for the new request."
                        ),
                    },
                )
            if not defer_codex_binding and not _session_resumes(repo_root, task_dir, session_id):
                transaction_stack.close()
                return _err(
                    "task_start refused: another open task owns the resolvable session focus",
                    data={
                        "task_dir": task_dir,
                        "status": terminal_resume_status,
                        "next_action": "Finish or park the currently focused task, then retry wbs(action='open').",
                    },
                )
        if resumed_existing and not fresh_run:
            if execution_mode and resumed.get("execution_mode") != execution_mode:
                transaction_stack.close()
                return _err(
                    "task_start refused: execution_mode differs from the preserved run",
                    data={
                        "task_dir": task_dir,
                        "status": terminal_resume_status,
                        "next_action": (
                            "Omit execution_mode to preserve this run, or pass fresh_run=true "
                            "to deliberately create a new evidence generation."
                        ),
                    },
                )
            if terminal_resume_status == "blocked":
                blocked_path = os.path.join(task_dir, "BLOCKED.md")
                blocked_artifact_snapshot[blocked_path] = _strict_regular_text_snapshot(
                    blocked_path, max_size=256 * 1024,
                )
            try:
                # Publish the unchanged-run marker while BLOCKED.md still makes
                # the task terminal. Removing BLOCKED.md is the commit point;
                # a crash before it leaves the task safely parked.
                if not defer_codex_binding:
                    write_active_marker(repo_root, task_dir, session_id=session_id)
                if terminal_resume_status == "blocked":
                    os.unlink(blocked_path)
                if not defer_codex_binding and not active_task_binding_matches(
                    repo_root, task_dir, control=resumed, session_id=session_id,
                ):
                    raise RuntimeError("task_start could not publish the preserved run binding")
                preserved_context = emit_compact_context(task_dir)
                if "error" in preserved_context:
                    raise RuntimeError(str(preserved_context.get("error") or "compact context unavailable"))
            except BaseException:
                restore_errors = []
                try:
                    _restore_text_snapshots(blocked_artifact_snapshot)
                except BaseException as exc:
                    restore_errors.append(exc)
                try:
                    restore_active_marker_snapshot(prior_marker_snapshot)
                except BaseException as exc:
                    restore_errors.append(exc)
                transaction_stack.close()
                if restore_errors:
                    raise RuntimeError(
                        "task_start preserve publication failed and rollback was incomplete"
                    ) from restore_errors[0]
                raise
            run_action = "preserved"
        elif resumed_existing:
            previous_run_id = str(read_task_control(task_dir).get("run_id") or "")
            _, task_control_snapshot = begin_task_run(task_dir)
            resumed = read_task_control(task_dir)
            new_run_id = str(resumed.get("run_id") or "")
            if previous_run_id and new_run_id and previous_run_id != new_run_id:
                superseded_run_id = previous_run_id
            # An explicitly fresh start is a new lifecycle generation and must
            # not inherit evidence collected for the previous run identity.
            # This destructive branch is reachable only through fresh_run=true.
            run_action = "reset"
        if run_action != "preserved":
            if execution_mode and resumed.get("execution_mode") != execution_mode:
                resumed["execution_mode"] = execution_mode
                write_task_control(task_dir, resumed)
            if terminal_resume_status == "blocked":
                blocked_path = os.path.join(task_dir, "BLOCKED.md")
                blocked_artifact_snapshot[blocked_path] = _strict_regular_text_snapshot(
                    blocked_path, max_size=256 * 1024,
                )
                # As on the preserving path, publish the marker before the
                # blocker removal that makes the task open.
                if not defer_codex_binding:
                    write_active_marker(repo_root, task_dir, session_id=session_id)
                try:
                    os.unlink(blocked_path)
                except FileNotFoundError:
                    pass
    except Exception:
        if resumed_existing and not fresh_run:
            transaction_stack.close()
            raise
        try:
            rollback_new_start()
        finally:
            transaction_stack.close()
        raise

    try:
        ctx = preserved_context if preserved_context is not None else emit_compact_context(task_dir)
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
        if not defer_codex_binding and not active_task_binding_matches(
            repo_root, task_dir, control=resumed, session_id=session_id,
        ):
            write_active_marker(repo_root, task_dir, session_id=session_id)
    except Exception:
        try:
            if resumed_existing and not fresh_run:
                _restore_text_snapshots(blocked_artifact_snapshot)
                restore_active_marker_snapshot(prior_marker_snapshot)
            else:
                rollback_new_start()
        finally:
            transaction_stack.close()
        raise

    transaction_stack.close()
    if superseded_run_id:
        warnings.append({"code": "TASK_RUN_SUPERSEDED", "stage": "task_start",
                         "message": "A new execution run requires current WBS checks."})
    if binding_handoff is not None:
        binding_handoff.update({"context": ctx})

    return _ok({
        "task_dir": task_dir, "task_id": tid, "task_context": ctx,
        **({"workspace": repo_root} if args.get("workspace") is not None else {}),
        "run_id": resumed["run_id"],
        "previous_run_id": superseded_run_id or None,
        "run_action": run_action,
        "evidence_preserved": run_action == "preserved",
        "start_status": "ready_with_warnings" if warnings else "ready",
        "task_created": not resumed_existing,
        "resumed": resumed_existing,
        "warnings": warnings,
        "next_action": ctx.get("next_action", ""),
    })


def _session_resumes(repo_root: str, task_dir: str, session_id: str) -> bool:
    """Exact current focus may be reasserted without borrowing shared hints."""
    held = resolve_active_task_dir(repo_root, session_id=session_id)
    if not held or task_control_status(held, read_task_control(held)) != "open":
        return True
    return os.path.realpath(held) == os.path.realpath(task_dir)


def handle_task_context(args: dict) -> dict:
    return _with_codex_main_focus(args, _handle_task_context)


def _handle_task_context(
    args: dict, *, defer_binding: bool = False, binding_handoff: dict | None = None,
) -> dict:
    ti = _req(args, "task_id")
    control_root, repo_root = _task_roots(args)
    td = canonical_task_dir(task_id=ti, repo_root=repo_root)
    control = _validated_task_control(td)
    if not control:
        return _invalid_task_control_error("task_context", td)
    # Only a process-owned identity may publish here. Ordinary Codex binding is
    # performed by PostToolUse from this successful structured result.
    exact_session_id = _current_session_identity(control_root)
    if _server_runtime() == "codex" and (args.get("workspace") is not None or defer_binding):
        exact_session_id = ""
    defer_codex_binding = not exact_session_id
    session_id = exact_session_id
    if not defer_codex_binding and task_control_status(td, control) == "open" and _session_resumes(
        repo_root, td, session_id,
    ):
        # Rebind only this exact session after proving it steals no open focus.
        write_active_marker(repo_root, td, session_id=session_id)
    ctx = emit_compact_context(td)
    if "error" in ctx:
        return _err("task_context failed", data=ctx)
    if binding_handoff is not None:
        binding_handoff.update({"context": ctx})
    return _ok({"task_dir": td, "task_id": ti, "run_id": control["run_id"],
                **({"workspace": repo_root} if args.get("workspace") is not None else {}),
                "task_context": ctx})


def handle_task_verify(args: dict) -> dict:
    ti = _req(args, "task_id")
    td = canonical_task_dir(task_id=ti, repo_root=_task_roots(args)[1])
    initial_control = _validated_task_control(td)
    if not initial_control:
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
    initial_wbs = wbs.read_state(td)
    progress = wbs.task_progress(td, _task_roots(args)[1], initial_control["run_id"], measure=True)
    from verification_target import measure_inputs
    target_inputs = measure_inputs(td, _task_roots(args)[1])
    with task_control_transaction(td):
        if read_task_control(td) != initial_control or wbs.read_state(td) != initial_wbs:
            return _err("task_verify refused: task authority or WBS changed during verification")
        ctx = emit_compact_context(td, measured_progress=progress, measured_target_inputs=target_inputs)
    payload = {"task_dir": td, **{key: ctx[key] for key in (
        "runtime_verdict", "verification_basis", "workflow_verification", "verification_targets",
        "next_action", "missing_for_close", "required_review_lenses", "required_qa_lenses", "progress")}}
    if verify_run is not None:
        payload["verify_run"] = verify_run
    return _ok(payload)


def handle_task_close(args: dict) -> dict:
    ti = _req(args, "task_id")
    td = canonical_task_dir(task_id=ti, repo_root=_task_roots(args)[1])
    initial_control = _validated_task_control(td)
    if not initial_control:
        return _invalid_task_control_error("task_close", td)
    initial_status = task_control_status(td, initial_control)
    if initial_status != "open":
        return _err(
            "task_close blocked: task is not open",
            data={"task_dir": td, "status": initial_status,
                  "next_action": _task_resume_next_action(initial_status)},
        )
    control_root = find_harness_root(td) or find_repo_root(td)
    with active_session_transaction(control_root), task_control_transaction(td):
        wbs.require_integrated_for_task(control_root, os.path.basename(td))
        def close_error(message, data):
            return _err(message, data=dict(data))

        ctx = emit_compact_context(td, measure_wbs=True)
        missing = ctx.get("missing_for_close") or []
        if missing:
            data = {
                "task_dir": td, "missing_for_close": missing, "task_context": ctx,
            }
            return close_error("task_close blocked", data)

        st = _validated_task_control(td)
        if not st:
            return _invalid_task_control_error("task_close", td)
        locked_status = task_control_status(td, st)
        if locked_status != "open":
            return close_error("task_close blocked: task changed terminal state", {
                "task_dir": td, "status": locked_status,
            })
        preclose_control = dict(st)
        session_id = _current_session_identity(control_root)
        preclose_marker_snapshot = active_marker_snapshot(control_root, session_id=session_id)
        try:
            publish_task_close(td, st)
            clear_active_marker(control_root, td, session_id=session_id, strict=True)
            if os.path.realpath(resolve_active_task_dir(control_root, session_id=session_id) or "") == os.path.realpath(td):
                raise RuntimeError("active task marker cleanup unavailable")

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
    td = canonical_task_dir(task_id=_req(args, "task_id"), repo_root=_task_roots(args)[1])
    st = _validated_task_control(td)
    if not st:
        return _invalid_task_control_error("task_blocked", td)
    status = task_control_status(td, st)
    if status != "open":
        return _err(
            "task_blocked refused: task is not open",
            data={"task_dir": td, "status": status,
                  "next_action": _task_resume_next_action(status)},
        )
    reason = _blocked_text(args, "blocked_reason")
    unblock = _blocked_text(args, "unblock_condition")
    task_root = find_harness_root(td) or find_repo_root(td)
    with active_session_transaction(task_root), task_control_transaction(td):
        return _handle_task_blocked_locked(td, reason=reason, unblock=unblock)


def _handle_task_blocked_locked(td: str, *, reason: str, unblock: str) -> dict:
    st = _validated_task_control(td)
    if not st:
        return _invalid_task_control_error("task_blocked", td)
    status = task_control_status(td, st)
    if status != "open":
        return _err(
            "task_blocked refused: task is not open",
            data={"task_dir": td, "status": status,
                  "next_action": _task_resume_next_action(status)},
        )
    resume_guidance = (
        "Call wbs(action='open') with this wbs_id to resume the same run and preserve "
        "its WBS checks and history. Pass fresh_run=true only to deliberately start "
        "a new execution run."
    )
    blocked_md = (
        "# BLOCKED\n\n"
        f"## Blocked Reason\n{reason}\n\n"
        f"## Unblock Condition\n{unblock}\n\n"
        f"## Resume\n{resume_guidance}\n\n"
        f"## Blocked At\n{now_iso()}\n"
    )
    blocked_path = os.path.join(td, "BLOCKED.md")
    # Markers live in the checkout that owns the task (a batch lead's worktree
    # or the main checkout), exactly as task_close resolves them.
    task_root = find_harness_root(td) or find_repo_root(td)
    session_id = _current_session_identity(task_root)
    marker_snapshot = active_marker_snapshot(task_root, session_id=session_id)
    blocked_snapshot = {
        blocked_path: _strict_regular_text_snapshot(blocked_path, max_size=256 * 1024)
    }
    try:
        _atomic_write_text(blocked_path, blocked_md)
        clear_active_marker(task_root, td, session_id=session_id, strict=True)
    except Exception:
        _restore_text_snapshots(blocked_snapshot)
        restore_active_marker_snapshot(marker_snapshot)
        raise
    return _ok({
        "task_dir": td,
        "status": "blocked",
        "runtime_verdict": "BLOCKED_ENV",
        "blocked_artifact": _task_artifact_rel(td, "BLOCKED.md"),
        "next_action": resume_guidance,
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


WBS_CHECK_TIMEOUT_MAX = 300


def handle_task_wbs(args: dict) -> dict:
    """Explicit bounded check or read-only work-pack proposal; never a verdict."""
    import wbs
    _, repo = _task_roots(args)
    td = canonical_task_dir(task_id=_req(args, "task_id"), repo_root=repo)
    control = _validated_task_control(td)
    if not control:
        return _invalid_task_control_error("task_wbs", td)
    action = args.get("action", "context")
    if action == "context":
        return _ok({"progress": wbs.task_progress(td, repo, control["run_id"], measure=False)})
    if action in {"block_outcome", "open_outcome"}:
        with task_control_transaction(td):
            if _validated_task_control(td) != control or task_control_status(td, control) != "open":
                raise ValueError("outcome operation requires the same open task run")
            body = wbs.block_outcome(td, _req(args, "leaf_id"), run_id=control["run_id"], clear=action == "open_outcome",
                                    reason=args.get("blocked_reason"), unblock_condition=args.get("unblock_condition"))
            return _ok(dict(body, task_dir=td, task_id=os.path.basename(td), run_id=control["run_id"],
                            wbs_id="WBS__" + os.path.basename(td)[6:]))
    if action == "dispatch":
        with task_control_transaction(td):
            status = task_control_status(td, control)
            if (_validated_task_control(td) != control or status not in {"open", "blocked"}
                    or status == "blocked" and not (wbs.read_state(td) or {}).get("batch")):
                raise ValueError("WBS dispatch requires the same open task or parked managed parent")
            if 'handoffs' in args:
                return _ok(wbs.publish_handoffs(td, control['run_id'], args['expected_admission'], args['handoffs']))
            return _ok(wbs.work_pack(td, repo, control["run_id"], bind=True,
                                    ownership=args.get("ownership"), host=args.get("host")))
    if action != "verify":
        raise ValueError("task_wbs action must be context, verify or dispatch")
    timeout = args.get("timeout", 60)
    if type(timeout) is not int or not 1 <= timeout <= WBS_CHECK_TIMEOUT_MAX:
        raise ValueError(f"WBS verification timeout must be 1..{WBS_CHECK_TIMEOUT_MAX} seconds")
    with task_control_transaction(td):
        current = _validated_task_control(td)
        if current != control or task_control_status(td, current) != "open":
            raise ValueError("WBS verification requires the same open task run")
        return _ok(wbs.verify(td, repo, current["run_id"], _req(args, "leaf_id"), timeout=timeout))


def _managed_dispatch(common: dict, args: dict) -> dict:
    """Coordinate the existing pool; task and batch owners keep their locks."""
    import wbs
    import batch_worktrees
    control_root, repo = _task_roots(common)
    operation = args.get("operation", "prepare")
    if operation != "prepare" and (repo != control_root or Path(repo, ".git").is_file()):
        raise ValueError("worktree lifecycle must run from the surviving control checkout")
    fields = {
        "prepare": {"ownership", "host"},
        "status": set(),
        "bind": {"slug", "worker_id"},
        "complete": {"slug", "worker_id", "result", "worker_stopped"},
        "resume": {"slug", "worker_stopped"},
        "recover": {"slug", "worker_stopped", "recovery_commit"},
        "extend": {"slug", "worker_id", "worker_stopped", "scopes", "expected_head", "expected_run_id", "reason"},
    }
    if not isinstance(operation, str) or operation not in fields:
        raise ValueError("unknown worktree lifecycle operation")
    supplied = set(args) - {"action", "wbs_id", "workspace", "operation"}
    if supplied - fields[operation]:
        raise ValueError("unexpected fields for worktree operation: " + ", ".join(sorted(supplied - fields[operation])))
    if operation in {"bind", "complete", "resume", "recover", "extend"}:
        _req(args, "slug")
    if operation in {"bind", "complete", "extend"}:
        _req(args, "worker_id")
    if operation in {"complete", "resume", "recover", "extend"} and args.get("worker_stopped") is not True:
        raise ValueError("confirm the actual worker and all nested writers stopped with worker_stopped=true")
    if operation == "complete" and not isinstance(args.get("result"), dict):
        raise ValueError("complete requires the actual structured worker result")
    if operation == "extend":
        if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", _req(args, "expected_head")):
            raise ValueError("expected_head must be the exact observed full commit SHA")
        _req(args, "expected_run_id")
        if len(_req(args, "reason")) > 2000:
            raise ValueError("scope extension reason exceeds 2000 characters")
        scopes = args.get("scopes")
        if (not isinstance(scopes, list) or not 1 <= len(scopes) <= 100
                or any(not isinstance(path, str) or not path for path in scopes)):
            raise ValueError("extend requires 1..100 literal relative scopes")
    if "recovery_commit" in args and (not isinstance(args["recovery_commit"], str)
                                     or not re.fullmatch(r"[0-9a-f]{40}", args["recovery_commit"])):
        raise ValueError("recovery_commit must acknowledge the exact reviewed resolved tip SHA")
    td = canonical_task_dir(task_id=common["task_id"], repo_root=repo)
    control = _validated_task_control(td)
    if not control:
        return _invalid_task_control_error("dispatch", td)
    status = task_control_status(td, control)
    admitted = None
    if operation == "prepare":
        from parallel_dispatch import host_slots
        try:
            host_slots(args.get('host'))
        except ValueError as exc:
            return _err(str(exc), data={'field': 'host', 'next_action': 'Supply actual capacity and unique live/reserved id-role inventories, verification reserve and capability booleans.'})
        if status not in {"open", "blocked"}:
            raise ValueError("prepare requires an open or parked owning WBS")
        bound = _invoke("task_wbs", handle_task_wbs, dict(common, action="dispatch",
                        ownership=args.get("ownership"), host=args.get("host")))
        if bound.get("isError"):
            return bound
        admitted = bound["structuredContent"]
        if admitted['execution_admission']['mode'] != 'managed':
            return bound
        if status == "open":
            parked = _invoke("task_blocked", handle_task_blocked, dict(common,
                blocked_reason="WBS execution is handed off to its bound isolated worktree pool.",
                unblock_condition="Integrate and close the exact bound work pack, then reopen this WBS for combined verification."))
            if parked.get("isError"):
                return parked
    elif operation != "status" and status not in {"blocked", "closed"}:
        raise ValueError("park the owning WBS before operating its worktree pool")
    state = wbs.read_state(td)
    if not state or not state.get("batch"):
        raise ValueError("no bound work pack; first dispatch the owning WBS with execution ownership")
    options = {key: args[key] for key in fields[operation] - {"ownership", "host"} if key in args}
    if admitted is not None:
        options['available_slots'] = admitted['available_slots']
    capability_deferred = (operation == 'prepare' and admitted is not None
                           and not (args['host']['spawn_available'] and args['host']['continuation_available']))
    try:
        # Keep retained reservations visible, but do not claim or create any
        # worktree when the fresh host cannot execute its handoff.
        body = batch_worktrees.execute(repo, state["batch"],
            operation='status' if capability_deferred else operation,
            **({} if capability_deferred else options))
    except Exception as exc:
        # Creation can fail after the parent is parked. Preserve the exact
        # bound identity in the response so retry does not invent another pack.
        return _err("worktree lifecycle refused: " + str(exc), data={
            "wbs_id": "WBS__" + common["task_id"][6:],
            "batch_id": state["batch"]["batch_id"], "operation": operation,
            "next_action": "Inspect dispatch(operation='status') for this same WBS; retain its exact pool and recover or retry the failed operation.",
        })
    if admitted is not None:
        published = _invoke('task_wbs', handle_task_wbs, dict(common, action='dispatch',
                            expected_admission=admitted['execution_admission'],
                            handoffs=[item['slug'] for item in body['handoffs']]))
        if published.get('isError'):
            return published
        body['execution_admission'] = published['structuredContent']
    if capability_deferred:
        body['capability_deferred'] = True
        body['reasons']['capability'] = admitted['execution_admission']['serial_facts']
        body['next_action'] = ('Retain the exact pool and existing handoffs; refresh actual host capabilities '
                               'and prepare again when spawn and continuation are available. Independent running work continues.')
    if operation in {'complete', 'recover'} and body.get('status') == 'ok':
        body['refill_required'] = True
        body['next_action'] = ('Immediately refresh actual host inventory and call dispatch prepare for this same WBS. '
                               'Bind newly ready handoffs while independent running siblings continue; no wave barrier.')
    body.update(wbs_id="WBS__" + common["task_id"][6:], operation=operation,
                execution="integrated" if body.get("status") == "closed" else "awaiting_worktrees")
    return _ok(body)


def handle_verification_target(args: dict) -> dict:
    """Preissue concrete inputs under the existing canonical control writer."""
    import verification_target
    task_id = _req(args, "task_id")
    _, repo = _task_roots(args)
    common = args
    td = canonical_task_dir(task_id=task_id, repo_root=repo)
    requested_spec = verification_target._spec(args.get("target"))
    initial_control = _validated_task_control(td)
    if not initial_control or task_control_status(td, initial_control) != "open":
        return _err("target issuance requires an open planned task")
    with active_session_transaction(find_harness_root(td) or repo), task_control_transaction(td):
        control = _validated_task_control(td)
        if control != initial_control or task_control_status(td, control) != "open":
            return _err("target not issued: task/run changed during readiness preflight")
        prior_targets = wbs.read_state(td).get(verification_target.FIELD)
        try:
            issued = verification_target.issue_target(td, repo, requested_spec, output_dirs=args["target"].get("output_dirs", []))
        except (ValueError, OSError) as error:
            # Atomic publication can succeed before a filesystem durability
            # error is reported. Inspect the owner index rather than lie.
            try:
                was_issued = wbs.read_state(td).get(verification_target.FIELD) != prior_targets
            except (ValueError, OSError):
                was_issued = None
            return _err(str(error), data={"issued": was_issued,
                        "next_action": "Preserve the task and target inputs. Correct the named input or restore stable inputs, then issue the target again before new native roles."})
    public_target = {key: issued[key] for key in ("token", "run_id", "snapshot_root", "input_fingerprint", "spec", "context")}
    return _ok(dict(wbs_id="WBS__" + task_id[6:], issued=True,
                    target=public_target))


def handle_wbs(args: dict) -> dict:
    """One public outcome tree; existing handlers retain execution authority."""
    import wbs
    action = _req(args, "action")
    if action == "migrate":
        return handle_wbs_migrate(args)
    _, repo = _task_roots(args)
    _require_migrated(repo)
    if action == "open" and not args.get("wbs_id"):
        from _lib import native_wbs_id
        selector = native_wbs_id(_req(args, "objective"))
    else:
        selector = _req(args, "wbs_id")
    raw_id = selector[5:] if selector.startswith("WBS__") else selector
    task_id = canonical_task_id(task_id=raw_id)
    common = {"task_id": task_id}
    if "workspace" in args:
        common["workspace"] = args["workspace"]
    if action == "target":
        allowed = {"action", "wbs_id", "workspace", "target"}
        if set(args) - allowed:
            raise ValueError("target accepts only target specification plus WBS identity/workspace")
        return _invoke("verification_target", handle_verification_target,
                       dict(common, target=args.get("target")))
    routes = {
        "open": ("task_start", {"fresh_run"}),
        "context": ("task_context", set()),
        "plan": ("write_plan", {"plan", "required_lenses"}),
        "check": ("task_wbs", {"timeout"}),
        "dispatch": ("task_wbs", {"ownership", "operation", "slug", "worker_id", "result", "worker_stopped", "host", "recovery_commit", "scopes", "expected_head", "expected_run_id", "reason"}),
        "verify": ("task_verify", set()),
        "close": ("task_close", set()),
        "block": ("task_blocked", {"blocked_reason", "unblock_condition"}),
        "next": ("task_context", set()),
    }
    if action not in routes:
        raise ValueError("unknown WBS action")
    operation, fields = routes[action]
    unknown = set(args) - ({"action", "wbs_id", "workspace"} | fields
                          | ({"node_id"} if action in {"check", "open", "block"} else set())
                          | ({"objective"} if action == "open" else set()))
    if unknown:
        allowed = {"action", "wbs_id", "workspace"} | fields
        if action in {"check", "open", "block"}:
            allowed.add("node_id")
        if action == "open":
            allowed.add("objective")
        hint = f" timeout is supported only by check (1..{WBS_CHECK_TIMEOUT_MAX} seconds)." if "timeout" in unknown else ""
        raise ValueError(f"unexpected fields for WBS action {action}: "
                         + ", ".join(sorted(unknown))
                         + "; allowed fields: " + ", ".join(sorted(allowed)) + "." + hint)
    if action in {"open", "block"} and "node_id" in args:
        if set(args) - {"action", "wbs_id", "workspace", "node_id", "blocked_reason", "unblock_condition"}:
            raise ValueError("outcome operations preserve the existing task run")
        if action == "open" and ("blocked_reason" in args or "unblock_condition" in args):
            raise ValueError("open outcome accepts only node_id")
        return _invoke("task_wbs", _EXECUTION["task_wbs"], dict(common,
                       action="open_outcome" if action == "open" else "block_outcome",
                       leaf_id=_req(args, "node_id"),
                       **{key: args[key] for key in ("blocked_reason", "unblock_condition") if key in args}))
    if action == "dispatch":
        return _managed_dispatch(common, args)
    call_args = dict(common, **{key: args[key] for key in fields if key in args})
    if action == "plan":
        if wbs.from_plan(_req(args, "plan")) is None:
            raise ValueError("WBS planning requires an outcome tree; begin with one coarse outcome and refine during work")
    if action == "check":
        call_args.update(action="verify", leaf_id=_req(args, "node_id"))
    result = _invoke(operation, _EXECUTION[operation], call_args)
    if result.get("isError"):
        return result
    body = dict(result["structuredContent"], wbs_id="WBS__" + task_id[6:])
    if action in {"open", "context", "next"}:
        _, repo = _task_roots(common)
        td = body["task_dir"]
        state = wbs.read_state(td)
        projection = body["task_context"].get("progress", {})
        body["tree"] = state["nodes"] if state else []
        body["progress"] = projection
        from batch_pending import pending
        body["pending_worktrees"] = pending(_task_roots(common)[0])
        if action == "next":
            body["ready"] = wbs.ready_outcomes(state, projection) if state else []
    return _ok(body)


def handle_write_plan(args: dict) -> dict:
    allowed_args = {"task_id", "task_dir", "plan", "required_lenses", "workspace"}
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
                  "next_action": _task_resume_next_action(status)},
        )
    preflight = _prepare_write_plan(args, td, control)
    if isinstance(preflight, dict) and preflight.get("isError"):
        return preflight
    with task_control_transaction(td):
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
                  "next_action": _task_resume_next_action(status)},
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
    # Recheck the exact authority while holding the task transaction.
    current = _validated_task_control(td)
    if current != expected_control or current != control or task_control_status(td, current) != "open":
        return _err(
            "write_plan refused: task changed before publication",
            data={"task_dir": td, "written": [],
                  "next_action": "Call wbs(action='context') and retry only if the task is open."},
        )
    written: list[str] = []
    bytes_written: dict[str, int] = {}
    plan_path = os.path.join(td, "PLAN.md")
    control_path = task_control_file(td)
    try:
        import wbs
        wbs_state = wbs.prepare(td, checked_plan, control["run_id"])
        state_path = os.path.join(td, wbs.STATE)
        snapshots = {
            path: _strict_regular_text_snapshot(path, max_size=1024 * 1024)
            for path in (plan_path, control_path, state_path)
        }
        _record_write(plan_path, checked_plan, written, bytes_written)
        write_task_control(td, candidate_control)
        if wbs_state is not None:
            _record_write(state_path, wbs.encode_state(wbs_state), written, bytes_written)
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


_EXECUTION = {
    "task_start": handle_task_start, "task_context": handle_task_context,
    "write_plan": handle_write_plan, "task_wbs": handle_task_wbs,
    "task_verify": handle_task_verify, "task_close": handle_task_close,
    "task_blocked": handle_task_blocked,
}


def _require_migrated(repo):
    legacy = Path(repo, "doc/harness/goals")
    if legacy.is_symlink() or (legacy.exists() and any(legacy.glob("*.json"))):
        raise ValueError('Retired Goal records require explicit wbs(action="migrate") preview and apply=true before continuing.')


def handle_wbs_migrate(args):
    unknown = set(args) - {"action", "apply"}
    if unknown or type(args.get("apply", False)) is not bool:
        raise ValueError("migrate accepts only action and boolean apply")
    import goal_migration
    repo = _control_root()
    return _ok(goal_migration.apply(repo) if args.get("apply", False) else goal_migration.preview(repo))


for _control_writer in (
    handle_task_start, handle_task_context, handle_task_close,
    handle_task_blocked, handle_write_plan, handle_task_wbs,
    handle_wbs_migrate, handle_verification_target,
):
    _bind_control_writer(_control_writer)
del _control_writer
del _bind_control_writer


# ── Tool definitions ─────────────────────────────────────────────────────

TOOL_DEFS: list[dict[str, Any]] = [
    {"name": "wbs", "title": "Work through one WBS outcome tree",
     "description": "One WBS outcome tree. open/context/next/plan do not hash source; current progress is unmeasured and historical credit is labelled. check and final verify/close measure current inputs. PLAN may include one wbs-inputs JSON fence with source roots, artifact files, exclude and deny lists, chosen at verification time rather than as an edit plan. block/open with node_id park/reopen one outcome while independent work continues. Dispatch owns isolated worktrees; current WBS checks and safe integration proof gate completion; independent review and QA remain workflow-owned.",
     "inputSchema": {"type": "object", "properties": {
         "action": {"type": "string", "enum": ["open", "context", "plan", "check", "next", "dispatch", "verify", "close", "block", "migrate", "target"]},
         "wbs_id": {"type": "string", "description": "Stable root ID; WBS__<id>, legacy TASK__<id>, or a bare safe ID."},
         "objective": {"type": "string", "description": "For open without an ID, derive one stable root for this objective, including native Goals."},
         "node_id": {"type": "string", "description": "check target, or local outcome for block/open; omission keeps root lifecycle behavior."}, "plan": {"type": "string"},
         "target": {"type": "object", "description": "target only: preissue a frozen source view before native review/QA. inputs (explicit WBS input policy), commands (argv arrays), lenses, purpose acceptance|diagnostic, optional environment key names, toolchain executables, and output_dirs outside selected inputs (not part of target identity). Use returned snapshot_root as the source view for independent review and QA."},
         "required_lenses": {"type": "array", "items": {"type": "string"}},
         "ownership": {"type": "object", "description": "Execution-only ownership by pending outcome ID, supplied at actual worktree dispatch.", "additionalProperties": {"type": "array", "items": {"type": "string"}}},
         "operation": {"type": "string", "enum": ["prepare", "status", "bind", "complete", "resume", "recover", "extend"], "description": "dispatch lifecycle operation. prepare creates owned worktrees; bind admits the actual worker; complete integrates/disposes stopped results; extend adds execution ownership for an exact stopped bound worker before integration; status is read-only."},
         "slug": {"type": "string", "description": "Exact request slug returned by managed dispatch."},
         "worker_id": {"type": "string", "description": "Actual native host worker identity, never inferred from lead text."},
         "result": {"type": "object", "description": "Actual structured stopped-worker result; existing task/run/close proof remains authoritative."},
         "worker_stopped": {"type": "boolean", "description": "Coordinator confirmed the native worker and all nested writers stopped; required for complete/resume/recover."},
         "recovery_commit": {"type": "string", "pattern": "^[0-9a-f]{40}$", "description": "recover only: acknowledge the exact reviewed resolved rebase tip. Never accepted by normal completion; original result identity is preserved."},
         "scopes": {"type": "array", "minItems": 1, "maxItems": 100, "items": {"type": "string"}, "description": "extend only: added literal execution scopes; original WBS intent and assigned scopes remain intact."},
         "expected_head": {"type": "string", "pattern": "^([0-9a-f]{40}|[0-9a-f]{64})$", "description": "extend only: exact observed bound worktree HEAD; stale observations refuse."},
         "expected_run_id": {"type": "string", "description": "extend only: exact existing task run, preserved by the grant."},
         "reason": {"type": "string", "minLength": 1, "maxLength": 2000, "description": "extend only: concrete reason for newly discovered execution ownership; grants earn no progress."},
         "host": {"type": "object", "additionalProperties": False,
                  "required": ["capacity", "live_agents", "reserved_slots", "verification_reserve", "spawn_available", "continuation_available"],
                  "properties": {
                      "capacity": {"type": "integer", "minimum": 1, "maximum": 256},
                      "live_agents": {"type": "array", "minItems": 1, "maxItems": 256, "items": {"type": "object", "required": ["id", "role"], "additionalProperties": False, "properties": {"id": {"type": "string", "minLength": 1, "maxLength": 256}, "role": {"type": "string", "minLength": 1}}}},
                      "reserved_slots": {"type": "array", "maxItems": 256, "items": {"type": "object", "required": ["id", "role"], "additionalProperties": False, "properties": {"id": {"type": "string", "minLength": 1, "maxLength": 256}, "role": {"type": "string", "minLength": 1}}}},
                      "verification_reserve": {"type": "integer", "minimum": 1, "maximum": 256},
                      "spawn_available": {"type": "boolean"}, "continuation_available": {"type": "boolean"}},
                  "description": "prepare only: current caller-observed capacity, including one live coordinator and distinct actual reservation IDs. Supported occupied review/QA lenses fulfill verification reserve once. Graph/ownership derives direct or managed admission; this is not authentication or a host reservation."},
         "timeout": {"type": "integer", "minimum": 1, "maximum": WBS_CHECK_TIMEOUT_MAX,
                     "description": f"check only: assertion timeout in seconds (1..{WBS_CHECK_TIMEOUT_MAX}, default 60); not accepted by verify or other actions."},
         "apply": {"type": "boolean", "default": False, "description": "Explicitly apply previewed legacy-data migration."},
         "fresh_run": {"type": "boolean"}, "blocked_reason": {"type": "string"},
         "unblock_condition": {"type": "string"},
         "workspace": {"type": "string", "description": "Registered linked worktree for an execution envelope."}},
         "required": ["action"], "additionalProperties": False},
     "handler": handle_wbs},

]

TOOLS = {t["name"]: t for t in TOOL_DEFS}


def list_tools() -> list[dict]:
    return [{k: v for k, v in TOOLS["wbs"].items() if k != "handler"}]


def call_tool(name: str, args: dict | None) -> dict:
    if name not in TOOLS:
        return _err(f"Unknown tool: {name}")
    return _invoke(name, TOOLS[name]["handler"], args)


def _invoke(name, handler, args):
    """Shared error framing for public WBS and private execution owners."""
    try:
        return handler(args or {})
    except _ToolArgumentError as e:
        return _err(str(e), data=e.data)
    except ValueError as e:
        message = str(e)
        supplied = args or {}
        if "canonical task root" in message:
            field = "task_storage_root"
            raw = "doc/harness/tasks"
            expected = "a real, non-symlink doc/harness/tasks directory inside the repository"
            next_action = "Restore doc/harness/tasks and its existing parents as real repository directories, then retry."
        else:
            field = next(
                (key for key in ("task_dir", "task_id", "slug") if key in message),
                next((key for key in ("task_dir", "task_id", "slug") if key in supplied), "selector"),
            )
            raw = supplied.get(field) if field != "selector" else None
            expected = _FIELD_EXPECTED.get(field, "a valid selector")
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
        self.runtime = ""
        global _SERVER
        _SERVER = self

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
        while True:
            req = self._read()
            if req is None:
                return
            self.handle_request(req)


def main() -> int:
    McpServer().serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
