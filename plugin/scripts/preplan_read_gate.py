#!/usr/bin/env python3
"""PreToolUse hook: BLOCK source/script/agent reads before plan session.

Blocking gate — exits 2 when:
  1. No active task AND target is a guarded path → block
  2. Active task but no plan session AND no plan_verdict PASS → block
  3. Plan session context/write phase or plan_verdict PASS → allow

Always allowed (even without plan session):
  - doc/harness/manifest.yaml
  - Root CLAUDE.md
  - Task-local artifacts (REQUEST.md, TASK_STATE.yaml, SESSION_HANDOFF.json, etc.)
  - doc/CLAUDE.md and doc/*/CLAUDE.md (registry files)
  - .claude/ directory

Escape hatch: set HARNESS_SKIP_PREREAD=1 to bypass.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import (
    read_hook_input,
    yaml_field,
    TASK_DIR,
    MANIFEST,
)

# --- Guarded path patterns ---
# These paths require an active plan session or plan_verdict PASS to read

GUARDED_PREFIXES = (
    "plugin/scripts/",
    "plugin/agents/",
    "plugin/skills/",
    "plugin/calibration/",
    "plugin/docs/",
    "src/",
    "app/",
    "api/",
    "lib/",
    "tests/",
)

# --- Always-allowed paths (even without plan session) ---

ALWAYS_ALLOWED_EXACT = {
    "doc/harness/manifest.yaml",
    "CLAUDE.md",
    "doc/CLAUDE.md",
}

ALWAYS_ALLOWED_PREFIXES = (
    "doc/harness/tasks/",
    ".claude/",
    ".claude-plugin/",
)

ALWAYS_ALLOWED_PATTERNS = (
    # doc/*/CLAUDE.md — registry files
    re.compile(r"^doc/[^/]+/CLAUDE\.md$"),
    # Any CLAUDE.md at root of any directory
    re.compile(r"^[^/]*/CLAUDE\.md$"),
)

# --- Bash commands that are effectively reads ---

READ_COMMANDS = (
    "cat", "sed", "head", "tail", "less", "more",
    "grep", "rg", "find", "ls", "tree",
    "awk", "wc", "file", "stat",
)


def _normalize_path(filepath):
    """Normalize a file path for comparison."""
    if not filepath:
        return ""
    fp = filepath
    if fp.startswith("./"):
        fp = fp[2:]
    # Strip absolute path prefix
    cwd = os.getcwd()
    if fp.startswith(cwd):
        fp = fp[len(cwd):].lstrip("/")
    return fp


def _is_always_allowed(filepath):
    """Return True if the path is always allowed regardless of plan state."""
    fp = _normalize_path(filepath)
    if not fp:
        return True  # No path = nothing to guard

    # Exact matches
    if fp in ALWAYS_ALLOWED_EXACT:
        return True

    # Prefix matches
    for prefix in ALWAYS_ALLOWED_PREFIXES:
        if fp.startswith(prefix):
            return True

    # Pattern matches
    for pattern in ALWAYS_ALLOWED_PATTERNS:
        if pattern.match(fp):
            return True

    # Non-source, non-guarded files are allowed
    # (e.g. package.json, tsconfig.json, etc.)
    return False


def _is_guarded_path(filepath):
    """Return True if the path requires plan session to read."""
    fp = _normalize_path(filepath)
    if not fp:
        return False

    # Check if always allowed first
    if _is_always_allowed(filepath):
        return False

    # Check guarded prefixes
    for prefix in GUARDED_PREFIXES:
        if fp.startswith(prefix):
            return True

    # Source file extensions are guarded
    SOURCE_EXTENSIONS = {
        ".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java",
        ".c", ".cpp", ".h", ".hpp", ".cs", ".rb", ".php", ".swift",
        ".kt", ".scala", ".sh", ".bash", ".zsh", ".sql",
        ".svelte", ".vue", ".astro",
    }
    _, ext = os.path.splitext(fp)
    if ext.lower() in SOURCE_EXTENSIONS:
        return True

    return False


def _has_plan_access():
    """Check if there's an active task with plan session or plan_verdict PASS.

    Returns (allowed: bool, reason: str).
    """
    if not os.path.isdir(TASK_DIR):
        return False, "no task directory"

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

        # Check plan_verdict PASS — full access
        plan_v = yaml_field("plan_verdict", state_file) or "pending"
        if plan_v == "PASS":
            return True, f"plan_verdict PASS on {entry}"

        # Check plan session token
        token_path = os.path.join(task_path, "PLAN_SESSION.json")
        if os.path.isfile(token_path):
            try:
                with open(token_path, "r", encoding="utf-8") as f:
                    token = json.load(f)
                state = token.get("state", "")
                phase = token.get("phase", "")
                if state == "open" and phase in ("context", "write"):
                    return True, f"plan session {phase} on {entry}"
            except (json.JSONDecodeError, OSError):
                pass

        # Check plan_session_state field in TASK_STATE
        pss = yaml_field("plan_session_state", state_file)
        if pss in ("context_open", "write_open"):
            return True, f"plan_session_state={pss} on {entry}"

    return False, "no active plan session or plan_verdict PASS"


def _extract_read_target(hook_data):
    """Extract the target path from a Read/Glob/Grep/LS PreToolUse payload.

    Returns (tool_name, target_path) or (None, None) if not a read tool.
    """
    if not hook_data:
        return None, None
    try:
        data = json.loads(hook_data)
    except (json.JSONDecodeError, TypeError):
        return None, None

    tool_name = data.get("tool_name", data.get("tool", ""))
    tool_input = data.get("tool_input", data.get("input", {}))

    if not isinstance(tool_input, dict):
        return None, None

    if tool_name == "Read":
        fp = tool_input.get("file_path", tool_input.get("filePath", ""))
        return tool_name, fp

    if tool_name in ("Glob", "LS"):
        fp = tool_input.get("path", tool_input.get("directory", ""))
        pattern = tool_input.get("pattern", "")
        # For Glob, the pattern itself might indicate guarded paths
        return tool_name, fp or pattern

    if tool_name == "Grep":
        fp = tool_input.get("path", "")
        return tool_name, fp

    if tool_name == "Bash":
        command = tool_input.get("command", "")
        return _check_bash_command(command)

    return None, None


def _check_bash_command(command):
    """Check if a Bash command is a read-like command targeting guarded paths.

    Returns (tool_name, target_path) or (None, None) if not a guarded read.
    """
    if not command:
        return None, None

    # Strip leading whitespace and split into parts
    parts = command.strip().split()
    if not parts:
        return None, None

    cmd = os.path.basename(parts[0])

    if cmd not in READ_COMMANDS:
        return None, None

    # Look for guarded paths in command arguments
    for arg in parts[1:]:
        if arg.startswith("-"):
            continue  # Skip flags
        norm = _normalize_path(arg)
        if _is_guarded_path(norm):
            return "Bash", norm

    return None, None


def main():
    # Escape hatch
    if os.environ.get("HARNESS_SKIP_PREREAD"):
        sys.exit(0)

    # Only gate on harness-managed repos
    if not os.path.isfile(MANIFEST):
        sys.exit(0)

    hook_data = read_hook_input()
    if not hook_data:
        sys.exit(0)

    tool_name, target_path = _extract_read_target(hook_data)
    if not tool_name or not target_path:
        sys.exit(0)

    # Normalize and check if guarded
    normalized = _normalize_path(target_path)
    if not _is_guarded_path(normalized):
        sys.exit(0)

    # Guarded path — check plan access
    allowed, reason = _has_plan_access()
    if allowed:
        sys.exit(0)

    # Block
    print(
        f"BLOCKED: Reading guarded path '{normalized}' before plan session. "
        f"Reason: {reason}. "
        f"Create a task and invoke /harness:plan first, or set "
        f"HARNESS_SKIP_PREREAD=1 to bypass."
    )
    sys.exit(2)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # Fail-closed on managed repos
        if os.path.isfile(MANIFEST):
            print(
                f"BLOCKED: preplan_read_gate error: {e}. "
                f"Fail-closed on managed repos. "
                f"Set HARNESS_SKIP_PREREAD=1 to bypass."
            )
            sys.exit(2)
        sys.exit(0)
