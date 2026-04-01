#!/usr/bin/env python3
"""PreToolUse hook: BLOCK unauthorized writes.

Blocking gate — exits 2 when:
  1. Source file write attempted without plan_verdict PASS on any active task.
  2. Protected artifact write attempted by wrong role.
  3. PLAN.md write attempted without active plan session token.
  4. Workflow control surface write attempted from a non-maintenance task.

Escape hatch: set HARNESS_SKIP_PREWRITE=1 to bypass (for emergency fixes).
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import (
    read_hook_input,
    yaml_field,
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


def _get_agent_role():
    """Get the canonical role of the current agent from CLAUDE_AGENT_NAME."""
    raw = os.environ.get("CLAUDE_AGENT_NAME", "")
    return AGENT_TO_ROLE.get(raw, raw)


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
                f"Run `mcp__harness__task_start` to compile routing, or set maintenance_task "
                f"in TASK_STATE.yaml. "
                f"(Set HARNESS_SKIP_PREWRITE=1 to bypass in emergencies.)",
                file=sys.stderr,
            )
            sys.exit(2)
        # Maintenance task — allow write to control surface
        sys.exit(0)

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
