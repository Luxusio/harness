#!/usr/bin/env python3
"""harness MCP server — self-contained, 7-field TASK_STATE.

No plugin-legacy dependency. All operations are direct file I/O.
MCP tools: goal_start, goal_context, goal_add_task, goal_next_task, goal_finish,
           task_start, task_context, task_verify, task_close, task_blocked,
           write_plan.
"""

from __future__ import annotations
import json
import os
import re
import stat
import subprocess
import sys
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
        "harness MCP — Goal-first control plane plus 7-field TASK_STATE. "
        "Use goal_start/goal_context/goal_add_task/goal_next_task/goal_finish "
        "for native /goal orchestration. A Goal owns a child task queue; create "
        "or attach child tasks as scope expands. "
        "When no native goal context is active, a plain repo-mutating request "
        "may open or resume a harness task directly with task_start/task_context; "
        "hooks do not create tasks automatically. "
        "Protocol tool names are bare: goal_start, goal_context, "
        "goal_add_task, goal_next_task, goal_finish, task_start, "
        "task_context, task_verify, task_close, task_blocked, and write_plan. "
        "write_plan is the canonical task-local PLAN/CHECKS/AUDIT writer. "
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
    now_iso, read_state, write_state, set_state_field,
    ensure_task_scaffold, emit_compact_context, sync_from_git_diff,
    artifact_exists, canonical_task_dir, canonical_task_id,
    find_harness_root, harness_root_resolution, find_repo_root,
    runtime_is_stale as _runtime_is_stale,
    write_active_marker, clear_active_marker,
    receipt_runtime_verdict, subagent_receipt_summary, record_subagent_receipt,
    receipt_review_verdict, review_receipt_summary, required_review_lenses,
    review_snapshot_scope, refresh_review_snapshot, git_snapshot_warnings,
    revalidate_request_source_authorities,
    receipt_stream_fingerprint,
    _workspace_changed_path_fingerprints, _control_root_touched_path_fingerprints,
    _git_head_for_receipt,
    write_task_close_attestation, clear_task_close_attestation,
    read_current_goal, start_harness_goal, add_goal_task, next_goal_task,
    finish_harness_goal,
)


def _control_root() -> str:
    candidate = find_repo_root()
    root, error = harness_root_resolution(candidate)
    if error:
        raise RuntimeError(f"invalid Harness workspace at {root}: {error}")
    return root or candidate
try:
    from environment_snapshot import snapshot as _env_snapshot  # type: ignore
except Exception:
    _env_snapshot = None
try:
    from codex_lifecycle_watcher import WatcherManager as _WatcherManager  # type: ignore
except Exception:
    _WatcherManager = None

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


AUDIT_HEADER = (
    "| # | phase | decision | classification | principle | rationale | rejected_option |\n"
    "|---|---|---|---|---|---|---|\n"
)


def _atomic_write_text(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    import tempfile
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", prefix=".mcp.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ── PR2 close-gate helpers ──────────────────────────────────────────────
#
# `_runtime_is_stale` lives in `_lib.runtime_is_stale` so both the MCP
# server (close + verify) and `stop_gate.py` can reach it without
# cross-import from `mcp/` into `scripts/`. Imported at the top of this
# file. See `_lib.py` for the full helper + skip-list constants.


_CHECKS_VALID_STATUSES = {"open", "implemented_candidate", "passed", "failed", "deferred"}


def _checks_scalar(value: str, *, label: str) -> str:
    scalar = value.strip()
    starts_quoted = bool(scalar) and scalar[0] in {'"', "'"}
    ends_quoted = bool(scalar) and scalar[-1] in {'"', "'"}
    if starts_quoted or ends_quoted:
        if len(scalar) < 2 or not starts_quoted or scalar[0] != scalar[-1]:
            raise ValueError(f"CHECKS.yaml contains malformed quoted {label}")
        return scalar[1:-1]
    return scalar


def _checks_item_indent(lines: list[str]) -> int:
    wrapper = next(
        (re.match(r"^(?:checks|acceptance_criteria|acs|acceptance):\s*$", line) for line in lines
         if re.match(r"^(?:checks|acceptance_criteria|acs|acceptance):\s*$", line)),
        None,
    )
    return 2 if wrapper else 0


def _parse_checks_text(text: str) -> list[dict]:
    if not text.strip():
        raise ValueError("CHECKS.yaml is empty")
    lines = text.splitlines()
    expected_item_indent = _checks_item_indent(lines)
    blocks: list[str] = []
    current: list[str] = []
    prefix: list[str] = []
    suffix_started = False
    for line in lines:
        item_match = re.match(r"^(\s*)-\s+id:\s*", line)
        if item_match and len(item_match.group(1)) == expected_item_indent:
            if suffix_started:
                raise ValueError("CHECKS.yaml AC list cannot resume after top-level suffix metadata")
            if current:
                blocks.append("\n".join(current))
            current = [line]
        elif current:
            leading = len(line) - len(line.lstrip())
            if line.strip() and expected_item_indent > 0 and leading < expected_item_indent:
                blocks.append("\n".join(current))
                current = []
                suffix_started = True
                prefix.append(line)
            else:
                current.append(line)
        else:
            prefix.append(line)
    if current:
        blocks.append("\n".join(current))
    prefix_nested_allowed = False
    prefix_valid = True
    for line in prefix:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        leading = len(line) - len(line.lstrip())
        if leading:
            if not prefix_nested_allowed:
                prefix_valid = False
                break
            continue
        field_match = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*):\s*(.*)$", line)
        if not field_match:
            prefix_valid = False
            break
        prefix_nested_allowed = field_match.group(2).strip() in {"", "|", ">", "|-", ">-"}
    if not prefix_valid or not blocks:
        raise ValueError("CHECKS.yaml has no parseable AC list")

    items: list[dict] = []
    seen: set[str] = set()
    for block in blocks:
        block_lines = block.splitlines()
        first_line = block_lines[0]
        m_id = re.match(r"^(\s*)-\s+id:\s*(.*?)\s*$", first_line)
        field_indent = len(m_id.group(1)) + 2 if m_id else 2
        nested_allowed = False
        direct_fields: list[tuple[str, str]] = []
        for line in block_lines[1:]:
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            leading = len(line) - len(line.lstrip())
            if leading < field_indent:
                raise ValueError("CHECKS.yaml contains malformed content inside an AC block")
            if leading > field_indent:
                if not nested_allowed:
                    raise ValueError("CHECKS.yaml contains malformed nested content inside an AC block")
                continue
            field_match = re.match(r"^\s+([A-Za-z_][A-Za-z0-9_-]*):\s*(.*)$", line)
            if not field_match:
                raise ValueError("CHECKS.yaml contains malformed content inside an AC block")
            field_name, field_value = field_match.groups()
            direct_fields.append((field_name, field_value.strip()))
            nested_allowed = field_value.strip() in {"", "|", ">", "|-", ">-"}
        status_matches = [value for name, value in direct_fields if name == "status"]
        if len(status_matches) != 1:
            raise ValueError("CHECKS.yaml AC has missing or duplicate status")
        title_values = [value for name, value in direct_fields if name in {"title", "description"}]
        ac_id = _checks_scalar(m_id.group(2) if m_id else "", label="AC id")
        status = _checks_scalar(status_matches[0], label="status")
        if not ac_id or not re.fullmatch(r"AC-[A-Za-z0-9_.-]+", ac_id):
            raise ValueError("CHECKS.yaml contains an empty or invalid AC id")
        if ac_id in seen:
            raise ValueError(f"CHECKS.yaml contains duplicate AC id {ac_id}")
        if status not in _CHECKS_VALID_STATUSES:
            raise ValueError(f"CHECKS.yaml AC {ac_id} has missing or invalid status")
        seen.add(ac_id)
        title = _checks_scalar(title_values[0] if title_values else "", label="title")
        items.append({"id": ac_id, "status": status, "title": title[:120]})
    return items


def _parse_checks_yaml(td: str) -> list[dict] | None:
    """Parse CHECKS.yaml into [{id, status, title}, ...].

    Returns ``None`` only when the file is missing (legacy compatibility), and
    ``[]`` when a present ledger is invalid. Uses a narrow block scanner so the
    control plane does not add a YAML dependency.
    """
    checks_path = os.path.join(td, "CHECKS.yaml")
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_NONBLOCK"):
        flags |= os.O_NONBLOCK
    try:
        fd = os.open(checks_path, flags)
    except FileNotFoundError:
        return None
    except OSError:
        return []
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return []
        with os.fdopen(fd, encoding="utf-8") as f:
            fd = -1
            text = f.read()
    except (OSError, UnicodeError):
        return []
    finally:
        if fd >= 0:
            os.close(fd)

    try:
        return _parse_checks_text(text)
    except ValueError:
        return []


_CHECKS_GATE_TERMINAL = {"passed", "deferred"}
_AC_AUTO_PROMOTE_STATUSES = {"open"}


def _split_checks_blocks(text: str) -> list[str]:
    lines = text.splitlines(keepends=True)
    blocks: list[list[str]] = [[]]
    in_ac = False
    for line in lines:
        if re.match(r"^\s*-\s+id:\s*", line):
            blocks.append([line])
            in_ac = True
        elif in_ac:
            blocks[-1].append(line)
        else:
            blocks[-1].append(line)
    return ["".join(block) for block in blocks]


def _yaml_quote(value: str) -> str:
    escaped = str(value or "").replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


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


def _checks_gate_status(td: str) -> tuple[str, list[dict]]:
    """Return (``"ok"``|``"blocked"``|``"absent"``, blocking_acs).

    - ``ok``: CHECKS.yaml present, every AC in {passed, deferred}.
    - ``blocked``: CHECKS.yaml present, at least one AC not terminal.
      ``blocking_acs`` is the non-terminal subset (id, status, title).
    - ``absent``: CHECKS.yaml missing — caller warn-logs and proceeds.
    """
    items = _parse_checks_yaml(td)
    if items is None:
        return "absent", []
    if not items:
        return "invalid", []
    blocking = [ac for ac in items if ac["status"] not in _CHECKS_GATE_TERMINAL]
    return ("blocked" if blocking else "ok"), blocking


def _truthy(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in {"1", "true", "yes", "y", "on"}
    return False


def _set_block_field(block: str, field: str, value: str) -> str:
    item_match = re.match(r"^(\s*)-\s+id:\s*", block)
    indent = (item_match.group(1) if item_match else "") + "  "
    pattern = rf"^{re.escape(indent)}{re.escape(field)}:\s*.*$"
    replacement = f"{indent}{field}: {value}"
    new, count = re.subn(pattern, replacement, block, count=1, flags=re.MULTILINE)
    if count:
        return new
    suffix = "\n" if block.endswith("\n") else ""
    return block.rstrip("\n") + f"\n{indent}{field}: {value}" + suffix


def _auto_promote_open_acs(td: str, evidence: str) -> list[str]:
    """Promote open CHECKS.yaml ACs to passed after an explicit QA PASS.

    Only ``status: open`` is eligible. Failed/deferred/in-progress statuses are
    left for explicit update_checks calls so a broad QA PASS cannot erase known
    exceptions or previous failures.
    """
    checks_path = os.path.join(td, "CHECKS.yaml")
    try:
        text = _read_regular_text_no_follow(checks_path)
    except (OSError, UnicodeError, ValueError):
        return []
    if not text.strip():
        return []
    try:
        _parse_checks_text(text)
    except ValueError:
        return []

    blocks: list[str] = []
    current: list[str] = []
    prefix_lines: list[str] = []
    suffix_lines: list[str] = []
    suffix_started = False
    expected_item_indent = _checks_item_indent(text.splitlines())
    for line in text.splitlines():
        item_match = re.match(r"^(\s*)-\s+id:\s*", line)
        if item_match and len(item_match.group(1)) == expected_item_indent:
            if suffix_started:
                return []
            if current:
                blocks.append("\n".join(current))
            current = [line]
        elif current:
            leading = len(line) - len(line.lstrip())
            if line.strip() and expected_item_indent > 0 and leading < expected_item_indent:
                blocks.append("\n".join(current))
                current = []
                suffix_started = True
                suffix_lines.append(line)
            else:
                current.append(line)
        elif suffix_started:
            suffix_lines.append(line)
        else:
            prefix_lines.append(line)
    if current:
        blocks.append("\n".join(current))
    if not blocks:
        return []

    promoted: list[str] = []
    new_blocks: list[str] = []
    safe_evidence = (evidence or "SUBAGENT_RECEIPTS.jsonl").replace("\n", " ").strip()
    if len(safe_evidence) > 240:
        safe_evidence = safe_evidence[:237].rstrip() + "..."
    for block in blocks:
        m_id = re.match(r"^\s*-\s+id:\s*(\S+)", block)
        item_match = re.match(r"^(\s*)-\s+id:\s*", block)
        direct_indent = (item_match.group(1) if item_match else "") + "  "
        m_status = re.search(
            rf"^{re.escape(direct_indent)}status:\s*(\S+)", block, re.MULTILINE
        )
        try:
            ac_id = _checks_scalar(m_id.group(1) if m_id else "", label="AC id")
            status = _checks_scalar(m_status.group(1) if m_status else "open", label="status")
        except ValueError:
            return []
        if ac_id and status in _AC_AUTO_PROMOTE_STATUSES:
            block = _set_block_field(block, "status", "passed")
            block = _set_block_field(block, "last_updated", now_iso())
            block = _set_block_field(block, "evidence", safe_evidence)
            promoted.append(ac_id)
        new_blocks.append(block)
    if not promoted:
        return []
    new_text = "\n".join(prefix_lines + new_blocks + suffix_lines).rstrip("\n") + "\n"
    try:
        _atomic_write_text(checks_path, new_text)
    except OSError:
        return []
    return promoted


def _reconcile_acs_from_qa(td: str) -> dict:
    """Promote open ACs after all required QA completion receipts pass."""
    checks_status, _ = _checks_gate_status(td)
    if checks_status == "invalid":
        return {
            "promoted_acs": [],
            "reason": "CHECKS.yaml is present but invalid; fix it before reconciliation",
        }
    st = read_state(td)
    runtime_verdict = receipt_runtime_verdict(td, st)
    if runtime_verdict != "PASS":
        return {
            "promoted_acs": [],
            "reason": "required QA completion verdicts have not passed",
        }
    promoted = _auto_promote_open_acs(td, "SUBAGENT_RECEIPTS.jsonl task_verify PASS")
    return {
        "promoted_acs": promoted,
        "reason": "promoted open ACs from completed QA PASS receipts" if promoted else "no open ACs to promote",
    }


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


def _validated_task_state(td: str) -> dict:
    state = read_state(td)
    return state if state.get("task_id") == os.path.basename(td) else {}


def _invalid_task_state_error(operation: str, td: str) -> dict:
    return _err(
        f"{operation} failed: missing or invalid TASK_STATE.yaml",
        data={
            "task_dir": td,
            "next_action": "Restore a regular TASK_STATE.yaml whose task_id matches the canonical task directory, or call task_start to initialize it.",
        },
    )


def _minimal_task_start_context(task_dir: str, task_id: str) -> dict:
    """Return conservative, non-routing context after scaffold commit."""
    state = read_state(task_dir)
    return {
        "task_id": task_id,
        "task_dir": task_dir,
        "status": str(state.get("status") or "created"),
        "runtime_verdict": str(state.get("runtime_verdict") or "pending").upper(),
        "touched_paths": list(state.get("touched_paths") or []),
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
    existing_state_path = os.path.join(task_dir, "TASK_STATE.yaml")
    resumed_existing = os.path.lexists(existing_state_path)
    if resumed_existing:
        existing_state = read_state(task_dir)
        if existing_state.get("task_id") != tid:
            raise ValueError(
                "task_start refused existing TASK_STATE.yaml whose task_id does not match the canonical task directory"
            )
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
    with review_snapshot_scope(deadline_seconds=40):
        scaffold = ensure_task_scaffold(
            task_dir, tid, request_text=request_text, repo_root=repo_root
        )
        original_resumed_state = read_state(task_dir) if resumed_existing else {}

        def rollback_new_start():
            clear_active_marker(repo_root, task_dir)
            if resumed_existing:
                if original_resumed_state:
                    write_state(task_dir, original_resumed_state)
                return
            cleanup = list(scaffold.get("created") or [])
            cleanup.append(os.path.join(task_dir, "TASK_BASELINE.json"))
            for artifact in cleanup:
                try:
                    os.unlink(artifact)
                except FileNotFoundError:
                    pass

        try:
            resumed = read_state(task_dir)
            terminal_resume = str(resumed.get("status") or "").lower() in {
                "blocked", "closed",
            }
            if terminal_resume:
                resumed["status"] = "created"
                resumed["runtime_verdict"] = "pending"
                resumed["closed_at"] = None
                resumed["updated"] = now_iso()
                write_state(task_dir, resumed)
            if execution_mode == "micro":
                set_state_field(task_dir, "plan_session_state", "micro_loop")
        except Exception:
            rollback_new_start()
            raise

        try:
            ctx = emit_compact_context(task_dir)
            if "error" in ctx:
                raise RuntimeError(str(ctx.get("error") or "compact context unavailable"))
        except GitBindingError:
            rollback_new_start()
            raise
        except Exception as exc:
            detail = str(exc)
            if detail.startswith((
                "Git HEAD snapshot unavailable:",
                "task baseline Git snapshot unavailable:",
                "task baseline Git diff unavailable:",
                "Git submodule snapshot unavailable",
                "required task baseline missing",
                "task baseline integrity unavailable",
                "Git snapshot deadline exhausted before ",
            )):
                rollback_new_start()
                raise
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
        warnings.extend(
            warning for warning in git_snapshot_warnings()
            if warning not in warnings
        )
        # The active marker is the final publication step. Recheck pinned
        # authorities immediately afterward so failure rolls back a new task.
        try:
            write_active_marker(repo_root, task_dir)
            revalidate_request_source_authorities(repo_root)
        except Exception:
            rollback_new_start()
            raise
        if terminal_resume:
            clear_task_close_attestation(task_dir)
            try:
                os.unlink(os.path.join(task_dir, "BLOCKED.md"))
            except FileNotFoundError:
                pass

    # Best-effort environment snapshot runs after the coherent Git/context scope.
    snapshot_path = ""
    if _env_snapshot is not None:
        try:
            snapshot_path = _env_snapshot(task_dir, repo_root) or ""
        except Exception:
            snapshot_path = ""

    return _ok({
        "task_dir": task_dir, "task_id": tid, "task_context": ctx,
        "environment_snapshot": snapshot_path,
        "start_status": "ready_with_warnings" if warnings else "ready",
        "task_created": not resumed_existing,
        "resumed": resumed_existing,
        "warnings": warnings,
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
    if not _validated_task_state(td):
        return _invalid_task_state_error("task_context", td)
    with review_snapshot_scope():
        ctx = emit_compact_context(td)
        if "error" in ctx:
            return _err("task_context failed", data=ctx)
        return _ok({
            "task_dir": td,
            "task_context": ctx,
            "subagent_receipts": subagent_receipt_summary(td),
            "review_receipts": review_receipt_summary(td),
            "git_snapshot_warnings": git_snapshot_warnings(),
        })


def handle_task_verify(args: dict) -> dict:
    ti = _req(args, "task_id")
    td = canonical_task_dir(task_id=ti, repo_root=_control_root())
    if not _validated_task_state(td):
        return _invalid_task_state_error("task_verify", td)
    with review_snapshot_scope():
        sync_from_git_diff(td)
        verify_run = None
        if _truthy(args.get("run_commands")):
            max_workers_raw = args.get("max_workers")
            max_workers = int(max_workers_raw) if isinstance(max_workers_raw, int) and max_workers_raw > 0 else None
            verify_run = _run_verify_runner(
                td,
                parallel=_truthy(args.get("parallel")) or args.get("parallel") is None,
                max_workers=max_workers,
            )
            # Verification commands may mutate generated or source files.
            refresh_review_snapshot()

        stale, stale_path = _runtime_is_stale(td)
        st = read_state(td)
        effective_verdict = receipt_runtime_verdict(td, st)
        if (st.get("runtime_verdict") or "pending").upper() != effective_verdict:
            set_state_field(td, "runtime_verdict", effective_verdict if effective_verdict != "PENDING" else "pending")

        ac_reconcile = {"promoted_acs": [], "reason": "not requested"}
        if _truthy(args.get("reconcile_acs")):
            ac_reconcile = _reconcile_acs_from_qa(td)

        st = read_state(td)
        rv = receipt_runtime_verdict(td, st)
        review_verdict = receipt_review_verdict(td, st)
        ctx = emit_compact_context(td)
        payload = {
            "task_dir": td, "runtime_verdict": rv,
            "touched_paths": st.get("touched_paths") or [],
            "next_action": ctx.get("next_action", ""),
            "missing_for_close": ctx.get("missing_for_close", []),
            "report_path": _task_artifact_rel(td, "SUBAGENT_RECEIPTS.jsonl"),
            "review_verdict": review_verdict,
            "required_review_lenses": required_review_lenses(td, st),
            "review_report_path": _task_artifact_rel(td, "REVIEW_RECEIPTS.jsonl"),
            "stale": stale,
            "stale_path": stale_path,
            "ac_reconcile": ac_reconcile,
            "subagent_receipts": subagent_receipt_summary(td),
            "review_receipts": review_receipt_summary(td),
            "git_snapshot_warnings": git_snapshot_warnings(),
        }
        if verify_run is not None:
            payload["verify_run"] = verify_run
        return _ok(payload)


def handle_task_close(args: dict) -> dict:
    ti = _req(args, "task_id")
    td = canonical_task_dir(task_id=ti, repo_root=_control_root())
    if not _validated_task_state(td):
        return _invalid_task_state_error("task_close", td)
    with review_snapshot_scope():
        def close_error(message, data):
            payload = dict(data)
            payload["git_snapshot_warnings"] = git_snapshot_warnings()
            return _err(message, data=payload)

        try:
            sync_from_git_diff(td)
            control_root = find_harness_root(td) or find_repo_root(td)
            initial_snapshot = _workspace_changed_path_fingerprints(control_root)
            initial_control_snapshot = _control_root_touched_path_fingerprints(
                td, control_root
            )
        except RuntimeError:
            return close_error("task_close blocked: Git changed-path snapshot unavailable", {
                "task_dir": td, "git_snapshot_unavailable": True,
            })
        initial_head = _git_head_for_receipt(td)
        ctx = emit_compact_context(td)
        missing = ctx.get("missing_for_close") or []
        stale, stale_path = _runtime_is_stale(td)
        checks_status, blocking = _checks_gate_status(td)
        if checks_status == "invalid":
            return close_error(
                "task_close blocked: CHECKS.yaml is present but invalid",
                {
                    "task_dir": td,
                    "next_action": "Repair CHECKS.yaml through write_plan or update_checks.py, then re-run task_verify.",
                },
            )
        if missing:
            data = {
                "task_dir": td, "missing_for_close": missing, "task_context": ctx,
                "stale": stale, "stale_path": stale_path,
            }
            if checks_status == "blocked":
                data["blocking_acs"] = blocking
            return close_error("task_close blocked", data)

        if stale:
            return close_error("task_close blocked: runtime verification stale — re-run task_verify", {
                "task_dir": td, "stale_path": stale_path,
            })

        if checks_status == "blocked":
            return close_error("task_close blocked: CHECKS gate", {
                "task_dir": td, "blocking_acs": blocking,
            })

        if not initial_head:
            return close_error("task_close blocked: Git HEAD unavailable", {
                "task_dir": td, "head_unavailable": True,
            })

        # Rebuild source-derived state and reread live receipts immediately
        # before granting close. This catches changes during the first gate.
        refresh_review_snapshot()
        try:
            final_receipts_before = receipt_stream_fingerprint(td)
        except RuntimeError:
            return close_error("task_close blocked: receipt stream snapshot unavailable", {
                "task_dir": td, "receipt_snapshot_unavailable": True,
            })
        final_ctx = emit_compact_context(td)
        final_missing = final_ctx.get("missing_for_close") or []
        final_stale, final_stale_path = _runtime_is_stale(td)
        final_checks_status, final_blocking = _checks_gate_status(td)
        # The final gates can take long enough for an uncommitted edit to land.
        # Rebuild once more after they finish, then compare the end snapshot.
        refresh_review_snapshot()
        try:
            final_snapshot = _workspace_changed_path_fingerprints(control_root)
            final_control_snapshot = _control_root_touched_path_fingerprints(
                td, control_root
            )
        except RuntimeError:
            return close_error("task_close blocked: final Git changed-path snapshot unavailable", {
                "task_dir": td, "git_snapshot_unavailable": True,
            })
        final_head = _git_head_for_receipt(td)
        try:
            final_receipts_after = receipt_stream_fingerprint(td)
        except RuntimeError:
            return close_error("task_close blocked: final receipt stream snapshot unavailable", {
                "task_dir": td, "receipt_snapshot_unavailable": True,
            })
        receipt_stream_changed = final_receipts_after != final_receipts_before
        snapshot_changed = final_snapshot != initial_snapshot
        control_snapshot_changed = (
            final_control_snapshot != initial_control_snapshot
        )
        head_unavailable = not final_head
        head_changed = final_head != initial_head
        if receipt_stream_changed or snapshot_changed or control_snapshot_changed or head_unavailable or head_changed or final_missing or final_stale or final_checks_status in {"blocked", "invalid"}:
            return close_error("task_close blocked: final freshness changed — re-run task_verify", {
                "task_dir": td,
                "receipt_stream_changed": receipt_stream_changed,
                "snapshot_changed": snapshot_changed,
                "control_snapshot_changed": control_snapshot_changed,
                "head_unavailable": head_unavailable,
                "head_changed": head_changed,
                "missing_for_close": final_missing,
                "task_context": final_ctx,
                "stale": final_stale,
                "stale_path": final_stale_path,
                "blocking_acs": final_blocking if final_checks_status == "blocked" else [],
                "checks_invalid": final_checks_status == "invalid",
            })

        try:
            revalidate_request_source_authorities(control_root)
        except GitBindingError as exc:
            return close_error(
                "task_close blocked: registered source authority changed",
                {
                    "task_dir": td,
                    "code": exc.code,
                    "path": exc.path,
                    "invariant": exc.invariant,
                    "next_action": exc.next_action,
                },
            )

        st = _validated_task_state(td)
        if not st:
            return _invalid_task_state_error("task_close", td)
        st["status"] = "closed"
        st["runtime_verdict"] = "PASS"
        st["closed_at"] = now_iso()
        st["updated"] = now_iso()
        write_state(td, st)
        write_task_close_attestation(
            td,
            st,
            head_sha=final_head,
            receipt_fingerprint=final_receipts_after,
        )

        repo_root = _control_root()
        goal = read_current_goal(repo_root)
        if goal.get("status") == "active" and any(
            isinstance(task, dict) and task.get("task_id") == os.path.basename(td)
            for task in goal.get("tasks", [])
        ):
            add_goal_task(repo_root, os.path.basename(td), status="closed", task_dir=td)

        clear_active_marker(repo_root, td)
        st = read_state(td)
        return _ok({
            "task_dir": td, "closed": True, "status": st.get("status"),
            "gate_artifact": _task_artifact_rel(td, "PLAN.md"),
            "git_snapshot_warnings": git_snapshot_warnings(),
        })


def handle_task_blocked(args: dict) -> dict:
    ti = _req(args, "task_id")
    reason = _req(args, "blocked_reason")
    unblock = _req(args, "unblock_condition")
    td = canonical_task_dir(task_id=ti, repo_root=_control_root())
    st = _validated_task_state(td)
    if not st:
        return _invalid_task_state_error("task_blocked", td)
    blocked_md = (
        "# BLOCKED\n\n"
        f"## Blocked Reason\n{reason}\n\n"
        f"## Unblock Condition\n{unblock}\n\n"
        f"## Blocked At\n{now_iso()}\n"
    )
    _atomic_write_text(os.path.join(td, "BLOCKED.md"), blocked_md)
    st["status"] = "blocked"
    st["runtime_verdict"] = "BLOCKED_ENV"
    st["updated"] = now_iso()
    write_state(td, st)
    clear_active_marker(_control_root(), td)
    return _ok({
        "task_dir": td,
        "status": "blocked",
        "runtime_verdict": "BLOCKED_ENV",
        "blocked_artifact": _task_artifact_rel(td, "BLOCKED.md"),
    })


def _plan_meta_dict(td: str, artifact: str, meta: dict | None = None) -> dict:
    out: dict[str, Any] = {
        "artifact": artifact,
        "task_id": os.path.basename(os.path.abspath(td)),
        "author_role": "plan-skill",
        "written_at": now_iso(),
    }
    if meta:
        out["plan_meta"] = meta
    return out


def _coerce_meta(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


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


def _normalize_audit_content(value: str) -> tuple[str, str]:
    """Accept natural Markdown audit tables and return canonical data rows."""
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    if lines and re.fullmatch(r"#{1,6}\s+audit trail", lines[0], re.IGNORECASE):
        lines.pop(0)
    canonical_header = (
        "#", "phase", "decision", "classification", "principle", "rationale",
        "rejected_option",
    )
    if lines and lines[0].startswith("|") and lines[0].endswith("|"):
        first_cells = tuple(
            cell.strip().lower() for cell in lines[0][1:-1].split("|")
        )
        if first_cells == canonical_header:
            lines.pop(0)
            if lines and re.fullmatch(r"\|(?:\s*:?-{3,}:?\s*\|)+", lines[0]):
                lines.pop(0)
        elif first_cells and first_cells[0] == "#":
            return "", (
                "Use the audit header columns '#, phase, decision, classification, "
                "principle, rationale, rejected_option', or pass data rows only."
            )
    if not lines:
        return "", "Pass at least one audit data row after the optional heading and table header."
    valid_rows = True
    for line in lines:
        if not line.startswith("|") or not line.endswith("|"):
            valid_rows = False
            break
        cells = [cell.strip() for cell in line[1:-1].split("|")]
        if (
            len(cells) < 3
            or any(not cell for cell in cells)
            or all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells)
        ):
            valid_rows = False
            break
    if not valid_rows:
        return "", (
            "Pass at least one complete audit data row with three or more "
            "non-empty cells, or a full Markdown table with an optional "
            "'# Audit Trail' heading, header row, and separator."
        )
    return "\n".join(lines) + "\n", ""


def _read_regular_text_no_follow(path: str) -> str:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_NONBLOCK"):
        flags |= os.O_NONBLOCK
    fd = os.open(path, flags)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("existing audit artifact is not a regular file")
        with os.fdopen(fd, "r", encoding="utf-8") as f:
            fd = -1
            return f.read()
    finally:
        if fd >= 0:
            os.close(fd)


def _record_write(path: str, text: str, written: list[str], bytes_written: dict[str, int]) -> None:
    _atomic_write_text(path, text)
    name = os.path.basename(path)
    written.append(name)
    bytes_written[name] = len(text.encode("utf-8"))


def handle_write_plan(args: dict) -> dict:
    """Write the minimal task-local planning artifacts in one MCP call."""
    td = _resolve_td(args)
    if not _validated_task_state(td):
        return _invalid_task_state_error("write_plan", td)
    raw_plan = args.get("plan")
    plan = raw_plan if isinstance(raw_plan, str) else ""
    raw_checks = args.get("checks")
    checks = raw_checks if isinstance(raw_checks, str) else None
    raw_audit = args.get("audit")
    audit = raw_audit if isinstance(raw_audit, str) else None
    meta = _coerce_meta(args.get("meta"))
    checked_plan = _nonempty_artifact_content(plan, artifact="plan", filename="PLAN.md")
    if isinstance(checked_plan, dict):
        return checked_plan
    plan_meta = json.dumps(_plan_meta_dict(td, "PLAN.md", meta), indent=2, ensure_ascii=False) + "\n"
    checked_checks = None
    if checks is not None:
        checked_checks = _nonempty_artifact_content(checks, artifact="checks", filename="CHECKS.yaml")
        if isinstance(checked_checks, dict):
            return checked_checks
        try:
            _parse_checks_text(checked_checks)
        except ValueError as exc:
            return _err(
                f"write_plan refused invalid CHECKS.yaml: {exc}",
                data={"artifact": "checks", "filename": "CHECKS.yaml", "written": [],
                      "next_action": "Fix the named CHECKS ledger error and retry write_plan."},
            )

    checked_audit = None
    audit_path = os.path.join(td, "AUDIT_TRAIL.md")
    audit_content = None
    if audit is not None:
        checked_audit = _nonempty_artifact_content(audit, artifact="audit", filename="AUDIT_TRAIL.md")
        if isinstance(checked_audit, dict):
            return checked_audit
        checked_audit, audit_error = _normalize_audit_content(checked_audit)
        if audit_error:
            return _err(
                "write_plan refused invalid AUDIT_TRAIL.md; could not understand the supplied audit table",
                data={"artifact": "audit", "filename": "AUDIT_TRAIL.md", "written": [],
                      "next_action": audit_error,
                      "example": "| 1 | phase | decision | classification | principle | rationale | rejected_option |"},
            )
        try:
            if os.path.isfile(audit_path):
                existing = _read_regular_text_no_follow(audit_path)
            elif os.path.lexists(audit_path):
                raise ValueError("existing audit artifact is not a regular file")
            else:
                existing = ""
        except (OSError, ValueError) as exc:
            return _err(
                f"write_plan refused unsafe AUDIT_TRAIL.md: {exc}",
                data={"artifact": "audit", "filename": "AUDIT_TRAIL.md", "written": [],
                      "next_action": "Replace the audit leaf with a regular repository file, then retry."},
            )
        first_line = existing.lstrip("\n").split("\n")[0] if existing.strip() else ""
        has_header = first_line.startswith("| # |")
        if not existing.strip():
            new_content = AUDIT_HEADER + checked_audit.rstrip("\n") + "\n"
        elif has_header:
            new_content = existing.rstrip("\n") + "\n" + checked_audit.rstrip("\n") + "\n"
        else:
            new_content = existing.rstrip("\n") + "\n\n" + AUDIT_HEADER + checked_audit.rstrip("\n") + "\n"
        audit_content = new_content

    written: list[str] = []
    bytes_written: dict[str, int] = {}
    _record_write(os.path.join(td, "PLAN.md"), checked_plan, written, bytes_written)
    _record_write(os.path.join(td, "PLAN.meta.json"), plan_meta, written, bytes_written)
    if checked_checks is not None:
        _record_write(os.path.join(td, "CHECKS.yaml"), checked_checks, written, bytes_written)
    if audit_content is not None:
        _record_write(audit_path, audit_content, written, bytes_written)

    return _ok({
        "artifact": "plan",
        "task_dir": td,
        "written": written,
        "bytes_written": bytes_written,
    })


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
     "description": "Create task scaffolding (7-field TASK_STATE) and return fresh context. Use directly for plain repo-mutating requests when no native goal context is active. Pass execution_mode='micro' for explicit no-plan develop->verify->close mode; verification remains mandatory.",
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
     "description": "Sync changed paths and compute verification state from fresh hook-owned review completion receipts followed by QA completion receipts. Optional reconcile_acs promotes open CHECKS.yaml ACs only after ordered review and QA gates pass.",
     "inputSchema": {"type": "object", "properties": {
         "task_id": {"type": "string"},
         "run_commands": {"type": "boolean"},
         "reconcile_acs": {"type": "boolean", "description": "Optional. When true, promote open CHECKS.yaml ACs only after all required QA completion receipts report a fresh PASS. Failed/deferred ACs are never promoted."},
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
    {"name": "task_blocked", "title": "Park a task on a real environment blocker",
     "description": "Record BLOCKED_ENV, write BLOCKED.md, set status=blocked, and clear this session's active marker. This is not completion.",
     "inputSchema": {"type": "object", "properties": {
         "task_id": {"type": "string"},
         "blocked_reason": {"type": "string"},
         "unblock_condition": {"type": "string"}},
         "required": ["task_id", "blocked_reason", "unblock_condition"],
         "additionalProperties": False},
     "handler": handle_task_blocked},
    {"name": "write_plan", "title": "Write task plan artifacts",
     "description": "Write the minimal task-local planning artifacts: PLAN.md and PLAN.meta.json, with optional CHECKS.yaml and AUDIT_TRAIL.md. This is the only MCP writer for plan-owned task artifacts.",
     "inputSchema": {"type": "object", "properties": {
         "task_id": {"type": "string"}, "task_dir": {"type": "string"},
         "plan": {"type": "string"},
         "checks": {"type": "string"},
         "audit": {"type": "string"},
         "meta": {"type": ["object", "string"]}},
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

    def _start_codex_watchers(self) -> None:
        if self.watcher_manager is not None or _WatcherManager is None:
            return
        try:
            self.watcher_manager = _WatcherManager(_control_root()).start()
        except Exception:
            # Lifecycle attestation is fail-closed.  A watcher failure must not
            # take down task_context or other MCP control-plane operations.
            self.watcher_manager = None

    def close(self) -> None:
        manager = self.watcher_manager
        self.watcher_manager = None
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
