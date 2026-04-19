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

Founder clarity (Garry Tan style): short sentences, no hedging ("I think", "maybe", "might"), active voice, no filler ("it's worth noting"), technical precision. Korean/English bilingual context: technical terms stay English, explanations may use Korean.

## Completeness — Boil the Lake

Every section fully completed before moving on. No "TBD", no placeholders. If a section produces fewer than 3 sentences, it is compression — expand. "No issues found" is valid only after stating what was examined and why nothing was flagged. Plan is not done until every AC has a verification path and every section is complete.

## Plan Mode Safe Operations

Safe: Read, AskUserQuestion, Agent dispatch, `/tmp/` writes. NOT safe during plan mode: writing source files under plugin/src/lib, git commits, mutating build/test commands. Sub-skill SKILL.md files are read for methodology only; never invoke write-capable skills or modify skill definitions during a plan session.

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

### 5.1 Rich plan review summary

```
## Plan Review Complete

### Plan Summary: [1-3 sentences describing what this plan does]
### Decisions Made: [N] total ([M] auto-decided, [K] taste, [J] user challenges)

### Per-Phase Review Scores
- Phase 1 CEO: [summary], Voice consensus [X/6 confirmed, Y disagree]
- Phase 2 Design: [summary or "skipped, no UI scope"], consensus [X/Y]
- Phase 3 Eng: [summary], Voice consensus [X/6 confirmed, Y disagree]
- Phase 4 DX: [summary or "skipped, no DX scope"], consensus [X/6]

### Cross-Phase Themes: [from 5.2.5, or "none"]
### Deferred Items: [count and summary from deferred-scope.md, or "none"]
### Deferred to TODOS.md
[Items added to TODOS.md during Phases 1-4. Format: "- <item> (Phase <N>, <principle>)".
 If none: "none"]
```

### 5.1.1 Collect all decisions

From consensus tables across Phases 1-4: Mechanical (silently applied), Taste, User Challenge.

### 5.2 Surface Taste decisions (informational only)

Cognitive load rules:
- **0 taste:** skip section entirely.
- **1-7 taste:** flat list.
- **8+ taste:** group by phase with warning: `⚠ High ambiguity (<N> taste decisions). Grouped by phase.`

Format:
```
Auto-decided (Taste):
- [item]: chose [option] over [option] because [principle applied]
```

### 5.2.5 Cross-Phase Themes

Scan each phase consensus table; normalise topics (lowercase, trim); group. Any topic in ≥2 phases is a Cross-Phase Theme (high-confidence signal).

```
Cross-Phase Themes (recurring in 2+ phases):
- <theme>: appeared in Phase <N>, Phase <N> — <brief description>
(none — no topics recurred across phases)
```

Note in PLAN.md `Cross-phase themes` section.

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

Invoke `AskUserQuestion` with the 5.1 rich plan review summary as context (the summary must already be visible in the preceding agent message) and the following gate question. Four options — interrogate and user-challenge overrides collapse into the built-in `Other` free-text mechanism.

`question` (use verbatim):
```
Plan review complete. Approve as-is, approve with overrides, revise a phase, or reject?
```

Options (4 — keep this order so the Recommended label sticks to Approve):

1. **Approve as-is (Recommended)** — accept every taste decision and proceed to Phase 6.
2. **Approve with overrides** — specify which taste decisions to flip or which user-challenge responses to apply (use `Other` to list the overrides).
3. **Revise — re-run a phase** — pick one phase to re-run (Scope → Phase 1; Design → Phase 2; Test/architecture → Phase 3; DX → Phase 4). If the user picks this, follow up with a second `AskUserQuestion` asking which phase to re-run.
4. **Reject** — start over from Phase 0 (clears all phase state).

**Handling:**
- **Approve as-is:** proceed to Phase 6.
- **Approve with overrides:** apply overrides (parse the `Other` free-text or the notes field); re-present the 5.1 summary with changes noted; re-offer the gate.
- **Other (no option picked, free-text only):** treat as Interrogate — answer the user's question fully, then re-present the 5.1 summary and re-offer the gate.
- **Revise:** ask the follow-up phase-selection question, re-run affected phases with updated scope; increment cycle counter; after 3 cycles proceed to Phase 6 with a warning block at the top of PLAN.md.
- **Reject:** clear all phase-level state and reset to Phase 0.

---

## Execution mode branches

| Mode | Phase 2 | Phase 4 | Dual voice | Mandatory outputs | Deferred scope | auto_decide |
|------|---------|---------|------------|-------------------|----------------|-------------|
| `light` | skip | skip | single-voice | single-voice versions | collected | premise+challenge still gated |
| `standard` | ui_scope gate | dx_scope gate | required | full dual-voice checklists | collected | CEO→SELECTIVE EXPANSION, DX→DX POLISH |

- **light**: Phases 0, 1, 3, 5, 6 with single-voice reasoning. Mandatory checklists still apply (single-voice versions). Gate options A-E available; summary simplified (no per-phase voice consensus scores).
- **standard**: default. Full pipeline.

Both modes: Phase 1 premise gate and Phase 5.3 User Challenges never auto-decided (except spawned mode auto-resolves premise gate).
