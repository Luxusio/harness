---
name: plan
description: Harness-native 7-phase dual-voice review pipeline that writes PLAN.md and related task contract artefacts via the CLI.
argument-hint: <task-slug>
user-invocable: true
allowed-tools: Read, Glob, Grep, Bash, AskUserQuestion, Agent, mcp__harness__task_start, mcp__harness__task_context
---

Harness-native 7-phase dual-voice review pipeline. Runs structured review across CEO, Design, Engineering, and DX lenses; builds adversarial consensus via two independent voices; classifies every decision; surfaces only contested items to the user; writes the final task contract through the protected-artifact CLI.

## Sub-files

This skill is split across four sub-files. Load on demand:

| File | Content |
|------|---------|
| `intake.md` | Phase 0 (spawned detection, session recovery, task pack read, git context, base branch, scope detection, execution-mode branch) |
| `review-phases.md` | Phases 1-4 (dual-voice template + per-lens dimensions, checklists, degradation matrix) |
| `decision-principles.md` | 6 Decision Principles, classification, auto-decide rules, completion status, repo ownership, AskUserQuestion format |
| `write-artifacts.md` | Phase 6 (PLAN.md / PLAN.meta.json / CHECKS.yaml assembly + CLI writes, learnings, close) |

Phase 5 (user-facing gate) stays inline below.

---

## Invariants

- **Dual Voice required.** Every review phase (1-4) spawns Voice A and Voice B via Agent. Single-voice is prohibited; degradation matrix applies when a voice fails.
- **Premise gate mandatory.** Phase 1.1 emits exactly one AskUserQuestion before Phase 5. Premises are never auto-decided.
- **Never-auto decisions.** User Challenge items get their own AskUserQuestion at Phase 5.3.
- **Write via CLI only.** PLAN.md, PLAN.meta.json, CHECKS.yaml, AUDIT_TRAIL.md go through `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/write_plan_artifact.py --artifact ...`. Never Write/Edit directly. CHECKS.yaml post-plan mutations use `update_checks.py` only.
- **Zero browser-flag participation.** Does not read/write/inspect the browser verification flag in TASK_STATE.yaml.
- **Workflow-lock awareness.** Trusts coordinator; no redundant check.
- **Read actual code.** Review phases MUST read source files, diffs, and referenced code. Reasoning from plan text alone is insufficient.
- **Never abort.** Both-voices-fail surfaces as a finding and continues. Blocked is terminal only for premise gate refusal. Never silently redirect to a shorter path. Auto-decide never redirects to interactive mid-pipeline.
- **Auto-decide mode.** When active, resolves intermediate AskUserQuestion except premise gate and User Challenge items via the 6 Decision Principles. Replaces judgment, not analysis depth.
- **Spawned session.** `spawned_session: true` or `HARNESS_SPAWNED=1` → force auto-decide, auto-resolve ALL AskUserQuestion (including premise gate), suppress upgrade/telemetry prompts, emit prose completion instead of waiting.
- **Sequential execution.** 0 → 1 → 2 → 3 → 4 → 5 → 6. Never parallel. Each phase completes fully before next.

## Voice

Plan-orchestrator voice: opinionated, concrete, builder-to-builder. The plan-skill is the entry point for review pipelines — sub-skills inherit voice rules but the parent sets the tone.

- Lead with the point. Say what the phase did, what it found, what changes downstream.
- Be concrete. Name files, functions, line numbers, AC ids, premise indices, decision principles. Real numbers over qualifiers.
- Tie technical choices to user outcomes — what the plan author sees, waits for, or now has confidence in.
- Be direct about quality. Bugs in the plan matter more than bugs in the implementation. Edge cases matter. Premise gaps matter.
- Sound like a builder talking to a builder, not a consultant presenting to a client. No founder cosplay, no hype.
- No em dashes. No AI vocabulary: `delve`, `crucial`, `robust`, `comprehensive`, `nuanced`, `multifaceted`, `furthermore`, `moreover`, `additionally`, `pivotal`, `landscape`, `tapestry`, `underscore`, `foster`, `showcase`, `intricate`, `vibrant`, `fundamental`, `significant`. These signal AI prose; cut them.
- Korean/English bilingual context: technical terms stay English, explanations may use Korean.
- The user has context you do not. Cross-model agreement is a recommendation, not a decision. The user decides at premise gate (1.1) and User Challenge gate (5.3).

Good: "Phase 3 Eng. AC-004 verification command already passes pre-edit (grep hit at write-artifacts.md:140). EUREKA — re-scope AC-004 to a smaller addition. Surface in HANDOFF."
Bad: "I've completed the engineering review phase and identified some considerations regarding AC-004 that may warrant additional examination."

## Anti-shortcut clause

PLAN.md is the OUTPUT of the interactive review, not a substitute for it. Writing every finding into one PLAN.md write and signaling completion without firing AskUserQuestion at the premise gate, User Challenges, or final approval is the precise failure mode the May 2026 transcript bug surfaced — the orchestrator explored, found issues, and dumped them into a deliverable rather than walking the user through them. If you have ANY non-trivial finding (Voice A/B disagreement, premise weakness, scope ambiguity, mode-selection edge case), the path from finding to PLAN.md write goes THROUGH AskUserQuestion (parent format at `decision-principles.md` § AskUserQuestion Format). Zero-finding phases are the only path that bypasses interactive surfacing. If you find yourself wanting to write PLAN.md with findings before asking, stop — that's the bug.

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

## PLAN_SESSION.json lifecycle

Open at Phase 0; update through Phase 6.

| State | Phase | Condition |
|-------|-------|-----------|
| `context_open` | 0-5 | Set at Phase 0 start |
| `write_open` | 6 | At Phase 6 start before any CLI write |
| `closed` | post-6 | After all CLI writes complete |

Required: `{"state": "...", "phase": "...", "source": "plan-skill"}`. The `source` is validated by `write_artifact.py plan` — mismatch rejects writes.

Mirror `plan_session_state` in TASK_STATE.yaml: `context_open` at 0, `write_open` at 6 start, `closed` after 6.

---

## Dual Voice Protocol (summary)

Phases 1-4 spawn Voice A (independent, no prior-phase context) and Voice B (same prompt + `## Prior phase findings` from earlier consensus). Exception: Phase 2 keeps both fully independent (aesthetic anchoring prevention). Consensus built phase-scoped; rows appended to AUDIT_TRAIL.md via CLI before moving to next phase.

Full protocol, dimensions, checklists, and degradation matrix: `review-phases.md`.

---

## Phase orchestration (load sub-files for detail)

1. **Phase 0** — `intake.md`. Always runs.
2. **Phase 1 — CEO Review** — `review-phases.md` § Phase 1. Always runs. Premise gate at 1.1 is mandatory user interaction.
3. **Phase 2 — Design Review** — `review-phases.md` § Phase 2. Only if `ui_scope=true` and not `execution_mode: light`.
4. **Phase 3 — Engineering Review** — `review-phases.md` § Phase 3. Always runs.
5. **Phase 4 — DX Review** — `review-phases.md` § Phase 4. Only if `dx_scope=true` and not `execution_mode: light`.
6. **Phase 5 — Final Approval Gate** — inline below.
7. **Phase 6 — Write artefacts** — `write-artifacts.md`. Always runs.

---

## Phase 5: Final Approval Gate (always runs)

### 5.0 Pre-Gate verification (max 2 retries)

Verify required outputs before collecting decisions:
- [ ] Phase 1: premise challenge user-confirmed; CEO consensus in AUDIT_TRAIL; phase-transition summary
- [ ] Phase 2 (if ran): Design consensus in AUDIT_TRAIL; phase-transition summary
- [ ] Phase 3: Engineering consensus in AUDIT_TRAIL; phase-transition summary
- [ ] Phase 4 (if ran): DX consensus in AUDIT_TRAIL; phase-transition summary
- [ ] AUDIT_TRAIL has ≥ 1 row per completed phase
- [ ] Dual voices ran (or single-voice degradation logged with reason) for each phase

If missing after 2 retries, proceed to 5.1 with warning block:
```
⚠ Pre-Gate Warning: proceeding with incomplete phase outputs.
Missing: <list>
```

### 5.1 Plan approval summary (user-facing)

The user only needs two things at the gate: **what this plan will do** and **what is explicitly out of scope**. Internal review state (decision counts, voice consensus tallies, taste classifications, cross-phase themes) is logged to `AUDIT_TRAIL.md` by §5.2 and §5.2.5 — never rendered here.

```
## Plan Approval

### What this plan will do
[2-3 sentences in plain outcome language. Concrete: which files change, which behavior changes, what the user has at the end. No process counters. No phase-by-phase voice scores. No internal classification tallies.]

### Out of scope
[Bulleted list pulled from PLAN.md "NOT in scope". The work direction the user needs to know.]
```

That is the entire user-facing summary. Anything more belongs in PLAN.md or AUDIT_TRAIL.md, not here.

**Hard guard.** Never render at this gate: the 4-rule body (`[Re-ground]/[Simplify]/[Recommend]/[Options]`), decision counts, taste tallies, voice consensus tallies, cross-phase summaries. Those belong in PLAN.md / AUDIT_TRAIL.md only. `decision-principles.md` § AskUserQuestion Format exempts §5.4.1 from the 4-rule body by design.

### 5.1.1 Collect all decisions

From consensus tables across Phases 1-4: Mechanical (silently applied), Taste, User Challenge.

### 5.2 Log Taste decisions to AUDIT_TRAIL (no gate render)

Taste decisions are written to AUDIT_TRAIL only. Do NOT surface them at the user-facing gate (§5.1) — that contradicts the outcome-focused gate design. Auditability is preserved through AUDIT_TRAIL.md; the user does not need to read every taste classification to approve a plan.

Cognitive load rules still apply for the AUDIT_TRAIL row format:
- **0 taste:** no rows written.
- **1-7 taste:** one row per decision, flat.
- **8+ taste:** one row per decision, with `phase_group` field set so the row is queryable post-task.

AUDIT_TRAIL row format (this is the on-disk format, not gate output):
```
Auto-decided (Taste):
- [item]: chose [option] over [option] because [principle applied]
```

### 5.2.5 Log Cross-Phase Themes to AUDIT_TRAIL (no gate render)

Cross-phase theme detection still runs — scan each phase consensus table; normalise topics (lowercase, trim); group; any topic in ≥2 phases is a high-confidence signal. The output goes to AUDIT_TRAIL.md and PLAN.md `Cross-phase themes` section. AUDIT_TRAIL only — do NOT render at the user-facing gate (§5.1).

```
Cross-Phase Themes (recurring in 2+ phases):
- <theme>: appeared in Phase <N>, Phase <N> — <brief description>
(none — no topics recurred across phases)
```

The reader of this output is the post-task auditor or the next plan-skill resume, not the user at the approval gate.

### 5.3 User Challenge gate

Cognitive load:
- **0 challenges:** skip entirely, go to 5.4.
- **1-7 challenges:** one AskUserQuestion per, in order. Do not batch.
- **8+ challenges:** warning at top, group by phase:
  ```
  ⚠ High ambiguity (<N> challenges). Questions grouped by phase. One question per challenge.
  ```

Per-challenge format — invoke `AskUserQuestion` with the challenge framed as a single question. Use the following body inside the `question` field (preserve the five-line block so reasoning is visible in the UI):

```
User Challenge: <item title>

Your stated direction: <from REQUEST.md or TASK_STATE.yaml>
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

If 5.3 responses changed scope, confirm updated scope before Phase 6.

### 5.4.1 Gate response options

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

## Execution mode branches

| Mode | Phase 2 | Phase 4 | Dual voice | Mandatory outputs | Deferred scope | auto_decide |
|------|---------|---------|------------|-------------------|----------------|-------------|
| `light` | skip | skip | single-voice | single-voice versions | collected | premise+challenge still gated |
| `standard` | ui_scope gate | dx_scope gate | required | full dual-voice checklists | collected | CEO→SELECTIVE EXPANSION, DX→DX POLISH |

- **light**: Phases 0, 1, 3, 5, 6 with single-voice reasoning. Mandatory checklists still apply (single-voice versions). Gate options A-E available; summary simplified (no per-phase voice consensus scores).
- **standard**: default. Full pipeline.

Both modes: Phase 1 premise gate and Phase 5.3 User Challenges never auto-decided (except spawned mode auto-resolves premise gate).

---

## Important Rules

Capstone — restating six load-bearing rules in one place. Most also appear in Invariants; consolidated here for at-a-glance reference.

- **Never abort.** The user invoked plan-skill. Surface every taste decision; never silently redirect to a shorter path. Both-voices-fail surfaces as a finding and continues.
- **Two gates.** The non-auto-decided AskUserQuestions are: (1) premise confirmation in Phase 1.1, and (2) User Challenges in Phase 5.3 — when both voices agree the user's stated direction should change. Everything else is auto-decided via the 6 Decision Principles.
- **Log every decision.** Every classification (Mechanical / Taste / User Challenge) gets a row in `AUDIT_TRAIL.md` via `write_plan_artifact.py --artifact audit`. No silent auto-decisions.
- **Full depth means full depth.** Complete every loaded sub-skill methodology section with its required evidence and decisions. "Full depth" means: read the code the section asks you to read, produce the outputs the section requires, identify every issue, decide each one. Fewer than 3 sentences for any review section is a compression signal — expand.
- **Artifacts are deliverables.** PLAN.md, PLAN.meta.json, CHECKS.yaml, AUDIT_TRAIL.md must exist on disk before Phase 6 closes the session. If any artifact is missing, the plan is incomplete. CHECKS.yaml mutations post-plan go through `update_checks.py` only.
- **Sequential order.** Phase 0 → 1 → 2 → 3 → 4 → 5 → 6. Never parallel. Each phase builds on the last; transition summaries appended to AUDIT_TRAIL.md before the next phase begins.
