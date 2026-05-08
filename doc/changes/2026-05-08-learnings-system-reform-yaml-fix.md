# 2026-05-08 — learnings system reform + close-gate YAML bugfix

**Task:** TASK__learnings-system-reform-and-yaml-bug-fix

User audit: "지금 러닝.제이슨엘 보면 '그래서 배운게 뭔데?' 소리가 절로 나오는데" — the `learnings.jsonl` was 6 identical noise lines, all `gate-warn / "CHECKS.yaml absent at close"`, with zero meaningful entries. Two bugs running together: a real script bug producing the noise, and a doc-theater system that never captured real learnings.

## What changed

**plugin/scripts/update_checks.py — bugfix (AC-001):**
- `_set_field` append branch derived indent from `"  "` hardcoded (2 spaces, list-item indent). Now derives from existing field line indent (4 spaces canonical for AC-item nested fields).
- Root cause: when promoting an AC whose `last_updated` field is missing, the field was inserted at list-item indent under `checks:`, mixing map keys and list items at the same level. PyYAML rejected the result, `_parse_checks_yaml` returned None, `_checks_gate_status` reported "absent", and `task_close` silently bypassed the AC validation gate. 6 tasks closed this session without their CHECKS.yaml being validated.
- Regression test: `tests/regression/task_learnings_reform/test_ac_001__update_checks_indent.py`.

**plugin/mcp/harness_server.py — gate-warn removal (AC-002):**
- `task_close` no longer auto-writes `gate-warn / "checks-missing-at-close"` entries to `learnings.jsonl`. Replaced with `pass` and a comment.
- Reasoning: gate-warn is a runtime alert, not a learning. Even after the AC-001 bug fix, the "absent" path remains rare (legit pre-PR2 task dirs), and runtime alerts belong in stderr or a separate alert log, not in a Tier-3 learnings store.

**plugin/skills/plan/write-artifacts.md §6.8 + plugin/skills/develop/SKILL.md Phase 8.5 — reshape (AC-003, AC-004):**
- Old: end-of-task checkbox prompts ("Reflect on session", "What took longer than expected", "operational friction sweep — log at least one operational learning per session"). Result across 6 tasks: 0 entries from agent execution.
- New: in-flight capture rule. **"When you discover something genuinely useful, log it the moment you find it — while it's fresh. Do NOT save reflections for the end. Do NOT log entries to fill a quota. If nothing was learned, write nothing."** Added concrete pass/fail examples — what passes the bar (`/plugin` shell-sub gotcha, `update_checks.py` indent bug, `write_plan_artifact.py --input` flag) vs. what doesn't (vague reflections, narration, tool-usage tautologies).
- Drop: 5-minute-save subjective rubric, calibration tables (small-N noise), open-ended self-prompts.

**doc/harness/learnings.jsonl — 3 real backfilled entries (AC-005):**
- `operational/plugin-slash-no-shell-sub`: `/plugin marketplace add` doesn't expand bash `$(pwd)`. Use `./` or literal path.
- `pitfall/update-checks-indent-bug`: the AC-001 bug itself. Future-proofing — if the bug regresses or a similar indent issue appears elsewhere, this entry catches it.
- `feedback/plan-gate-verbose-summary`: user feedback that Phase 5 plan-gate's voice-consensus / cross-phase-theme rollups buried work direction; outcome-focused summary is the answer.
- These 3 entries serve as the canonical reference shape for what a good learning looks like.

## Excluded from scope

- `promote_learnings.py` Tier 3→2→1 promotion pipeline. Exists, isn't run in current sessions. Keep-or-kill decision deferred until we see if the new in-flight rule produces meaningful entries.
- Cleanup of 6 historical broken CHECKS.yaml in closed task dirs. Closed-state read-only by convention; no future runs read them.
- Promoting any backfilled learning to Tier 2 patterns. Premature — let entries accumulate first.

## Impact

Two-pronged: (a) a real correctness bug (silent close-gate bypass on 6/6 tasks this session) is fixed and regression-tested; (b) the learnings system stops being doc theater — the new rule trades quantity for quality (no quota, no checkbox, no end-of-task reflection prompts), with concrete examples grounding what counts as a real entry. The 3 backfilled entries demonstrate the new bar.

## Smoke test

Closing this task's own CHECKS.yaml (5 ACs) produced correctly-indented `last_updated` at 4-space indent across all 5 entries — bug fix verified end-to-end on its own task lifecycle.

## References

- PLAN.md: `doc/harness/tasks/TASK__learnings-system-reform-and-yaml-bug-fix/PLAN.md`
- HANDOFF.md: `doc/harness/tasks/TASK__learnings-system-reform-and-yaml-bug-fix/HANDOFF.md`
- Regression test: `tests/regression/task_learnings_reform/test_ac_001__update_checks_indent.py`
