# 2026-05-08 — plan-ceo-review STOP-wording carryover + Prior Learnings cleanup

**Task:** TASK__tighten-plan-ceo-review-stop-wording-carryover

Closes the last cross-skill voice-consistency gap left over from the 2026-05-07 ceo-voice alignment task and cleans up a pre-existing broken bash block flagged during prior QA. Companion to TASK__align-ceo-voice-with-gstack, TASK__align-design-review-with-gstack, and TASK__align-eng-and-devex-review-with-gstack.

## What changed

**plan-ceo-review/SKILL.md (+41 / -21):**

- **AC-001 — STOP-wording tightened across 13 sites.** Replaced `"If no issues or fix is obvious, state what you'll do and move on — don't waste a question"` with `"If no issues, say so and move on. A finding with an 'obvious fix' is still a finding and still needs user approval — surface it via AskUserQuestion"`. Applied to:
  - 1 meta-rule at line 1136 (`## CRITICAL RULE — How to ask questions` § Escape hatch).
  - 12 per-section `**STOP.**` markers at lines 738, 769, 798, 813, 849, 861, 901, 912, 928, 943, 958, 980. Sibling skills do NOT carry the weak wording in their per-section STOPs (verified).
  - Scope was broadened mid-implementation (originally line 1140 only) with explicit user re-approval per C-07.

- **AC-002 — `## Prior Learnings` block restored.** Replaced empty `if/else` branches and dangling text fragment "smarter on their codebase over time." (residue from `2bc12f1 refactor: de-gstack the four plan review skills`) with a coherent harness-native equivalent that uses `doc/harness/learnings.jsonl` (Tier 3) and `doc/harness/patterns/*.md` (Tier 2) — the canonical paths from `plugin/CLAUDE.md` §11.

## Excluded from scope (with rationale)

- **Same broken bash defect in plan-eng-review/SKILL.md:249-258, plan-design-review/SKILL.md (~L738), plan-devex-review/SKILL.md (~L48,~L581).** All four sibling skills carry the same de-gstack residue. User scoped this task to plan-ceo-review only. Filed for a separate cross-file cleanup task.
- **Other gstack content alignment.** Already excluded by `doc/harness/SPEC.md` design choices.

## Impact

Cross-skill voice consistency under `plugin/skills/plan/` reaches 4-of-4 fully aligned. Anyone running `Skill(plan-ceo-review)` standalone or as a sub-skill in `Skill(harness:plan)` Phase 1 now sees consistent enforcement of the Anti-shortcut clause: a "finding with an obvious fix" is still a finding and still needs user approval — both at the meta-rule level and at every per-section STOP marker.

## Cross-skill consistency snapshot

| Skill | Voice | Confusion Protocol | Anti-shortcut | Meta STOP | Per-section STOP |
|-------|:-:|:-:|:-:|:-:|:-:|
| plan-ceo-review | ✓ | ✓ | ✓ | tight | tight (13 sites) |
| plan-design-review | ✓ | ✓ | ✓ | tight | tight |
| plan-eng-review | ✓ | ✓ | ✓ | tight | tight |
| plan-devex-review | ✓ | ✓ | ✓ | tight | tight |

## Cross-skill follow-up (deferred)

- `_CROSS_PROJ` empty-bash residue cleanup in plan-eng-review, plan-design-review, plan-devex-review. Same fix pattern as AC-002. One small task per file, or a single multi-file task.

## References

- PLAN.md: `doc/harness/tasks/TASK__tighten-plan-ceo-review-stop-wording-carryover/PLAN.md`
- HANDOFF.md: `doc/harness/tasks/TASK__tighten-plan-ceo-review-stop-wording-carryover/HANDOFF.md`
- Prior tasks in this series: `doc/changes/2026-05-07-plan-ceo-review-gstack-voice-alignment.md`, `doc/changes/2026-05-08-plan-design-review-gstack-alignment.md`, `doc/changes/2026-05-08-eng-and-devex-review-gstack-alignment.md`
- gstack source: `https://github.com/garrytan/gstack`
