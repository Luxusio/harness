# plan skill: autoplan parity gaps (G1-G6)
date: 2026-04-12
task: TASK__plan-autoplan-parity-gaps
file: plugin/skills/plan/SKILL.md

## What changed

Six additive insertions to the plan skill, closing workflow gaps identified against gstack autoplan. No existing content was modified. All insertions are backward-compatible.

| ID | Name | Location |
|----|------|----------|
| G1 | TODOS.md auto-update | Phase 3 mandatory outputs + Deferred Scope Surface |
| G2 | Plan Status Footer / Review Status table | Phase 6.8a (new sub-step) |
| G3 | AskUserQuestion Format section | Near skill top, before sub-skill execution protocol |
| G4 | Phase 0.0 Session Recovery | Before Phase 0 (new phase) |
| G5 | Design doc integration | Phase 0.4.5, after office-hours completes |
| G6 | Phase 6.9 Completion Report | After Phase 6.8 session close (new sub-step) |

## Why

The plan skill lacked:
- a standard question format (users lost context after long analysis blocks)
- session resume capability (interrupted sessions had no recovery path)
- deferred scope surfacing to TODOS.md (items were collected but never propagated)
- a structured completion signal (no machine-readable exit status)
- review provenance in PLAN.md (no record of which review phases ran and their outcomes)
- explicit design doc pickup after office-hours (path was implicit and unlogged)

## Key decisions

- Codex / cross-model sections from the autoplan source were excluded — not applicable to the harness context.
- G4 (Session Recovery) reads AUDIT_TRAIL.md phase-summary rows as the source of truth; it is informational scaffolding, not a hard gate. Unreadable AUDIT_TRAIL falls back to Phase 0 normally.
- G1 (TODOS.md) appends only if TODOS.md already exists at project root; it never creates the file.
- G6 status codes: DONE, DONE_WITH_CONCERNS (degraded voice, unresolved spec issues, or open user challenges), BLOCKED (only on CLI write failure).
- G2 Review Status table is appended to PLAN.md as a trailing section via re-run of the CLI write — preserves existing write_artifact.py plan invocation pattern.

## Caveats

- Phase 0.0 adds a new phase number; any hardcoded phase-number references in external documentation should be updated.
- G5 design doc search uses file timestamps relative to TASK_STATE.yaml, which may miss design files created before the task opened.
- write_artifact.py plan call count remains 17 (unchanged); regression check verifies this.
