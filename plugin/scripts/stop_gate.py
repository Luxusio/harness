#!/usr/bin/env python3
"""Stop hook — remind about open tasks on session end."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import find_repo_root, read_state, TASK_DIR


def main():
    repo_root = find_repo_root()
    tasks_dir = os.path.join(repo_root, TASK_DIR)
    if not os.path.isdir(tasks_dir):
        sys.exit(0)

    open_tasks = []
    for entry in os.scandir(tasks_dir):
        if not entry.is_dir() or not entry.name.startswith("TASK__"):
            continue
        st = read_state(entry.path)
        status = (st.get("status") or "").lower()
        if status not in ("closed", "archived", "stale", ""):
            open_tasks.append((entry.name, status))

    if open_tasks:
        print(f"BLOCKED: {len(open_tasks)} open task(s) — complete the canonical loop before stopping.", file=sys.stderr)
        for name, status in open_tasks[:3]:
            print(f"  - {name} [{status}] → run Skill(harness:develop) or Skill(harness:run) to continue", file=sys.stderr)
        if len(open_tasks) > 3:
            print(f"  ... and {len(open_tasks) - 3} more", file=sys.stderr)
        sys.exit(2)

    sys.exit(0)


if __name__ == "__main__":
    main()
