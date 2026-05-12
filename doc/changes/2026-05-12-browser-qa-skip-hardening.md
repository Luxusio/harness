# 2026-05-12 — browser-QA skip hardening + gate next-action standardization

**Task:** TASK__retro-2026-05-12-browser-qa-skip-hardening

Two retrospectives merged into one hardening pass. The harness now refuses to close a task that touched frontend files in a project with `manifest.qa.browser_qa_supported: true` unless `CRITIC__qa.md` contains a `qa-browser` section, and every gate-block message now carries a `next_action_command` so the orchestrator gets a one-step resolution instead of having to grep for the helper.

## What changed

**plugin/scripts/_gate_response.py (NEW):**

- Canonical helper `gate_response(decision, *, reason, next_action_command, owner_skill, docs)` returns the shape every gate writes. Backwards-compatible — `decision` + `reason` preserved for legacy consumers.

**plugin/scripts/_lib.py:**

- `emit_permission_decision` accepts `next_action_command` / `owner_skill` / `docs` kwargs and appends them as an arrow-prefixed tail to `permissionDecisionReason` (PreToolUse contract stays shape-stable).
- `emit_compact_context` now appends `"qa-browser evidence in CRITIC__qa.md"` to `missing_for_close` when manifest declares `qa.browser_qa_supported: true`, touched_paths include frontend files (`.tsx/.jsx/.vue/.svelte/.html/.css/.scss` or `/components/`, `/pages/`, `/views/`, `/routes/` fragments), and `CRITIC__qa.md` lacks a qa-browser header. The `task_close` MCP refuses while that entry is present.
- New helpers: `_read_nested_manifest_field`, `_frontend_touched`, `_has_qa_browser_section`.

**plugin/scripts/stop_gate.py:**

- Reason text now derives `next_action_command` from the active task's first `missing_for_close` item — "Spawn Agent(...harness:qa-browser...)" or "Skill('harness:plan'...)" instead of a static "do task_verify or task_close".

**plugin/scripts/prewrite_gate.py + plugin/scripts/mcp_bash_guard.py:**

- Owner-rejection messages now pass `next_action_command` mapped from the rule_id / protected-artifact basename ("Spawn Agent(subagent_type='harness:developer',...)" for HANDOFF.md, `python3 plugin/scripts/update_checks.py ...` for CHECKS.yaml, etc.).

**plugin/scripts/update_checks.py:**

- New AC kind `browser_interaction`. Promotion to `passed` requires `owner: qa-browser` (gate violation message names the next action).
- `--test-evidence` pointing to `CRITIC__qa.md` is accepted for `browser_interaction` ACs iff the file contains a `## qa-browser` (or `### qa-browser`) header.

**plugin/scripts/verification_gap_check.py (NEW) + plugin/hooks/hooks.json:**

- New SessionStart hook script (existing event type — no new hook event). Prints `[verification-gap] active task <id>: browser QA required ...` when manifest + diff + missing CRITIC section conditions match. Kill switch: `HARNESS_DISABLE_VERIFY_GAP=1`.

**plugin/mcp/harness_server.py:**

- `write_critic_qa` accepts a new `manual_ux_verification` arg. Rendered CRITIC__qa.md always contains a `## Manual UX verification` section.
- `lens='browser'` + empty `manual_ux_verification` → placeholder + `runtime_verdict` forced to PENDING regardless of input verdict.
- `lens != 'browser'` (or legacy no-lens) + empty arg → `_n/a — non-browser lens_` placeholder, verdict preserved.
- `_worst_verdict` normalizes both inputs to uppercase canonical form before comparing (state file may carry legacy lowercase `pending`).
- `write_critic_qa` `inputSchema` extended with the new optional `manual_ux_verification` property.

**plugin/skills/run/SKILL.md:**

- Phase 4 strategy bullet rewritten as a MUST clause naming the close-gate enforcement point: "MUST spawn qa-browser when `manifest.qa.browser_qa_supported: true` AND the diff contains any frontend file. Skipping is blocked by task_close (see `plugin/scripts/_lib.py:emit_compact_context`)."

**Tests (5 new + 1 updated):**

- `tests/regression/retro_2026_05_12_browser_qa_skip_hardening/test_ac_001__gate_response.py` (8 tests)
- `tests/regression/retro_2026_05_12_browser_qa_skip_hardening/test_ac_002__close_gate.py` (9 tests)
- `tests/regression/retro_2026_05_12_browser_qa_skip_hardening/test_ac_004__browser_interaction_kind.py` (4 tests)
- `tests/regression/retro_2026_05_12_browser_qa_skip_hardening/test_ac_005__verification_gap.py` (6 tests)
- `tests/regression/retro_2026_05_12_browser_qa_skip_hardening/test_ac_006__manual_ux_section.py` (4 tests)
- `tests/regression/speed_up_developer_qa_via_subagent_team/test_ac_004__lens_merge.py` (2 tests updated to match new AC-006 contract — browser lens calls now supply `manual_ux`).

All 31 new tests + 5 prior-task tests pass via `python3 -m unittest discover`.

## Deferred to follow-up task

6 ACs deferred per user direction at mid-task scope checkpoint (Cluster A+B in scope, Cluster C+D follow-up):

- AC-007 `update_checks.py --batch <yaml>` mode
- AC-008 `verify_close.py` CLI wrapper
- AC-009 hash-based staleness replacing mtime
- AC-010 `plugin/agents/TOOL_GRANTS.md` doc
- AC-011 `find_harness_script.py` helper
- AC-012 `submodule_diff.py` helper

## Impact

The browser-QA skip pattern surfaced in the 2026-05-12 retros becomes structurally implausible at six layered enforcement points (plan-time browser_interaction kind, develop-time MUST clause, verify-time SessionStart inject, pre-close emit_compact_context gate, close-time task_close refusal, and MCP-time CRITIC manual-UX section + verdict downgrade). Every gate response now names the next CLI / MCP call to issue.

## References

- PLAN.md: `doc/harness/tasks/TASK__retro-2026-05-12-browser-qa-skip-hardening/PLAN.md`
- HANDOFF.md: `doc/harness/tasks/TASK__retro-2026-05-12-browser-qa-skip-hardening/HANDOFF.md`
- DOC_SYNC.md: `doc/harness/tasks/TASK__retro-2026-05-12-browser-qa-skip-hardening/DOC_SYNC.md`
- Prior task baseline: `doc/changes/2026-05-12-parallel-fanout-and-lens-aware-qa-merge.md` (lens-aware merge introduced; this task extends with manual_ux contract)
- MCP reload caveat: harness_server.py changes activate next session restart per the 2026-05-08 learning.
