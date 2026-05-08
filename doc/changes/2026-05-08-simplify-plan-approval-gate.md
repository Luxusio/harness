# 2026-05-08 — plan-skill Phase 5 gate simplified to outcome-focused

**Task:** TASK__simplify-plan-approval-gate-output

User-feedback-driven simplification of `plugin/skills/plan/SKILL.md` Phase 5 final approval gate. The user reaction to the previous verbose gate output: "뭐 그래서 뭘 하겠다는건지 뭔소리인지 모르겠어" — the actual work direction was buried under internal review-process detail.

## What changed

**plugin/skills/plan/SKILL.md (-4 net, 298 → 294, with substantial restructure inside):**

- **AC-001 — §5.1 reshaped.** "Rich plan review summary" → "Plan approval summary (user-facing)". Six sub-headings (Decisions Made counter / Per-Phase Voice consensus scores / Cross-Phase Themes / Deferred Items / Deferred to TODOS.md / Plan Summary) collapsed to two: **What this plan will do** (2-3 plain-outcome sentences) and **Out of scope** (deferrals from PLAN.md). The user only needs work direction, not internal classification tallies, at the approval gate.

- **AC-002 — §5.2 + §5.2.5 marked AUDIT_TRAIL-only.** Processing logic retained (cognitive load rules, cross-phase theme detection still run); rendering at gate removed. Section headings renamed:
  - §5.2: "Surface Taste decisions (informational only)" → "Log Taste decisions to AUDIT_TRAIL (no gate render)".
  - §5.2.5: "Cross-Phase Themes" → "Log Cross-Phase Themes to AUDIT_TRAIL (no gate render)".

- **AC-003 — §5.4.1 simplified to binary.** 4-option gate (Approve as-is / Approve with overrides / Revise — re-run a phase / Reject) → 2-option (Approve / Reject) with AskUserQuestion's automatic Other slot treated as Modify. Question text simplified to verbatim "Approve plan?". Handler restructured: 5 sub-cases (Approve as-is / overrides / Other-interrogate / Revise / Reject) → 3 sub-cases (Approve / Reject / Other → Modify with 3 internal sub-cases for question / scope-override / phase-rerun).

## Excluded from scope (with rationale)

- Other plan/ sub-files (intake.md, review-phases.md, decision-principles.md, write-artifacts.md). Single-file feedback scope.
- §5.0 / §5.1.1 / §5.3 / §5.4. Not in user feedback — pre-gate checks, decision collection, user-challenge gate (already user-facing AskUserQuestion-per-challenge), and scope confirmation are unchanged.
- AUDIT_TRAIL.md write logic in §5.2 / §5.2.5. Preserved — only rendering changed.
- write-artifacts.md §6.10 Completion report. Different audience (post-task summary), out of feedback scope.

## Impact

Anyone running `Skill(harness:plan)` now sees this at the approval gate:

```
## Plan Approval

### What this plan will do
[2-3 sentences in plain outcome language]

### Out of scope
[Bulleted list of deferrals]

Approve plan?
1. Approve — proceed to develop (Recommended)
2. Reject — discard and reset to Phase 0
   (Other → free-text Modify: any change request, question, or phase re-run)
```

Internal review state (taste decisions, voice consensus tallies, cross-phase themes, decisions-made counter) is logged to AUDIT_TRAIL.md exactly as before — auditability and post-task review remain intact. The change is purely about what the user sees at the approval moment.

## Memory

User feedback saved to `feedback_plan_gate_format.md` so future plan-skill edits keep the gate outcome-focused and don't re-introduce process-detail rendering.

## Phase 5 sub-section structure post-task

| § | Title | Visibility |
|---|-------|-----------|
| 5.0 | Pre-Gate verification (max 2 retries) | internal |
| 5.1 | Plan approval summary (user-facing) | user-facing — reshaped |
| 5.1.1 | Collect all decisions | internal |
| 5.2 | Log Taste decisions to AUDIT_TRAIL (no gate render) | AUDIT_TRAIL only |
| 5.2.5 | Log Cross-Phase Themes to AUDIT_TRAIL (no gate render) | AUDIT_TRAIL only |
| 5.3 | User Challenge gate | user-facing (per-challenge AskUserQuestion) |
| 5.4 | Final scope confirmation | conditional |
| 5.4.1 | Gate response options | user-facing — binary |

## References

- PLAN.md: `doc/harness/tasks/TASK__simplify-plan-approval-gate-output/PLAN.md`
- HANDOFF.md: `doc/harness/tasks/TASK__simplify-plan-approval-gate-output/HANDOFF.md`
- Memory: `~/.claude/projects/-project-harness-e14968053086/memory/feedback_plan_gate_format.md`
