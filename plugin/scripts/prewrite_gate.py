#!/usr/bin/env python3
"""PreToolUse hook: BLOCK source file writes without plan approval.

Blocking gate — exits 2 when source file write is attempted without plan_verdict PASS.
Checks:
  1. Is the target a source file? (skip harness operational files)
  2. Is there an active harness task with plan_verdict: PASS?
  3. If not, BLOCK the write (exit 2).

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
    "CLAUDE.md", "PLAN.md", "HANDOFF.md", "DOC_SYNC.md", "REQUEST.md",
    "RESULT.md", "TASK_STATE.yaml", "CHECKS.yaml", "SESSION_HANDOFF.json",
    "TEAM_PLAN.md", "TEAM_SYNTHESIS.md",
    "CRITIC__plan.md", "CRITIC__runtime.md", "CRITIC__document.md",
    "QA__runtime.md",
}


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

    # Check extension
    _, ext = os.path.splitext(fp)
    return ext.lower() in SOURCE_EXTENSIONS


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

    if not _is_source_file(filepath):
        sys.exit(0)

    # Source file write detected — check plan approval
    if not os.path.isfile(MANIFEST):
        # Harness not initialized — no gate (don't block non-harness repos)
        sys.exit(0)

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

    # Plan approved — allow write
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # On unexpected errors, allow the write (fail-open to avoid blocking work)
        pass
    sys.exit(0)
