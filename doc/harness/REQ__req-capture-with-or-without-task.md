# REQ - Req Capture With Or Without Task

## Intent
REQ capture must remain available regardless of whether a harness task is open. The mechanism comprises four surfaces: (1) an always-on SessionStart banner line reminding the operator of the REQ capture path, (2) the write_req_doc MCP tool accepting calls with or without task_id, (3) the prewrite_gate allowing doc/<area>/REQ__*.md writes outside an active task, and (4) the scripts/req_scaffold.py CLI as a task-free entry point. The bug this REQ captures is the user-observed friction ("REQ를 적는걸 까먹는다") that occurred whenever the user did not invoke harness:run before stating an observable requirement (resolved by this task, 2026-05-31).

## Observable Behavior
- Every SessionStart prints exactly one banner line containing the substring 'REQ:' and the literal path placeholder 'doc/<area>/REQ__<slug>.md' and the cue 'write_req_doc'.
- mcp__harness__write_req_doc accepts calls with task_id omitted or set to the empty string. In that case the response source field starts with 'adhoc:' followed by an ISO 8601 UTC timestamp; the response task_dir is the empty string; the REQ file is written to doc/<area>/REQ__<slug>.md.
- mcp__harness__write_req_doc with task_id supplied retains identical legacy behavior — response source is 'task: <task_id>', response task_dir is the canonical task directory path.
- prewrite_gate.py allows Write/Edit/MultiEdit of any path matching doc/**/REQ__*.md outside an active harness task; the gate exits 0 and prints nothing.
- plugin/scripts/req_scaffold.py is invokable from the command line with --task-id omitted and writes the same REQ file format the MCP handler produces.
- The MCP tool JSON schema for write_req_doc does not list task_id in the required array.

## Acceptance Signals
- Every SessionStart prints exactly one banner line containing the substring 'REQ:' and the literal path placeholder 'doc/<area>/REQ__<slug>.md' and the cue 'write_req_doc'.
- mcp__harness__write_req_doc accepts calls with task_id omitted or set to the empty string. In that case the response source field starts with 'adhoc:' followed by an ISO 8601 UTC timestamp; the response task_dir is the empty string; the REQ file is written to doc/<area>/REQ__<slug>.md.
- mcp__harness__write_req_doc with task_id supplied retains identical legacy behavior — response source is 'task: <task_id>', response task_dir is the canonical task directory path.
- prewrite_gate.py allows Write/Edit/MultiEdit of any path matching doc/**/REQ__*.md outside an active harness task; the gate exits 0 and prints nothing.
- plugin/scripts/req_scaffold.py is invokable from the command line with --task-id omitted and writes the same REQ file format the MCP handler produces.
- The MCP tool JSON schema for write_req_doc does not list task_id in the required array.

## Verification Cues
- tests/test_session_start_req_reminder.py — banner contents and drift_warn entry registration.
- tests/test_write_req_doc_task_optional.py — four cases: missing task_id, empty task_id, supplied task_id, schema required array.
- tests/test_prewrite_gate_req_doc_outside_task.py — gate behavior under doc/ui/REQ__*.md and doc/harness/REQ__*.md outside a task.
- Manual smoke: python3 plugin/scripts/req_scaffold.py --area common --slug smoke --intent ... should write the file with no harness task open.
- After install.py --force on the next session, calling mcp__harness__write_req_doc without task_id from an MCP client should return ok with adhoc: source.

## Non-Goals
- Automated detection of when the user has stated a requirement is explicitly out of scope (user rejected keyword/contains heuristics).
- This REQ does not require write_req_doc to function without an MCP server present; the standalone CLI covers that case.
- It does not promise that capturing a REQ outside a task automatically links the REQ to the task that resolves the underlying request; provenance for adhoc REQs is the source field's adhoc:<ISO8601> timestamp only.
- The banner line is not promised to be the only or final REQ-related reminder surface — additional reminders may be added later if usage data warrants.

## Source
- created: 2026-05-31
- source: C-100 (CONTRACTS.local.md): bug report -> REQ doc for expected normal behavior. Bug: REQ-forgetting friction when harness:run is not invoked. Resolved by this task (TASK__session-start-req-reminder-and-drift-warn). Captured ad-hoc via req_scaffold.py CLI which dogfoods the same task-optional write path the loosened MCP handler now exposes.
