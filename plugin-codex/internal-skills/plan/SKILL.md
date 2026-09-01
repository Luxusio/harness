---
name: plan
description: Harness planning pipeline with a compact low-risk branch and a full reviewed branch. Both publish canonical PLAN.md and task lens declarations via MCP.
user-invocable: false
---

Codex planning pipeline with compact and full procedures. Conservatively eligible low-risk work uses a bounded assessment; all other work uses the 7-phase reviewed procedure. Both publish through the protected-artifact MCP.

> Current artifact model: acceptance criteria live in `PLAN.md`; `write_plan`
> publishes it with required lens declarations in `TASK.json`.

> **Codex runtime notes** (delta from Claude):
> - **Dual Voice is capability-routed.** Discover deferred tools before deciding. When `spawn_agent` or an external model route is available, run independent Voice A and Voice B contexts. Use one inline critical-reviewer pass only when no independent route exists, and record that fallback in PLAN.md's Review Status section.
> - **Sub-skills are inlined, not invoked.** Claude's `Skill("harness:plan-ceo-review", task_id)` chain has no Codex equivalent. The orchestrator reads each internal prompt's SKILL.md content inline and executes the methodology in the same conversation. Codex keeps these prompts under `${HARNESS_PLUGIN_ROOT}/internal-skills/` so they remain packaged without appearing in the user-visible skill menu.
> - **AskUserQuestion = conversational ask.** Full planning uses Phase 1.1 premise, Phase 5.3 User Challenge, and Phase 5.4.1 final approval gates. Compact planning asks only genuine Phase 5.3 User Challenges. Each ask is prose rather than a structured envelope.
> - **`${CLAUDE_PLUGIN_ROOT}` → `${HARNESS_PLUGIN_ROOT}`** for bash invocations that remain. Plan artifact writes use MCP `write_plan`.
> - **MCP tool names** bare (`task_start`, `task_context`, `write_plan`) — not Claude-prefixed form. Where the Claude source mentions a prefixed name, read it as bare.

## Sub-files

This skill is split across four sub-files (Claude tree until AC-005 ports them):

| File | Content |
|------|---------|
| `intake.md` | Phase 0 (spawned detection, recovery, task context, scope detection, planning-procedure branch) |
| `review-phases.md` | Phases 1-4 (review template + per-lens dimensions, checklists, degradation matrix) |
| `decision-principles.md` | 6 Decision Principles, classification, auto-decide rules, completion status, repo ownership, ask format |
| `write-artifacts.md` | Phase 6 (PLAN.md / TASK.json lens declarations + MCP writes, learnings, close) |

Phase 5 (procedure-aware user gate) stays inline below. Read sub-files from `${HARNESS_PLUGIN_ROOT}/internal-skills/plan/<file>`.

---

## Invariants (Codex variant)

- **Independent voices by capability.** Where the Claude source says "Voice A + Voice B via Agent", use separate subagent or external-model contexts when exposed. Otherwise run ONE inline critical-reviewer pass and note the degradation in PLAN.md `Review Status`; use the `single-voice` degradation row only for that fallback.
- **Compact plans stay canonical.** Low-risk planning still writes PLAN.md with stable ACs, path scope, tests, and a durable-doc decision. It never skips develop-time review, QA, receipts, close, or install verification.
- **Premise gate mandatory for full plans.** Phase 1.1 emits one conversational ask before Phase 5. Compact planning asks only when a genuine User Challenge exists.
- **Never-auto decisions.** User Challenge items get their own ask at Phase 5.3.
- **Write via MCP only.** PLAN.md and TASK.json required-lens declarations go through `write_plan`. Never Write/Edit them directly.
- **Workflow-lock awareness.** Trusts coordinator; no redundant check.
- **Read actual code.** Review phases MUST read source files, diffs, and referenced code. Reasoning from plan text alone is insufficient.
- **Never abort.** Full planning surfaces review failures and never silently shortens. Compact may escalate to full after inspection, but never bypasses its fail-closed assessment.
- **Auto-decide mode.** When active, resolves intermediate asks except premise gate and User Challenge items via the 6 Decision Principles.
- **Spawned session.** `spawned_session: true` or `HARNESS_SPAWNED=1` → force auto-decide, auto-resolve ALL asks (including premise gate), emit prose completion instead of waiting.
- **Sequential execution by procedure.** Compact runs 0 → bounded assessment → 5.0 → 5.3 only when challenged → 6. Full runs 0 → 1 → 2 → 3 → 4 → 5 → 6.

## Voice

Plan-orchestrator voice: opinionated, concrete, builder-to-builder.

- Lead with the point. Say what the phase did, what it found, what changes downstream.
- Be concrete. Name files, functions, line numbers, AC ids, premise indices, decision principles.
- Tie technical choices to user outcomes.
- Be direct about quality. Bugs in the plan matter more than bugs in the implementation.
- Sound like a builder talking to a builder, not a consultant.
- No em dashes. No AI vocabulary: `delve`, `crucial`, `robust`, `comprehensive`, `nuanced`, `multifaceted`, `furthermore`, `moreover`, `additionally`, `pivotal`, `landscape`, `tapestry`, `underscore`, `foster`, `showcase`, `intricate`, `vibrant`, `fundamental`, `significant`.
- Korean/English bilingual context: technical terms stay English, explanations may use Korean.
- The user has context you do not. Full planning uses premise, User Challenge, and final approval gates; compact asks only genuine User Challenges.

Good: "Phase 3 Eng. AC-004 verification command already passes pre-edit (grep hit at write-artifacts.md:140). EUREKA — re-scope AC-004 to a smaller addition. Surface before writing PLAN.md."
Bad: "I've completed the engineering review phase and identified some considerations regarding AC-004 that may warrant additional examination."

## Anti-shortcut clause

PLAN.md is the output of the selected procedure, not a substitute for it. Full planning passes premise, User Challenge, and final approval gates. Compact may omit premise/final approval only after bounded inspection and escalation recheck; every genuine User Challenge is still surfaced before publication.

## Confusion Protocol

For high-stakes orchestrator-level ambiguity — execution mode selection, scope detection edge cases, premise-gate response interpretation, mid-pipeline scope expansion — STOP. Name it in one sentence, present 2-3 options with concrete tradeoffs, and ask.

Auto-decide scope-partition choices (split / combine / defer / do-a-subset) via Principle P1 (Choose completeness).

## Context Health

- **`[PROGRESS]` summary at phase boundaries.** When a phase takes longer than ~5 minutes, surface a 1-2 sentence checkpoint: done, next, surprises.
- **Loop detection.** If the same finding fires 3 times without converging, STOP and reassess. Options: re-confirm premise via ask; pause for user check-in. Looping silently is worse than asking.
- Progress summaries and loop-detection notices NEVER mutate git state.

## Completeness — Boil the Lake

Complete every section before moving on. Use concrete content in each required section. If a section produces fewer than 3 sentences, expand. "No issues found" is valid only after stating what was examined.

---

## Sub-skill execution protocol (Codex variant)

Each review phase reads its corresponding Codex internal prompt and executes the methodology inline:

- Phase 1 → `${HARNESS_PLUGIN_ROOT}/internal-skills/plan-ceo-review/SKILL.md` (read + execute inline)
- Phase 2 → `${HARNESS_PLUGIN_ROOT}/internal-skills/plan-design-review/SKILL.md` (only if ui_scope=true)
- Phase 3 → `${HARNESS_PLUGIN_ROOT}/internal-skills/plan-eng-review/SKILL.md`
- Phase 4 → `${HARNESS_PLUGIN_ROOT}/internal-skills/plan-devex-review/SKILL.md` (only if dx_scope=true)

These sub-skills are heavy dual-voice review pipelines on the Claude side. On Codex, route them through independent contexts when available; otherwise record the fallback in PLAN.md Review Status.

---

## Optional PLAN_SESSION.json scratch

Keep ordinary same-session planning state in working context. Do not create
PLAN_SESSION.json by default. It may be used when planning is explicitly
resumable, delegated across contexts, or otherwise needs recovery across a
turn. It is never task control or artifact authority.

| State | Phase | Condition |
|-------|-------|-----------|
| `context_open` | 0-5 | Set at Phase 0 start |
| `write_open` | 6 | At Phase 6 start before MCP artifact writes |
| `closed` | post-6 | Transitional state immediately before scratch removal |

When used, it contains `{"state": "...", "phase": "...", "source": "plan-skill"}` and may include transport hints. Remove it after successful `write_plan`. Ignore stale or malformed legacy scratch and reconstruct from PLAN.md/task context; do not bulk-migrate historical tasks.

---

## Capability-Routed Voice Protocol (Codex variant)

Phases 1-4 first discover `spawn_agent` and external-model routes. When an
independent route exists, run Voice A and Voice B in separate contexts and
synthesize their results. Only when none exists, run ONE inline
critical-reviewer pass. Every pass:
- Reads the phase brief (lens dimensions from `review-phases.md`).
- Produces findings per dimension.
- Classifies each finding (Mechanical / Taste / User Challenge).
- Retains a consensus row for PLAN.md, marked `single-voice` only when the independent route was unavailable.

The fallback has less cross-blind-spot detection and no Voice A vs Voice B
disagreement surfacing, so record it explicitly. A Codex run with independent
subagents is not a degraded single-voice run merely because the runtime differs.

Full protocol, dimensions, checklists, and degradation matrix: `review-phases.md` (Claude tree). The Codex orchestrator uses the `single-voice` row only as the capability fallback.

---

## Phase orchestration

1. **Phase 0** — always runs and selects `compact` or `full` planning procedure; TASK.json remains `standard` or `micro` only.
2. **Compact branch** — conservatively classified low-risk standard work gets one bounded assessment, genuine User Challenges only, then canonical publication.
3. **Full branch Phase 1 — CEO Review** — premise gate at 1.1 is mandatory.
4. **Full branch Phase 2 — Design Review** — only if `ui_scope=true`.
5. **Full branch Phase 3 — Engineering Review** — runs with independent voices when available.
6. **Full branch Phase 4 — DX Review** — only if `dx_scope=true`.
7. **Phase 5 — Final Approval Gate** — compact plans may proceed without confirmation only when intent/scope are explicit and there are no User Challenges.
8. **Phase 6 — Write artefacts** — always writes through `write_plan`; then removes optional PLAN_SESSION.json scratch.

The compact assessment must inspect the named code/docs and recheck every
escalation family against discovered dependencies, callers, data flows,
configuration, and observable effects. Any discovered trigger, unknown,
cross-component impact, or unresolved material choice abandons compact and
restarts at full Phase 1 before Phase 5/6.

---

## Phase 5: Procedure-aware user gate

Branch once after §5.0:

- **compact:** retain decisions/themes for PLAN.md, run §5.3 only for actual
  User Challenges, then proceed directly to Phase 6. With zero challenges,
  proceed directly from §5.0 to Phase 6. Do not emit §5.1 or run §5.4.1.
- **full:** run §§5.1 through 5.4.1 as written.

### 5.0 Pre-Gate verification (max 2 retries)

For `full`, verify required outputs before collecting decisions:
- [ ] Phase 1: premise challenge user-confirmed; CEO consensus retained; phase-transition summary
- [ ] Phase 2 (if ran): Design consensus retained; phase-transition summary
- [ ] Phase 3: Engineering consensus retained; phase-transition summary
- [ ] Phase 4 (if ran): DX consensus retained; phase-transition summary
- [ ] PLAN.md Review Status has ≥ 1 row per completed phase
- [ ] Any single-voice fallback is logged with the concrete unavailable independent route for each affected phase.

If missing after 2 retries, proceed to 5.1 with warning block:
```
⚠ Pre-Gate Warning: proceeding with incomplete phase outputs.
Missing: <list>
```

For `compact`, require a named code/docs assessment, a post-inspection recheck
of every escalation family, no remaining unknown/material choice, and complete
canonical PLAN.md fields. Missing compact evidence escalates to full Phase 1;
it never publishes under an incomplete-review warning.

### 5.1 Plan approval summary (user-facing)

The user only needs two things at the gate: **what this plan will do** and **what is explicitly out of scope**. Internal review state is retained for PLAN.md, never rendered here.

Emit:
```
## Plan Approval

### What this plan will do
[2-3 sentences in plain outcome language. Concrete: which files change, which behavior changes, what the user has at the end. No process counters. No phase voice scores.]

### Out of scope
[Bulleted list pulled from PLAN.md "NOT in scope".]
```

That is the entire user-facing summary.

**Hard guard.** Never render at this gate: the 4-rule body, decision counts, taste tallies, or cross-phase summaries. Those belong in PLAN.md only.

### 5.1.1 Collect all decisions

From the available consensus rows across Phases 1-4: Mechanical (silently applied), Taste, User Challenge.

### 5.2 Retain Taste decisions for PLAN.md (no gate render)

Taste decisions go to PLAN.md's Decision Audit Trail only.

```
Auto-decided (Taste):
- [item]: chose [option] over [option] because [principle applied]
```

### 5.2.5 Retain Cross-Phase Themes for PLAN.md (no gate render)

Cross-phase theme detection writes to PLAN.md's `Cross-phase themes` section.

### 5.3 User Challenge gate

Cognitive load:
- **0 challenges:** compact goes directly to Phase 6; full goes to 5.4.
- **1-7 challenges:** ask one at a time, in order. Do not batch into one turn.
- **8+ challenges:** warning at top, group by phase.

Per-challenge ask format (conversational):

> **User Challenge: <item title>**
>
> Your stated direction: <from REQUEST.md or the current conversation>
> Reviewer recommends: <alternative>
> Reasoning: <why the reviewer disagrees>
> Blind spots: <what the reviewer may miss about your context>
> Downside cost of proceeding as stated: <concrete estimate>
>
> A) Accept the recommendation (Recommended) — switch to the alternative.
> B) Keep my original direction — known risk acknowledged.
> C) Modify — describe in your next reply.

Reply parsing: A/B follow the option; any free-text reply treats as Modify.

**Note on single-voice fallback User Challenges:** when no independent route was available, one reviewer is weaker evidence than two agreeing voices and may flag challenges that dual-voice disagreement would resolve. Err on the side of fewer challenges; if uncertain, classify as Taste.

### 5.4 Final scope confirmation

Full procedure only. If 5.3 responses changed scope, confirm updated scope
before Phase 6. Compact incorporates the answer without a second confirmation.

### 5.4.1 Gate response options

Full procedure only. Compact never runs this subsection.

Emit §5.1 summary, then ask:

> **Approve plan?**
>
> A) Approve — proceed to develop (Recommended). Move to Phase 6 artefact write.
> B) Reject — discard and reset to Phase 0.
>
> Any other reply is treated as Modify — describe what you want changed (taste-decision overrides, scope adjustments, "re-run Phase 3 with X premise", clarifying questions). The orchestrator interprets the free-text and either revises the plan or answers the question, then re-offers the gate.

**Handling:**
- **Approve:** proceed to Phase 6.
- **Reject:** clear all phase-level state and reset to Phase 0.
- **Modify:** parse the free-text. Three sub-cases:
  - *Pure question (no change request):* answer fully, re-present §5.1 summary, re-offer the gate.
  - *Scope override or taste-decision flip:* apply, re-present §5.1 summary with changes noted, re-offer the gate.
  - *Phase re-run request:* re-run affected phases with updated scope; increment cycle counter; after 3 cycles proceed to Phase 6 with a warning block at top of PLAN.md.

---

## Planning procedure branches

TASK.json execution_mode remains exactly `standard|micro`; never persist
`light`. Micro keeps its explicit no-plan contract. Standard planning selects:

| Procedure | Eligibility | Review shape | Mandatory output |
|-----------|-------------|--------------|------------------|
| `compact` | bounded, unambiguous, low blast radius, all scope/test decisions evident | one code/context assessment; ask only genuine User Challenges | canonical PLAN.md with stable ACs, in/out scope, allowed/test/forbidden paths, verification, durable-doc decision |
| `full` | explicit request, uncertain inputs, or any escalation trigger | capability-routed CEO/Engineering plus scoped Design/DX phases | full canonical PLAN.md |

Escalate to full for security/auth/permissions/secrets, data/schema/migrations,
public API or observable UI behavior, destructive operations, dependency/
platform/configuration/workflow-control changes, unclear acceptance or a
material user choice, cross-component scope, and high-risk maintenance.
Unknown means full; file count alone never proves low risk. Both procedures
retain the same runtime review, conditional security review, QA, receipts,
close fingerprint, Goal continuation, and verified installation boundaries.

---

## Important Rules

- **Never abort.** Surface every decision; never silently redirect to a shorter path.
- **Durable Docs Decision.** Every PLAN.md classifies documentation impact
  before develop starts. This is a judgment step, not a blanket REQ
  requirement. Use one of these outcomes in the reason: `REQ needed`,
  `Pattern/skill doc enough`, or `No durable doc needed`. Then include:
  `REQ: doc/<area>/REQ__<name>.md | n/a`,
  `GUIDE: doc/<area>/GUIDE__<name>.md | n/a`,
  `ADR: doc/<area>/ADR__<name>.md | n/a`,
  `POLICY: doc/<area>/POLICY__<name>.md | n/a`,
  and `Reason: <one sentence>`. Use `REQ` for observable behavior or contracts
  that implementation and QA must satisfy, `GUIDE` for reusable coding/design/
  testing guidance, `ADR` for significant technical choices with alternatives
  or tradeoffs, and `POLICY` only for external security/legal/data/approval
  constraints that harness cannot fully enforce by itself. Write docs under a
  DDD-style area folder such as `doc/ui/REQ__filter-bar.md`,
  `doc/api/REQ__oauth-login.md`, `doc/auth/ADR__token-storage.md`, or
  `doc/common/GUIDE__coding-style.md`. New pages, admin/backoffice screens, routes, controllers, and endpoints are REQ-required even when additive.
  PLAN.md acceptance criteria are task-local artifacts and never substitute for a durable `REQ`; `REQ: n/a` is invalid for observable UI/API behavior.
  A concrete REQ path is required before develop starts for observable UI/API/backoffice work. If target files or surfaces include observable UI, API, backoffice/admin screens, routes, controllers, or endpoints and the decision says `REQ: n/a`, treat that as a blocking plan defect: revise the PLAN before Phase 6 and do not defer this to close.
  Happy path: when request text, user feedback, target surfaces, or known file
  paths imply observable UI/API/mobile/native/desktop behavior, pick the REQ
  path proactively. If no suitable REQ exists, select a new path such as
  `doc/ui/REQ__mobile-reader-navigation.md`; develop will create/update it with
  a direct `doc/<area>/REQ__*.md` update or `req_scaffold.py` before source edits. Safety gates exist
  only to catch misses, not as the normal discovery mechanism.
  When the change affects harness process, agent instructions, coding patterns,
  or verification practice but not a product/runtime contract, prefer `GUIDE`
  or skill/pattern docs and write `REQ: n/a` with `Pattern/skill doc enough` in
  the reason. For purely mechanical, test-only, or internal refactors, write
  `No durable doc needed` and name the unchanged durable knowledge surface.
- **User gates.** Full planning asks for premise confirmation and User Challenges. Compact planning asks only genuine User Challenges when intent is otherwise explicit.
- **Log every decision.** Every classification gets a row in PLAN.md's Decision Audit Trail.
- **Full depth means full depth.** Complete every loaded methodology section with its required evidence and decisions.
- **Artifacts are deliverables.** PLAN.md and valid required lenses in TASK.json must exist before Phase 6 closes the session.
- **Intent is preserved in PLAN.** Every PLAN.md includes `Original Request / Intent Summary`. If `REQUEST.md` exists, summarize it and cite it. If it is absent, gitignored, or under 15 non-empty lines, summarize the current user prompt and explicitly label the source as `conversation summary` so future reviewers can evaluate intent without relying on task-local request artifacts.
- **Sequential order.** Compact: Phase 0 → bounded assessment → 5 (only if challenged) → 6. Full: Phase 0 → 1 → 2 → 3 → 4 → 5 → 6.

---
