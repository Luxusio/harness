# REQ process task-set-fields-via-mcp
summary: Coordinator must use task_set_fields MCP tool to update coordinator-settable TASK_STATE fields
status: active
updated: 2026-04-07
freshness: current
verified_at: 2026-04-07T00:00:00Z
derived_from:
  - plugin/mcp/harness_server.py
  - plugin/scripts/hctl.py
  - tests/test_task_set_fields.py
confidence: high
kind: process

## Rule

Coordinator must use `mcp__plugin_harness_harness__task_set_fields` (or `hctl set-fields`)
to update coordinator-settable fields in TASK_STATE.yaml. Direct Read/Write of TASK_STATE.yaml
by coordinator is prohibited for these fields.

## Coordinator-settable fields

maintenance_task, lane, mutates_repo, doc_sync_required, qa_required, browser_required,
risk_level, parallelism, doc_sync_expected.

## Blocked fields

plan_verdict, runtime_verdict, document_verdict, status, state_revision, parent_revision,
schema_version, touched_paths, roots_touched, verification_targets, agent_run_* prefix.
Each blocked field has a dedicated tool: write_artifact (verdicts), task_close (status),
record_agent_run (agent_run_*), task_update_paths (touched_paths / roots_touched).

## Evidence

Implemented in TASK__mcp-task-set-fields. 19 tests pass in tests/test_task_set_fields.py.
