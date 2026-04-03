#!/usr/bin/env python3
"""PreToolUse hook: BLOCK unauthorized writes.

Blocking gate — exits 2 when:
  1. Source file write attempted without plan_verdict PASS on any active task.
  2. Protected artifact write attempted by wrong role.
  3. PLAN.md write attempted without active plan session token.
  4. Workflow control surface write attempted from a non-maintenance task.
  5. Team task source write falls outside TEAM_PLAN.md owned writable paths.

Escape hatch: set HARNESS_SKIP_PREWRITE=1 to bypass (for emergency fixes).
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import (
    read_hook_input,
    yaml_field,
    team_artifact_status,
    is_team_task,
    parse_team_plan,
    resolve_team_path_ownership,
    get_team_worker_name,
    team_worker_summary_relpath,
    normalize_path,
    TASK_DIR,
    MANIFEST,
)

# ---------------------------------------------------------------------------
# Workflow control surface — files that define harness runtime behaviour.
# Writes to these are only permitted from tasks where maintenance_task=true.
# ---------------------------------------------------------------------------

WORKFLOW_CONTROL_SURFACE = {
    # Agent prompts / CLAUDE.md
    "plugin/CLAUDE.md",
    "plugin/agents/harness.md",
    # Execution / orchestration docs
    "plugin/docs/execution-modes.md",
    "plugin/docs/orchestration-modes.md",
    # Hook manifest / plugin MCP surface
    "plugin/hooks/hooks.json",
    "plugin/.mcp.json",
    # Setup skill and templates
    "plugin/skills/setup/SKILL.md",
    "plugin/skills/setup/templates/CLAUDE.md",
    "plugin/skills/setup/templates/doc/harness/manifest.yaml",
    # MCP / CLI control plane
    "plugin/scripts/hctl.py",
    "plugin/scripts/mcp_bash_guard.py",
    "plugin/mcp/harness_server.py",
}

# --- Source file detection ---

# Extensions considered "source code" — writes to these trigger the gate
SOURCE_EXTENSIONS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java",
    ".c", ".cpp", ".h", ".hpp", ".cs", ".rb", ".php", ".swift",
    ".kt", ".scala", ".sh", ".bash", ".zsh", ".sql",
    ".svelte", ".vue", ".astro",
}

# Paths that are always exempt (harness operational files)
EXEMPT_PREFIXES = (
    "doc/harness/tasks/",
    "doc/harness/critics/",
    "doc/harness/review-overlays/",
    "doc/harness/maintenance/",
    "doc/harness/archive/",
    ".claude/",
)

EXEMPT_FILENAMES = {
    "CLAUDE.md", "REQUEST.md",
    "TASK_STATE.yaml", "CHECKS.yaml", "SESSION_HANDOFF.json",
    "TEAM_PLAN.md", "TEAM_SYNTHESIS.md",
    "RESULT.md",
    "PLAN_SESSION.json",
    "DIRECTIVES_PENDING.yaml",
}

# Protected artifacts with their authorized owner roles
PROTECTED_ARTIFACT_OWNERS = {
    "PLAN.md": {"plan-skill"},
    "HANDOFF.md": {"developer"},
    "DOC_SYNC.md": {"writer"},
    "CRITIC__plan.md": {"critic-plan"},
    "CRITIC__runtime.md": {"critic-runtime"},
    "CRITIC__document.md": {"critic-document"},
    "QA__runtime.md": {"critic-runtime"},
}

# Agent name normalization: maps CLAUDE_AGENT_NAME values to canonical roles
AGENT_TO_ROLE = {
    "harness:developer": "developer",
    "developer": "developer",
    "harness:writer": "writer",
    "writer": "writer",
    "harness:critic-plan": "critic-plan",
    "critic-plan": "critic-plan",
    "harness:critic-runtime": "critic-runtime",
    "critic-runtime": "critic-runtime",
    "harness:critic-document": "critic-document",
    "critic-document": "critic-document",
    "harness:harness": "harness",
    "harness": "harness",
}


def _get_raw_agent_name():
    return os.environ.get("CLAUDE_AGENT_NAME", "").strip()


def _get_agent_role():
    """Get the canonical role of the current agent from CLAUDE_AGENT_NAME."""
    raw = _get_raw_agent_name()
    if raw in AGENT_TO_ROLE:
        return AGENT_TO_ROLE[raw]

    role_prefixes = (
        ("harness:critic-document", "critic-document"),
        ("harness:critic-runtime", "critic-runtime"),
        ("harness:critic-plan", "critic-plan"),
        ("harness:developer", "developer"),
        ("harness:writer", "writer"),
        ("harness:harness", "harness"),
        ("critic-document", "critic-document"),
        ("critic-runtime", "critic-runtime"),
        ("critic-plan", "critic-plan"),
        ("developer", "developer"),
        ("writer", "writer"),
        ("harness", "harness"),
    )
    for prefix, role in role_prefixes:
        if raw == prefix:
            return role
        if raw.startswith(prefix + ":") or raw.startswith(prefix + "/") or raw.startswith(prefix + "@"):
            return role
    return raw


def _get_team_worker_name(known_workers=None):
    """Best-effort worker identity from env or worker-suffixed agent names."""
    return get_team_worker_name(known_workers=known_workers, raw_agent_name=_get_raw_agent_name())


def _is_source_file(filepath):
    """Return True if filepath is a source file that should be gated."""
    if not filepath:
        return False

    # Normalize
    fp = filepath
    if fp.startswith("./"):
        fp = fp[2:]

    # Check exempt prefixes
    for prefix in EXEMPT_PREFIXES:
        if fp.startswith(prefix):
            return False

    # Check exempt filenames
    basename = os.path.basename(fp)
    if basename in EXEMPT_FILENAMES:
        return False

    # Check if it's a protected artifact (handled separately)
    if basename in PROTECTED_ARTIFACT_OWNERS:
        return False

    # Meta sidecar files are always exempt
    if basename.endswith(".meta.json"):
        return False

    # Check extension
    _, ext = os.path.splitext(fp)
    return ext.lower() in SOURCE_EXTENSIONS


def _is_protected_artifact(filepath):
    """Return True if filepath is a protected artifact."""
    if not filepath:
        return False
    basename = os.path.basename(filepath)
    return basename in PROTECTED_ARTIFACT_OWNERS


def _check_plan_session_token(task_dir):
    """Check if there's an active plan session token allowing PLAN.md writes.

    Returns True if plan session is open with phase=write.
    """
    if not task_dir:
        return False
    token_path = os.path.join(task_dir, "PLAN_SESSION.json")
    if not os.path.isfile(token_path):
        return False
    try:
        with open(token_path, "r", encoding="utf-8") as f:
            import json as _json
            token = _json.load(f)
        state = token.get("state", "")
        phase = token.get("phase", "")
        return state == "open" and phase in ("write", "context")
    except (json.JSONDecodeError, OSError):
        return False


def _find_active_tasks():
    """Return list of (task_id, plan_verdict) for non-closed tasks."""
    if not os.path.isdir(TASK_DIR):
        return []
    active = []
    for entry in sorted(os.listdir(TASK_DIR)):
        if not entry.startswith("TASK__"):
            continue
        task_path = os.path.join(TASK_DIR, entry)
        if not os.path.isdir(task_path):
            continue
        state_file = os.path.join(task_path, "TASK_STATE.yaml")
        if not os.path.isfile(state_file):
            continue
        status = yaml_field("status", state_file)
        if status in ("closed", "archived", "stale"):
            continue
        plan_v = yaml_field("plan_verdict", state_file) or "pending"
        active.append((entry, plan_v))
    return active


def _find_active_task_dir():
    """Return the most recently updated active task directory, or None."""
    if not os.path.isdir(TASK_DIR):
        return None
    best = None
    best_updated = ""
    for entry in sorted(os.listdir(TASK_DIR)):
        if not entry.startswith("TASK__"):
            continue
        task_path = os.path.join(TASK_DIR, entry)
        if not os.path.isdir(task_path):
            continue
        state_file = os.path.join(task_path, "TASK_STATE.yaml")
        if not os.path.isfile(state_file):
            continue
        status = yaml_field("status", state_file)
        if status in ("closed", "archived", "stale"):
            continue
        updated = yaml_field("updated", state_file) or ""
        if updated >= best_updated:
            best_updated = updated
            best = task_path
    return best


def _check_team_plan_ready(task_dir):
    """Return (allowed, message) for team-task source writes.

    Team tasks must complete TEAM_PLAN.md before source writes begin.
    """
    if not task_dir or not is_team_task(task_dir):
        return True, ""

    artifact_state = team_artifact_status(task_dir)
    if artifact_state.get("plan_ready"):
        return True, ""

    reasons = []
    missing_sections = artifact_state.get("plan_missing_sections") or []
    if missing_sections:
        reasons.append("missing sections: " + ", ".join(missing_sections))
    if artifact_state.get("plan_has_placeholders"):
        reasons.append("remove TODO/TBD placeholders")
    semantic_errors = artifact_state.get("plan_semantic_errors") or []
    if semantic_errors:
        reasons.extend(list(semantic_errors[:3]))

    return False, (
        "BLOCKED: team task source writes require completed TEAM_PLAN.md first. "
        + ("; ".join(reasons) if reasons else "Finish TEAM_PLAN.md before worker execution.")
    )


def _check_team_write_ownership(task_dir, filepath):
    """Enforce TEAM_PLAN.md writable ownership for team tasks."""
    if not task_dir or not is_team_task(task_dir):
        return True, ""

    artifact_state = team_artifact_status(task_dir)
    if not artifact_state.get("plan_ready"):
        return _check_team_plan_ready(task_dir)

    plan_data = parse_team_plan(os.path.join(task_dir, "TEAM_PLAN.md"))
    if not plan_data.get("ownership_ready"):
        reasons = plan_data.get("errors") or artifact_state.get("plan_semantic_errors") or [
            "TEAM_PLAN.md ownership metadata is invalid"
        ]
        return False, "BLOCKED: TEAM_PLAN.md ownership rules are invalid. " + "; ".join(reasons[:3])

    ownership = resolve_team_path_ownership(plan_data, filepath)
    if ownership.get("shared_read_only"):
        return False, (
            f"BLOCKED: '{filepath}' is listed under shared read-only paths in TEAM_PLAN.md. "
            "Only non-mutating reads are allowed for that surface."
        )

    owners = list(ownership.get("owners") or [])
    if not owners:
        workers = ", ".join(plan_data.get("workers") or []) or "none"
        return False, (
            f"BLOCKED: '{filepath}' is outside TEAM_PLAN.md owned writable paths. "
            f"Declare a worker owner before mutating it. Known workers: {workers}."
        )
    if len(owners) > 1:
        return False, (
            f"BLOCKED: '{filepath}' matches multiple TEAM_PLAN.md owners ({', '.join(owners)}). "
            "Fix overlapping ownership before mutating source files."
        )

    current_worker = _get_team_worker_name(plan_data.get("workers") or [])
    if current_worker:
        if current_worker not in (plan_data.get("workers") or []):
            roster = ", ".join(plan_data.get("workers") or []) or "none"
            return False, (
                f"BLOCKED: current worker '{current_worker}' is not in TEAM_PLAN.md worker roster ({roster})."
            )
        owner = owners[0]
        if owner != current_worker:
            return False, (
                f"BLOCKED: '{filepath}' is owned by '{owner}' in TEAM_PLAN.md. "
                f"Current worker is '{current_worker}'."
            )
        if current_worker in (ownership.get("forbidden_by") or []):
            return False, (
                f"BLOCKED: TEAM_PLAN.md forbids worker '{current_worker}' from mutating '{filepath}'."
            )
    return True, ""


def _team_worker_summary_target(filepath):
    """Return worker id for team/worker-<name>.md writes, else ''."""
    fp = normalize_path(str(filepath or "").strip())
    if not fp:
        return ""
    match = re.search(r"(?:^|/)team/(worker-[A-Za-z0-9_.-]+)\.md$", fp)
    return match.group(1) if match else ""


def _check_team_artifact_write(task_dir, filepath):
    """Enforce team-only coordination artifact ownership.

    This supplements protected-artifact ownership by ensuring:
      - only the designated synthesis owner(s) write TEAM_SYNTHESIS.md / HANDOFF.md
      - workers only write their own team/worker-<name>.md summary artifact
    """
    if not task_dir or not is_team_task(task_dir):
        return True, ""

    basename = os.path.basename(str(filepath or ""))
    artifact_state = team_artifact_status(task_dir)
    plan_workers = list(artifact_state.get("plan_workers") or [])
    current_worker = _get_team_worker_name(plan_workers)
    synthesis_workers = list(artifact_state.get("synthesis_workers") or [])

    summary_target = _team_worker_summary_target(filepath)
    if summary_target:
        if plan_workers and summary_target not in plan_workers:
            roster = ", ".join(plan_workers) or "none"
            return False, (
                f"BLOCKED: '{filepath}' is not in the TEAM_PLAN.md worker roster ({roster})."
            )
        if current_worker and current_worker != summary_target:
            return False, (
                f"BLOCKED: '{filepath}' is owned by worker '{summary_target}'. "
                f"Current worker is '{current_worker}'."
            )
        return True, ""

    if basename == "TEAM_SYNTHESIS.md":
        if not artifact_state.get("plan_ready"):
            return False, (
                "BLOCKED: TEAM_SYNTHESIS.md requires a completed TEAM_PLAN.md first. "
                "Finish worker ownership and synthesis rules before writing synthesis."
            )
        if synthesis_workers and current_worker and current_worker not in synthesis_workers:
            owners = ", ".join(synthesis_workers[:4])
            return False, (
                f"BLOCKED: TEAM_SYNTHESIS.md is reserved for synthesis owner(s) [{owners}] in TEAM_PLAN.md. "
                f"Current worker is '{current_worker}'."
            )
        return True, ""

    if basename in ("CRITIC__runtime.md", "QA__runtime.md"):
        final_phase_started = bool(
            artifact_state.get("synthesis_ready")
            or artifact_state.get("team_runtime_verification_needed")
        )
        if final_phase_started and synthesis_workers and current_worker and current_worker not in synthesis_workers:
            owners = ", ".join(synthesis_workers[:4])
            return False, (
                f"BLOCKED: final team runtime verification artifacts are reserved for synthesis owner(s) [{owners}] once TEAM_SYNTHESIS.md is ready. "
                f"Current worker is '{current_worker}'."
            )
        return True, ""

    if basename == "DOC_SYNC.md":
        documentation_owners = list(artifact_state.get("team_doc_sync_owners") or [])
        owner_source = str(artifact_state.get("team_doc_sync_owner_source") or "")
        if documentation_owners and owner_source in ("explicit", "inferred") and current_worker and current_worker not in documentation_owners:
            owners = ", ".join(documentation_owners[:4])
            return False, (
                f"BLOCKED: DOC_SYNC.md is reserved for documentation owner(s) [{owners}] in TEAM_PLAN.md. "
                f"Current worker is '{current_worker}'."
            )
        return True, ""

    if basename == "CRITIC__document.md":
        documentation_owners = list(artifact_state.get("team_document_critic_owners") or [])
        owner_source = str(artifact_state.get("team_document_critic_owner_source") or "")
        if documentation_owners and owner_source in ("explicit", "inferred") and current_worker and current_worker not in documentation_owners:
            owners = ", ".join(documentation_owners[:4])
            return False, (
                f"BLOCKED: CRITIC__document.md is reserved for document critic owner(s) [{owners}] in TEAM_PLAN.md. "
                f"Current worker is '{current_worker}'."
            )
        return True, ""

    if basename == "HANDOFF.md":
        if synthesis_workers and current_worker and current_worker not in synthesis_workers:
            owners = ", ".join(synthesis_workers[:4])
            return False, (
                f"BLOCKED: HANDOFF.md refresh for team tasks is reserved for synthesis owner(s) [{owners}]. "
                f"Current worker is '{current_worker}'."
            )
        return True, ""

    return True, ""


def _is_workflow_control_surface(filepath):
    """Return True if filepath is a workflow control surface file.

    These files define harness runtime behaviour; they may only be written
    when the active task has maintenance_task=true.
    """
    if not filepath:
        return False
    fp = filepath
    if fp.startswith("./"):
        fp = fp[2:]
    return fp in WORKFLOW_CONTROL_SURFACE


def _active_task_is_maintenance():
    """Return True if the most recently updated active task has maintenance_task=true."""
    task_dir = _find_active_task_dir()
    if not task_dir:
        return False
    state_file = os.path.join(task_dir, "TASK_STATE.yaml")
    val = yaml_field("maintenance_task", state_file) or "false"
    return str(val).lower() in ("true", "1", "yes")


def _extract_file_path(hook_data):
    """Extract the target file path from PreToolUse hook payload.

    The payload structure for Write/Edit tools:
      {"tool_name": "Write", "tool_input": {"file_path": "/abs/path/to/file"}}
      {"tool_name": "Edit", "tool_input": {"file_path": "/abs/path/to/file"}}
    """
    if not hook_data:
        return None
    try:
        data = json.loads(hook_data)
    except (json.JSONDecodeError, TypeError):
        return None

    tool_name = data.get("tool_name", data.get("tool", ""))
    if tool_name not in ("Write", "Edit", "MultiEdit"):
        return None

    tool_input = data.get("tool_input", data.get("input", {}))
    if isinstance(tool_input, dict):
        fp = tool_input.get("file_path", tool_input.get("filePath", ""))
        if fp:
            # Convert absolute path to relative (strip cwd)
            cwd = os.getcwd()
            if fp.startswith(cwd):
                fp = fp[len(cwd):].lstrip("/")
            return fp
    return None


def _check_protected_artifact_write(filepath):
    """Check if a protected artifact write is authorized.

    Returns (allowed: bool, message: str).
    """
    basename = os.path.basename(filepath)
    allowed_roles = PROTECTED_ARTIFACT_OWNERS.get(basename)
    if allowed_roles is None:
        return True, ""

    current_role = _get_agent_role()

    # Special case: PLAN.md requires plan session token
    if basename == "PLAN.md":
        # Find task dir from filepath
        task_dir = _find_active_task_dir()
        if _check_plan_session_token(task_dir):
            return True, ""
        # Also allow if plan_verdict is already PASS (plan update)
        if task_dir:
            state_file = os.path.join(task_dir, "TASK_STATE.yaml")
            pss = yaml_field("plan_session_state", state_file)
            if pss in ("context_open", "write_open"):
                return True, ""
        return False, (
            f"BLOCKED: PLAN.md write requires active plan session token "
            f"(PLAN_SESSION.json state=open, phase=write). "
            f"Current agent role: '{current_role}'. "
            f"Use /harness:plan to create PLAN.md."
        )

    # For other protected artifacts, check role
    if current_role in allowed_roles:
        return True, ""

    expected = ", ".join(sorted(allowed_roles))
    return False, (
        f"BLOCKED: {basename} is a protected artifact owned by [{expected}]. "
        f"Current agent role: '{current_role}'. "
        f"Only authorized roles may write this artifact."
    )


def main():
    # Escape hatch for emergency fixes
    if os.environ.get("HARNESS_SKIP_PREWRITE"):
        sys.exit(0)

    hook_data = read_hook_input()
    if not hook_data:
        sys.exit(0)

    filepath = _extract_file_path(hook_data)
    if not filepath:
        sys.exit(0)

    # Check if harness is initialized
    if not os.path.isfile(MANIFEST):
        # Harness not initialized — no gate (don't block non-harness repos)
        sys.exit(0)

    # --- Workflow control surface lock ---
    # Files that define harness runtime behaviour may only be written from
    # tasks where maintenance_task=true.
    if _is_workflow_control_surface(filepath):
        if not _active_task_is_maintenance():
            print(
                f"BLOCKED: '{filepath}' is a workflow control surface file. "
                f"Direct writes are only permitted from maintenance tasks "
                f"(maintenance_task=true in TASK_STATE.yaml). "
                f"Run `mcp__plugin_harness_harness__task_start` to compile routing, or set maintenance_task "
                f"in TASK_STATE.yaml. "
                f"(Set HARNESS_SKIP_PREWRITE=1 to bypass in emergencies.)",
                file=sys.stderr,
            )
            sys.exit(2)
        # Maintenance task — allow write to control surface
        sys.exit(0)

    active_task_dir = _find_active_task_dir()
    team_artifact_ok, team_artifact_message = _check_team_artifact_write(active_task_dir, filepath)
    if not team_artifact_ok:
        print(team_artifact_message, file=sys.stderr)
        sys.exit(2)

    # --- Protected artifact ownership check ---
    if _is_protected_artifact(filepath):
        allowed, message = _check_protected_artifact_write(filepath)
        if not allowed:
            print(message, file=sys.stderr)
            sys.exit(2)
        # Protected artifact with correct role — allow
        sys.exit(0)

    # --- Source file write check ---
    if not _is_source_file(filepath):
        sys.exit(0)

    # Source file write detected — check plan approval
    active_tasks = _find_active_tasks()

    if not active_tasks:
        # No active task — untracked mutation → BLOCK
        print(
            "BLOCKED: Source file write with no active harness task. "
            "This mutation is untracked. Create a task folder and "
            "run /harness:plan before implementing. "
            "(Set HARNESS_SKIP_PREWRITE=1 to bypass in emergencies.)"
        )
        sys.exit(2)

    # Check if any active task has plan_verdict: PASS
    any_plan_passed = any(pv == "PASS" for _, pv in active_tasks)

    if not any_plan_passed:
        task_list = ", ".join(f"{tid} (plan: {pv})" for tid, pv in active_tasks)
        print(
            f"BLOCKED: Source file write but plan_verdict is not PASS. "
            f"Active tasks: {task_list}. "
            f"Complete plan approval before implementing. "
            f"(Set HARNESS_SKIP_PREWRITE=1 to bypass in emergencies.)"
        )
        sys.exit(2)

    team_ready, team_message = _check_team_plan_ready(active_task_dir)
    if not team_ready:
        print(team_message)
        sys.exit(2)

    team_write_ok, team_write_message = _check_team_write_ownership(active_task_dir, filepath)
    if not team_write_ok:
        print(team_write_message)
        sys.exit(2)

    # Source file write — check actor is developer
    current_role = _get_agent_role()
    if current_role and current_role not in ("developer", "harness", ""):
        print(
            f"BLOCKED: Source file write by non-developer role '{current_role}'. "
            f"Only developer role may write source files. "
            f"(Set HARNESS_SKIP_PREWRITE=1 to bypass in emergencies.)"
        )
        sys.exit(2)

    # Plan approved + authorized role — allow write
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # Fail-CLOSED on managed harness repos — do not silently allow
        if os.path.isfile(MANIFEST):
            print(
                f"BLOCKED: prewrite gate encountered an error: {e}. "
                f"Fail-closed on managed repos. "
                f"Set HARNESS_SKIP_PREWRITE=1 to bypass."
            )
            sys.exit(2)
        # Non-harness repo: fail-open to avoid blocking unrelated work
        sys.exit(0)
