# REQ process plan-skill-review-pipeline
tags: [req, process, plan-skill, review-pipeline]
summary: plan skill must conservatively select a compact low-risk procedure or the full dual-voice review pipeline; both publish canonical PLAN.md.
freshness: current
updated: 2026-08-12
verified_at: 2026-08-12T00:00:00Z

## Requirement

`plugin/skills/plan/SKILL.md` implements two planning procedures inside the
existing task lifecycle. Low-risk, bounded, unambiguous standard tasks may use
a compact assessment. Everything else uses the full 7-phase review pipeline.
Both publish canonical PLAN.md; neither changes TASK.json execution modes or
develop-time verification gates. The old 9-step linear procedure and its
separate acceptance ledger are retired and must not be restored.

## Procedure selection

Compact selection is automatic only when all relevant acceptance, path-scope,
test, and durable-doc choices are evident and blast radius is low. Explicit
full-plan requests win. Missing/uncertain inputs and security/auth/permissions/
secrets, data/schema/migrations, public API or observable UI behavior,
destructive operations, dependencies/platform/configuration/workflow-control,
material user choices, cross-component scope, and high-risk maintenance all
force the full procedure. File count alone is not an eligibility rule.

## Phase structure

| Phase | Name | Condition |
|-------|------|-----------|
| 0 | Intake + Context + procedure selection | always |
| compact | Bounded code/context assessment | low-risk standard tasks only |
| 1 | CEO Review | full procedure (mandatory premise AskUserQuestion) |
| 2 | Design Review | full procedure and ui_scope: true |
| 3 | Engineering Review | full procedure |
| 4 | DX Review | full procedure and dx_scope: true |
| 5 | Procedure-aware user gate | full asks final approval; compact asks only genuine User Challenges and otherwise proceeds directly to publication |
| 6 | Write PLAN.md + declared lenses in TASK.json | always |

## Invariants

- **Dual Voice Protocol**: every full-procedure review phase spawns Voice A (Claude subagent) and Voice B (Codex exec or second independent Agent). Both must complete before consensus is built. Single-voice review is prohibited.
- **Decision Classification**: every contested item is classified as Mechanical (auto-decide silently), Taste (auto-decide + surface at Phase 5.2), or User Challenge (never auto-decide; present at Phase 5.3 with full framing).
- **User Challenge gate**: both voices must independently agree that the user's direction should change for an item to become a User Challenge. Each User Challenge gets its own AskUserQuestion — never batched.
- **Premise gate**: Phase 1.1 is mandatory in the full procedure. Compact planning asks only when it finds a genuine User Challenge.
- **Canonical compact output**: compact planning still writes stable ACs, in/out scope, allowed/test/forbidden paths, verification, and a Durable Docs Decision into PLAN.md.
- **Unchanged runtime gates**: both procedures retain independent code review, conditional security review, ordered QA, receipts, close fingerprint, Goal continuation, and verified installation.
- **6 Decision Principles**: applied to every contested item; first applicable principle wins. Conflict resolution priority varies by phase (CEO: P1+P2, Eng: P5+P3, Design: P5+P1).

## Harness integration constraints

- No gstack binaries or telemetry (no `~/.gstack/` writes).
- `codex exec` is optional; fall back to second Agent if unavailable.
- `PLAN.md` is the mandatory acceptance-intent output.
- `write_plan` publishes the canonical `required_lenses` set into the exact
  four-field `TASK.json`; it creates no planning metadata or audit sidecar.
- `PLAN_SESSION.json` is optional recovery scratch. Normal same-session planning
  does not create it; successful plan publication removes it when present.
- Verification evidence comes from lifecycle-owned `RECEIPTS.jsonl`.

## Why the old workflow was replaced

The linear procedure had no adversarial review, no premise validation, and no structured decision audit trail. It produced plans that reflected a single voice and left taste/direction disagreements implicit. The review pipeline forces surface-level consensus before the plan is written, which reduces mid-implementation surprises and provides an audit trail for every auto-decision.

## Source task

`TASK__plan-autoplan-workflow` (2026-04-10)
