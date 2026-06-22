# REQ - Req Capture With Or Without Task

status: accepted

## Intent
REQ capture must remain available regardless of whether a harness task is open.
User-stated durable requirements must not live only in chat history, task-local
state, or transient hook logs. The current mechanism is direct committed REQ
updates under `doc/<area>/REQ__*.md`, with `plugin/scripts/req_scaffold.py` as
the low-friction CLI helper when no existing REQ fits.

This supersedes the older MCP REQ-writer flow. The MCP server no longer exposes
a REQ writer; `write_plan` owns task-local plan artifacts, while REQ documents
are normal committed repo docs.

## Observable Behavior
- A REQ can be created or updated while no harness task is active by editing
  `doc/<area>/REQ__*.md` directly or by running `plugin/scripts/req_scaffold.py`
  with `--task-id` omitted.
- A REQ can be linked to a task by including a source line such as
  `- source: task: TASK__example`; `prewrite_gate.py` accepts that back-link as
  a valid task-to-REQ reference.
- `prewrite_gate.py` allows Write/Edit/MultiEdit of paths matching
  `doc/**/REQ__*.md` outside an active harness task.
- The harness MCP tool list does not include a REQ writer.
- When task feedback reveals an uncaptured durable requirement, the resolving
  task creates or updates the REQ directly or through `req_scaffold.py`.

## Acceptance Signals
- `tests/test_req_doc_automation.py` covers REQ detection and scaffold output.
- `tests/test_req_scaffold_status_field.py` covers accepted/candidate status
  rendering for scaffolded REQ docs.
- `tests/test_prewrite_gate_req_doc_outside_task.py` covers direct REQ writes
  outside active tasks.
- `tests/test_prewrite_gate_req_back_reference.py` covers task source back-links.
- `tests/test_harness_mcp_server.py` asserts the MCP tool list does not expose a
  REQ writer.

## Verification Cues
- Manual smoke: run `python3 plugin/scripts/req_scaffold.py --area common
  --slug smoke --intent ... --observable-behavior ... --verification-cues ...`
  with no active harness task; it writes `doc/common/REQ__smoke.md`.
- Manual MCP check: `tools/list` for the harness server includes `write_plan`
  and excludes the old REQ writer.

## Non-Goals
- This REQ does not require keyword-only automatic requirement detection.
- This REQ does not require a task-local evidence artifact for REQ capture.
- This REQ does not promise that an ad-hoc REQ is automatically assigned to a
  future goal; provenance is the committed doc content until a task links it.

## Source
- created: 2026-05-31
- updated: 2026-06-22
- source: C-100/C-101 durable requirement capture, updated for the goal-based
  MCP surface that removed the old REQ writer.
