#!/usr/bin/env python3
"""TeammateIdle hook — checks team worker produced minimum deliverables.

Non-blocking. Advisory feedback only.
stdin: JSON | exit 0: always
"""

import sys
import os
import glob

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import (read_hook_input, json_field, yaml_field,
                  TASK_DIR, MANIFEST)


def main():
    data = read_hook_input()

    if not os.path.isfile(MANIFEST):
        sys.exit(0)
    if not os.path.isdir(TASK_DIR):
        sys.exit(0)

    # Find active team tasks
    for entry in sorted(os.listdir(TASK_DIR)):
        task_path = os.path.join(TASK_DIR, entry)
        if not os.path.isdir(task_path) or not entry.startswith("TASK__"):
            continue
        state_file = os.path.join(task_path, "TASK_STATE.yaml")
        if not os.path.isfile(state_file):
            continue

        status = yaml_field("status", state_file) or ""
        if status in ("closed", "archived", "stale"):
            continue

        orch_mode = yaml_field("orchestration_mode", state_file) or "solo"
        if orch_mode != "team":
            continue

        team_status = yaml_field("team_status", state_file) or ""
        if team_status != "running":
            continue

        # Check for worker summaries
        team_dir = os.path.join(task_path, "team")
        if not os.path.isdir(team_dir):
            print(f"HINT: {entry} is a running team task but has no team/ directory yet.")
            print("  Workers should leave summaries under team/worker-<name>.md")
            continue

        worker_files = glob.glob(os.path.join(team_dir, "worker-*.md"))
        if not worker_files:
            print(f"HINT: {entry} team/ directory exists but no worker-*.md summaries found.")
            print("  Each worker should leave a brief summary of their work.")

    sys.exit(0)


if __name__ == "__main__":
    main()
