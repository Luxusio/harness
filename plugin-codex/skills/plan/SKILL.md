---
name: plan
description: Harness 7-phase review pipeline that writes PLAN.md and related task contract artefacts via the CLI. Codex variant runs single-voice (Claude-only dual-voice fan-out deferred to v2).
---

# GENERATED-CANDIDATE — hand-ported v1.5 spike from plugin/skills/plan/SKILL.md (298L source).
# Source canonical at plugin/skills/plan/SKILL.md. Sync engine output for AC-005 will replace.
# Lives here only to measure porting friction for AC-003 of TASK__dual-runtime-v1.5-spike-and-sync.


Codex-variant 7-phase review pipeline. Runs structured review across CEO, Engineering, and DX lenses (Design lens optional); single-voice on Codex (dual-voice Agent fan-out remains Claude-only); classifies every decision; surfaces only contested items to the user; writes the final task contract through the protected-artifact CLI.

> **Codex runtime notes** (delta from Claude):
> - **Dual Voice is degraded to single voice** on Codex v1.5. Claude's invariant "Phases 1-4 spawn Voice A and Voice B via Agent" cannot apply — Codex has no Agent fan-out tool. The orchestrator runs one critical-reviewer pass per phase instead of two independent voices. Cross-model adversariality is lost; flag this in PLAN.md's Review Status section. Use `claude $/harness:plan <task>` for dual-voice fidelity on high-stakes plans.
> - **Sub-skills are inlined, not invoked.** Claude's `Skill("harness:plan-ceo-review", task_id)` chain has no Codex equivalent. The orchestrator reads each sub-skill's SKILL.md content inline and executes the methodology in the same conversation. Sub-skill ports are AC-005 sync-engine scope (v2 in v1.5 → uses Claude-side sub-skill files at `plugin/skills/plan-*-review/SKILL.md`).
> - **AskUserQuestion = conversational ask.** Three mandatory user-gates remain: Phase 1.1 premise gate, Phase 5.3 User Challenge gate, Phase 5.4.1 final approval. Each becomes "ask the user X with options A/B/C; read the reply" prose. Same content, no structured envelope.
> - **`${CLAUDE_PLUGIN_ROOT}` → `${HARNESS_PLUGIN_ROOT}`** for all bash invocations of write_plan_artifact.py / update_checks.py.
> - **MCP tool names** bare (`task_start`, `task_context`) — NOT prefixed `mcp__harness__*` form. Where the Claude source mentions a prefixed name, read it as bare.

## Sub-files

This skill is split across four sub-files (Claude tree until AC-005 ports them):

| File | Content |
|------|---------|
| `intake.md` | Phase 0 (spawned detection, session recovery, task pack read, git context, base branch, scope detection, execution-mode branch) |
| `review-phases.md` | Phases 1-4 (review template + per-lens dimensions, checklists, degradation matrix) |
| `decision-principles.md` | 6 Decision Principles, classification, auto-decide rules, completion status, repo ownership, ask format |
| `write-artifacts.md` | Phase 6 (PLAN.md / PLAN.meta.json / CHECKS.yaml assembly + CLI writes, learnings, close) |

Phase 5 (user-facing gate) stays inline below. Read sub-files from `plugin/skills/plan/<file>` in the Claude tree until v2 ports them.

---

## Invariants (Codex variant)

- **Single Voice** by default on Codex v1.5. Where the Claude source says "Voice A + Voice B via Agent", the Codex orchestrator runs ONE critical-reviewer pass and notes the degradation in PLAN.md `Review Status`. The degradation matrix at `review-phases.md` § Degradation matrix has a `single-voice` row that's the default here.
- **Premise gate mandatory.** Phase 1.1 emits one conversational ask before Phase 5. Premises are never auto-decided (except spawned mode).
- **Never-auto decisions.** User Challenge items get their own ask at Phase 5.3.
- **Write via CLI only.** PLAN.md, PLAN.meta.json, CHECKS.yaml, AUDIT_TRAIL.md go through `python3 ${HARNESS_PLUGIN_ROOT}/scripts/write_plan_artifact.py --artifact ...`. Never Write/Edit directly. CHECKS.yaml post-plan mutations use `update_checks.py` only.
- **Workflow-lock awareness.** Trusts coordinator; no redundant check.
- **Read actual code.** Review phases MUST read source files, diffs, and referenced code. Reasoning from plan text alone is insufficient.
- **Never abort.** Single-voice surfaces findings as findings; never silently redirects. Blocked is terminal only for premise gate refusal.
- **Auto-decide mode.** When active, resolves intermediate asks except premise gate and User Challenge items via the 6 Decision Principles.
- **Spawned session.** `spawned_session: true` or `HARNESS_SPAWNED=1` → force auto-decide, auto-resolve ALL asks (including premise gate), emit prose completion instead of waiting.
- **Sequential execution.** 0 → 1 → 2 → 3 → 4 → 5 → 6. Never parallel.

## Voice

Plan-orchestrator voice: opinionated, concrete, builder-to-builder.

- Lead with the point. Say what the phase did, what it found, what changes downstream.
- Be concrete. Name files, functions, line numbers, AC ids, premise indices, decision principles.
- Tie technical choices to user outcomes.
- Be direct about quality. Bugs in the plan matter more than bugs in the implementation.
- Sound like a builder talking to a builder, not a consultant.
- No em dashes. No AI vocabulary: `delve`, `crucial`, `robust`, `comprehensive`, `nuanced`, `multifaceted`, `furthermore`, `moreover`, `additionally`, `pivotal`, `landscape`, `tapestry`, `underscore`, `foster`, `showcase`, `intricate`, `vibrant`, `fundamental`, `significant`.
- Korean/English bilingual context: technical terms stay English, explanations may use Korean.
- The user has context you do not. The user decides at premise gate (1.1) and User Challenge gate (5.3).

Good: "Phase 3 Eng. AC-004 verification command already passes pre-edit (grep hit at write-artifacts.md:140). EUREKA — re-scope AC-004 to a smaller addition. Surface in HANDOFF."
Bad: "I've completed the engineering review phase and identified some considerations regarding AC-004 that may warrant additional examination."

## Anti-shortcut clause

PLAN.md is the OUTPUT of the interactive review, not a substitute for it. Writing every finding into one PLAN.md write and signaling completion without asking the user at the premise gate, User Challenges, or final approval is the precise failure mode the May 2026 transcript bug surfaced. If you have ANY non-trivial finding, the path from finding to PLAN.md write goes THROUGH an interactive user-ask. Zero-finding phases are the only path that bypasses interactive surfacing.

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

Each review phase reads its corresponding sub-skill from the Claude tree (until AC-005 ports them) and executes the methodology inline:

- Phase 1 → `plugin/skills/plan-ceo-review/SKILL.md` (read + execute inline)
- Phase 2 → `plugin/skills/plan-design-review/SKILL.md` (only if ui_scope=true)
- Phase 3 → `plugin/skills/plan-eng-review/SKILL.md`
- Phase 4 → `plugin/skills/plan-devex-review/SKILL.md` (only if dx_scope=true)

These sub-skills are heavy dual-voice review pipelines on the Claude side. On Codex v1.5 the orchestrator runs them single-voice — same dimensions, same outputs, one reviewer instead of two. Surface this in the Phase N consensus row of AUDIT_TRAIL.md.

---

## PLAN_SESSION.json lifecycle

Open at Phase 0; update through Phase 6. (Same as Claude — runtime-agnostic.)

| State | Phase | Condition |
|-------|-------|-----------|
| `context_open` | 0-5 | Set at Phase 0 start |
| `write_open` | 6 | At Phase 6 start before any CLI write |
| `closed` | post-6 | After all CLI writes complete |

Required: `{"state": "...", "phase": "...", "source": "plan-skill"}`. Mirror `plan_session_state` in TASK_STATE.yaml.

---

## Single-Voice Protocol (Codex variant)

Phases 1-4 run ONE critical-reviewer pass per phase. The pass:
- Reads the phase brief (lens dimensions from `review-phases.md`).
- Produces findings per dimension.
- Classifies each finding (Mechanical / Taste / User Challenge).
- Appends a `single-voice` consensus row to AUDIT_TRAIL.md.

Compared to Claude's dual-voice: less cross-blind-spot detection, faster turnaround, no Voice A vs Voice B disagreement surfacing. For high-stakes plans, the user should run `claude $/harness:plan <task>` instead.

Full protocol, dimensions, checklists, and degradation matrix: `review-phases.md` (Claude tree). The Codex orchestrator uses the `single-voice` row of the degradation matrix as its default.

---

## Phase orchestration

1. **Phase 0** — `intake.md`. Always runs.
2. **Phase 1 — CEO Review** — `review-phases.md` § Phase 1. Always runs. Premise gate at 1.1 is mandatory user interaction.
3. **Phase 2 — Design Review** — `review-phases.md` § Phase 2. Only if `ui_scope=true` and not `execution_mode: light`.
4. **Phase 3 — Engineering Review** — always runs.
5. **Phase 4 — DX Review** — only if `dx_scope=true` and not `execution_mode: light`.
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
- [ ] Single-voice mode logged with reason for each phase (the reason on Codex v1.5 is "Codex variant — no Agent fan-out").

If missing after 2 retries, proceed to 5.1 with warning block:
```
⚠ Pre-Gate Warning: proceeding with incomplete phase outputs.
Missing: <list>
```

### 5.1 Plan approval summary (user-facing)

The user only needs two things at the gate: **what this plan will do** and **what is explicitly out of scope**. Internal review state (decision counts, taste classifications, cross-phase themes) is logged to `AUDIT_TRAIL.md` — never rendered here.

Emit:
```
## Plan Approval

### What this plan will do
[2-3 sentences in plain outcome language. Concrete: which files change, which behavior changes, what the user has at the end. No process counters. No phase voice scores.]

### Out of scope
[Bulleted list pulled from PLAN.md "NOT in scope".]
```

That is the entire user-facing summary.

**Hard guard.** Never render at this gate: the 4-rule body (`[Re-ground]/[Simplify]/[Recommend]/[Options]`), decision counts, taste tallies, cross-phase summaries. Those belong in PLAN.md / AUDIT_TRAIL.md only.

### 5.1.1 Collect all decisions

From single-voice consensus rows across Phases 1-4: Mechanical (silently applied), Taste, User Challenge.

### 5.2 Log Taste decisions to AUDIT_TRAIL (no gate render)

Same as Claude — taste decisions go to AUDIT_TRAIL only.

```
Auto-decided (Taste):
- [item]: chose [option] over [option] because [principle applied]
```

### 5.2.5 Log Cross-Phase Themes to AUDIT_TRAIL (no gate render)

Same as Claude — cross-phase theme detection writes to AUDIT_TRAIL + PLAN.md `Cross-phase themes` section.

### 5.3 User Challenge gate

Cognitive load:
- **0 challenges:** skip entirely, go to 5.4.
- **1-7 challenges:** ask one at a time, in order. Do not batch into one turn.
- **8+ challenges:** warning at top, group by phase.

Per-challenge ask format (conversational):

> **User Challenge: <item title>**
>
> Your stated direction: <from REQUEST.md or TASK_STATE.yaml>
> Reviewer recommends: <alternative>
> Reasoning: <why the reviewer disagrees>
> Blind spots: <what the reviewer may miss about your context>
> Downside cost of proceeding as stated: <concrete estimate>
>
> A) Accept the recommendation (Recommended) — switch to the alternative.
> B) Keep my original direction — known risk acknowledged.
> C) Modify — describe in your next reply.

Reply parsing: A/B follow the option; any free-text reply treats as Modify.

**Note on single-voice User Challenges:** in Claude's dual-voice flow, a User Challenge fires only when BOTH voices agree the user is wrong. Single-voice has weaker signal — Codex's single reviewer may flag User Challenges that dual-voice would resolve via Voice A vs Voice B disagreement. Err on the side of fewer challenges; if uncertain, classify as Taste.

### 5.4 Final scope confirmation

If 5.3 responses changed scope, confirm updated scope before Phase 6.

### 5.4.1 Gate response options

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

## Execution mode branches

| Mode | Phase 2 | Phase 4 | Voices | Mandatory outputs | auto_decide |
|------|---------|---------|--------|-------------------|-------------|
| `light` | skip | skip | single | single-voice versions | premise+challenge still gated |
| `standard` (Codex default) | ui_scope gate | dx_scope gate | single (Codex variant) | full checklists | CEO→SELECTIVE EXPANSION, DX→DX POLISH |

On Codex v1.5, `standard` and `light` differ only in whether Phase 2/4 run at all. Voice count is single in both — the only way to get dual-voice on this repo is to run plan from Claude.

Both modes: Phase 1 premise gate and Phase 5.3 User Challenges never auto-decided (except spawned mode).

---

## Important Rules

- **Never abort.** Surface every decision; never silently redirect to a shorter path.
- **Durable Docs Decision.** Every PLAN.md includes:
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
- **Two gates.** The non-auto-decided asks are: (1) premise confirmation in Phase 1.1, and (2) User Challenges in Phase 5.3.
- **Log every decision.** Every classification (Mechanical / Taste / User Challenge) gets a row in `AUDIT_TRAIL.md` via `write_plan_artifact.py --artifact audit`.
- **Full depth means full depth.** Complete every loaded methodology section with its required evidence and decisions.
- **Artifacts are deliverables.** PLAN.md, PLAN.meta.json, CHECKS.yaml, AUDIT_TRAIL.md must exist on disk before Phase 6 closes the session.
- **Sequential order.** Phase 0 → 1 → 2 → 3 → 4 → 5 → 6.

---

# v1.5 spike measurements (this port — captured during hand-port for spike-report.md)

| Category | Source lines | Result | % |
|---|---|---|---|
| As-is portable (voice rules, anti-shortcut clause, confusion protocol, context health, completeness, decision-logging rules, gate cognitive-load rules, mode branches, important-rules capstone) | ~120 | reused verbatim or near-verbatim | 40% |
| Trivial rewrite (`${CLAUDE_PLUGIN_ROOT}` → `${HARNESS_PLUGIN_ROOT}`, MCP de-prefix, frontmatter prune) | ~15 | line-for-line | 5% |
| Significant restructure (Dual Voice → Single Voice degradation throughout: invariants rewritten, dual-voice protocol section replaced with single-voice protocol, sub-skill execution model from `Skill()` chain to inline-read, 3 AskUserQuestion sites → conversational asks with same content) | ~115 | semantically equivalent for single-voice; dual-voice fidelity lost | 39% |
| Dropped (Plan Mode Safe Operations section — Codex has no plan-mode concept; the `Plan Status Footer` boilerplate that was redundant with `[PROGRESS]` summaries; some Claude-specific commentary on "AskUserQuestion satisfies plan mode's end-of-turn requirement") | ~30 | not represented | 10% |
| Codex-additive (runtime notes header, single-voice protocol section, "use claude $/harness:plan for dual-voice" callout, single-voice classification note in §5.3) | ~25 | new content | 8% |
| Total source | 298 | ~308 emitted | — |

Key port observations:
- **Single Voice = the load-bearing degradation.** Every reference to Voice A/B/Agent across the file (4+ sites) needed restructuring. The plan-* review sub-skills (4014L total in Claude source) are 100% dual-voice — they don't port at all to single voice without rewrite. v1.5 reads them inline from Claude tree but the dual-voice methodology gets executed as single-voice. Quality delta is real and surfaced.
- **3 mandatory ask gates** (1.1 premise / 5.3 challenge / 5.4.1 approval) all ported to conversational form. Content identical, envelope different.
- **4 Skill() chain calls to sub-skills** → "read sub-skill SKILL.md inline" prose. Functional but adds wordcount.
- **Plan Mode references** dropped entirely (Codex has no plan-mode concept).
- The `Important Rules` capstone shrank by 1 bullet (the "Never abort" line stayed; the "Two gates" line stayed; specific Voice A/B language was paraphrased to "reviewer" singular).

Conclusion for AC-004: plan port is **45% reuse (as-is + trivial) + 39% restructure + 10% drop**. Highest restructure% of the three (vs setup 19%, run 29%). Confirms what Phase 3 v1 Eng Voice B argued: text-substitution canonical form ROT-BLIND on Agent/Skill control-flow primitives. Strong vote for **YAML/JSON intermediate** canonical with declarative `voices: [a, b]` / `voices: [single]` field that the sync engine renders runtime-appropriately.

For sync engine MVP (AC-005), this implies:
- Canonical schema needs `voices_required: <int>` field per phase (Claude renders dual, Codex renders single).
- `sub_skills:` field listed declaratively (Claude renders `Skill()` chain, Codex renders "read inline" prose).
- `interactions:` field with `{type: "ask", content: "...", options: [...]}` rows (Claude renders AskUserQuestion call, Codex renders conversational ask prose).
- Pure prose sections (voice rules, capstones) ride through both runtimes verbatim.

That structure aligns with Phase 3 v1 Voice B option #2 (YAML intermediate). AC-004 decision will codify this.
