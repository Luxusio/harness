---
name: plan
description: Harness planning pipeline with a compact low-risk branch and a full reviewed branch. Both publish canonical PLAN.md and task lens declarations via MCP.
user-invocable: false
---

Codex planning pipeline with compact and full procedures. Conservatively eligible low-risk work uses a bounded assessment; all other work uses the full single-reviewer Plan Review (Phase 0 → 1 → 5 → 6). Both publish through the protected-artifact MCP.

> Current artifact model: acceptance criteria live in `PLAN.md`; `wbs(action="plan")`
> publishes it with required lens declarations in `TASK.json`.

> **Codex runtime notes** (delta from Claude):
> - **One independent reviewer, capability-mapped.** Discover deferred tools before deciding. When `spawn_agent` is available, run exactly one independent reviewer subagent per phase. Use one inline critical-reviewer pass only when no independent route exists, and record that phase as `coordinator-only` in PLAN.md's Review Status section.
> - **Sub-skills are inlined, not invoked.** Claude's `Skill("harness:plan-ceo-review", task_id)` chain has no Codex equivalent. The orchestrator reads each internal prompt's SKILL.md content inline and executes the methodology in the same conversation. Codex keeps these prompts under `${HARNESS_PLUGIN_ROOT}/internal-skills/` so they remain packaged without appearing in the user-visible skill menu.
> - **AskUserQuestion = conversational ask.** Both procedures collect unresolved material premises and User Challenges into one Phase 5.3 interaction. Phase 5.4.1 runs only for explicit pre-code approval; a later new delta returns to the specific Phase 5.3 format.
> - **`${CLAUDE_PLUGIN_ROOT}` → `${HARNESS_PLUGIN_ROOT}`** for bash invocations that remain. Plan artifact writes use MCP `wbs(action="plan")`.
> - **One MCP tool:** bare `wbs`; use open/context/plan actions. Claude uses the runtime-qualified name of this same tool.

## Sub-files

This skill is split across four sub-files, copied from the Claude tree into the installed payload by `install.py`:

| File | Content |
|------|---------|
| `intake.md` | Phase 0 (spawned detection, recovery, task context, scope detection, planning-procedure branch) |
| `review-phases.md` | Phase 1 (merged CEO+Eng review template, dimensions, checklists, findings table) |
| `decision-principles.md` | 6 Decision Principles, classification, auto-decide rules, completion status, repo ownership, ask format |
| `write-artifacts.md` | Phase 6 (PLAN.md / TASK.json lens declarations + MCP writes, learnings, close) |

Phase 5 (procedure-aware user gate) stays inline below. Read sub-files from `${HARNESS_PLUGIN_ROOT}/internal-skills/plan/<file>`.

---

## Invariants (Codex variant)

- **Independent reviewer by capability.** Where the Claude source says "independent reviewer subagent via Agent", use `spawn_agent` when exposed. Otherwise run ONE inline critical-reviewer pass and record the phase as `coordinator-only` in PLAN.md `Review Status`.
- **Compact plans stay canonical.** Low-risk planning still writes PLAN.md with stable ACs, path scope, tests, and a durable-doc decision. It never skips develop-time review, QA, close, or install verification.
- **Premise analysis mandatory.** Phase 1.1 always extracts and source-classifies premises; only unresolved material premises require user input.
- **One decision interaction.** Unresolved material premises and User Challenges are collected and asked together at Phase 5.3.
- **Write via MCP only.** PLAN.md and TASK.json required-lens declarations go through `wbs(action="plan")`. Never Write/Edit them directly.
- **Workflow-lock awareness.** Trusts coordinator; no redundant check.
- **Read actual code.** Review phases MUST read source files, diffs, and referenced code. Reasoning from plan text alone is insufficient.
- **Never abort.** Full planning surfaces review failures and never silently shortens. Compact may escalate to full after inspection, but never bypasses its fail-closed assessment.
- **Auto-decide mode.** When active, resolves Mechanical and Taste items via the 6 Decision Principles, never unresolved material user decisions.
- **Spawned session.** `spawned_session: true` or `HARNESS_SPAWNED=1` → force auto-decide within explicitly delegated authority and relay undelegated material decisions.
- **Sequential execution by procedure.** Compact runs 0 → bounded assessment → 5.0 → 5.3 only when challenged → 6. Full runs 0 → 1 → 5 → 6.

## Voice

Plan-orchestrator voice: opinionated, concrete, builder-to-builder.

- Lead with the point. Say what the phase did, what it found, what changes downstream.
- Be concrete. Name files, functions, line numbers, AC ids, premise indices, decision principles.
- Tie technical choices to user outcomes.
- Be direct about quality. Bugs in the plan matter more than bugs in the implementation.
- Sound like a builder talking to a builder, not a consultant.
- No em dashes. No AI vocabulary: `delve`, `crucial`, `robust`, `comprehensive`, `nuanced`, `multifaceted`, `furthermore`, `moreover`, `additionally`, `pivotal`, `landscape`, `tapestry`, `underscore`, `foster`, `showcase`, `intricate`, `vibrant`, `fundamental`, `significant`.
- Korean/English bilingual context: technical terms stay English, explanations may use Korean.
- The user has context you do not. Both procedures ask only unresolved material decisions; review depth never manufactures an approval requirement.

Good: "Phase 1. AC-004 verification command already passes pre-edit (grep hit at write-artifacts.md:140). EUREKA — re-scope AC-004 to a smaller addition. Surface before writing PLAN.md."
Bad: "I've completed the engineering review phase and identified some considerations regarding AC-004 that may warrant additional examination."

## Anti-shortcut clause

PLAN.md is the output of the selected procedure, not a substitute for it. Both procedures finish premise analysis and resolve every material user decision. Explicit request, clarification, or delegation can already provide authorization; do not manufacture premise or final approval rounds from review depth.

## Confusion Protocol

For high-stakes orchestrator-level ambiguity — execution mode selection, scope detection edge cases, conflicting review output, or mid-pipeline scope expansion — add the choice to the consolidated decision bundle. After that bundle, ask only for a new unauthorized material delta.

Auto-decide scope partitioning within the explicitly authorized outcome via P1.
Expanding or reducing the requested outcome is a material user decision.

## Context Health

- **`[PROGRESS]` summary at phase boundaries.** When a phase takes longer than ~5 minutes, surface a 1-2 sentence checkpoint: done, next, surprises.
- **Loop detection.** If the same finding fires 3 times without converging, STOP and surface only the unresolved material choice; do not repeat an already authorized premise. Looping silently is worse than asking.
- Progress summaries and loop-detection notices NEVER mutate git state.

## Completeness — Boil the Lake

Complete every section before moving on. Use concrete content in each required section. "No issues found" is valid only after stating what was examined.

---

## Sub-skill execution protocol (Codex variant)

Phase 1 reads both corresponding Codex internal prompts and executes the
combined methodology inline:

- `${HARNESS_PLUGIN_ROOT}/internal-skills/plan-ceo-review/SKILL.md` (premise/scope, read + execute inline)
- `${HARNESS_PLUGIN_ROOT}/internal-skills/plan-eng-review/SKILL.md` (architecture/tests/failure, read + execute inline)

These sub-skills are heavy review pipelines on the Claude side. On Codex, route them through one independent reviewer subagent via `spawn_agent` when available; otherwise run inline and record the phase as `coordinator-only` in PLAN.md Review Status.

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

When used, it contains `{"state": "...", "phase": "...", "source": "plan-skill"}`. Remove it after successful `wbs(action="plan")`. Ignore stale or malformed legacy scratch and reconstruct from PLAN.md/task context; do not bulk-migrate historical tasks.

---

## Reviewer Protocol (Codex variant)

Phase 1 first discovers `spawn_agent`. When it is available, spawn exactly
one independent reviewer subagent per phase. Only when no independent route
exists, run ONE inline critical-reviewer pass and record the phase as
`coordinator-only`. Every pass:
- Reads the phase brief (lens dimensions from `review-phases.md`).
- Produces findings per dimension.
- Classifies each finding (Mechanical / Taste / User Challenge).
- Retains a findings row for PLAN.md, marked `coordinator-only` only when the independent route was unavailable.

The `coordinator-only` fallback has less independent detection, so record it
explicitly. One reviewer is the normal case; a Codex run with an independent
subagent is not a degraded run merely because the runtime differs. Any
coordinator-only phase makes the completion status DONE_WITH_CONCERNS and the
plan report VERDICT `REVIEWED_DEGRADED` (`write-artifacts.md`, shared across
runtimes).

Full protocol, dimensions, checklists, and findings table: `review-phases.md` (Claude tree). The Codex orchestrator uses the `coordinator-only` row only as the capability fallback.

---

## Phase orchestration

1. **Phase 0** — always runs and selects `compact` or `full` planning procedure; TASK.json remains `standard` or `micro` only.
2. **Compact branch** — conservatively classified low-risk standard work gets one bounded assessment, unresolved material decisions only, then canonical publication.
3. **Full branch Phase 1 — Plan Review** — premise extraction and authorization classification are mandatory analysis. One reviewer subagent (when available) merges CEO (premise/scope) and Engineering (architecture/tests/failure) methodology; adds a UI checklist when `ui_scope=true`.
4. **Phase 5 — Consolidated Decision Gate** — either procedure asks once only when unresolved material decisions remain.
5. **Phase 6 — Write artefacts** — always writes through `wbs(action="plan")`; then removes optional PLAN_SESSION.json scratch.

The compact assessment must inspect the named code/docs and recheck every
escalation family against discovered dependencies, callers, data flows,
configuration, and observable effects. Any discovered trigger, unknown,
cross-component impact, or unresolved material choice abandons compact and
restarts at full Phase 1 before Phase 5/6.

---

## Phase 5: Procedure-aware user gate

Branch once after §5.0:

- **compact and full:** retain decisions/themes for PLAN.md, collect unresolved
  material premises and User Challenges, and run §5.3 once only when that set
  is non-empty. A complete answer authorizes the resulting scope. With no
  unresolved material decision or unauthorized delta, proceed to Phase 6. Run
  §5.4.1 only when the user explicitly requested pre-code plan approval.

### 5.0 Pre-Gate verification (max 2 retries)

For `full`, verify required outputs before collecting decisions:
- [ ] Phase 1: premises source-classified and authorized or queued; CEO+Eng findings retained; phase-transition summary; UI checklist present when `ui_scope=true`
- [ ] PLAN.md Review Status has ≥ 1 row for Phase 1
- [ ] Any coordinator-only fallback is logged with the concrete unavailable independent route for each affected phase.

If missing after 2 retries, proceed to 5.1 with warning block:
```
⚠ Pre-Gate Warning: proceeding with incomplete phase outputs.
Missing: <list>
```
This warning may cover incomplete review metadata, never an unresolved material
user decision; unresolved decisions still block Phase 6.

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

From Phase 1's available findings rows: Mechanical (silently applied), Taste, User Challenge.

### 5.2 Retain Taste decisions for PLAN.md (no gate render)

Taste decisions go to PLAN.md's Decision Audit Trail only.

```
Auto-decided (Taste):
- [item]: chose [option] over [option] because [principle applied]
```

### 5.2.5 Retain Cross-Phase Themes for PLAN.md (no gate render)

Cross-phase theme detection writes to PLAN.md's `Cross-phase themes` section.

### 5.3 Consolidated material-decision gate

Combine unresolved material premises and User Challenges after all applicable
review phases. With zero items, proceed directly to Phase 6. Otherwise ask once
with up to three short questions in the same conversational interaction. If
more than three items remain, group related items into at most three named
decision bundles instead of interrupting sequentially.

Each item names the current authorized direction, recommended alternative,
reason, blind spot, downside, and concrete options. A complete answer authorizes
the resulting scope. Free text may answer item by item. If an answer is partial,
ask again only for unanswered items. Re-run only affected review work and ask
again only if it introduces a new unauthorized material delta.

**Note on coordinator-only fallback User Challenges:** one reviewer is the
normal case; a coordinator-only fallback is weaker evidence for the
recommendation, but review confidence never changes decision ownership.
Retain every unauthorized material user-owned choice in the bundle; only
non-material alternatives may be classified as Taste.

### 5.4 Authorization recheck

Compare the final outcome, in-scope boundary, out-of-scope boundary, material
risk acceptance, irreversible actions, and external-state effects with the
latest explicit request, clarification, delegation, and any §5.3 answer. With
no unauthorized material delta, proceed directly to Phase 6. Mechanical
implementation, tests, docs, and review detail inside the authorized outcome do
not create a new approval requirement.

### 5.4.1 Gate response options

Run only when the user explicitly requested to approve the completed plan
before coding. If §5.4 finds a new unauthorized material delta, return that
specific delta to §5.3 instead of substituting generic plan approval.
Compact/full selection alone never runs it.

Emit §5.1 summary, then ask:

> **Approve plan?**
>
> A) Approve — proceed to develop (Recommended). Move to Phase 6 artefact write.
> B) Reject — discard and reset to Phase 0.
>
> Any other reply is treated as Modify — describe what you want changed (taste-decision overrides, scope adjustments, "re-run Phase 1 with X premise", clarifying questions). The orchestrator interprets the free-text and either revises the plan or answers the question, then re-offers the gate.

**Handling:**
- **Approve:** proceed to Phase 6.
- **Reject:** clear all phase-level state and reset to Phase 0.
- **Modify:** parse the free-text. Three sub-cases:
  - *Pure question (no change request):* answer fully, re-present §5.1 summary, re-offer the gate.
  - *Scope override or taste-decision flip:* apply it. Re-offer only when the user explicitly requested pre-code approval; otherwise the answer is the authorization.
  - *Phase re-run request:* re-run affected phases with updated scope. Ask again only for a newly introduced material delta. After 3 non-converging cycles, stop with unresolved items.

---

## Planning procedure branches

TASK.json execution_mode remains exactly `standard|micro`; never persist
`light`. Micro keeps its explicit no-plan contract. Standard planning selects:

| Procedure | Eligibility | Review shape | Mandatory output |
|-----------|-------------|--------------|------------------|
| `compact` | bounded, unambiguous, low blast radius, all scope/test decisions evident | one code/context assessment; ask only unresolved material decisions | canonical PLAN.md with stable ACs, in/out scope, allowed/test/forbidden paths, verification, durable-doc decision |
| `full` | explicit request, uncertain inputs, or any escalation trigger | capability-routed Plan Review (CEO+Eng merged, plus a UI checklist when ui_scope=true) | full canonical PLAN.md |

Escalate to full for security/auth/permissions/secrets, data/schema/migrations,
public API or observable UI behavior, destructive operations, dependency/
platform/configuration/workflow-control changes, unclear acceptance or a
material user choice, cross-component scope, and high-risk maintenance.
Unknown means full; file count alone never proves low risk. Both procedures
retain the same runtime review, conditional security review, QA,
close fingerprint, Goal continuation, and verified installation boundaries.

---

## Important Rules

- **Never abort.** Record every decision in PLAN.md; never silently redirect to a shorter path.
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
- **User gates.** Both procedures ask once for unresolved material decisions. Explicit request and clarification are reusable authorization; review depth does not manufacture premise or final approval rounds.
- **Log every decision.** Every classification gets a row in PLAN.md's Decision Audit Trail.
- **Full depth means full depth.** Complete every loaded methodology section with its required evidence and decisions.
- **Artifacts are deliverables.** PLAN.md and valid required lenses in TASK.json must exist before Phase 6 closes the session.
- **Intent is preserved in PLAN.** Every PLAN.md includes `Original Request / Intent Summary`. If `REQUEST.md` exists, summarize it and cite it. If it is absent, gitignored, or under 15 non-empty lines, summarize the current user prompt and explicitly label the source as `conversation summary` so future reviewers can evaluate intent without relying on task-local request artifacts.
- **Sequential order.** Compact: Phase 0 → bounded assessment → 5 (only if challenged) → 6. Full: Phase 0 → 1 → 5 → 6.

---
