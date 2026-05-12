# 2026-05-12 — plan-skill Phase 5 gate: outcome-only enforcement

**Task:** TASK__fix-plan-skill-phase5-gate-outcome-only

The plan-skill final approval gate (Phase 5 §5.4.1) is now formally exempt from the 4-rule body (`[Re-ground]/[Simplify]/[Recommend]/[Options]`). It uses the §5.1 outcome-only template: "What this plan will do" + "Out of scope" + binary Approve/Reject. Internal review state (decision counts, taste tallies, voice consensus, cross-phase themes) stays in `AUDIT_TRAIL.md` / PLAN.md and is never rendered at the gate.

## What changed

**plugin/skills/plan/decision-principles.md (185 lines):**

- §AskUserQuestion Format "Applies to" line dropped `gate options (5.4.1)`. Now applies to: premise gate (1.1), prerequisite offer (0.4.5), User Challenge (5.3).
- Immediately below, a 3-line `**Exception — Phase 5 final approval gate (§5.4.1):**` block documents the reason — the gate intentionally hides internal review state and applying the 4-rule body would re-leak it.

**plugin/skills/plan/SKILL.md (296 lines, §5.1):**

- Appended one `**Hard guard.**` paragraph immediately after the §5.1 closing line. Names the leaks to refuse (4-rule body, decision counts, taste tallies, voice consensus, cross-phase summaries) and cites the decision-principles.md exception by file for future readers.

## Impact

Premise gate (§1.1) and User Challenge gate (§5.3) keep the 4-rule body — those slots need user context grounding. Only §5.4.1 changed. Aligns the runtime gate with the long-standing user feedback memory (`feedback_plan_gate_format.md`) and the SKILL.md §5.1 design intent that pre-dated this fix.

## References

- PLAN.md: `doc/harness/tasks/TASK__fix-plan-skill-phase5-gate-outcome-only/PLAN.md`
- HANDOFF.md: `doc/harness/tasks/TASK__fix-plan-skill-phase5-gate-outcome-only/HANDOFF.md`
- DOC_SYNC.md: `doc/harness/tasks/TASK__fix-plan-skill-phase5-gate-outcome-only/DOC_SYNC.md`
- User feedback memory: `feedback_plan_gate_format.md`
