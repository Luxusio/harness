# 2026-05-12 — Stop asking scope-partition questions

**Task:** TASK__stop-asking-scope-partition-questions

plan-skill and develop-skill now auto-decide scope-partition meta-decisions (split / combine / defer / do-a-subset) via Principle P1 (Choose completeness) instead of routing them through user gates. The three intentional user gates (premise §1.1, User Challenge §5.3, final approval §5.4.1) stay exactly as they are.

## What changed

**plugin/skills/plan/SKILL.md (Confusion Protocol):**

- Appended a "Not a trigger" paragraph stating: scope-partition decisions (split into smaller tasks, combine items, defer to follow-up, do a subset) are NEVER Confusion Protocol triggers — auto-decide via P1. Rationale: "the cost of 'do more' is more work, not unwound work; that does not meet the protocol's bar."

**plugin/skills/plan/decision-principles.md (Decision Classification):**

- Appended a "Scope decisions" bullet under Classification — Mechanical by default, P1 auto-decide, "do more vs do less" never User Challenge unless the user explicitly contradicts the request.

**plugin/skills/develop/SKILL.md (Error Philosophy):**

- Appended a "No mid-task scope cuts" paragraph — develop executes the plan as approved at plan-skill Phase 5; AskUserQuestion is reserved for concrete BLOCKED escalation, never for AC-drop / task-split / item-defer meta questions.

## Impact

User reported repeated scope-partition meta-questions across recent tasks ("split into smaller task?", "Cluster A only?", "merge retro #2?"). All three were Confusion-Protocol false positives — the user's stated scope was already clear; the orchestrator was asking for permission to do less. After this change, those slots auto-resolve to "do more" (P1) and the user only sees the three documented gates.

## References

- PLAN.md: `doc/harness/tasks/TASK__stop-asking-scope-partition-questions/PLAN.md`
- HANDOFF.md: `doc/harness/tasks/TASK__stop-asking-scope-partition-questions/HANDOFF.md`
- DOC_SYNC.md: `doc/harness/tasks/TASK__stop-asking-scope-partition-questions/DOC_SYNC.md`
- Prior outcome-only gate task: `doc/changes/2026-05-12-plan-skill-phase5-gate-outcome-only.md`
