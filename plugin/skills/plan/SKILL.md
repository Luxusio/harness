---
name: plan
description: Harness-native planning pipeline with a compact low-risk branch and a full dual-voice branch; both write PLAN.md and task lens declarations via MCP.
argument-hint: <task-slug>
user-invocable: false
allowed-tools: Read, Glob, Grep, Bash, AskUserQuestion, Agent, mcp__plugin_harness_harness__task_start, mcp__plugin_harness_harness__task_context, mcp__plugin_harness_harness__write_plan
---

Harness-native planning pipeline. Conservatively eligible low-risk work uses the compact procedure; all other work uses the full 7-phase dual-voice review. Both publish the final task contract through the protected-artifact MCP.

> Current artifact model: acceptance criteria live in `PLAN.md`; `write_plan`
> publishes it with required lens declarations in `TASK.json`.

## Sub-files

This skill is split across four sub-files. Load on demand:

| File | Content |
|------|---------|
| `intake.md` | Phase 0 (spawned detection, session recovery, task pack read, git context, base branch, scope detection, planning-procedure branch) |
| `review-phases.md` | Phases 1-4 (dual-voice template + per-lens dimensions, checklists, degradation matrix) |
| `decision-principles.md` | 6 Decision Principles, classification, auto-decide rules, completion status, repo ownership, AskUserQuestion format |
| `write-artifacts.md` | Phase 6 (PLAN.md / TASK.json lens declarations + MCP writes, learnings, close) |

Phase 5 (procedure-aware user gate) stays inline below.

---

## Invariants

- **Full-plan Dual Voice required.** Every full-plan review phase (1-4) spawns Voice A and Voice B via Agent. Single-voice is prohibited; degradation matrix applies when a voice fails.
- **Compact plans stay canonical.** The low-risk branch still writes PLAN.md with stable ACs, path scope, tests, and a durable-doc decision. It never skips develop-time review, QA, receipts, close, or install verification.
- **Premise gate mandatory for full plans.** Phase 1.1 emits exactly one AskUserQuestion before Phase 5. Compact plans ask only when a real User Challenge exists.
- **Never-auto decisions.** User Challenge items get their own AskUserQuestion at Phase 5.3.
- **Write via MCP only.** PLAN.md and TASK.json required-lens declarations go through `write_plan`. Never Write/Edit them directly.
- **Zero browser-flag participation.** Does not invent or inspect undeclared browser verification flags.
- **Workflow-lock awareness.** Trusts coordinator; no redundant check.
- **Read actual code.** Review phases MUST read source files, diffs, and referenced code. Reasoning from plan text alone is insufficient.
- **Never abort.** In full planning, both-voices-fail surfaces as a finding and continues; premise refusal may block. Never silently shorten a selected full procedure. Compact may escalate to full after inspection, but never bypasses its own fail-closed assessment.
- **Auto-decide mode.** When active, resolves intermediate AskUserQuestion except premise gate and User Challenge items via the 6 Decision Principles. Replaces judgment, not analysis depth.
- **Spawned session.** `spawned_session: true` or `HARNESS_SPAWNED=1` → force auto-decide, auto-resolve ALL AskUserQuestion (including premise gate), suppress upgrade/usage-stats prompts, emit prose completion instead of waiting.
- **Sequential execution by procedure.** Compact runs 0 → bounded assessment → 5.0 → 5.3 only when challenged → 6. Full runs 0 → 1 → 2 → 3 → 4 → 5 → 6. Review phases never overlap.

## Voice

Plan-orchestrator voice: opinionated, concrete, builder-to-builder. The plan-skill is the entry point for review pipelines — sub-skills inherit voice rules but the parent sets the tone.

- Lead with the point. Say what the phase did, what it found, what changes downstream.
- Be concrete. Name files, functions, line numbers, AC ids, premise indices, decision principles. Real numbers over qualifiers.
- Tie technical choices to user outcomes — what the plan author sees, waits for, or now has confidence in.
- Be direct about quality. Bugs in the plan matter more than bugs in the implementation. Edge cases matter. Premise gaps matter.
- Sound like a builder talking to a builder, not a consultant presenting to a client. No founder cosplay, no hype.
- No em dashes. No AI vocabulary: `delve`, `crucial`, `robust`, `comprehensive`, `nuanced`, `multifaceted`, `furthermore`, `moreover`, `additionally`, `pivotal`, `landscape`, `tapestry`, `underscore`, `foster`, `showcase`, `intricate`, `vibrant`, `fundamental`, `significant`. These signal AI prose; cut them.
- Korean/English bilingual context: technical terms stay English, explanations may use Korean.
- The user has context you do not. Cross-model agreement is a recommendation, not a decision. Full planning uses premise, User Challenge, and final approval gates; compact asks only genuine User Challenges.

Good: "Phase 3 Eng. AC-004 verification command already passes pre-edit (grep hit at write-artifacts.md:140). EUREKA — re-scope AC-004 to a smaller addition. Surface before writing PLAN.md."
Bad: "I've completed the engineering review phase and identified some considerations regarding AC-004 that may warrant additional examination."

## Anti-shortcut clause

PLAN.md is the output of the selected planning procedure, not a substitute for it. Full planning must pass premise, User Challenge, and final approval gates. Compact planning may publish without premise/final approval only after its bounded inspection and escalation recheck pass; every genuine User Challenge still goes through AskUserQuestion. Never hide a finding inside PLAN.md instead of surfacing the gate required by the selected procedure.

## Confusion Protocol

For high-stakes orchestrator-level ambiguity — execution mode selection, scope detection edge cases, conflicting Voice A/B output the principles cannot resolve, premise-gate response interpretation, mid-pipeline scope expansion — STOP. Name it in one sentence, present 2-3 options with concrete tradeoffs, and ask via AskUserQuestion (parent format at `decision-principles.md` § AskUserQuestion Format).

Reserve this protocol for high-stakes planning ambiguity where the wrong choice changes intent, scope, or verification outcome. The bar is: "if I pick wrong, the entire plan is built on a misread of intent or scope, and the cost to unwind shows up in develop or verify, not now."

Auto-decide scope-partition choices (split into smaller tasks, combine items, defer to follow-up, do a subset) via Principle P1 (Choose completeness). The cost of "do more" is more work, not unwound work; that does not meet the protocol's bar.

## Context Health

Soft directive — degrade gracefully, never block.

- **`[PROGRESS]` summary at phase boundaries.** When a phase takes longer than ~5 minutes (Phase 1 + 3 dual-voice spawns are the longest), surface a 1-2 sentence checkpoint: done, next, surprises. Helps the user track progress without scrolling, and helps you self-check direction.
- **Loop detection.** If the same finding, the same Voice A/B disagreement, or the same decision rule fires 3 times without converging, STOP and reassess. Options: re-confirm premise via AskUserQuestion; spawn fresh-context Voice C; or pause for user check-in. Looping silently is worse than asking.
- Progress summaries and loop-detection notices NEVER mutate git state.

## Completeness — Boil the Lake

Every section fully completed before moving on. No "TBD", no placeholders. If a section produces fewer than 3 sentences, it is compression — expand. "No issues found" is valid only after stating what was examined and why nothing was flagged. Plan is not done until every AC has a verification path and every section is complete.

## Plan Mode Safe Operations

Safe: Read, AskUserQuestion, Agent dispatch, `/tmp/` writes. NOT safe during plan mode: writing source files under plugin/src/lib, git commits, mutating build/test commands. Sub-skill SKILL.md files are read for methodology only; never invoke write-capable skills or modify skill definitions during a plan session.

**Plan-mode + skill interaction:**
- Skill files are executable instructions, not reference. Follow the skill step-by-step from its first phase, never as a survey.
- The first AskUserQuestion the skill emits is the workflow entering plan mode, not a violation of plan mode.
- AskUserQuestion satisfies plan mode's end-of-turn requirement.
- At a STOP point, stop immediately. Do NOT continue the workflow or call ExitPlanMode there.
- Call ExitPlanMode only after the skill workflow completes, or if the user tells you to cancel the skill or leave plan mode.

## Plan Status Footer

End of each phase:
```
Phase <N> complete | Findings: <count> | Decisions: <count> | Next: Phase <N+1>
```

---

## Sub-skill execution protocol

Each review phase MUST load its corresponding sub-skill file from disk before running:
- Phase 1 → `plugin/skills/plan-ceo-review/SKILL.md`
- Phase 2 → `plugin/skills/plan-design-review/SKILL.md` (only if ui_scope=true)
- Phase 3 → `plugin/skills/plan-eng-review/SKILL.md`
- Phase 4 → `plugin/skills/plan-devex-review/SKILL.md` (only if dx_scope=true)

Iterate every non-skip-listed section at full depth. See `review-phases.md` for the skip list.

---

## Optional PLAN_SESSION.json scratch

Keep ordinary same-session planning state in working context. Do not create
PLAN_SESSION.json by default. It may be created when planning is explicitly
resumable, delegated across contexts, or otherwise needs recovery across a turn.
It is scratch only and never controls task routing or artifact ownership.

| State | Phase | Condition |
|-------|-------|-----------|
| `context_open` | 0-5 | Set at Phase 0 start |
| `write_open` | 6 | At Phase 6 start before MCP artifact writes |
| `closed` | post-6 | Transitional state immediately before scratch removal |

When scratch is used, it has `{"state": "...", "phase": "...", "source": "plan-skill"}` and may include transport hints. Remove it after a successful `write_plan`. Ignore stale or malformed legacy scratch and reconstruct from PLAN.md/task context; do not bulk-migrate historical tasks.


---

## Dual Voice Protocol (summary)

Phases 1-4 spawn Voice A (independent, no prior-phase context) and Voice B (same prompt + `## Prior phase findings` from earlier consensus). Exception: Phase 2 keeps both fully independent. Consensus stays in working context and is materialized in PLAN.md.

Full protocol, dimensions, checklists, and degradation matrix: `review-phases.md`.

---

## Phase orchestration (load sub-files for detail)

1. **Phase 0** — `intake.md`. Always runs and selects `compact` or `full` planning procedure; TASK.json remains `standard` or `micro` only.
2. **Compact branch** — for conservatively classified low-risk standard work, perform one bounded assessment and proceed to Phase 5. Ask only genuine User Challenges.
3. **Full branch Phase 1 — CEO Review** — `review-phases.md` § Phase 1. Premise gate at 1.1 is mandatory user interaction.
4. **Full branch Phase 2 — Design Review** — `review-phases.md` § Phase 2. Only if `ui_scope=true`.
5. **Full branch Phase 3 — Engineering Review** — `review-phases.md` § Phase 3.
6. **Full branch Phase 4 — DX Review** — `review-phases.md` § Phase 4. Only if `dx_scope=true`.
7. **Phase 5 — Final Approval Gate** — inline below. Compact plans may proceed without a confirmation only when there are no User Challenges and intent/scope are already explicit.
8. **Phase 6 — Write artefacts** — `write-artifacts.md`. Always runs.

---

## Phase 5: Procedure-aware user gate

Branch once after §5.0:

- **compact:** retain decisions/themes for PLAN.md, run §5.3 only when actual
  User Challenges exist, then proceed directly to Phase 6. With zero challenges,
  proceed directly from §5.0 to Phase 6. Compact does not emit the §5.1 approval
  summary and does not run §5.4.1 `Approve plan?`.
- **full:** run §§5.1 through 5.4.1 as written.

### 5.0 Pre-Gate verification (max 2 retries)

For `full`, verify required outputs before collecting decisions:
- [ ] Phase 1: premise challenge user-confirmed; CEO consensus retained; phase-transition summary
- [ ] Phase 2 (if ran): Design consensus retained; phase-transition summary
- [ ] Phase 3: Engineering consensus retained; phase-transition summary
- [ ] Phase 4 (if ran): DX consensus retained; phase-transition summary
- [ ] PLAN.md Review Status has ≥ 1 row per completed phase
- [ ] Dual voices ran (or single-voice degradation logged with reason) for each phase

If missing after 2 retries, proceed to 5.1 with warning block:
```
⚠ Pre-Gate Warning: proceeding with incomplete phase outputs.
Missing: <list>
```

For `compact`, verify instead:
- [ ] named code/docs were inspected;
- [ ] every escalation family was rechecked after inspection;
- [ ] no unknown, discovered escalation, or unresolved material choice remains;
- [ ] the compact assessment row and canonical PLAN.md fields are complete.

Missing compact evidence is not warning-only. Escalate to full Phase 1 and do
not publish or attest a compact plan.

### 5.1 Plan approval summary (user-facing)

The user only needs two things at the gate: **what this plan will do** and **what is explicitly out of scope**. Internal review state is retained for PLAN.md, never rendered here.

```
## Plan Approval

### What this plan will do
[2-3 sentences in plain outcome language. Concrete: which files change, which behavior changes, what the user has at the end. No process counters. No phase-by-phase voice scores. No internal classification tallies.]

### Out of scope
[Bulleted list pulled from PLAN.md "NOT in scope". The work direction the user needs to know.]
```

That is the entire user-facing summary. Anything more belongs in PLAN.md, not here.

**Hard guard.** Never render at this gate: the 4-rule body, decision counts, taste tallies, voice consensus tallies, or cross-phase summaries. Those belong in PLAN.md only.

### 5.1.1 Collect all decisions

From consensus tables across Phases 1-4: Mechanical (silently applied), Taste, User Challenge.

### 5.2 Retain Taste decisions for PLAN.md (no gate render)

Taste decisions are retained for PLAN.md's Decision Audit Trail. Do not surface them at the user-facing gate.

Cognitive load rules still apply for the PLAN decision rows:
- **0 taste:** no rows written.
- **1-7 taste:** one row per decision, flat.
- **8+ taste:** one row per decision, with `phase_group` field set so the row is queryable post-task.

PLAN.md decision-row format:
```
Auto-decided (Taste):
- [item]: chose [option] over [option] because [principle applied]
```

### 5.2.5 Retain Cross-Phase Themes for PLAN.md (no gate render)

Cross-phase theme detection still runs. Put the output in PLAN.md's `Cross-phase themes` section; do not render it at the user-facing gate.

```
Cross-Phase Themes (recurring in 2+ phases):
- <theme>: appeared in Phase <N>, Phase <N> — <brief description>
(none — no topics recurred across phases)
```

The reader of this output is the implementer or later reviewer, not the user at the approval gate.

### 5.3 User Challenge gate

Cognitive load:
- **0 challenges:** compact goes directly to Phase 6; full goes to 5.4.
- **1-7 challenges:** one AskUserQuestion per, in order. Do not batch.
- **8+ challenges:** warning at top, group by phase:
  ```
  ⚠ High ambiguity (<N> challenges). Questions grouped by phase. One question per challenge.
  ```

Per-challenge format — invoke `AskUserQuestion` with the challenge framed as a single question. Use the following body inside the `question` field (preserve the five-line block so reasoning is visible in the UI):

```
User Challenge: <item title>

Your stated direction: <from REQUEST.md or the current conversation>
Both reviewers recommend: <alternative>
Reasoning: <why both voices agree>
Blind spots: <what voices may miss about your context>
Downside cost of proceeding as stated: <concrete estimate>
```

Options (3 — keep in this order, put "Accept the recommendation" first so the Recommended label sticks to it):

1. **Accept the recommendation (Recommended)** — switch to the reviewers' alternative.
2. **Keep my original direction** — proceed with the stated direction; reviewers' concern is accepted as a known risk.
3. **Modify** — user-specified change (the reviewers' alternative needs adjustment).

Map `Other` (AskUserQuestion's built-in free-text escape) to option 3 (Modify) — treat the user's note as the modified direction and fold it into scope. One question per challenge; wait for each answer before emitting the next.

### 5.4 Final scope confirmation

Full procedure only. If 5.3 responses changed scope, confirm updated scope
before Phase 6. Compact incorporates the user's challenge answer and proceeds
without a second confirmation.

### 5.4.1 Gate response options

Full procedure only. Compact never runs this subsection.

Invoke `AskUserQuestion` with the §5.1 summary visible in the preceding agent message and the following gate question. Binary options — modify and interrogate collapse into the built-in `Other` free-text mechanism.

`question` (use verbatim):
```
Approve plan?
```

Options (2 — keep this order so the Recommended label sticks to Approve):

1. **Approve — proceed to develop (Recommended)** — accept the plan as-is. Move to Phase 6 artefact write, then develop skill.
2. **Reject — discard and reset to Phase 0** — clear all phase state, abandon the plan.

`Other` is treated as **Modify**: the user types whatever they want changed (taste-decision overrides, scope adjustments, "re-run Phase 3 with X premise", clarifying questions). The handler below interprets the free-text and either revises the plan or answers the question, then re-offers the gate.

**Handling:**
- **Approve:** proceed to Phase 6.
- **Reject:** clear all phase-level state and reset to Phase 0.
- **Other → Modify:** parse the user's free-text. Three sub-cases:
  - *Pure question (no change request):* treat as Interrogate — answer fully, re-present the §5.1 summary, re-offer the gate.
  - *Scope override or taste-decision flip:* apply, re-present the §5.1 summary with changes noted, re-offer the gate.
  - *Phase re-run request (e.g. "re-run Phase 3 with X"):* re-run affected phases with updated scope; increment cycle counter; after 3 cycles proceed to Phase 6 with a warning block at the top of PLAN.md.

---

## Planning procedure branches

The exact TASK.json mode remains `standard|micro`. Standard work uses the
fail-closed compact/full selection in `intake.md` Phase 0.7. Compact planning
collapses plan-time review into one bounded assessment but retains the same
canonical PLAN.md output and every develop-time verification boundary. Full
planning follows Phases 0 through 6 and the complete dual-voice checklists.

---

## Important Rules

Capstone — restating six load-bearing rules in one place. Most also appear in Invariants; consolidated here for at-a-glance reference.

- **Never abort.** The user invoked plan-skill. Surface every taste decision; never silently redirect to a shorter path. Both-voices-fail surfaces as a finding and continues.
- **User gates.** Full planning asks for premise confirmation and User Challenges. Compact planning asks only genuine User Challenges; it does not manufacture a premise or approval round when intent is already explicit.
- **Log every decision.** Every classification gets a row in PLAN.md's Decision Audit Trail. No silent auto-decisions.
- **Full depth means full depth.** Complete every loaded sub-skill methodology section with its required evidence and decisions. "Full depth" means: read the code the section asks you to read, produce the outputs the section requires, identify every issue, decide each one. Fewer than 3 sentences for any review section is a compression signal — expand.
- **Artifacts are deliverables.** PLAN.md and valid required lenses in TASK.json must exist before Phase 6 closes the session.
- **Sequential order.** Compact: Phase 0 → bounded assessment → 5 (only if challenged) → 6. Full: Phase 0 → 1 → 2 → 3 → 4 → 5 → 6. Review phases never overlap.
