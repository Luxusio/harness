# 2026-05-08 — plan/ orchestrator gstack autoplan alignment

**Task:** TASK__align-plan-orchestrator-with-gstack-autoplan

Five orchestrator-level content backfills in `plugin/skills/plan/` (the parent plan-skill that runs the dual-voice review pipeline), aligning with `gstack autoplan/SKILL.md`. Closes the parent-skill gap left after the 4 sub-skill alignment tasks (2026-05-07, 2026-05-08).

## What changed

**plan/SKILL.md (+30 lines, 248 → 278):**

- **AC-001 — Voice block expanded.** Replaced 1-line founder paragraph with full plan-orchestrator voice rules. Lead-with-the-point, concrete file/line/AC-id refs, AI-vocab blocklist (`delve`, `crucial`, etc.), no-em-dashes rule, builder-to-builder framing, paired Good/Bad example using a plan-orchestration scenario.
- **AC-002 — `## Confusion Protocol` (new section).** STOP rule for high-stakes orchestrator-level ambiguity (mode selection, scope detection edge cases, conflicting Voice A/B output, premise-gate response interpretation, mid-pipeline scope expansion). Cross-references `decision-principles.md` § AskUserQuestion Format.
- **AC-003 — `## Context Health` (new section).** Soft directive — `[PROGRESS]` checkpoint summaries at phase boundaries when phases run >5 min; loop detection (3 occurrences without convergence → STOP and reassess via premise re-confirm / fresh Voice C / user check-in). Never mutates git state.
- **AC-005 — `## Anti-shortcut clause` (new section).** Names PLAN.md as the OUTPUT of the interactive review, not a substitute for AskUserQuestion at premise gate / User Challenges / final approval. References the May 2026 transcript bug as the failure mode this rule prevents.

**plan/write-artifacts.md (+6 lines, 193 → 199):**

- **AC-004 — §6.8 Operational Self-Improvement extended.** Added a concrete plan-time tooling friction example list to §6.8 (which already covered operational learnings at a generic level): `write_plan_artifact.py --input` flag (not `--content-file`); `PLAN_SESSION.json` `phase: write` (not `"6"`); `update_checks.py` mutates only existing ACs; `HARNESS_SKIP_PREWRITE=1` is one-shot. Mid-implementation EUREKA: original PLAN.md AC-004 premise was wrong — §6.8 already had `type=operational` and "5+ minutes" rubric. Re-scoped in-flight to add concrete examples instead of restructuring.

## Excluded from scope (with rationale)

- **All gstack infra.** Preamble bash, Telemetry, Artifacts Sync (gbrain, vendoring), `~/.gstack/projects/`, Question Tuning, Continuous Checkpoint Mode (deliberate divergence — `## Plan Mode Safe Operations` already prohibits git commits during plan mode), Filesystem Boundary — Codex Prompts (Codex CLI specific), Model-Specific Behavioral Patch, `/sync-gbrain`, designer binary, comparison-board HTTP server. Stripped by design per `doc/harness/SPEC.md`.
- **AskUserQuestion `D<N>/ELI10/✅❌/Net line` format.** Deliberate divergence — harness uses `[Re-ground]/[Simplify]/[Recommend]/[Options]` (decision-principles.md § AskUserQuestion Format).
- **Writing Style + curated jargon glossary (~80 lines).** Weight Budget breach (CONTRACTS C-13).
- **Step 0: Detect platform and base branch in gstack form.** Already covered in harness `intake.md` Phase 0.4.2.
- **Sub-skill content alignment.** Done in prior tasks (2026-05-07, 2026-05-08 series).

## Impact

Anyone running `Skill(harness:plan)` now sees orchestrator-level voice rules, an explicit anti-shortcut clause matching the 4 sub-skills, a Confusion Protocol that fires on parent-skill ambiguity (not just sub-skill in-phase ambiguity), and a Context Health soft directive that surfaces progress checkpoints + catches loop conditions before they consume context silently. The §6.8 plan-tooling friction examples codify lessons learned across the 5-task gstack alignment series — future plan tasks save 5+ minutes by avoiding these specific gotchas.

## Cross-skill alignment matrix (post-task)

| Section | gstack autoplan | harness plan/ |
|---------|:-:|:-:|
| Voice | full | full ✓ (was thin paragraph) |
| Anti-shortcut clause | implicit (Important Rules) | explicit ✓ |
| Confusion Protocol | yes | yes ✓ |
| Context Health (soft directive) | yes | yes ✓ |
| Operational Self-Improvement | yes | yes ✓ (with concrete plan-tooling examples) |
| 6 Decision Principles | yes | yes (decision-principles.md) |
| Repo Ownership / Search Before Building / Completion Status | yes | yes (decision-principles.md) |
| Premise gate / User Challenge / Final Approval | yes | yes (already present) |
| AskUserQuestion Format | gstack-specific | harness-native (deliberate divergence) |
| gstack infra | present | stripped by design |

## Closing the gstack alignment series

This task closes the multi-task gstack-vs-harness alignment series:
- 2026-05-07: plan-ceo-review voice alignment.
- 2026-05-08: plan-design-review alignment.
- 2026-05-08: plan-eng-review + plan-devex-review 4-gap pattern.
- 2026-05-08: plan-ceo-review STOP-wording carryover (13 sites).
- 2026-05-08 (this task): plan/ orchestrator parent — Voice, Confusion Protocol, Context Health, Operational Self-Improvement plan-tooling examples, Anti-shortcut clause.

Five sub-skill / orchestrator surfaces fully aligned. Remaining gstack content gaps in harness plan/ are deliberate divergences (AskUserQuestion format, gstack infra) or weight-budget exclusions (Writing Style + jargon glossary).

## References

- PLAN.md: `doc/harness/tasks/TASK__align-plan-orchestrator-with-gstack-autoplan/PLAN.md`
- HANDOFF.md: `doc/harness/tasks/TASK__align-plan-orchestrator-with-gstack-autoplan/HANDOFF.md`
- gstack source: `https://github.com/garrytan/gstack` (autoplan/SKILL.md, 1713 lines)
- Prior tasks in this series: `doc/changes/2026-05-07-plan-ceo-review-gstack-voice-alignment.md`, `doc/changes/2026-05-08-plan-design-review-gstack-alignment.md`, `doc/changes/2026-05-08-eng-and-devex-review-gstack-alignment.md`, `doc/changes/2026-05-08-plan-ceo-review-stop-wording-carryover.md`
