#!/usr/bin/env python3
import sys, os, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import (read_hook_input, json_field, json_array, yaml_field, yaml_array,
                  manifest_field, is_browser_first_project, is_doc_path,
                  extract_roots, TASK_DIR, MANIFEST, now_iso)

# Stop hook — catches premature completion attempts.
# BLOCKING: exit 2 prevents stop when tasks are still open.
# stdin: JSON | exit 0: allow stop | exit 2: BLOCK stop

# No harness initialized — allow stop
if not os.path.exists(".claude/harness/manifest.yaml"):
    sys.exit(0)
if not os.path.isdir(TASK_DIR):
    sys.exit(0)

open_tasks = []
blocked_tasks = []
pending_doc_sync = []

for entry in sorted(os.listdir(TASK_DIR)):
    task_path = os.path.join(TASK_DIR, entry)
    if not os.path.isdir(task_path):
        continue

    state_file = os.path.join(task_path, "TASK_STATE.yaml")
    task_id = entry

    if not os.path.exists(state_file):
        continue

    status = yaml_field("status", state_file) or ""

    if status in ("closed", "archived", "stale"):
        continue
    elif status == "blocked_env":
        blocked_tasks.append(task_id)
    else:
        open_tasks.append(f"{task_id} [status: {status or 'unknown'}]")
        # Warn about pending DOC_SYNC for repo-mutating open tasks
        mutates = yaml_field("mutates_repo", state_file) or ""
        if mutates in ("true", "unknown"):
            if not os.path.exists(os.path.join(task_path, "DOC_SYNC.md")):
                pending_doc_sync.append(task_id)

if open_tasks:
    print("BLOCKED: Cannot stop — open tasks remain:")
    for t in open_tasks:
        print(f"  - {t}")
    if blocked_tasks:
        print(f"Note: {len(blocked_tasks)} task(s) are blocked_env (need env fix):")
        for t in blocked_tasks:
            print(f"  - {t}")
    if pending_doc_sync:
        print(f"Note: {len(pending_doc_sync)} repo-mutating task(s) still need DOC_SYNC.md:")
        for t in pending_doc_sync:
            print(f"  - {t}")
    sys.exit(2)

if blocked_tasks:
    print(f"WARNING: Stopping with {len(blocked_tasks)} blocked_env task(s):")
    for t in blocked_tasks:
        print(f"  - {t}")

sys.exit(0)
