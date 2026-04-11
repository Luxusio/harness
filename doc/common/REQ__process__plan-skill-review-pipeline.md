# REQ process plan-skill-review-pipeline
tags: [req, process, plan-skill, review-pipeline]
summary: plan skill must run the 7-phase dual-voice review pipeline; old linear procedure is retired.
freshness: current
updated: 2026-04-11
verified_at: 2026-04-11T00:00:00Z

## Requirement

`plugin/skills/plan/SKILL.md` implements a **7-phase review pipeline**. The old 9-step linear procedure (compile routing → read → clarify → write PLAN.md → write CHECKS.yaml → close) is retired and must not be restored.

## Phase structure

| Phase | Name | Condition |
|-------|------|-----------|
| 0 | Intake + Context | always |
| 1 | CEO Review | always (mandatory premise AskUserQuestion) |
| 2 | Design Review | ui_scope: true only |
| 3 | Engineering Review | always |
| 4 | DX Review | dx_scope: true only |
| 4.5 | Outside Voice — Final Plan Challenge | always (skipped in light) |
| 5 | Final Approval Gate | always |
| 5.5 | Spec Review Loop | always (skipped in light) |
| 6 | Write PLAN.md + CHECKS.yaml + PLAN.meta.json | always |

## Invariants

- **Dual Voice Protocol**: every review phase spawns Voice A (Claude subagent) and Voice B (Codex exec or second independent Agent). Both must complete before consensus is built. Single-voice review is prohibited.
- **Decision Classification**: every contested item is classified as Mechanical (auto-decide silently), Taste (auto-decide + surface at Phase 5.2), or User Challenge (never auto-decide; present at Phase 5.3 with full framing).
- **User Challenge gate**: both voices must independently agree that the user's direction should change for an item to become a User Challenge. Each User Challenge gets its own AskUserQuestion — never batched.
- **Premise gate**: Phase 1.1 is the one mandatory user interaction before Phase 5. Premises are never auto-decided.
- **6 Decision Principles**: applied to every contested item; first applicable principle wins. Conflict resolution priority varies by phase (CEO: P1+P2, Eng: P5+P3, Design: P5+P1).
- **Adversarial review layers**: Phase 4.5 dispatches a whole-plan fresh-context reviewer between Phase 4 and Phase 5. Phase 5.5 runs a closed adversarial review loop (max 3 rounds, 5 dimensions) after Phase 5 user approval clears, before Phase 6 writes PLAN.md. Both layers fail non-blockingly; findings are informational unless the user accepts them.

## Harness integration constraints

- No gstack binaries or telemetry (no `~/.gstack/` writes).
- `codex exec` is optional; fall back to second Agent if unavailable.
- `PLAN_SESSION.json` open/write/close lifecycle is required (not optional).
- `plan_session_state` transitions in `TASK_STATE.yaml`: `context_open` → `write_open` → `closed`.
- `PLAN.meta.json` with `author_role: plan-skill` is a required output.
- `PLAN.md` and `CHECKS.yaml` are mandatory final outputs.

## Why the old workflow was replaced

The linear procedure had no adversarial review, no premise validation, and no structured decision audit trail. It produced plans that reflected a single voice and left taste/direction disagreements implicit. The review pipeline forces surface-level consensus before the plan is written, which reduces mid-implementation surprises and provides an audit trail for every auto-decision.

## Source task

`TASK__plan-autoplan-workflow` (2026-04-10)
