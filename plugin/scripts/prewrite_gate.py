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
"""
from __future__ import annotations

import fnmatch
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from _lib import (
        read_hook_input,
        emit_permission_decision,
        _log_gate_error,
        _escape_hint,
        log_gate_bypass,
        find_repo_root,
        yaml_array,
        now_iso,
        TASK_DIR,
    )
except Exception:
    # _lib unavailable: fail-open. `|| true` would mask an ImportError exit
    # anyway; making it explicit keeps behaviour deterministic.
    sys.exit(0)


# ── Static policy ──────────────────────────────────────────────────────────

# Owner values are space-free so the structured reason tail stays grep-stable.
# Human text in the deny message names the concrete tool (e.g. update_checks.py).
PROTECTED_ARTIFACTS = {
    "PLAN.md": "plan-skill",
    "PLAN.meta.json": "plan-skill",
    "CHECKS.yaml": "plan-skill-or-update_checks",
    "AUDIT_TRAIL.md": "plan-skill",
    "CRITIC__runtime.md": "qa-cli",
    "HANDOFF.md": "developer",
    "DOC_SYNC.md": "developer",
}

# Human-readable owner description (used in deny message text, not in the tail).
PROTECTED_ARTIFACT_HUMAN = {
    "PLAN.md": "plan-skill (Skill(harness:plan))",
    "PLAN.meta.json": "plan-skill",
    "CHECKS.yaml": "plan-skill (initial) + scripts/update_checks.py (updates)",
    "AUDIT_TRAIL.md": "plan-skill",
    "CRITIC__runtime.md": "qa-browser / qa-api / qa-cli",
    "HANDOFF.md": "developer",
    "DOC_SYNC.md": "developer",
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
    "doc/harness/health-history.jsonl",
    "doc/harness/patterns",
    "doc/harness/retros",
    "doc/harness/visual-baselines",
    "doc/harness/benchmark",
    "doc/harness/audits",
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


# ── Learnings.jsonl parse-fail log (reuse pre-existing pattern) ────────────


def _log_gate_parse_fail(repo_root, reason):
    try:
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


def _deny(rule_id, file_path, owner, human_text, repo_root):
    tail = _tail(rule_id, file_path, owner, repo_root)
    hint = _escape_hint(GATE_NAME)
    emit_permission_decision("deny", f"{tail} {human_text}\n{hint}")


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


# ── Main ───────────────────────────────────────────────────────────────────


def main():
    # One-shot escape hatch: log and allow (silent stdout).
    if os.environ.get("HARNESS_SKIP_PREWRITE") == "1":
        data = read_hook_input()
        tool_input = data.get("tool_input") or {}
        fp = tool_input.get("file_path") or tool_input.get("path") or ""
        log_gate_bypass(GATE_NAME, fp)
        return 0

    data = read_hook_input()
    if not data:
        return 0

    tool_name = data.get("tool_name", "")
    if tool_name not in ("Write", "Edit", "MultiEdit"):
        return 0

    tool_input = data.get("tool_input") or {}
    file_path = tool_input.get("file_path") or tool_input.get("path") or ""
    if not file_path:
        return 0

    file_path = os.path.abspath(file_path)
    repo_root = find_repo_root()
    tasks_dir = os.path.join(repo_root, TASK_DIR)
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
            f"tool (e.g. Skill(harness:plan) for PLAN.md, update_checks.py for CHECKS.yaml)."
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
        active_file = os.path.join(tasks_dir, ".active")
        maint = False
        if os.path.isfile(active_file):
            try:
                with open(active_file) as f:
                    active_dir = f.read().strip()
                if active_dir and os.path.isdir(active_dir):
                    maint = os.path.isfile(os.path.join(active_dir, "MAINTENANCE"))
            except Exception:
                maint = False
        if not maint:
            rel = _rel(file_path, repo_root)
            human = (
                f"{rel} is a workflow-control-surface file. Direct writes are "
                f"only permitted from tasks with a MAINTENANCE marker. "
                f"Run the maintain skill or touch doc/harness/tasks/<task>/MAINTENANCE."
            )
            _deny("workflow-control-surface", file_path, "maintain-skill", human, repo_root)
            return 0
        return 0

    # Source files require an active task with PLAN.md.
    active_file = os.path.join(tasks_dir, ".active")
    if not os.path.isfile(active_file):
        human = (
            "No active task. Source writes require the canonical loop. "
            "Run Skill(harness:run) or Skill(harness:plan) first."
        )
        _deny("no-active-task", file_path, "plan-skill", human, repo_root)
        return 0

    try:
        with open(active_file) as f:
            active_dir = f.read().strip()
    except Exception as exc:
        human = f"Cannot read .active ({exc}). Run Skill(harness:run) to create a new task."
        _deny("invalid-active", file_path, "plan-skill", human, repo_root)
        return 0

    if not (active_dir and os.path.isdir(active_dir) and active_dir.startswith(tasks_dir)):
        human = "Active task points to invalid path. Run Skill(harness:run) to create a new task."
        _deny("invalid-active", file_path, "plan-skill", human, repo_root)
        return 0

    if not os.path.isfile(os.path.join(active_dir, "PLAN.md")):
        if not os.path.isfile(os.path.join(active_dir, "MAINTENANCE")):
            human = "PLAN.md does not exist yet. Run Skill(harness:plan) first."
            _deny("C-02-plan-first", file_path, "plan-skill", human, repo_root)
            return 0

    # Scope-lock enforcement as the last check: active task + PLAN.md confirmed.
    try:
        task_id = os.path.basename(active_dir)
        should_block, reason_text = _handle_scope_lock(
            file_path, active_dir, repo_root, task_id,
        )
        if should_block:
            emit_permission_decision("deny", reason_text)
            return 0
    except Exception as exc:
        _log_gate_parse_fail(repo_root, f"scope-lock enforcement error: {exc}")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main() or 0)
    except Exception as exc:
        try:
            _log_gate_error(exc, "prewrite_gate")
        except Exception:
            pass
        sys.exit(0)
