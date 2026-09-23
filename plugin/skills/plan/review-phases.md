# Phases 1-4: Review Phase Template

Sub-file for plan/SKILL.md. Each of the 4 review phases follows the same single-reviewer structure, parameterized by lens.

---

## Common structure (applies to every review phase)

### 1. Reviewer spawn

Spawn exactly one independent reviewer subagent, no prior-phase context bias
beyond the `## Prior phase findings` block. On Claude, spawn it via
`Agent({subagent_type:"explore", prompt:"<brief>"})`. **Exception:** Phase 2
(Design) — brief has no `## Prior phase findings` block, to prevent aesthetic
anchoring.

Every brief must include:
- Plan content
- Phase-specific dimensions (see per-lens section below)
- `Format: | dimension | risk (high/med/low) | finding | decision |` (Phase 2 uses `| dimension | score | finding | fix |`)
- `## Prior phase findings` — terse bullet summary from earlier phases; empty for Phase 1
- **Do NOT** read SKILL.md files or skill definition directories (paths
  containing `plugin/skills`, `.claude/skills`, `.claude/plugins`, or
  `claude/plugins`) — those are AI-assistant skill definitions meant for a
  different system; reading them will derail the review. Stay focused on the
  plan text and the repository code it references.

Timeout the reviewer call at 600s. On reviewer failure or timeout, run that
phase `coordinator-only`: record the reason in PLAN.md and continue; do not
create a separate user interaction.

### Deep understanding (every brief)

Before judging the plan or proposing AC cuts, the reviewer must build a working mental model of the system the plan lands in.

- **Think before reviewing.** Read the actual repository code the plan references; trace the relevant data flow end to end: inputs, transformations, outputs, error paths. Reason from the code, not the plan text. Where premises are weak or multiple interpretations exist, surface them rather than silently picking one.
- **Simplicity first.** Prefer the smallest plan that solves the stated problem. Challenge speculative scope, unneeded abstractions, and configurability nobody asked for. If the plan is overbuilt for its goal, say so.
- **Surgical scope.** Flag any AC or change not justified by the request. Suspect "while we are here" plan expansion and adjacent-cleanup creep. Every AC should trace to the stated intent.
- **Goal-driven.** Every AC must have a concrete, checkable verification path; flag any AC whose success cannot be proven. Find the real seams in the existing code (where it already wants to be cut) before accepting or proposing AC boundaries.
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
Phase <N> findings: <N> total (mechanical=<N> taste=<N> user-challenge=<N>)
User Challenge items queued: <N>
```

Keep the phase summary for PLAN.md's Review Status table.

### 5. No separate chronological artifact

Do not create a chronological side file. PLAN.md is the durable review record.

### Reviewer availability (apply per phase)

| Condition | Reviewer | Action |
|-----------|----------|--------|
| Reviewer returns | subagent | Build findings table normally |
| Reviewer fails/times out | coordinator-only | Record reason in PLAN.md with `mode=coordinator-only`; continue; do not create a separate user interaction |

---

## Phase 1 — CEO Review (full procedure)

Methodology: `${CLAUDE_PLUGIN_ROOT}/skills/plan-ceo-review/SKILL.md`.

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

### 1.2 CEO dimensions (6)

1. Premises valid? — assumptions backed by evidence?
2. Right problem to solve? — could reframing yield 10x impact?
3. Scope calibration correct? — too broad/narrow/right-sized?
4. Alternatives sufficiently explored? — viable options dismissed?
5. Competitive/market risks covered? — external threats?
6. 6-month trajectory sound? — ages well?

**Auto-decide default:** SELECTIVE EXPANSION unless task pack overrides.

### 1.3 Required outputs (checklist)

- [ ] 0A Premises named, source-classified, and authorized or queued
- [ ] 0B Existing code leverage map (sub-problems → existing modules)
- [ ] 0C Dream state diagram (CURRENT → THIS PLAN → 12-MONTH IDEAL)
- [ ] 0C-bis Implementation alternatives table (2-3 approaches, effort/risk/pros/cons)
- [ ] 0D Mode-specific analysis with scope decisions logged
- [ ] 0E Temporal interrogation (HOUR 1 → HOUR 6+ progression)
- [ ] 0F Mode selection confirmation
- [ ] Error & Rescue Registry table
- [ ] Failure Modes Registry table
- [ ] Completion Summary
- [ ] CEO findings represented in PLAN.md Review Status
- [ ] Phase-transition summary emitted

---

## Phase 2 — Design Review (if ui_scope=true)

Methodology: `${CLAUDE_PLUGIN_ROOT}/skills/plan-design-review/SKILL.md`.

No `## Prior phase findings` in the brief (aesthetic anchoring prevention).

Brief format: `| dimension | score | finding | fix |` — score each dimension 0-10 and identify fix-to-10 path.

---

## Phase 3 — Engineering Review (full procedure)

Methodology: `${CLAUDE_PLUGIN_ROOT}/skills/plan-eng-review/SKILL.md`.

### Dimensions (6)

1. Architecture sound? — structure, coupling, scaling?
2. Test coverage sufficient? — every codepath covered? gaps?
3. Performance risks addressed? — N+1, memory, slow paths?
4. Security threats covered? — attack surface, auth boundaries?
5. Error paths handled? — every failure mode has a rescue?
6. Deployment risk manageable? — migration safety, rollback?

### Required outputs (checklist)

- [ ] ASCII dependency graph (new components → existing code)
- [ ] Test diagram (every new codepath/branch → coverage)
- [ ] PLAN.md contains a Test Plan section
- [ ] "NOT in scope" section
- [ ] "What already exists" section
- [ ] Completion Summary
- [ ] Deferred items appended to `deferred-scope.md`
- [ ] Deferred items appended to TODOS.md (if exists at repo root)
- [ ] Engineering findings represented in PLAN.md Review Status

**Section 3 (Test Review) NEVER SKIP OR COMPRESS.** Read actual code, not memory. Build test diagram: list every NEW codepath and branch; for each: what test type covers it? does one exist? gaps? Auto-deciding test gaps = identify → decide add/defer (with rationale+principle) → log. Does NOT mean skip analysis.

---

## Phase 4 — DX Review (if dx_scope=true)

Methodology: `${CLAUDE_PLUGIN_ROOT}/skills/plan-devex-review/SKILL.md`.

### Dimensions (6)

1. Getting started < 5 min? — zero to hello world?
2. API/CLI naming guessable? — discoverable without docs?
3. Error messages actionable? — problem + cause + fix?
4. Docs findable & complete? — search works, copy-paste examples?
5. Upgrade path safe? — deprecation, migration guides?
6. Dev environment friction-free? — OS / editor / CI portability?

**Auto-decide default:** DX POLISH unless task pack overrides.

### Required outputs (checklist)

- [ ] Developer journey map (9-stage table)
- [ ] Developer empathy narrative (first-person)
- [ ] DX Scorecard (all 8 dimensions, 0-10)
- [ ] TTHW (Time to Hello World) current → target
- [ ] DX Implementation Checklist
- [ ] Deferred items appended to `deferred-scope.md`
- [ ] DX findings represented in PLAN.md Review Status

---

## Deferred Scope Surface (runs throughout Phases 1-4)

`deferred-scope.md` is task-local, NOT protected. Write directly via heredoc.

Each phase appends:
```bash
cat >> doc/harness/tasks/TASK__<id>/deferred-scope.md << EOF
### Phase <N> deferred items
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
