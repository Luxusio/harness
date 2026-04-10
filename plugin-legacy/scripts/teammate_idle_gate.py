#!/usr/bin/env python3
"""TeammateIdle hook — checks team worker produced minimum deliverables.

Non-blocking. Advisory feedback only.
stdin: JSON | exit 0: always
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import (read_hook_input, yaml_field,
                  TASK_DIR, MANIFEST, team_artifact_status)


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

        team_state = team_artifact_status(task_path)
        team_status = team_state.get("derived_status") or yaml_field("team_status", state_file) or ""
        if team_status not in ("running", "degraded"):
            continue

        if not team_state.get("worker_summary_required"):
            continue

        team_dir = team_state.get("worker_summary_dir") or os.path.join(task_path, "team")
        if not os.path.isdir(team_dir):
            print(f"HINT: {entry} is a running team task but has no team/ directory yet.")
            print("  Workers should leave summaries under team/worker-<name>.md")
            continue

        missing_workers = list(team_state.get("worker_summary_missing_workers") or [])
        if missing_workers:
            print(
                f"HINT: {entry} is missing worker summaries for: {', '.join(missing_workers[:6])}."
            )
            print("  Each planned worker should leave team/worker-<name>.md before synthesis.")

        worker_errors = list(team_state.get("worker_summary_errors") or [])
        if worker_errors:
            print(f"HINT: {entry} has incomplete worker summaries.")
            print("  " + " | ".join(worker_errors[:3]))

    sys.exit(0)


if __name__ == "__main__":
    main()
