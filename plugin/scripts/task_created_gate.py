#!/usr/bin/env python3
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import (read_hook_input, hook_json_get, json_field, json_array, yaml_field, yaml_array,
                  manifest_field, is_browser_first_project, is_doc_path,
                  extract_roots, TASK_DIR, MANIFEST, now_iso,
                  TASK_STATE_SCHEMA_VERSION,
                  exit_if_unmanaged_repo)

# Legacy task bootstrap helper — initializes minimal task artifacts when invoked manually.
# Non-blocking (exit 0 always).
# stdin: JSON | exit 0: success | exit 2: block (unused)

def main():
    exit_if_unmanaged_repo()

    data = read_hook_input()

    task_id = hook_json_get(data, "task_id") or os.environ.get("HARNESS_TASK_ID", "")

    if not task_id:
        sys.exit(0)

    # Ignore non-harness task IDs (e.g. Claude Code internal numeric IDs)
    if not task_id.startswith("TASK__"):
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
schema_version: {TASK_STATE_SCHEMA_VERSION}
state_revision: 0
parent_revision: null
status: created
lane: unknown
execution_mode: pending
planning_mode: standard
mutates_repo: unknown
qa_required: pending
qa_mode: {qa_mode}
plan_verdict: pending
runtime_verdict: pending
runtime_verdict_freshness: current
document_verdict: pending
document_verdict_freshness: current
runtime_verdict_fail_count: 0
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
orchestration_mode: pending
team_provider: none
team_status: n/a
team_size: 0
team_reason: ""
team_plan_required: false
team_synthesis_required: false
fallback_used: none
workflow_violations: []
workflow_mode: compliant
risk_level: pending
parallelism: 1
workflow_locked: true
maintenance_task: false
routing_compiled: false
routing_source: pending
compliance_claim: strict
artifact_provenance_required: true
result_required: false
plan_session_state: closed
capability_delegation: unknown
collapsed_mode_approved: false
collapsed_reason: ""
directive_capture_state: clean
pending_directive_ids: []
complaint_capture_state: clean
pending_complaint_ids: []
last_complaint_at: null
agent_run_developer_count: 0
agent_run_developer_last: null
agent_run_writer_count: 0
agent_run_writer_last: null
agent_run_critic_plan_count: 0
agent_run_critic_plan_last: null
agent_run_critic_runtime_count: 0
agent_run_critic_runtime_last: null
agent_run_critic_document_count: 0
agent_run_critic_document_last: null
updated: {now_iso()}
""")
        print(f"INFO: Initialized {state_file}")

    # HANDOFF.md is NOT created here — it is a developer-owned artifact.
    # Developer creates HANDOFF.md after implementation with verification breadcrumbs.

    # Create REQUEST.md stub if missing
    request_file = os.path.join(target, "REQUEST.md")
    if not os.path.exists(request_file):
        request_text = hook_json_get(data, "description") or hook_json_get(data, "request") or ""
        body = request_text if request_text else "<!-- Request details pending -->"
        with open(request_file, "w") as f:
            f.write(f"""# Request: {task_id}
created: {now_iso()}

{body}
""")
        print(f"INFO: Created {request_file}")

    sys.exit(0)


if __name__ == "__main__":
    main()
