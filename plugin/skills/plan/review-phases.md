# Phase 1: Review Phase Template

Sub-file for plan/SKILL.md. The full procedure's single review phase, merging
the CEO (premise/problem/scope/alternatives) and Engineering (architecture/
tests/failure/deployment/performance/security) lenses into one reviewer pass.

---

## Common structure

### 1. Reviewer spawn

Spawn exactly one independent reviewer subagent for Phase 1. On Claude, spawn
it via `Agent({subagent_type:"explore", prompt:"<brief>"})`.

Every brief must include:
- Plan content
- Phase 1 dimensions (see § 1.2 below)
- `Format: | dimension | risk (high/med/low) | finding | decision |`
- **Do NOT** read SKILL.md files or skill definition directories (paths
  containing `plugin/skills`, `.claude/skills`, `.claude/plugins`, or
  `claude/plugins`) — those are AI-assistant skill definitions meant for a
  different system; reading them will derail the review. Stay focused on the
  plan text and the repository code it references.

Timeout the reviewer call at 900s — one reviewer now covers both the CEO and
Engineering lenses, so the phase needs more wall-clock time than a
single-lens pass did. On reviewer failure or timeout, run Phase 1
`coordinator-only`: record the reason in PLAN.md and continue; do not create
a separate user interaction.

### Deep understanding (every brief)

Before judging the plan or proposing AC cuts, the reviewer must build a working mental model of the system the plan lands in.

- **Think before reviewing.** Read the actual repository code the plan references; trace the relevant data flow end to end: inputs, transformations, outputs, error paths. Reason from the code, not the plan text. Where premises are weak or multiple interpretations exist, surface them rather than silently picking one.
- **Simplicity first.** Prefer the smallest plan that solves the stated problem. Challenge speculative scope, unneeded abstractions, and configurability nobody asked for. If the plan is overbuilt for its goal, say so.
- **Surgical scope.** Flag any AC or change not justified by the request. Suspect "while we are here" plan expansion and adjacent-cleanup creep. Every AC should trace to the stated intent.
- **Goal-driven.** Every AC must have a concrete, checkable verification path; flag any AC whose success cannot be proven. Find the real seams in the existing code (where it already wants to be cut) before accepting or proposing AC boundaries.
- **Width check.** Flag any AC whose `**Files:**` or `**Tests:**` paths overlap another AC's without a helper-extract AC both depend on, and flag any single AC that bundles independent file seams that could instead run as separate parallel lanes.
- **Scope note:** this instruction targets repository source code and test files. It does NOT extend to SKILL.md files or skill definition directories, which the rule above already prohibits. Repository code yes; skill-definition files no.

### 2. Build findings table

For each reviewer finding:
1. Classify: Mechanical / Taste / User Challenge (see `decision-principles.md`)
2. Apply the per-phase conflict-resolution priority from `decision-principles.md` § Per-phase priority (the single source for phase priorities)
3. Record a findings row. If the coordinator and reviewer disagree on
   classification, escalate to the higher tier.

Keep rows in working context and materialize them once in PLAN.md's
`Decision Audit Trail` during Phase 6. Do not create a second audit artifact.

Audit row format (7 pipe-delimited columns):
```
# | phase | decision | classification | principle | rationale | rejected_option
```

Materialize each auto-decided row in PLAN.md's `## Decision Audit Trail` section.

### 3. Findings table display

```
<LENS> REVIEW — FINDINGS TABLE:
═══════════════════════════════════════════════════════════════
| dimension | risk | finding | decision |
| --------- | ---- | ------- | -------- |
| <dimension 1> | <high/med/low> | <finding> | <Mechanical/Taste/User Challenge> |
...
═══════════════════════════════════════════════════════════════
```

### 4. Phase-transition summary

```
Phase 1 findings: <N> total (mechanical=<N> taste=<N> user-challenge=<N>)
User Challenge items queued: <N>
```

Keep the phase summary for PLAN.md's Review Status table.

### 5. No separate chronological artifact

Do not create a chronological side file. PLAN.md is the durable review record.

### Reviewer availability

| Condition | Reviewer | Action |
|-----------|----------|--------|
| Reviewer returns | subagent | Build findings table normally |
| Reviewer fails/times out | coordinator-only | Record reason in PLAN.md with `mode=coordinator-only`; continue; do not create a separate user interaction |

---

## Phase 1 — Plan Review (full procedure)

Methodology: `${CLAUDE_PLUGIN_ROOT}/skills/plan-ceo-review/SKILL.md` (premise,
problem framing, scope, alternatives) and
`${CLAUDE_PLUGIN_ROOT}/skills/plan-eng-review/SKILL.md` (architecture, tests,
failure handling, deployment, performance, security). One reviewer covers
both; there is no separate Design or DX phase in the pipeline.

### 1.1 Premise extraction and authorization (MANDATORY ANALYSIS)

Extract the top 3-5 premises and record the source of each. Classify them before
review:

- **authorized:** directly stated in the current request, a later clarification,
  or an explicit parent delegation;
- **evidence-backed:** established by repository evidence and does not choose a
  product outcome, material scope, risk acceptance, irreversible action, or
  external-state change for the user;
- **unresolved material:** unsupported and would make one of those choices.

Authorized and evidence-backed premises do not produce a question. Carry every
unresolved material premise into the Phase 5 consolidated decision bundle, and
review the relevant alternatives provisionally. Premise extraction is always
mandatory; a separate premise-confirmation interaction is not.

For each proposed normative REQ/WBS outcome, compare its claimed origin with
the actual user message or cited requirement, not another model summary.
An authentic quote must support the added detail; a topic match is insufficient.
Keep explicit/scoped-confirmed user intent separate from grounded derivation
and non-binding assumptions/proposals. Missing access to a source is not proof
of user approval. Unsupported changes to outcome/scope remain material questions
before publication; routine implementation choices stay autonomous in PLAN/ADR.
Only user_explicit/scoped user_confirmed rows can be accepted user requirements.
Accepted document status does not accept derived work or candidates. Check a
derived item's link to the existing requirement/standing constraint and its
reasoning: necessary implementation can enter WBS and be verified autonomously,
but a new product outcome, service, cost or user-facing constraint cannot be
authorized by a derived label. Apply the same check to WBS revisions caused by
new feedback or development discoveries. Keep exact excerpts separate from
authored claims and confirmation interpretations; missing grounding is unverified.

### 1.2 Dimensions (10)

1. Premises valid? — assumptions backed by evidence?
2. Right problem to solve? — could reframing yield 10x impact?
3. Scope calibration correct? — too broad/narrow/right-sized?
4. Alternatives sufficiently explored? — viable options dismissed?
5. Architecture sound? — structure, coupling, scaling?
6. Test coverage sufficient? — every codepath covered? gaps? breaking/regression tests?
7. Error paths handled? — every failure mode has a rescue?
8. Deployment risk manageable? — migration safety, rollback?
9. Performance risks addressed? — N+1, memory, slow paths?
10. Security threats covered? — attack surface, auth boundaries?

Competitive/market risk and 6-month trajectory dimensions are recorded `n/a`
unless the plan itself claims a competitive positioning or multi-month
rollout risk worth evaluating.

**Auto-decide default:** SELECTIVE EXPANSION unless task pack overrides.

### 1.3 Required outputs (checklist)

- [ ] Premises named, source-classified, and authorized or queued
- [ ] Implementation alternatives table (2-3 approaches, effort/risk/pros/cons)
- [ ] ASCII dependency graph (new components → existing code)
- [ ] Test diagram (every new codepath/branch → coverage)
- [ ] PLAN.md contains a Test Plan section
- [ ] Error & Rescue Registry table
- [ ] Failure Modes Registry table
- [ ] "NOT in scope" section
- [ ] "What already exists" section
- [ ] Deferred items appended to `deferred-scope.md`
- [ ] Deferred items appended to TODOS.md (if exists at repo root)
- [ ] Completion Summary
- [ ] Phase 1 findings represented in PLAN.md Review Status
- [ ] Phase-transition summary emitted

**Test diagram section NEVER SKIP OR COMPRESS.** Read actual code, not memory. Build test diagram: list every NEW codepath and branch; for each: what test type covers it? does one exist? gaps? Auto-deciding test gaps = identify → decide add/defer (with rationale+principle) → log. Does NOT mean skip analysis.

### 1.4 UI checklist (when `ui_scope=true`)

Phase 1 additionally requires, inline in the same review pass:

- [ ] Interaction-state table (loading/empty/error/success/partial/recovery)
- [ ] User journey through the affected screens
- [ ] Accessibility requirements (keyboard, screen reader, contrast, focus order — whatever applies)

No 0-10 scoring loop, no mockups, no visual-style rules — rendered visual
quality stays with ux-browser/qa-browser. If Phase 1 falls back to
`coordinator-only`, the coordinator produces this checklist directly; the
fallback still creates no separate user interaction.

---

## Deferred Scope Surface (runs during Phase 1)

`deferred-scope.md` is task-local, NOT protected. Write directly via heredoc.

Phase 1 appends:
```bash
cat >> doc/harness/tasks/TASK__<id>/deferred-scope.md << EOF
### Phase 1 deferred items
- <item>: deferred because <rationale> (principle: <P#>)
EOF
```

Full planning creates this file at Phase 1 start (`touch`), and Phase 6.2
incorporates its summary into PLAN.md "NOT in scope". Compact planning does not
create the sidecar; any deferred item is written directly into PLAN.md.

---

## Compression brake

If any review section produces fewer than 3 sentences of analysis, it is compression — expand before moving on. "No issues found" valid only after stating what was examined and why nothing flagged (min 1-2 sentences). "Skipped" is never valid for a non-skip-listed section.

**Skip list** (already handled by pipeline — do not re-run): Preamble/boilerplate, AskUserQuestion Format, Completeness Principle, Telemetry, Platform detection (Phase 0), Prerequisite Skill Offer (Phase 0.4.5).
