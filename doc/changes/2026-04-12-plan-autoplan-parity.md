# plan/SKILL.md second autoplan-parity pass
date: 2026-04-12
task: TASK__plan-autoplan-parity

## Summary

A follow-on content pass on `plugin/skills/plan/SKILL.md` (936 → 1148 lines, +22%)
to close remaining gaps versus the gstack `/autoplan` methodology that were not
addressed in the earlier sub-task (`TASK__plan-workflow-sub-f-autoplan-parity`).
All changes are prompt-only — no script, schema, or template files were modified.

## What changed

| Area | Detail |
|---|---|
| Sub-skill execution protocol | Skip list and compression brake added; sub-skills that produce thin output are retried before proceeding |
| "Read actual code" invariant | Explicit rule: plan skill must read referenced source files, not infer from names |
| "Never abort" invariant | Pipeline must complete all phases even under token pressure; compression is allowed, omission is not |
| CEO checklist (0A-0F) | Phase 1 now requires all six premise-challenge sub-steps before Voice A/B synthesis |
| Eng mandatory outputs | ASCII dependency graph and test diagram are required; "Never compress Section 3" rule added |
| DX mandatory outputs | Developer journey map (9 stages) and TTHW (Time-to-Hello-World) assessment required |
| Phase 5 approval gate | Expanded to options A-E: approve, conditional approve, revise-rerun, partial reject, full reject |
| deferred-scope.md | Deferred items now written to a dedicated file; replaces ad-hoc TODOS.md references |
| Incremental decision audit | Audit rows written per decision as it is made, not in a batch at phase end |
| UI/DX scope keywords | Expanded keyword list with 2+ match threshold and false-positive exclusion rules |
| Per-phase review log | Each phase appends a JSON summary row to the review log on completion |
| Restore point re-run block | `## Re-run Instructions` block updated to cover new gate options and deferred-scope path |
| Office-hours invocation | Phase 0 now documents the inline path for invoking office-hours when REQUEST.md is thin |
| Execution mode table | Updated to reflect new features (deferred-scope, A-E gate, compression brake) |

## Key decisions

- **Cross-model voice excluded.** Voice B remains on the Agent tool. No `codex exec`,
  `gemini`, or `omc ask` calls are added. Deferred to a future task.
- **deferred-scope.md instead of TODOS.md.** A named artifact with a stable path is
  easier to reference from HANDOFF.md and critic playbooks than an ad-hoc TODOS file.

## Verification

All 15 acceptance criteria passed structural grep against the updated SKILL.md:
- Sub-skill skip list present
- Compression brake rule present
- "read actual code" phrase present
- "never abort" phrase present
- CEO sub-steps 0A through 0F all present
- ASCII graph requirement present
- "Never compress Section 3" present
- Journey map (9 stages) present
- TTHW present
- Gate options A through E present
- deferred-scope.md reference present
- Incremental audit instruction present
- Per-phase JSON summary row present
- Restore point re-run block updated
- Office-hours inline path present

## Caveats

- File grew 22% (936 → 1148 lines). Future passes should consider extracting
  reference tables to a companion reference file to keep the main SKILL.md navigable.
- Prompt-only change — no runtime test is possible. Behavior is verified by structural
  grep only; actual agent output quality requires a live plan run.
