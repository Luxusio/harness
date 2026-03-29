#!/usr/bin/env python3
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import (read_hook_input, json_field, json_array, yaml_field, yaml_array,
                  manifest_field, is_browser_first_project, is_doc_path,
                  extract_roots, TASK_DIR, MANIFEST, now_iso)

# TaskCreated hook — initializes minimal task artifacts.
# Non-blocking (exit 0 always).
# stdin: JSON | exit 0: success | exit 2: block (unused)

data = read_hook_input()

task_id = json_field("task_id", data) or os.environ.get("HARNESS_TASK_ID", "")

if not task_id:
    sys.exit(0)

target = os.path.join(TASK_DIR, task_id)
os.makedirs(target, exist_ok=True)

# Detect browser-first from manifest
browser_required = "false"
qa_mode = "auto"
if is_browser_first_project():
    browser_required = "true"
    qa_mode = "browser-first"

# Initialize TASK_STATE.yaml if missing
state_file = os.path.join(target, "TASK_STATE.yaml")
if not os.path.exists(state_file):
    with open(state_file, "w") as f:
        f.write(f"""task_id: {task_id}
status: created
lane: unknown
mutates_repo: unknown
qa_required: pending
qa_mode: {qa_mode}
plan_verdict: pending
runtime_verdict: pending
document_verdict: pending
browser_required: {browser_required}
doc_sync_required: false
doc_changes_detected: false
touched_paths: []
roots_touched: []
verification_targets: []
blockers: []
review_overlays: []
risk_tags: []
performance_task: false
orchestration_mode: solo
team_provider: none
team_status: n/a
team_size: 0
team_reason: ""
team_plan_required: false
team_synthesis_required: false
fallback_used: none
updated: {now_iso()}
""")
    print(f"INFO: Initialized {state_file}")

# Create HANDOFF.md stub if missing
handoff_file = os.path.join(target, "HANDOFF.md")
if not os.path.exists(handoff_file):
    with open(handoff_file, "w") as f:
        f.write(f"""# Handoff: {task_id}
status: pending
updated: {now_iso()}
""")
    print(f"INFO: Created {handoff_file} stub")

# Create REQUEST.md stub if missing
request_file = os.path.join(target, "REQUEST.md")
if not os.path.exists(request_file):
    request_text = json_field("description", data) or json_field("request", data) or ""
    body = request_text if request_text else "<!-- Request details pending -->"
    with open(request_file, "w") as f:
        f.write(f"""# Request: {task_id}
created: {now_iso()}

{body}
""")
    print(f"INFO: Created {request_file}")

sys.exit(0)
