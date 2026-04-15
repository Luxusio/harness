#!/usr/bin/env python3
"""PreToolUse hook — enforce artifact ownership and plan-first rule.

Exits 0 to allow, exits 2 to block.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import read_state, find_repo_root, TASK_DIR

PROTECTED_ARTIFACTS = {
    "PLAN.md": "plan-skill",
    "CRITIC__runtime.md": "qa-browser",  # also qa-api, qa-cli — checked by prefix match
    "HANDOFF.md": "developer",
    "DOC_SYNC.md": "developer",
    "CHECKS.yaml": "plan-skill + update_checks.py CLI",
}


def main():
    try:
        data = json.loads(sys.stdin.read())
    except Exception:
        sys.exit(0)

    tool_name = data.get("tool_name", "")
    if tool_name not in ("Write", "Edit"):
        sys.exit(0)

    tool_input = data.get("tool_input") or {}
    file_path = tool_input.get("file_path") or tool_input.get("path") or ""
    if not file_path:
        sys.exit(0)

    file_path = os.path.normpath(file_path)
    repo_root = find_repo_root()

    # Block writes to protected artifacts
    basename = os.path.basename(file_path)
    if basename in PROTECTED_ARTIFACTS:
        owner = PROTECTED_ARTIFACTS[basename]
        print(f"BLOCKED: {basename} is owned by {owner}. Use the appropriate MCP tool.", file=sys.stderr)
        sys.exit(2)

    # Allow writes inside task dirs
    tasks_dir = os.path.join(repo_root, TASK_DIR)
    if file_path.startswith(tasks_dir):
        sys.exit(0)

    # For source files, check PLAN.md exists on active task
    active_file = os.path.join(tasks_dir, ".active")
    if os.path.isfile(active_file):
        try:
            with open(active_file) as f:
                active_dir = f.read().strip()
            if active_dir:
                if not os.path.isfile(os.path.join(active_dir, "PLAN.md")):
                    if not os.path.isfile(os.path.join(active_dir, "MAINTENANCE")):
                        print("BLOCKED: PLAN.md does not exist yet. Run plan skill first.", file=sys.stderr)
                        sys.exit(2)
        except Exception:
            pass

    sys.exit(0)


if __name__ == "__main__":
    main()
