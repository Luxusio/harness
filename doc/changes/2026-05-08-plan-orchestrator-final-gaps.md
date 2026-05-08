# 2026-05-08 — plan/ orchestrator final gaps closed

**Task:** TASK__plan-orchestrator-final-gaps

Closes the final 2 gstack-vs-harness orchestrator gaps surfaced by a post-task audit on TASK__align-plan-orchestrator-with-gstack-autoplan. With this task, the gstack alignment series across `plugin/skills/plan/` (parent) + 4 sub-skills (plan-ceo-review, plan-design-review, plan-eng-review, plan-devex-review) is fully complete.

## What changed

**plugin/skills/plan/SKILL.md (+20 lines, 278 → 298):**

- **AC-001 — `## Plan Mode Safe Operations` extended.** Added 5 plan-mode/skill-interaction rules under a new `**Plan-mode + skill interaction:**` sub-block:
  1. Skill files are executable instructions, not reference. Follow step-by-step from first phase.
  2. The first AskUserQuestion the skill emits is the workflow entering plan mode, not a violation of plan mode.
  3. AskUserQuestion satisfies plan mode's end-of-turn requirement.
  4. At a STOP point, stop immediately. Do NOT continue the workflow or call ExitPlanMode there.
  5. Call ExitPlanMode only after the skill workflow completes, or if the user tells you to cancel.
  
  Generic plan-mode + Claude Code skill rules — not gstack-infra. Without these, an agent in plan mode might prematurely call ExitPlanMode at the first AskUserQuestion (which would be the bug).

- **AC-002 — `## Important Rules` capstone block at end of file.** Six load-bearing rules consolidated for at-a-glance reference: Never abort, Two gates, Log every decision, Full depth means full depth, Artifacts are deliverables, Sequential order. 4 of 6 also exist in invariants (this is the capstone restating); 2 (Log every decision, Artifacts are deliverables) were previously only implicit.

## Excluded from scope (with rationale)

- All gstack infra (Preamble, Telemetry, Artifacts Sync, Question Tuning, Continuous Checkpoint Mode, Filesystem Boundary — Codex Prompts, Model-Specific Behavioral Patch, `~/.gstack/`). Stripped by design per `doc/harness/SPEC.md`.
- AskUserQuestion `D<N>/ELI10/✅❌/Net line` format. Deliberate divergence — harness uses `[Re-ground]/[Simplify]/[Recommend]/[Options]`.
- Writing Style + curated jargon glossary (~80 lines). CONTRACTS C-13 weight breach.

## Impact

`Skill(harness:plan)` now documents the full plan-mode + skill-interaction protocol and consolidates load-bearing invariants in a single capstone. Anyone running plan-skill in Claude Code's plan mode has explicit rules for ExitPlanMode timing and STOP-point behavior. The capstone gives both human reviewers and agents a one-screen reference for the skill's six non-negotiable rules.

## Final cross-skill alignment matrix

After this task closes, the gstack-vs-harness alignment series is complete:

| Surface | Status |
|---------|:-:|
| plan/SKILL.md (orchestrator) | ✓ |
| plan/intake.md | ✓ |
| plan/review-phases.md | ✓ |
| plan/decision-principles.md | ✓ |
| plan/write-artifacts.md | ✓ |
| plan-ceo-review/SKILL.md | ✓ |
| plan-design-review/SKILL.md | ✓ |
| plan-eng-review/SKILL.md | ✓ |
| plan-devex-review/SKILL.md | ✓ |

Remaining gstack content gaps in harness plan/ are **deliberate divergences** (AskUserQuestion format) or **explicit exclusions** (gstack-infra, Writing Style glossary per C-13).

## The gstack alignment series — full timeline

1. **2026-05-07:** plan-ceo-review voice alignment (`TASK__align-ceo-voice-with-gstack`).
2. **2026-05-08:** plan-design-review alignment (`TASK__align-design-review-with-gstack`).
3. **2026-05-08:** plan-eng-review + plan-devex-review 4-gap pattern (`TASK__align-eng-and-devex-review-with-gstack`).
4. **2026-05-08:** plan-ceo-review STOP-wording carryover, 13 sites (`TASK__tighten-plan-ceo-review-stop-wording-carryover`).
5. **2026-05-08:** plan/ orchestrator parent — Voice, Confusion Protocol, Context Health, Operational Self-Improvement, Anti-shortcut (`TASK__align-plan-orchestrator-with-gstack-autoplan`).
6. **2026-05-08 (this task):** plan/SKILL.md final 2 gaps — Plan Mode Safe Operations expansion + Important Rules capstone.

Six tasks, ~190 lines added across 6 files (5 in plugin/skills/plan/ subtree + 1 helper file), all light-mode doc-only edits, zero code surface, zero test surface, zero runtime risk.

## References

- PLAN.md: `doc/harness/tasks/TASK__plan-orchestrator-final-gaps/PLAN.md`
- HANDOFF.md: `doc/harness/tasks/TASK__plan-orchestrator-final-gaps/HANDOFF.md`
- Prior tasks in this series: `doc/changes/2026-05-07-plan-ceo-review-gstack-voice-alignment.md`, `doc/changes/2026-05-08-plan-design-review-gstack-alignment.md`, `doc/changes/2026-05-08-eng-and-devex-review-gstack-alignment.md`, `doc/changes/2026-05-08-plan-ceo-review-stop-wording-carryover.md`, `doc/changes/2026-05-08-plan-orchestrator-gstack-alignment.md`
- gstack source: `https://github.com/garrytan/gstack` (autoplan/SKILL.md, 1713 lines)
