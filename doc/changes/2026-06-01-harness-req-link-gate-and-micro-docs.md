---
date: 2026-06-01
task: TASK__harness-proportionality-routing-p1-p2-p3
type: maintenance
freshness: current
---

# Harness REQ-link Gate Fix + Micro-Mode Documentation

Two harness producer/consumer mismatches closed in a single MAINTENANCE-class
diff (~140 LOC across 5 files).

**AC-001 — Prewrite REQ gate accepts task-back-link.**
`plugin/scripts/prewrite_gate.py::_task_has_req_reference` now follows the
back-link that `req_scaffold.write_req_doc` writes into every REQ doc
(`- source: task: <task_id>`). Before this fix, the gate only grepped
PLAN/HANDOFF/DOC_SYNC bodies for a REQ path string, forcing every
observable-UI/API task to mirror the REQ path into PLAN.md via a redundant
`write_plan_artifact` round-trip. The new depth-2 scan over
`doc/<area>/REQ__*.md` (exploiting the existing `req_scaffold.py:55`
convention) keeps gate latency at ~0.055s — a 76× improvement over the
intermediate `os.walk` implementation that briefly flaked the test suite.
Regression tests live in `tests/test_prewrite_gate_req_back_reference.py`.

**AC-002 — Micro-mode `next_action` surfaces PLAN exemption.**
`plugin/scripts/_lib.py::emit_compact_context` micro-loop branch now emits
a `next_action` that explicitly states "PLAN.md is exempt under
execution_mode='micro' (REQ durable-doc gate still applies)" — closing
the discoverability gap that drove agents to fall back to `standard`
mode unnecessarily. `task_start { execution_mode: 'micro' }` already
exempted PLAN.md via `plan_session_state: micro_loop`; this PR just makes
the rule discoverable through the tool response.

**AC-003 — Runtime rules doc.** `plugin/CLAUDE.md` § 4 Plan-first rule
gained a one-line note documenting the micro exemption.

## Original scope and re-scope

Originally bundled (PR-A: P1 + P2 + P3) where P1 was a new `lite` mode.
Both CEO voices (Agent + codex via `omc ask codex`) independently rejected
P1 at the Phase 1 premise gate on five concrete grounds:
proportionality already exists (`_is_micro_loop_state` at `_lib.py:1327`),
`lite` collides with existing `light`, `doc/common/REQ__process__plan-skill-review-pipeline.md`
mandates the 7-phase pipeline, P2 is an independent gate fix, and P3 is
documentation rather than enforcement. User selected re-scope to P2+P3.
P1 deferred to a follow-up task to be framed as "auto-route to existing
`light`/`micro` based on size signals" (no new mode), backed by an
alternatives table and golden-replay coverage.

## Effect on contributors

- Observable-UI/API tasks no longer pay an extra `write_plan_artifact`
  round-trip when the REQ was registered via `write_req_doc(task_id=...)`.
- Agents discovering harness for the first time can read
  `plugin/CLAUDE.md` § 4 and the `task_start` `next_action` to learn the
  `execution_mode: micro` PLAN-exemption rule.
- One pre-existing test flake (`test_prewrite_denies_observable_source_edit_without_req_link`
  under xdist 16-worker contention) is now stable.
