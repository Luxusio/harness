#!/usr/bin/env python3
"""PreToolUse hook — enforce artifact ownership, plan-first rule, scope lock.

Signalling contract:
  - Deny: emit JSON ``{"hookSpecificOutput":{"hookEventName":"PreToolUse",
    "permissionDecision":"deny","permissionDecisionReason":"..."}}`` on stdout,
    then exit 0. Claude Code blocks the tool call; the hook wrapper's
    ``|| true`` (C-12 fail-safe) does not mask the decision because it rides
    on stdout payload, not the process exit code.
  - Allow: silent exit 0 (silence is the trust signal for allowed calls).
  - Unexpected exception inside main() → logged via ``_log_gate_error`` and
    exit 0 (fail-open). Top-level import errors are caught at module load so
    a broken ``_lib`` cannot freeze the session.

Escape hatch: ``HARNESS_SKIP_PREWRITE=1`` → one-shot allow + log ``gate-bypass``.
Dormant-repo behavior follows manifest strictness. Strict-compliance repos
  deny source writes until task_start establishes the canonical loop. Other
  repos remain fail-open when no task is active.
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import sys
from contextlib import redirect_stdout
from io import StringIO

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from _lib import (
        read_hook_input,
        emit_permission_decision,
        _log_gate_error,
        log_gate_crash,
        last_hook_input,
        _escape_hint,
        log_gate_bypass,
        find_repo_root,
        find_harness_root,
        harness_root_resolution,
        yaml_array,
        now_iso,
        TASK_DIR,
        resolve_active_task_dir,
        iter_active_task_dirs,
        read_state,
        current_session_id,
        is_harness_enabled_repo,
        _manifest_bool,
    )
except Exception:
    # _lib unavailable: fail-open. `|| true` would mask an ImportError exit
    # anyway; making it explicit keeps behaviour deterministic.
    sys.exit(0)


# ── Static policy ──────────────────────────────────────────────────────────

# Owner values are space-free so the structured reason tail stays grep-stable.
# Human text in the deny message names the concrete owning tool or lifecycle hook.
PROTECTED_ARTIFACTS = {
    "PLAN.md": "plan-skill",
    "PLAN.meta.json": "plan-skill",
    "AUDIT_TRAIL.md": "plan-skill",
    "RECEIPTS.jsonl": "receipt-lifecycle-hook",
    "SUBAGENT_RECEIPTS.jsonl": "legacy-receipt-hook",
    "REVIEW_RECEIPTS.jsonl": "legacy-receipt-hook",
    "INSTALL_RECEIPT.json": "verified-install-helper",
    "TASK_BASELINE.json": "task-start-runtime",
    "TASK_RUN.json": "task-start-runtime",
    "CONVERSATION.md": "conversation-hook",
}

# Human-readable owner description (used in deny message text, not in the tail).
PROTECTED_ARTIFACT_HUMAN = {
    "PLAN.md": "plan-skill (Skill(harness:plan))",
    "PLAN.meta.json": "plan-skill",
    "AUDIT_TRAIL.md": "plan-skill",
    "RECEIPTS.jsonl": "Codex/Claude review and QA lifecycle hooks",
    "SUBAGENT_RECEIPTS.jsonl": "legacy lifecycle hooks",
    "REVIEW_RECEIPTS.jsonl": "legacy lifecycle hooks",
    "INSTALL_RECEIPT.json": "scripts/install_verified.py",
    "TASK_BASELINE.json": "task-start runtime",
    "TASK_RUN.json": "task-start runtime",
    "CONVERSATION.md": "Codex/Claude conversation hooks",
}

SOURCE_EXTENSIONS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java",
    ".c", ".cpp", ".h", ".hpp", ".cs", ".rb", ".php", ".swift",
    ".kt", ".scala", ".sh", ".bash", ".zsh", ".sql",
    ".svelte", ".vue", ".astro",
}

# Paths below these prefixes (relative to repo root) are exempt from the
# plan-first requirement. Matched as string prefixes.
EXEMPT_PREFIXES = (
    "doc/harness/learnings.jsonl",
    "doc/harness/qa",
    "doc/harness/checkpoints",
    "doc/harness/patterns",
    "doc/harness/retros",
    "doc/harness/visual-baselines",
)

# Workflow control surfaces (relative to repo root). Direct writes are only
# permitted from a task with a MAINTENANCE marker; the maintain/plan skills
# own specific artifacts inside this set and route through their own flows.
WORKFLOW_CONTROL_SURFACE = {
    "plugin/CLAUDE.md",
    "plugin/hooks/hooks.json",
    "plugin/mcp/harness_server.py",
    "plugin/scripts/prewrite_gate.py",
    "plugin/scripts/mcp_bash_guard.py",
    "plugin/scripts/stop_gate.py",
    "plugin/scripts/_lib.py",
    "doc/harness/manifest.yaml",
}

# Structured-reason doc link per rule id (rendered into ``docs=`` tail field).
RULE_DOCS = {
    "C-02-plan-first": "doc/harness/patterns/prewrite-gate.md",
    "C-05-protected-artifact": "doc/harness/patterns/prewrite-gate.md",
    "scope-lock-forbidden": "doc/harness/patterns/scope-lock.md",
    "no-active-task": "doc/harness/patterns/prewrite-gate.md",
    "invalid-active": "doc/harness/patterns/prewrite-gate.md",
    "workflow-control-surface": "doc/harness/patterns/prewrite-gate.md",
}

GATE_NAME = "prewrite"


# ── Path classification helpers (module-level; imported by mcp_bash_guard) ──


def _rel(path, repo_root):
    """Best-effort repo-relative path with ``./`` stripped."""
    try:
        rel = os.path.relpath(os.path.abspath(path), repo_root)
    except ValueError:
        rel = path
    if rel.startswith("./"):
        rel = rel[2:]
    return rel


def _is_protected_artifact(path):
    """Return True if ``path``'s basename is a protected artifact."""
    if not path:
        return False
    return os.path.basename(path) in PROTECTED_ARTIFACTS


def _is_workflow_control_surface(path, repo_root=None):
    """Return True if ``path`` is a harness workflow-control-surface file."""
    if not path:
        return False
    root = repo_root or find_repo_root()
    return _rel(path, root) in WORKFLOW_CONTROL_SURFACE


def _is_source_file(path, repo_root=None):
    """Return True if ``path`` looks like a gated source file.

    Excludes: exempt prefixes, protected artifacts, workflow control surfaces,
    files inside task directories. Matches ``SOURCE_EXTENSIONS``.
    """
    if not path:
        return False
    root = repo_root or find_repo_root()
    rel = _rel(path, root)

    if rel.startswith(TASK_DIR + os.sep) or rel == TASK_DIR:
        return False
    for prefix in EXEMPT_PREFIXES:
        if rel == prefix or rel.startswith(prefix + os.sep) or rel.startswith(prefix):
            return False
    if _is_protected_artifact(path):
        return False
    if _is_workflow_control_surface(path, repo_root=root):
        return False

    _, ext = os.path.splitext(rel)
    return ext.lower() in SOURCE_EXTENSIONS


def _task_has_req_reference(task_dir: str, repo_root: str = "") -> bool:
    import re
    req_re = re.compile(r"doc/[^)\]\s`'\"]+/REQ__[A-Za-z0-9_.-]+\.md")
    # Scan task-local PLAN body for any REQ path string.
    for name in ("PLAN.md",):
        path = os.path.join(task_dir, name)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                if req_re.search(f.read()):
                    return True
        except OSError:
            continue
    # Back-link path: walk doc/**/REQ__*.md and look for a `source: task: <id>`
    # line written by req_scaffold.py.
    task_id = os.path.basename(os.path.normpath(task_dir))
    if not task_id.startswith("TASK__"):
        return False
    if not repo_root:
        # task_dir is always <repo>/doc/harness/tasks/TASK__<id> — walk 4 up.
        repo_root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.dirname(task_dir)))
        )
    doc_root = os.path.join(repo_root, "doc")
    if not os.path.isdir(doc_root):
        return False
    # REQ docs always live at doc/<area>/REQ__*.md per req_scaffold.create_req_doc
    # (req_scaffold.py:55 — `rel = os.path.join("doc", area, f"REQ__{slug}.md")`).
    # Depth-2 scan instead of os.walk keeps gate latency O(area-dirs × REQs-per-area)
    # so this back-link path doesn't dominate every source-edit gate call.
    back_link = f"source: task: {task_id}"
    try:
        areas = os.listdir(doc_root)
    except OSError:
        return False
    for area in areas:
        if area == "harness":
            # Skip doc/harness/ (contains tasks/, patterns/, etc; no project REQs).
            continue
        area_path = os.path.join(doc_root, area)
        if not os.path.isdir(area_path):
            continue
        try:
            entries = os.listdir(area_path)
        except OSError:
            continue
        for name in entries:
            if not (name.startswith("REQ__") and name.endswith(".md")):
                continue
            path = os.path.join(area_path, name)
            try:
                with open(path, encoding="utf-8") as f:
                    if back_link in f.read():
                        return True
            except OSError:
                continue
    return False


def _observable_req_needed_for_path(relpath: str) -> bool:
    try:
        from req_detector import detect_req_need  # type: ignore
        result = detect_req_need(paths=[relpath])
    except Exception:
        return False
    return bool(result.get("requires_req")) and str(result.get("confidence") or "").lower() in {
        "high",
        "medium",
    }


# ── Learnings.jsonl parse-fail log (reuse pre-existing pattern) ────────────


def _log_gate_parse_fail(repo_root, reason):
    try:
        if not is_harness_enabled_repo(repo_root):
            return
        learn_path = os.path.join(repo_root, "doc", "harness", "learnings.jsonl")
        os.makedirs(os.path.dirname(learn_path), exist_ok=True)
        entry = json.dumps({
            "ts": now_iso(),
            "type": "gate-parse-fail",
            "source": "prewrite_gate",
            "key": "gate-parse-fail",
            "insight": reason,
        })
        with open(learn_path, "a", encoding="utf-8") as f:
            f.write(entry + "\n")
    except Exception:
        pass


# ── Scope lock (PROGRESS.md allowed/forbidden/test paths) ──────────────────


def _read_progress_paths(active_dir):
    progress_path = os.path.join(active_dir, "PROGRESS.md")
    if not os.path.isfile(progress_path):
        return None
    return {
        "allowed_paths": yaml_array("allowed_paths", progress_path),
        "test_paths": yaml_array("test_paths", progress_path),
        "forbidden_paths": yaml_array("forbidden_paths", progress_path),
    }


def _path_matches(file_path, patterns, repo_root):
    try:
        rel = os.path.relpath(file_path, repo_root)
    except ValueError:
        return False
    for pat in patterns:
        pat = pat.strip()
        if not pat or os.path.isabs(pat) or pat.startswith(".."):
            continue
        if fnmatch.fnmatch(rel, pat):
            return True
        if fnmatch.fnmatch(rel, pat.rstrip("/") + "/**"):
            return True
        if pat.endswith("/") and rel.startswith(pat):
            return True
    return False


def _parse_progress_paths_safe(active_dir, repo_root):
    try:
        result = _read_progress_paths(active_dir)
        if result is None:
            return None
        cleaned = {}
        for key in ("allowed_paths", "test_paths", "forbidden_paths"):
            valid = []
            for raw in result.get(key, []):
                raw = raw.strip()
                if not raw or os.path.isabs(raw) or raw.startswith(".."):
                    if raw:
                        _log_gate_parse_fail(
                            repo_root,
                            f"PROGRESS.md {key} entry invalid (skipped): {raw}",
                        )
                    continue
                candidate = os.path.realpath(os.path.join(repo_root, raw))
                if not candidate.startswith(os.path.realpath(repo_root)):
                    _log_gate_parse_fail(
                        repo_root,
                        f"PROGRESS.md {key} entry resolves out-of-tree (skipped): {raw}",
                    )
                    continue
                valid.append(raw)
            cleaned[key] = valid
        return cleaned
    except Exception as exc:
        _log_gate_parse_fail(repo_root, f"PROGRESS.md parse error: {exc}")
        return None


# ── Structured reason tail + deny emission ─────────────────────────────────


def _tail(rule_id, file_path, owner, repo_root):
    docs = RULE_DOCS.get(rule_id, "doc/harness/patterns/prewrite-gate.md")
    return (
        f"[gate={GATE_NAME} rule={rule_id} "
        f"path={_rel(file_path, repo_root)} owner={owner} docs={docs}]"
    )


_RULE_NEXT_ACTION = {
    "C-02-plan-first": ("Skill('harness:plan', '<task_id>')",
                        "plan-skill",
                        "plugin/CLAUDE.md § 4 Plan-first rule"),
    "invalid-active": ("Open or resume a harness task; use native /goal when an explicit Goal is intended",
                       "harness-goal",
                       "plugin/CLAUDE.md § 6 Auto-routing"),
    "C-05-protected-artifact": ("",  # populated dynamically by callers via owner
                                "",
                                "CONTRACTS.md § C-05"),
    "C-09-scope-lock": ("Add the file to PROGRESS.md allowed_paths or revert the write",
                        "developer",
                        "doc/harness/patterns/prewrite-gate.md § scope-lock"),
}


def _owner_to_next_action(owner: str) -> str:
    """Map a protected-artifact owner string to a concrete next_action_command."""
    runtime = _runtime_name()
    if not owner:
        return ""
    o = owner.lower()
    if "plan-skill" in o:
        return _tool_hint("write_plan", _runtime_name())
    if "receipt-lifecycle-hook" in o:
        return "Spawn and await the required reviewer or QA agent; lifecycle hooks record RECEIPTS.jsonl"
    return ""


def _runtime_name() -> str:
    env = (os.environ.get("HARNESS_RUNTIME") or "").lower()
    if env in ("codex", "claude"):
        return env
    payload = last_hook_input()
    if payload.get("session_id") or os.environ.get("CODEX_HOME"):
        return "codex"
    return "claude"


def _tool_hint(tool: str, runtime: str | None = None) -> str:
    runtime = runtime or _runtime_name()
    args = {
        "write_plan": "task_id=..., plan=..., audit=..., meta=...",
    }.get(tool, "...")
    if runtime == "codex":
        return f"{tool} {{ {args} }}"
    return f"mcp__plugin_harness_harness__{tool}({args})"


def _deny(rule_id, file_path, owner, human_text, repo_root):
    tail = _tail(rule_id, file_path, owner, repo_root)
    hint = _escape_hint(GATE_NAME)
    mapped = _RULE_NEXT_ACTION.get(rule_id, ("", "", ""))
    next_action = mapped[0] or _owner_to_next_action(owner)
    owner_skill = mapped[1] or owner
    docs = mapped[2]
    emit_permission_decision(
        "deny", f"{tail} {human_text}\n{hint}",
        next_action_command=next_action,
        owner_skill=owner_skill,
        docs=docs,
    )


# ── Scope-lock enforcement ─────────────────────────────────────────────────


def _handle_scope_lock(file_path, active_dir, repo_root, task_id):
    """Return ``(should_block, reason_text)`` for the given write.

    Pure — does NOT emit on stdout; the caller (``main`` in this module)
    renders the deny. ``reason_text`` is non-empty only when ``should_block``
    is True, and is the full human + tail + escape-hint blob ready for
    ``emit_permission_decision``. Tests consume it to assert message content.

    One-shot bypass via ``HARNESS_DISABLE_SCOPE_LOCK=1``; the env var's
    lifetime belongs to the shell invoking the hook.
    """
    if os.environ.get("HARNESS_DISABLE_SCOPE_LOCK") == "1":
        try:
            audit_dir = os.path.join(active_dir, "audit")
            os.makedirs(audit_dir, exist_ok=True)
            flag_path = os.path.join(audit_dir, "scope-lock-bypass.flag")
            with open(flag_path, "w") as f:
                f.write(f"bypass at {now_iso()} for {file_path}\n")
        except Exception:
            pass
        return False, ""

    # Clear any stale bypass flag so presence alone does not grant bypass.
    try:
        flag_path = os.path.join(active_dir, "audit", "scope-lock-bypass.flag")
        if os.path.isfile(flag_path):
            os.unlink(flag_path)
    except Exception:
        pass

    paths = _parse_progress_paths_safe(active_dir, repo_root)
    if paths is None:
        return False, ""

    forbidden = paths.get("forbidden_paths", [])
    allowed = paths.get("allowed_paths", [])
    if forbidden and _path_matches(file_path, forbidden, repo_root):
        matching = next(
            (p for p in forbidden if _path_matches(file_path, [p], repo_root)),
            "?",
        )
        allowed_summary = ", ".join(allowed[:3])
        if len(allowed) > 3:
            allowed_summary += ", ..."
        rel = _rel(file_path, repo_root)
        human = (
            f"scope-lock: {rel} is in forbidden_paths for {task_id}. "
            f"forbidden: {matching}. allowed: {allowed_summary}. "
            f"Options: (a) edit doc/harness/tasks/{task_id}/PROGRESS.md to move "
            f"to allowed_paths, (b) revert this edit and move it to a separate "
            f"task, (c) one-shot bypass via HARNESS_DISABLE_SCOPE_LOCK=1."
        )
        tail = _tail("scope-lock-forbidden", file_path, "developer", repo_root)
        hint = _escape_hint(GATE_NAME)
        return True, f"{tail} {human}\n{hint}"
    return False, ""


# ── Dormant-repo helper ────────────────────────────────────────────────────


def _has_open_tasks(tasks_dir: str) -> bool:
    """Return True if tasks_dir contains any TASK__* whose status is not
    closed/stale/archived. Missing or unreadable status is treated as open
    (conservative: err toward deny rather than silent allow)."""
    if not os.path.isdir(tasks_dir):
        return False
    for entry in os.listdir(tasks_dir):
        if not entry.startswith("TASK__"):
            continue
        state_file = os.path.join(tasks_dir, entry, "TASK_STATE.yaml")
        if not os.path.isfile(state_file):
            return True  # malformed task dir — treat as open (conservative)
        try:
            # minimal line-scan for status: avoid full YAML parse to stay stdlib-light
            status = ""
            with open(state_file) as f:
                for line in f:
                    if line.startswith("status:"):
                        status = line.split(":", 1)[1].strip()
                        break
            if status not in ("closed", "stale", "archived", "blocked"):
                return True
        except Exception:
            return True  # read error — conservative
    return False


# ── Main ───────────────────────────────────────────────────────────────────


def _check_path(data: dict, file_path: str) -> None:
    payload_cwd = str(data.get("cwd") or "").strip()
    hook_cwd = os.path.realpath(payload_cwd or os.getcwd())
    requested_path = os.path.abspath(
        file_path if os.path.isabs(file_path) else os.path.join(hook_cwd, file_path)
    )
    if payload_cwd:
        harness_root, harness_error = harness_root_resolution(hook_cwd)
        repo_root = harness_root or find_repo_root(hook_cwd)
    else:
        candidate_root = find_repo_root()
        harness_root, harness_error = harness_root_resolution(candidate_root)
        repo_root = harness_root or candidate_root
    if harness_error:
        _deny(
            "invalid-harness-workspace",
            requested_path,
            "harness:setup",
            f"Harness workspace configuration is invalid: {harness_error}",
            repo_root,
        )
        return 0
    if not is_harness_enabled_repo(repo_root):
        return 0
    file_path = os.path.realpath(requested_path)
    try:
        requested_common = os.path.commonpath([repo_root, requested_path])
    except ValueError:
        requested_common = ""
    try:
        physical_common = os.path.commonpath([repo_root, file_path])
    except ValueError:
        physical_common = ""
    if physical_common != repo_root:
        if requested_common == repo_root:
            _deny(
                "symlink-outside-control",
                requested_path,
                "developer",
                "The requested path resolves outside the Harness control root.",
                repo_root,
            )
        return 0
    tasks_dir = os.path.join(repo_root, TASK_DIR)
    try:
        common = os.path.commonpath([repo_root, file_path])
    except ValueError:
        common = ""
    if common != repo_root:
        return 0
    inside_task_dir = (
        file_path == tasks_dir
        or file_path.startswith(tasks_dir + os.sep)
    )

    basename = os.path.basename(file_path)
    if inside_task_dir and basename in PROTECTED_ARTIFACTS:
        owner = PROTECTED_ARTIFACTS[basename]
        owner_human = PROTECTED_ARTIFACT_HUMAN.get(basename, owner)
        human = (
            f"{basename} is owned by {owner_human}. Use the owning skill or MCP "
            f"tool (e.g. write_plan for PLAN.md)."
        )
        _deny("C-05-protected-artifact", file_path, owner, human, repo_root)
        return 0

    if inside_task_dir:
        return 0

    # Exempt prefixes (harness operational files) allowed without a task.
    for prefix in EXEMPT_PREFIXES:
        prefix_abs = os.path.join(repo_root, prefix)
        if file_path == prefix_abs or file_path.startswith(prefix_abs + os.sep):
            return 0

    # Workflow control surface: only permitted from a MAINTENANCE task.
    if _is_workflow_control_surface(file_path, repo_root=repo_root):
        maint = False
        active_dir = resolve_active_task_dir(repo_root)
        if active_dir and os.path.isdir(active_dir):
            maint = os.path.isfile(os.path.join(active_dir, "MAINTENANCE"))
        if not maint:
            rel = _rel(file_path, repo_root)
            human = (
                f"{rel} is a workflow-control-surface file. Direct writes are "
                f"only permitted from tasks with a MAINTENANCE marker. "
                f"Touch doc/harness/tasks/<task>/MAINTENANCE and record the reason "
                f"in the task plan or final summary."
            )
            _deny("workflow-control-surface", file_path, "maintain-skill", human, repo_root)
            return 0
        return 0

    # Source files require an active task with PLAN.md.
    if not _is_source_file(file_path, repo_root=repo_root):
        return 0

    active_dir = resolve_active_task_dir(repo_root)
    if not active_dir:
        # Strict-compliance repositories cannot bypass the task/QA close gate
        # merely because this is the first write of a new request.
        strict = _manifest_bool(
            repo_root, "capabilities", "strict_compliance_requires_delegation"
        )
        if not strict and not _has_open_tasks(tasks_dir):
            return 0
        human = (
            "No active task. Source writes require the canonical loop. "
            "On Codex invoke $harness:run to open or resume the task before editing; "
            "use native /goal when an explicit Goal is intended."
        )
        _deny("no-active-task", file_path, "plan-skill", human, repo_root)
        return 0

    if not (active_dir and os.path.isdir(active_dir) and active_dir.startswith(tasks_dir)):
        human = (
            "Active task points to invalid path. On Codex invoke $harness:run "
            "to repair or resume routing before source writes."
        )
        _deny("invalid-active", file_path, "plan-skill", human, repo_root)
        return 0

    if not os.path.isfile(os.path.join(active_dir, "PLAN.md")):
        st = read_state(active_dir)
        micro_loop = str(st.get("plan_session_state") or "").strip().lower() in {
            "micro",
            "micro_loop",
            "no_plan_micro",
            "develop_verify_close",
        }
        if not os.path.isfile(os.path.join(active_dir, "MAINTENANCE")) and not micro_loop:
            human = (
                "PLAN.md does not exist yet. On Codex invoke $harness:run; "
                "the public entry loads the internal plan phase."
            )
            _deny("C-02-plan-first", file_path, "plan-skill", human, repo_root)
            return 0

    rel = _rel(file_path, repo_root)
    if _observable_req_needed_for_path(rel) and not _task_has_req_reference(active_dir):
        human = (
            f"{rel} looks like observable UI/API/native/desktop behavior, but "
            "the active task has no linked doc/<area>/REQ__*.md. Create or "
            "update the durable REQ first directly or with req_scaffold.py, "
            "then retry the source edit."
        )
        _deny("C-REQ-observable-doc-required", file_path, "developer", human, repo_root)
        return 0

    for other_dir in iter_active_task_dirs(repo_root):
        if os.path.normpath(other_dir) == os.path.normpath(active_dir):
            continue
        st = read_state(other_dir)
        if rel in (st.get("touched_paths") or []):
            other_id = os.path.basename(other_dir.rstrip("/"))
            human = (
                f"{rel} is already touched by active task {other_id}. "
                "Source-file conflicts between active tasks are blocked; finish "
                "or block one task before editing the same file."
            )
            _deny("C-09-scope-lock", file_path, "developer", human, repo_root)
            return 0

    # Scope-lock enforcement as the last check: active task + PLAN.md confirmed.
    try:
        task_id = os.path.basename(active_dir)
        should_block, reason_text = _handle_scope_lock(
            file_path, active_dir, repo_root, task_id,
        )
        if should_block:
            _sc = _RULE_NEXT_ACTION.get("C-09-scope-lock", ("", "", ""))
            emit_permission_decision(
                "deny", reason_text,
                next_action_command=_sc[0], owner_skill=_sc[1], docs=_sc[2],
            )
            return 0
    except Exception as exc:
        _log_gate_parse_fail(repo_root, f"scope-lock enforcement error: {exc}")

    return 0


_PATCH_PATH_RE = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$", re.MULTILINE)
_PATCH_MOVE_RE = re.compile(r"^\*\*\* Move to: (.+)$", re.MULTILINE)


def _write_paths(data: dict) -> list[str]:
    tool_name = str(data.get("tool_name") or "")
    tool_input = data.get("tool_input") or {}
    if tool_name in ("Write", "Edit", "MultiEdit") and isinstance(tool_input, dict):
        path = tool_input.get("file_path") or tool_input.get("path") or ""
        return [str(path)] if path else []
    if tool_name != "apply_patch":
        return []
    if isinstance(tool_input, str):
        patch = tool_input
    elif isinstance(tool_input, dict):
        patch = tool_input.get("patch") or tool_input.get("input") or ""
    else:
        patch = ""
    if not isinstance(patch, str):
        return []
    paths = _PATCH_PATH_RE.findall(patch) + _PATCH_MOVE_RE.findall(patch)
    return list(dict.fromkeys(path.removesuffix("\r") for path in paths))


def main():
    data = read_hook_input()
    if not data:
        return 0
    paths = _write_paths(data)

    # One-shot escape hatch: log and allow (silent stdout).
    if os.environ.get("HARNESS_SKIP_PREWRITE") == "1":
        log_gate_bypass(GATE_NAME, ",".join(paths)[:400])
        return 0

    for file_path in paths:
        decision = StringIO()
        with redirect_stdout(decision):
            _check_path(data, file_path)
        if decision.getvalue():
            sys.stdout.write(decision.getvalue())
            return 0
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main() or 0)
    except Exception as exc:
        # AC-007: payload-aware crash log (gate-crash). last_hook_input() returns
        # the dict that main()'s read_hook_input() cached; survives the except
        # boundary without threading hook_input through main's signature.
        try:
            log_gate_crash(exc, "prewrite_gate", last_hook_input())
        except Exception:
            pass
        sys.exit(0)
