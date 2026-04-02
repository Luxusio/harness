#!/usr/bin/env python3
"""SessionStart hook: emit a very small repo/task summary."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import (
    exit_if_unmanaged_repo,
    read_hook_input,
    yaml_field,
    get_browser_qa_status,
    is_profile_enabled,
    is_tooling_ready,
    manifest_field,
    TASK_DIR,
    MANIFEST,
)


def _tooling_flags():
    if not os.path.isfile(MANIFEST):
        return []

    flags = []
    if is_profile_enabled("symbol_lane_enabled"):
        flags.append("symbol:on")
    elif is_tooling_ready("lsp_ready") or is_tooling_ready("cclsp_ready"):
        flags.append("symbol:ready")

    if is_profile_enabled("ast_grep_enabled"):
        flags.append("ast:on")
    elif is_tooling_ready("ast_grep_ready"):
        flags.append("ast:ready")

    if is_profile_enabled("observability_enabled"):
        flags.append("obs:on")
    elif is_tooling_ready("observability_ready"):
        flags.append("obs:ready")

    return flags


def _active_tasks(limit=2):
    items = []
    if not os.path.isdir(TASK_DIR):
        return items, 0, 0

    blocked = 0
    for entry in sorted(os.listdir(TASK_DIR)):
        if not entry.startswith("TASK__"):
            continue
        task_path = os.path.join(TASK_DIR, entry)
        state_file = os.path.join(task_path, "TASK_STATE.yaml")
        if not os.path.isfile(state_file):
            continue
        status = yaml_field("status", state_file) or "unknown"
        if status in ("closed", "archived", "stale"):
            continue
        lane = yaml_field("lane", state_file) or "unknown"
        if status == "blocked_env":
            blocked += 1
        items.append((entry, status, lane))

    preview = [f"{tid}[{status}/{lane}]" for tid, status, lane in items[:limit]]
    hidden = max(0, len(items) - limit)
    return preview, hidden, blocked


def main():
    read_hook_input()
    exit_if_unmanaged_repo()

    project_name = manifest_field("name") or "repo"
    browser = get_browser_qa_status()
    tooling = ", ".join(_tooling_flags()) or "no extra tooling"
    tasks, hidden, blocked = _active_tasks()

    print(f"harness ready | project={project_name} | browser={browser} | tools={tooling}")
    if tasks:
        suffix = f" (+{hidden} more)" if hidden else ""
        print(f"active tasks: {', '.join(tasks)}{suffix}")
    else:
        print("active tasks: none")
    if blocked:
        print(f"blocked_env: {blocked}")
    print("hint: use mcp__plugin_harness_harness__task_context for the canonical task pack")


if __name__ == "__main__":
    main()
