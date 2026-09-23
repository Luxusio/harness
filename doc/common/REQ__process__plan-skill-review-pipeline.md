# REQ process plan-skill-review-pipeline
tags: [req, process, plan-skill, review-pipeline]
summary: plan skill must conservatively select a compact low-risk procedure or the full single-independent-reviewer pipeline; both publish canonical PLAN.md.
freshness: current
updated: 2026-09-23
verified_at: 2026-09-23T00:00:00Z

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
| 1 | CEO Review | full procedure (premise extraction and source-classification mandatory; AskUserQuestion only for unresolved material premises, asked together with User Challenges at Phase 5.3) |
| 2 | Design Review | full procedure and ui_scope: true |
| 3 | Engineering Review | full procedure |
| 4 | DX Review | full procedure and dx_scope: true |
| 5 | Procedure-aware user gate | both procedures run one consolidated decision interaction (§5.3) only when unresolved material premises or User Challenges remain, otherwise proceed directly to publication; explicit pre-code final approval (§5.4.1) runs only if the user asked for it |
| 6 | Write PLAN.md + declared lenses in TASK.json | always |

## Invariants

- **Single independent reviewer**: every full-procedure review phase spawns
  exactly one independent reviewer subagent (Claude: `Agent`; Codex:
  `spawn_agent`). There is no second voice and no cross-model transport.
  Single-reviewer review is the only supported mode — it is not a degraded
  fallback of a dual-voice protocol.
- **Reviewer-failure fallback**: if the reviewer subagent fails or times out,
  the phase runs `coordinator-only` with a recorded reason and the workflow
  continues without a separate user interaction. Any coordinator-only phase
  sets the completion status to DONE_WITH_CONCERNS and the report VERDICT to
  `REVIEWED_DEGRADED — <phases> ran coordinator-only`.
- **Decision Classification**: every finding is classified by the coordinator
  as Mechanical (auto-decide silently), Taste (auto-decide, then recorded in
  PLAN.md's Decision Audit Trail and never rendered at the user-facing gate),
  or User Challenge (never auto-decide; present at Phase 5.3 with full
  framing). Reviewer agreement is not required for a User Challenge — the
  coordinator classifies each finding using the existing decision principles.
  If the coordinator's classification and the reviewer's stated concern level
  disagree, the item escalates to the higher tier rather than being decided
  silently.
- **One consolidated decision interaction**: User Challenges surfaced across a
  phase are gathered into one consolidated decision interaction per Phase 5.3,
  not one `AskUserQuestion` per challenge.
- **Premise gate**: Phase 1.1 is mandatory in the full procedure. Compact
  planning asks only when it finds a genuine User Challenge.
- **Canonical compact output**: compact planning still writes stable ACs, in/out
  scope, allowed/test/forbidden paths, verification, and a Durable Docs
  Decision into PLAN.md.
- **Unchanged runtime gates**: both procedures retain independent code review,
  conditional security review, ordered QA, receipts, close fingerprint, Goal
  continuation, and verified installation.
- **6 Decision Principles**: applied to every contested item; first applicable
  principle wins. Conflict resolution priority varies by phase per
  `plugin/skills/plan/decision-principles.md`: CEO P6+P3, Design P5+P6, Eng
  P5+P3, DX P5+P3.

## Harness integration constraints

- No gstack binaries or telemetry (no `~/.gstack/` writes).
- `PLAN.md` is the mandatory acceptance-intent output.
- `write_plan` publishes the canonical `required_lenses` set into the exact
  four-field `TASK.json`; it creates no planning metadata or audit sidecar.
- `PLAN_SESSION.json` is optional recovery scratch. Normal same-session planning
  does not create it; successful plan publication removes it when present.
  Malformed legacy scratch is treated as equivalent to absent scratch, and any
  legacy `cross_model_voice` key it carries is ignored.
- Verification evidence comes from lifecycle-owned `RECEIPTS.jsonl`.

## Why the old workflow was replaced

The linear procedure had no adversarial review, no premise validation, and no
structured decision audit trail. It produced plans that reflected a single
voice and left taste/direction disagreements implicit. The review pipeline
forces an independent pre-code check before the plan is written, which reduces
mid-implementation surprises and provides an audit trail for every
auto-decision.

## Why dual-voice was removed (2026-09-23)

The review pipeline originally spawned two voices per phase (Voice A + Voice
B, optionally cross-model via codex exec or a second Agent) and required
consensus before a finding was auto-decided. Evidence from five historical
full reviews with numeric Review Status tables recorded disagreement on 1 of
31 dimensions and 8 disagreements across all phases, one of which became a
User Challenge; Phase 1 Voice B received an empty prior-findings block, so on
same-model transport it reran the identical prompt. That evidence, combined
with the user's explicit direction to remove all dual-voice machinery,
motivated collapsing to exactly one independent reviewer subagent per phase.
Single-reviewer review keeps the only independent pre-code check while
removing the cross-model transport, consensus table, and dual-voice
degradation matrix. Develop-time review-code, QA lenses, receipts,
`task_verify`, and `task_close` are unchanged.

## Source task

`TASK__plan-autoplan-workflow` (2026-04-10) — original review pipeline.
`TASK__plan-single-voice-review` (2026-09-23) — collapse to single independent
reviewer and remove the Claude Stop hook.
