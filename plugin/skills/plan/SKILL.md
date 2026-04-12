---
name: plan
description: Harness-native 7-phase dual-voice review pipeline that writes PLAN.md and related task contract artefacts via the CLI.
argument-hint: <task-slug>
user-invocable: true
allowed-tools: Read, Glob, Grep, Bash, AskUserQuestion, Agent, mcp__plugin_harness_harness__task_start, mcp__plugin_harness_harness__task_context
---

This skill implements the harness-native 7-phase dual-voice review pipeline for task planning. It runs structured review across CEO, Design, Engineering, and DX lenses, builds adversarial consensus via two independent voices, classifies every decision, surfaces only contested items to the user, and writes the final task contract through the protected-artifact CLI. The old 9-step linear procedure is retired.

## Invariants

- **Dual Voice required.** Every review phase (1-4) spawns Voice A and Voice B via the Agent tool. Single-voice review is prohibited; degradation matrix applies when a voice fails.
- **Premise gate is mandatory.** Phase 1.1 emits exactly one `AskUserQuestion` before Phase 5. Premises are never auto-decided.
- **Never-auto decisions.** User Challenge items are never auto-decided. Each gets its own `AskUserQuestion` at Phase 5.3.
- **Write via CLI only.** PLAN.md, PLAN.meta.json, CHECKS.yaml, and AUDIT_TRAIL.md are written exclusively through `python3 plugin-legacy/scripts/write_artifact.py plan --artifact ...`. Never call Write/Edit on these artefacts directly.
- **Zero browser-flag participation.** This skill does not read, write, enforce, or inspect the browser verification flag in TASK_STATE.yaml. Treat it as opaque metadata owned by critic-runtime. See `REQ__process__browser-required-enforcement.md`.
- **Workflow-lock refusal.** If the task pack shows `workflow_locked: true` AND `maintenance_task: false` AND the proposed plan touches paths under `plugin/**`, `plugin-legacy/**`, `doc/harness/manifest.yaml`, or setup templates, refuse to write PLAN.md with a concrete message citing the flag.
- **Read actual code.** Each review phase MUST read actual source files, diffs, and code referenced by the plan. Reasoning from memory or plan text alone is insufficient. If a section asks for a dependency graph, ASCII diagram, or code map — read the files first.
- **Never abort.** The pipeline does not abort. If both voices fail (blocked), surface the gap as a finding and continue. Blocked is never a terminal state except for premise gate refusal. Surface all taste decisions; never silently redirect to a shorter path. When auto_decide is active, never redirect to interactive review. Surface all taste decisions at the Phase 5 gate.
- **Auto-decide mode.** When `auto_decide` is active, intermediate AskUserQuestion calls (except premise gate and User Challenge items) are resolved by the 6 Decision Principles. Two gates are never auto-decided: (a) premise confirmation (Phase 1.1) and (b) User Challenge items (Phase 5.3). Auto-decide replaces the USER's judgment on taste items, but does NOT reduce analysis depth.
- **Sequential execution required.** Phases execute in strict order: 0 → 1 → 2 → 3 → 4 → 4.5 → 5 → 5.5 → 6 → 7. NEVER run phases in parallel. Each phase must complete fully before the next begins.

---

## AskUserQuestion Format

Every `AskUserQuestion` emitted by this skill MUST begin with a one-line context header as the very first line of the question text:

```
Task: TASK__<id> | Phase: <current phase number> | Step: <step name>
```

This ensures the user can orient themselves after a long analysis block.

**Format:**
```
Task: TASK__<id> | Phase: <1/1.1/2/3/4/5/5.3> | Step: <descriptive name>

<question body>

A) ...
B) ...
```

**Applies to:** all AskUserQuestion calls including premise gate (Phase 1.1), prerequisite offer (Phase 0.4.5), User Challenge items (Phase 5.3), and gate options (Phase 5.4.1).

Do NOT add lengthy recap of prior phases. The one-line header is sufficient orientation.

**Completeness scoring (required for every option):**

Each option MUST include a `Completeness: X/10` score:
- **10** — Complete implementation: all edge cases, full coverage, no follow-up needed
- **7** — Happy path: covers the main flow but skips some edges
- **3** — Shortcut: defers significant work

If both options are 8+, recommend the higher one. If one option is ≤5, flag it explicitly.

When an option involves significant effort, show both effort scales:
`(human: ~X days / plan-skill: ~Y min)`

**Effort reference:**

| Task type | Human team | Plan-skill | Compression |
|-----------|-----------|------------|-------------|
| Boilerplate | 2 days | 15 min | ~100× |
| Tests | 1 day | 15 min | ~50× |
| Feature | 1 week | 30 min | ~30× |
| Bug fix | 4 hours | 15 min | ~20× |

---

## Sub-skill execution protocol

Each review phase MUST load the corresponding sub-skill file from disk before running:
- Phase 1 → `plugin/skills/plan-ceo-review/SKILL.md`
- Phase 2 → `plugin/skills/plan-design-review/SKILL.md` (only if ui_scope=true)
- Phase 3 → `plugin/skills/plan-eng-review/SKILL.md`
- Phase 4 → `plugin/skills/plan-devex-review/SKILL.md` (only if dx_scope=true)

Iterate every non-skipped section at full depth. Follow the section's required outputs exactly.

**Skip list** (these sections are already handled by this pipeline — do not re-run them):
- Preamble / boilerplate sections
- AskUserQuestion Format
- Completeness Principle — Boil the Lake
- Telemetry / analytics
- Platform detection (handled by Phase 0)
- Prerequisite Skill Offer / BENEFITS_FROM sections (handled by Phase 0.4.5)
- Outside Voice / Design Outside Voices (handled by Phase 4.5)

**Compression brake:** If any review section produces fewer than 3 sentences of analysis, it is compression and must be expanded before moving on. "No issues found" is a valid result only after stating what was examined and why nothing was flagged (minimum 1-2 sentences). "Skipped" is never valid for a non-skip-listed section.

---

## PLAN_SESSION.json lifecycle

Open `PLAN_SESSION.json` in the task directory at the start of Phase 0. Keep it updated through Phase 6.

| State | Phase | Condition |
|-------|-------|-----------|
| `context_open` | 0 through 5 | Set at Phase 0 start; maintained through all review phases |
| `write_open` | 6 | Transition at Phase 6 start before any CLI write |
| `closed` | post-6 | Set after all CLI writes complete |

Required fields:
```json
{
  "state": "context_open",
  "phase": "context",
  "source": "plan-skill"
}
```

The `source` field must be `plan-skill` at all times. The CLI (`write_artifact.py plan`) validates this field and rejects writes if `source` is missing or mismatched.

Also set `plan_session_state` in `TASK_STATE.yaml` to mirror the session state:
- Phase 0 start: `plan_session_state: context_open`
- Phase 6 start: `plan_session_state: write_open`
- After Phase 6 complete: `plan_session_state: closed`

---

## Dual Voice Protocol

Each review phase (1-4) spawns two independent Agent subagents:

**Voice A** — Independent review at full depth. No prior-phase context is provided. Prompt includes only the task pack, plan draft, and the phase-specific review brief. Returns structured findings: severity, finding, fix.

**Voice B** — Same prompt as Voice A, with a `## Prior phase findings` section appended. The section contains a terse bullet summary from consensus tables of earlier phases. Exception: Phase 2 (Design) keeps both voices fully independent to prevent aesthetic anchoring.

Both voices must return structured responses before consensus is built. The skill parses each voice's findings into the consensus table, then appends it to `AUDIT_TRAIL.md` via the audit CLI.

### Degradation matrix

| Condition | Mode | Action |
|-----------|------|--------|
| Both voices return findings | `dual-voice` (nominal) | Build consensus table normally |
| One voice fails or times out | `single-voice` (degraded) | Log failure reason to `AUDIT_TRAIL.md` with `mode=single-voice`; continue with available voice |
| Both voices fail | `blocked` | Stop phase; emit `AskUserQuestion` with blocked status and failure details before proceeding |

**Phase-scoped vs whole-plan fresh reviews:** Voice A/B operate within a single review phase (phase-scoped independence). Phase 4.5 Outside Voice and Phase 5.5 Spec Review Loop operate on the full assembled plan (whole-plan-scoped fresh-context review). Use them in addition to, not instead of, phase Voice A/B.

---

## What Auto-Decide Means

When `auto_decide` is active (set in the task pack or passed as a flag), the pipeline runs the full 7-phase review without intermediate user interaction — except for the two non-auto-decided gates listed below.

**MUST:**
- Resolve every Mechanical and Taste decision using the 6 Decision Principles (first applicable principle wins).
- Log every auto-decided item to AUDIT_TRAIL.md immediately, one row per decision.
- Surface all auto-decided Taste items at Phase 5.2 for informational review.
- Default CEO mode to SELECTIVE EXPANSION (Phase 1).
- Default DX mode to DX POLISH (Phase 4).
- Complete all mandatory phase outputs at full depth — auto-decide replaces judgment, not analysis.

**MUST NOT:**
- Auto-decide premise confirmation (Phase 1.1). This gate always requires user input.
- Auto-decide User Challenge items (Phase 5.3). Each gets its own AskUserQuestion.
- Reduce Voice A/B depth or skip any mandatory output checklist item.
- Redirect to interactive review mid-pipeline. All decisions accumulate and surface at Phase 5.

The two gates that are **never auto-decided**:
1. **Phase 1.1 — Premise confirmation.** Premises are always presented to the user before review begins.
2. **Phase 5.3 — User Challenge gate.** Items where both voices recommend changing a user-stated direction are always surfaced individually.

---

## The 6 Decision Principles

Applied to every contested item between Voice A and Voice B. First applicable principle wins.

| Code | Name | Rule |
|------|------|------|
| P1 | User Sovereignty | If the user has explicitly stated a direction, honour it. Disagreement is surfaced as context, not a block. |
| P2 | Scope Stability | Prefer the narrower scope when both approaches are viable. Expansion requires explicit user approval. |
| P3 | Safety First | If one option reduces risk of data loss, irreversibility, or security exposure, prefer it. |
| P4 | Reversibility | Prefer the option that is easier to undo or roll back. |
| P5 | Evidence Weight | Prefer the position with more concrete supporting evidence (test data, measured latency, prior precedent). |
| P6 | Craft Standard | When all else is equal, prefer the approach that is more maintainable, readable, or testable. |

**Per-phase conflict-resolution priority:**
- Phase 1 (CEO): P1 + P2 (user direction and scope stability dominate)
- Phase 2 (Design): P5 + P1 (evidence-backed design choices, then user direction)
- Phase 3 (Engineering): P5 + P3 (evidence-backed technical choices, then safety)
- Phase 4 (DX): P5 + P3 (same as Engineering for API and tooling decisions)

---

## Completion Status Protocol

When completing the skill workflow, report status using exactly one of:

- **DONE** — All steps completed successfully. Evidence provided for each claim.
- **DONE_WITH_CONCERNS** — Completed, but with issues the user should know. List each concern.
- **BLOCKED** — Cannot proceed. Use this format:
  ```
  STATUS: BLOCKED
  REASON: [1-2 sentences]
  ATTEMPTED: [what was tried]
  RECOMMENDATION: [what the user should do next]
  ```
- **NEEDS_CONTEXT** — Missing information required to continue:
  ```
  STATUS: NEEDS_CONTEXT
  MISSING: [exactly what information is needed]
  IMPACT: [what is blocked until this is provided]
  ```

**Escalation rule:** If any phase has been attempted 3 times without success, STOP and emit
`STATUS: BLOCKED`. Bad work is worse than no work.

---

## Repo Ownership — See Something, Say Something

When reading source files during review phases, you may encounter issues outside the current
task's scope (other files, other features, existing bugs).

`REPO_MODE` determines how to handle these:

- **`solo`** — You own everything in this repo. Investigate proactively and offer to fix.
  Flag via one-sentence note: what you noticed and its impact.
- **`collaborative`** — Other developers may own adjacent code. Flag via AskUserQuestion
  but do NOT fix without explicit user approval. May be someone else's work.
- **`unknown`** (default) — Treat as `collaborative`. Flag but do not fix.

Detect repo mode from task pack or TASK_STATE.yaml (`repo_mode` field). If absent: `unknown`.

Always flag anything that looks wrong — even in `collaborative` mode. One sentence,
what you noticed and its potential impact. Never silently ignore a visible defect.

---

## Decision Classification

Every contested item between Voice A and Voice B is classified before it is acted on.

**Mechanical** — Objectively correct answer exists (wrong import, broken reference, missing required field). Auto-decide silently. Append one audit row per item.

**Taste** — Two reasonable approaches with different tradeoffs (naming, structure, sequencing). Auto-decide using the applicable principles. Surface at Phase 5.2 for user awareness.

**User Challenge** — Both voices independently recommend changing a direction the user has stated. Never auto-decide. Surface at Phase 5.3 with full framing: the user's stated direction, the dual-model recommendation, reasoning, identified blind spots, and estimated downside cost of proceeding as stated.

If voices disagree on the classification itself, escalate to the higher tier (e.g. Taste vs. User Challenge escalates to User Challenge).

**adversarial** — Rows generated by Phase 4.5 Outside Voice or Phase 5.5 Spec Review Loop fresh-context reviewers. All surfaced at Phase 5 user gate for informational review; none are auto-applied. User sovereignty: the user decides whether to accept any adversarial finding.

---

## Protected-artefact CLI usage

All plan artefact writes go through the CLI. The session must be in `write_open` state before any of the following commands are run.

Write PLAN.md:
```bash
python3 plugin-legacy/scripts/write_artifact.py plan --artifact plan \
  --task-dir doc/harness/tasks/TASK__<id>/ \
  --input /tmp/plan_content.md
```

Write PLAN.meta.json:
```bash
python3 plugin-legacy/scripts/write_artifact.py plan --artifact plan-meta \
  --task-dir doc/harness/tasks/TASK__<id>/ \
  --input /tmp/plan_meta.json
```

Write CHECKS.yaml:
```bash
python3 plugin-legacy/scripts/write_artifact.py plan --artifact checks \
  --task-dir doc/harness/tasks/TASK__<id>/ \
  --checks /tmp/checks_content.yaml
```

Append to AUDIT_TRAIL.md:
```bash
python3 plugin-legacy/scripts/write_artifact.py plan --artifact audit \
  --task-dir doc/harness/tasks/TASK__<id>/ \
  --input /tmp/audit_row.txt \
  --append
```

Audit row format (7 pipe-delimited columns):
```
# | phase | decision | classification | principle | rationale | rejected_option
```

---

## Context Recovery

**Always runs at skill invocation start, before Phase 0.0.**

Check for prior sessions by reading the task's AUDIT_TRAIL.md phase-summary rows:

```bash
if [ -f "doc/harness/tasks/TASK__<id>/AUDIT_TRAIL.md" ]; then
  grep "phase-summary" doc/harness/tasks/TASK__<id>/AUDIT_TRAIL.md | tail -5
fi
```

If prior phase-summary rows are found, emit a one-paragraph welcome briefing:

```
Resuming TASK__<id>. Last completed phase: <N>. [brief summary from last phase-summary row].
```

If AUDIT_TRAIL.md is absent: proceed silently, no message.

This section is informational only. It never blocks.

---

## Phase 0.0: Session Recovery

**Runs only when resuming an interrupted plan session.**

Check for prior phase progress in the task directory:

```bash
if [ -f "doc/harness/tasks/TASK__<id>/AUDIT_TRAIL.md" ]; then
  grep "phase-summary" doc/harness/tasks/TASK__<id>/AUDIT_TRAIL.md | tail -10
fi
```

If AUDIT_TRAIL.md contains `phase-summary` rows:
1. Extract the highest completed phase number from the JSON rows (e.g. `"phase":"3"` → last completed = Phase 3).
2. Emit a one-line recovery notice:
   ```
   Resuming TASK__<id>: last completed phase = <N>. Continuing from Phase <N+1>.
   ```
3. Load prior consensus summaries from the audit rows for use as `## Prior phase findings` in remaining phases.
4. Skip all phases ≤ <N>. Do not re-run completed phases.

If AUDIT_TRAIL.md is absent, empty, or contains no `phase-summary` rows, proceed from Phase 0 normally (fresh start).

If AUDIT_TRAIL.md is unreadable, log the error inline and proceed from Phase 0:
```
Note: AUDIT_TRAIL.md unreadable (<reason>) — starting fresh.
```

**This phase is informational only. It never blocks.**

---

## Phase 0: Intake + Context

**Always runs.**

### 0.1 Open session

Write `PLAN_SESSION.json` in the task directory:
```json
{"state": "context_open", "phase": "context", "source": "plan-skill"}
```

Set `plan_session_state: context_open` in `TASK_STATE.yaml` via Bash.

### 0.1.5 Load project learnings

Check for a project learnings file:
```bash
if [ -f ".harness/learnings.jsonl" ]; then
  tail -5 .harness/learnings.jsonl
fi
```

If the file exists and has entries, read the last 5 and incorporate any relevant operational
knowledge into the plan context (e.g. known build quirks, deferred scope patterns, env var
requirements). Log the count:
```
LEARNINGS: <N> entries loaded
```
If the file does not exist: `LEARNINGS: 0` — proceed normally.

### 0.2 Run task_start

```
mcp__plugin_harness_harness__task_start { task_id: "<ARGUMENTS>" }
```

Extract from task pack: `risk_level`, `planning_mode`, `compat.execution_mode`, `workflow_locked`, `maintenance_task`, `ui_scope`, `dx_scope`, `must_read`.

### 0.3 Workflow-lock refusal check

If `workflow_locked: true` AND `maintenance_task: false`:
- Inspect the proposed scope. If any target path is under `plugin/**`, `plugin-legacy/**`, `doc/harness/manifest.yaml`, or setup templates:
  - Refuse: "Cannot write PLAN.md. Task pack shows `workflow_locked: true` and `maintenance_task: false`. This scope touches protected workflow surfaces. Set `maintenance_task: true` in TASK_STATE.yaml or obtain explicit workflow-lock clearance before planning."
  - Stop. Do not proceed to Phase 1.

### 0.4 Read task pack

Read in this order: `TASK_STATE.yaml`, `REQUEST.md` (if exists), existing `PLAN.md` (if exists), files listed in `must_read`.

### 0.4.5 Prerequisite offer

After reading the task pack, check whether the request is sufficiently scoped.

**Trigger condition:** REQUEST.md is absent OR REQUEST.md exists but contains fewer than 15 non-empty lines.

**If triggered:**

1. Check whether an `office-hours` skill is available (look for `plugin/skills/office-hours/SKILL.md` or equivalent).

2. **If `office-hours` is available**, emit one `AskUserQuestion`:
   ```
   The request appears brief (under 15 lines). A scope-sharpening prerequisite is available.

   A) Run the office-hours prerequisite to sharpen scope before planning
   B) Skip — proceed directly to planning with the current request
   C) Clarify inline — I'll answer 3 goal-sharpening questions now
   ```
   - If A: invoke the office-hours skill, then resume from Phase 0.5 with the output.
   - If B: proceed to Phase 0.5 immediately.
   - If C: ask 3 inline goal-sharpening questions in a single AskUserQuestion, then proceed.

3. **If `office-hours` is not available**, emit one `AskUserQuestion` with 3 goal-sharpening questions:
   ```
   The request appears brief. Please answer these 3 questions to sharpen scope:
   1. What is the single most important outcome this plan should deliver?
   2. What is explicitly NOT in scope (name at least one thing)?
   3. What does success look like at the end of the first implementation step?
   ```
   Proceed after user responds.

**Office-hours inline invocation (when user selects A):**
1. Read `plugin/skills/office-hours/SKILL.md` using the Read tool.
2. If the file is unreadable (missing, permission error), skip with message: "office-hours skill unavailable — proceeding to inline questions" and fall back to option C (3 inline goal-sharpening questions).
3. If readable, execute the office-hours skill at full depth, skipping preamble and boilerplate sections (same skip list as sub-skill execution protocol above).
4. On completion, search for a design document written during the office-hours session:

```bash
find doc/ -name "*design*.md" -newer doc/harness/tasks/TASK__<id>/TASK_STATE.yaml \
  2>/dev/null | head -3
ls doc/harness/tasks/TASK__<id>/*design*.md 2>/dev/null | head -3
```

If a file is found:
- Read it in full.
- Append its content as `## Design Context` to the task pack used in Phases 1-4.
- Log discovery in `AUDIT_TRAIL.md` via the audit CLI:
  ```
  | <#> | 0.4.5 | design-doc-found | log | - | <path> | - |
  ```

If no file is found, proceed without design context (same as before). Do not re-offer the prerequisite gate.

**Skip cleanly** if the trigger condition is not met — never hard-gate. Do not loop or re-ask after one response.

### 0.5 Restore point

If a prior `PLAN.md` exists in the task directory, capture a restore point:
```bash
_TS=$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p doc/harness/tasks/TASK__<id>/restore-points/
cp doc/harness/tasks/TASK__<id>/PLAN.md \
   doc/harness/tasks/TASK__<id>/restore-points/pre-plan-${_TS}.md
```

After copying, append a `## Re-run Instructions` section to the restore file:
```bash
cat >> doc/harness/tasks/TASK__<id>/restore-points/pre-plan-${_TS}.md << 'EOF'

## Re-run Instructions

To restore this plan: copy this file back over PLAN.md in the task directory.
Then re-run the plan skill with the same task slug to resume from this state.
The restore point was captured at: ${_TS}
EOF
```

Record the relative restore point path (e.g. `restore-points/pre-plan-${_TS}.md`) in memory for use in Phase 6.2.

### 0.6 Scope detection

Read the task pack text (TASK_STATE.yaml + REQUEST.md) and scan for keywords. Use the rules below — do NOT run grep bash commands for scope detection.

**UI scope keywords:** `ui_scope`, `frontend`, `component`, `css`, `html`, `react`, `vue`, `design system`, `stylesheet`, `layout`, `visual`, `button`, `modal`, `dashboard`, `sidebar`, `nav`, `dialog`

**DX scope keywords:** `dx_scope`, `api`, `cli`, `sdk`, `devex`, `developer experience`, `ergonomics`, `tooling`, `integration`, `plugin`, `endpoint`, `REST`, `GraphQL`, `gRPC`, `webhook`, `command`, `flag`, `argument`, `terminal`, `shell`, `library`, `package`, `npm`, `pip`, `import`, `require`, `developer docs`, `getting started`, `onboarding`, `debug`, `implement`, `error message`

**2+ match threshold:** Set `ui_scope=true` only if 2 or more distinct UI keywords appear in the task pack text. A single keyword match is insufficient. Apply the same 2+ match threshold for `dx_scope`.

**False-positive exclusions (do not count as matches):**
- `\bpage\b` alone (e.g. "page 3", "next page") — does not count as a UI keyword
- `\bUI\b` as a standalone acronym in a non-design context (e.g. "UI thread", "UI process") — does not count unless paired with design intent
- `\bapi\b` in "API keys" or "API credentials" context without developer-tool intent — does not count as DX
- `\bcli\b` in "CLI arguments" for a non-developer-facing tool — does not count unless the product exposes the CLI to developers

**Structural DX triggers (override threshold — set dx_scope=true immediately):**
- "product IS a developer tool" — any statement that the primary artifact is a CLI, SDK, plugin, or developer framework
- "AI agent is primary user" — any statement that the consumer of the output is an AI model or agent harness

If the task pack already sets `ui_scope: true` or `dx_scope: true`, honour that value without re-evaluation.

### 0.7 broad-build planning_mode branch

If `planning_mode == broad-build`, write the trio before Phase 1. These are task-local non-protected files — use Bash heredoc or Write tool directly (not via `write_artifact.py`):

```
doc/harness/tasks/TASK__<id>/01_product_spec.md     — product vision, user story, success criteria
doc/harness/tasks/TASK__<id>/02_design_language.md  — UI/UX patterns, component conventions, tokens
doc/harness/tasks/TASK__<id>/03_architecture.md     — system design, data flow, integration points
```

These three files become additional context for Phases 1-4.

### 0.8 Execution mode branch

Read `compat.execution_mode` from the task pack:

- **`light`**: Skip dual voices in Phases 1 and 3. Run single-voice reasoning block instead. Skip Phase 2 and Phase 4 entirely regardless of ui_scope/dx_scope. Produce narrow contract.
- **`standard`** (default): Full pipeline with dual voices. Phase 2 runs only if `ui_scope=true`. Phase 4 runs only if `dx_scope=true`.
- **`sprinted`**: Full pipeline with dual voices. After Phase 6, set `critic_plan: mandatory` in PLAN.meta.json so critic-plan FAIL becomes a hard block.

**Auto-decide detection:** Also check for `auto_decide: true` in the task pack (TASK_STATE.yaml) or as an explicit flag passed to the skill invocation. This is independent of `execution_mode` and may be combined with any mode.

If `auto_decide: true` is detected:
1. Set `auto_decide=true` in the local session context. Record in `PLAN_SESSION.json` as `"auto_decide": true`.
2. Apply auto-decide defaults: CEO mode defaults to SELECTIVE EXPANSION; DX mode defaults to DX POLISH.
3. Follow the rules in the "What Auto-Decide Means" section for all subsequent phases.
4. The interactive path remains the default — auto-decide only activates when the flag is explicitly set.

---

## Phase 1: CEO Review

**Always runs.**

Methodology: `plugin/skills/plan-ceo-review/SKILL.md`

### 1.1 Premise extraction and gate

Read the task pack and draft scope. Extract the top 3-5 premises the plan depends on (e.g. "users will adopt self-serve", "latency under 100ms is achievable with current infra", "scope is bounded to X").

Emit a single premise-confirmation AskUserQuestion (the one mandatory user interaction before Phase 5):

```
Premises for TASK__<id>:

1. <premise 1>
2. <premise 2>
3. <premise 3>

Do these premises hold?

A) Yes, all hold — proceed with review
B) Yes with caveats — (please describe)
C) No, these need revisiting — (please describe)
```

**Premises are never auto-decided.** Wait for user response before proceeding. If the user selects C or provides material caveats, update the task scope and re-extract premises before continuing.

### 1.2 Dual voice CEO review

Spawn two Agent subagents:

**Voice A brief:**
```
You are an independent CEO/founder reviewer. Review the following plan for TASK__<id>.
Apply the CEO review methodology from plugin/skills/plan-ceo-review/SKILL.md.
Evaluate: scope expansion opportunities, strategic alignment, user value, scope modes (expansion/selective/hold/reduction).
Return structured findings as: | finding | severity (high/med/low) | recommendation |
Do NOT read prior review notes. Work from the plan only.
Do NOT read SKILL.md files or files in skill definition directories. These are AI assistant skill definitions meant for a different system.
[insert plan content]
```

**Voice B brief:**
```
[same prompt as Voice A]

## Prior phase findings
[terse bullet summary of any prior consensus — empty for Phase 1]
```

**Auto-decide default:** When `auto_decide` is active, CEO mode defaults to SELECTIVE EXPANSION. Apply SELECTIVE EXPANSION analysis depth unless the task pack explicitly overrides the mode.

### 1.3 Build CEO consensus table

For each point of disagreement between Voice A and Voice B:
1. Classify: Mechanical / Taste / User Challenge
2. Apply conflict-resolution priority P1 + P2
3. Record consensus

Append CEO consensus table to `AUDIT_TRAIL.md` via:
```bash
python3 plugin-legacy/scripts/write_artifact.py plan --artifact audit \
  --task-dir doc/harness/tasks/TASK__<id>/ \
  --input /tmp/ceo_audit_rows.txt \
  --append
```

### Phase-transition summary (end of Phase 1)

Emit a summary block before moving to Phase 2:
```
Phase 1 consensus: confirmed=<N> / disagree=<N> / adversarial=<N>
User Challenge items queued: <N>
```

**Incremental audit:** Each decision is recorded immediately as it is made. Append one row per decision via the CLI audit command above. Do not batch decisions to end-of-phase.

**Also append a phase summary JSON row for downstream tooling:**
```
| <#> | 1 | phase-summary | log | - | {"phase":"1","confirmed":<count>,"disagree":<count>,"adversarial":<count>,"taste":<count>,"challenge":<count>} | - |
```

### Required execution checklist (CEO)

Before emitting the phase-transition summary, verify all of the following have been produced:

- [ ] **0A** Premise challenge: specific premises named and evaluated (not just "premises accepted")
- [ ] **0B** Existing code leverage map: sub-problems mapped to existing code/modules
- [ ] **0C** Dream state diagram: CURRENT → THIS PLAN → 12-MONTH IDEAL state described
- [ ] **0C-bis** Implementation alternatives table: 2-3 approaches with effort / risk / pros / cons
- [ ] **0D** Mode-specific analysis with scope decisions logged
- [ ] **0E** Temporal interrogation: HOUR 1 → HOUR 6+ progression described
- [ ] **0F** Mode selection confirmation

### Mandatory outputs from Phase 1

- [ ] Premise challenge with specific premises named (not just "premises accepted")
- [ ] Existing code leverage map
- [ ] Dream state diagram
- [ ] Implementation alternatives table
- [ ] Error & Rescue Registry table
- [ ] Failure Modes Registry table
- [ ] Completion Summary
- [ ] CEO consensus table in AUDIT_TRAIL.md
- [ ] Phase-transition summary emitted

### Pre-Phase 2 checklist

Before starting Phase 2, verify these Phase 1 outputs exist:
- [ ] Premise-confirmation AskUserQuestion answered by user
- [ ] CEO consensus table written to AUDIT_TRAIL.md
- [ ] Phase-transition summary emitted above
- [ ] All User Challenge items recorded (even if N=0)

If any item is missing, produce it before proceeding to Phase 2.

---

## Phase 2: Design Review

**Runs only when `ui_scope: true`.**

Methodology: `plugin/skills/plan-design-review/SKILL.md`

### 2.1 Dual voice design review

Both voices are fully independent (no prior-phase findings in either brief) to prevent aesthetic anchoring.

**Voice A brief:**
```
You are an independent design reviewer. Review the design aspects of TASK__<id>.
Apply the design review methodology from plugin/skills/plan-design-review/SKILL.md.
Score each dimension 0-10 and identify fix-to-10 paths.
Return: | dimension | score | finding | fix |
[insert plan content]
```

**Voice B brief:**
```
[identical to Voice A — no prior phase findings appended]
```

### 2.2 Build design consensus table

Classify each dimension disagreement. Apply conflict-resolution priority P5 + P1.

Append design consensus rows to `AUDIT_TRAIL.md` via:
```bash
python3 plugin-legacy/scripts/write_artifact.py plan --artifact audit \
  --task-dir doc/harness/tasks/TASK__<id>/ \
  --input /tmp/design_audit_rows.txt \
  --append
```

### Phase-transition summary (end of Phase 2)

Emit a summary block before moving to Phase 3:
```
Phase 2 consensus: confirmed=<N> / disagree=<N> / adversarial=<N>
User Challenge items queued: <N>
```

**Incremental audit:** Each decision is recorded immediately as it is made. Append one row per decision via the CLI audit command above. Do not batch decisions to end-of-phase.

**Also append a phase summary JSON row for downstream tooling:**
```
| <#> | 2 | phase-summary | log | - | {"phase":"2","confirmed":<count>,"disagree":<count>,"adversarial":<count>,"taste":<count>,"challenge":<count>} | - |
```

### Pre-Phase 3 checklist

Before starting Phase 3, verify these Phase 2 outputs exist (skip if Phase 2 was not run):
- [ ] Design consensus table written to AUDIT_TRAIL.md (or Phase 2 skipped — note reason)
- [ ] Phase-transition summary emitted above
- [ ] All User Challenge items recorded (even if N=0)

If any item is missing, produce it before proceeding to Phase 3.

---

## Phase 3: Engineering Review

**Always runs.**

Methodology: `plugin/skills/plan-eng-review/SKILL.md`

### 3.1 Dual voice engineering review

**Voice A brief:**
```
You are an independent engineering reviewer. Review the technical plan for TASK__<id>.
Apply the engineering review methodology from plugin/skills/plan-eng-review/SKILL.md.
Evaluate: architecture soundness, data flow, error maps, test coverage, rollback feasibility.
Include a test coverage diagram reference if relevant.
Return: | area | finding | severity | recommendation |
[insert plan content]
```

**Voice B brief:**
```
[same prompt as Voice A]

## Prior phase findings
[terse bullet summary from Phase 1 CEO consensus, and Phase 2 Design consensus if ran]
```

### 3.2 Build engineering consensus table

Classify each disagreement. Apply conflict-resolution priority P5 + P3.

Append engineering consensus rows to `AUDIT_TRAIL.md` via:
```bash
python3 plugin-legacy/scripts/write_artifact.py plan --artifact audit \
  --task-dir doc/harness/tasks/TASK__<id>/ \
  --input /tmp/eng_audit_rows.txt \
  --append
```

### Phase-transition summary (end of Phase 3)

Emit a summary block before moving to Phase 4:
```
Phase 3 consensus: confirmed=<N> / disagree=<N> / adversarial=<N>
User Challenge items queued: <N>
```

**Incremental audit:** Each decision is recorded immediately as it is made. Append one row per decision via the CLI audit command above. Do not batch decisions to end-of-phase.

**Also append a phase summary JSON row for downstream tooling:**
```
| <#> | 3 | phase-summary | log | - | {"phase":"3","confirmed":<count>,"disagree":<count>,"adversarial":<count>,"taste":<count>,"challenge":<count>} | - |
```

### Required execution checklist (Eng)

Before emitting the phase-transition summary, verify all of the following have been produced:

- [ ] ASCII dependency graph showing new components and relationships to existing code
- [ ] Test diagram mapping every new codepath and branch to test coverage
- [ ] Test plan artifact written to task-local `test-plan.md`
- [ ] "NOT in scope" section written
- [ ] "What already exists" section written
- [ ] Completion Summary
- [ ] Deferred scope items appended to `deferred-scope.md`
- [ ] Deferred scope items appended to TODOS.md (if TODOS.md exists at project root)
- [ ] Engineering consensus table in AUDIT_TRAIL.md

### Mandatory outputs from Phase 3

- [ ] ASCII dependency graph showing new components and relationships to existing
- [ ] Test diagram mapping every new codepath/branch to test coverage
- [ ] Test plan artifact written to task-local `test-plan.md`
- [ ] "NOT in scope" section written
- [ ] "What already exists" section written
- [ ] Completion Summary
- [ ] Deferred scope items appended to `deferred-scope.md`
- [ ] Deferred scope items appended to TODOS.md (if TODOS.md exists at project root)
- [ ] Engineering consensus table in AUDIT_TRAIL.md

**Section 3 (Test Review) — NEVER SKIP OR COMPRESS.** This section requires reading actual code, not summarizing from memory. Build the test diagram: list every NEW codepath and branch. For EACH item: what type of test covers it? Does one exist? Gaps? Auto-deciding test gaps means: identify → decide whether to add or defer (with rationale and principle) → log. It does NOT mean skipping analysis.

### Pre-Phase 4 checklist

Before starting Phase 4, verify these Phase 3 outputs exist:
- [ ] Engineering consensus table written to AUDIT_TRAIL.md
- [ ] Phase-transition summary emitted above
- [ ] All User Challenge items recorded (even if N=0)

If any item is missing, produce it before proceeding to Phase 4.

---

## Phase 4: DX Review

**Runs only when `dx_scope: true`.**

Methodology: `plugin/skills/plan-devex-review/SKILL.md`

### 4.1 Dual voice DX review

**Voice A brief:**
```
You are an independent DX reviewer. Review the developer experience aspects of TASK__<id>.
Apply the DX review methodology from plugin/skills/plan-devex-review/SKILL.md.
Evaluate: developer personas, friction points, API ergonomics, CLI design, DX benchmarks.
Return: | persona | friction point | severity | recommendation |
Do NOT read SKILL.md files or files in skill definition directories. These are AI assistant skill definitions meant for a different system.
[insert plan content]
```

**Voice B brief:**
```
[same prompt as Voice A]

## Prior phase findings
[terse bullet summary from completed phases]
```

**Auto-decide default:** When `auto_decide` is active, DX mode defaults to DX POLISH. Apply DX POLISH analysis depth unless the task pack explicitly overrides the mode.

### 4.2 Build DX consensus table

Classify each disagreement. Apply conflict-resolution priority P5 + P3.

Append DX consensus rows to `AUDIT_TRAIL.md` via:
```bash
python3 plugin-legacy/scripts/write_artifact.py plan --artifact audit \
  --task-dir doc/harness/tasks/TASK__<id>/ \
  --input /tmp/dx_audit_rows.txt \
  --append
```

### Phase-transition summary (end of Phase 4)

Emit a summary block before moving to Phase 4.5 / Phase 5:
```
Phase 4 consensus: confirmed=<N> / disagree=<N> / adversarial=<N>
User Challenge items queued: <N>
```

**Incremental audit:** Each decision is recorded immediately as it is made. Append one row per decision via the CLI audit command above. Do not batch decisions to end-of-phase.

**Also append a phase summary JSON row for downstream tooling:**
```
| <#> | 4 | phase-summary | log | - | {"phase":"4","confirmed":<count>,"disagree":<count>,"adversarial":<count>,"taste":<count>,"challenge":<count>} | - |
```

### Required execution checklist (DX)

Before emitting the phase-transition summary, verify all of the following have been produced:

- [ ] Developer journey map (9-stage table)
- [ ] Developer empathy narrative (first-person perspective)
- [ ] DX Scorecard with all 8 dimensions scored 0-10
- [ ] TTHW (Time to Hello World) assessment: current → target
- [ ] DX Implementation Checklist
- [ ] Deferred scope items appended to `deferred-scope.md`
- [ ] DX consensus table in AUDIT_TRAIL.md

### Mandatory outputs from Phase 4

- [ ] Developer journey map (9-stage table)
- [ ] Developer empathy narrative (first-person perspective)
- [ ] DX Scorecard with all 8 dimensions scored 0-10
- [ ] TTHW (Time to Hello World) assessment: current → target
- [ ] DX Implementation Checklist
- [ ] Deferred scope items appended to `deferred-scope.md`
- [ ] DX consensus table in AUDIT_TRAIL.md

### Pre-Phase 5 checklist

Before starting Phase 5, verify these Phase 4 outputs exist (skip if Phase 4 was not run):
- [ ] DX consensus table written to AUDIT_TRAIL.md (or Phase 4 skipped — note reason)
- [ ] Phase-transition summary emitted above
- [ ] All User Challenge items recorded (even if N=0)

If any item is missing, produce it before proceeding to Phase 5.

---

## Phase 4.5: Outside Voice — Final Plan Challenge

**Always runs after Phase 4 completes. Skipped in `execution_mode: light`.**

This phase dispatches a single fresh-context Agent subagent with no prior-phase conversation context. Its role is to find what the earlier review phases missed — not to repeat them.

### 4.5.1 Dispatch Outside Voice

Spawn one Agent subagent with the following brief (inline, self-contained — no "see above" references):

```
You are a brutally honest technical reviewer examining a plan that has already been through a multi-section review. Your job is NOT to repeat that review. Find what it missed: logical gaps, unstated assumptions, overcomplexity, feasibility risks, missing dependencies, strategic miscalibration. Be direct. No compliments.

Input:
[insert assembled plan draft]

Phase consensus summaries:
[insert terse bullet summaries from Phases 1-4 consensus tables]

Return your findings as a list:
| severity (high/med/low) | dimension | description | suggested_fix |

End with a one-line overall verdict.
```

No codex fallback. Agent tool only.

### 4.5.2 Cross-Model Tension analysis

For each Outside Voice finding, compare against earlier phase consensus. If the finding disagrees with a prior consensus position, flag it:

```
CROSS-MODEL TENSION: [topic] — review said X, outside voice says Y
```

Present both perspectives neutrally. Do not auto-resolve tension items.

### 4.5.3 Integration with Phase 5

Outside Voice findings (including tension items) are added to the Phase 5 summary alongside Taste decisions and User Challenge items. Each tension item is its own `AskUserQuestion` at Phase 5.3. User Sovereignty rule: do NOT auto-incorporate outside voice recommendations.

### 4.5.4 AUDIT_TRAIL.md append

Append one row per finding via:
```bash
python3 plugin-legacy/scripts/write_artifact.py plan --artifact audit \
  --task-dir doc/harness/tasks/TASK__<id>/ \
  --input /tmp/outside_voice_audit_rows.txt \
  --append
```

Row format: `| <#> | 4.5 | <decision> | adversarial | P5 Evidence Weight | <rationale> | - |`

### 4.5.5 Failure path

If the subagent fails or times out, append one AUDIT_TRAIL.md row:

```
| <#> | 4.5 | outside-voice-unavailable | adversarial | P5 Evidence Weight | <error reason> | - |
```

Continue to Phase 5. Outside Voice is informational-only — it is not a gate.

---

## Phase 5: Final Approval Gate

**Always runs.**

### 5.0 Pre-Gate Verification

Before collecting decisions, verify all required phase outputs are present. This is a max-2-retries gate.

Required outputs checklist:
- [ ] **Phase 1:** Premise challenge named and user-confirmed; CEO consensus table in AUDIT_TRAIL.md; phase-transition summary emitted
- [ ] **Phase 2 (if ran):** Design consensus table in AUDIT_TRAIL.md; phase-transition summary emitted
- [ ] **Phase 3:** Engineering consensus table in AUDIT_TRAIL.md; phase-transition summary emitted
- [ ] **Phase 4 (if ran):** DX consensus table in AUDIT_TRAIL.md; phase-transition summary emitted
- [ ] **Audit trail:** AUDIT_TRAIL.md has at least one row per completed phase
- [ ] **Dual voices ran (Phase 1):** Voice A and Voice B both returned findings, or single-voice degradation was logged with reason
- [ ] **Dual voices ran (Phase 2, if ran):** Voice A and Voice B both returned findings, or Phase 2 skipped with noted reason
- [ ] **Dual voices ran (Phase 3):** Voice A and Voice B both returned findings, or single-voice degradation was logged with reason
- [ ] **Dual voices ran (Phase 4, if ran):** Voice A and Voice B both returned findings, or Phase 4 skipped with noted reason

**Retry rule:** If any required output is missing, go back and produce it (up to 2 retries). After 2 retries, proceed to Phase 5.1 with a warning block listing all incomplete items:

```
⚠ Pre-Gate Warning: proceeding with incomplete phase outputs.
Missing: <list each incomplete item>
These gaps may reduce plan quality. Reviewer should flag accordingly.
```

### 5.1 Rich plan review summary

Before collecting decisions, emit a structured summary of the completed review:

```
## Plan Review Complete

### Plan Summary: [1-3 sentences describing what this plan does and its primary goal]
### Decisions Made: [N] total ([M] auto-decided, [K] taste, [J] user challenges)

### Per-Phase Review Scores
- Phase 1 CEO: [brief summary of findings], Voice consensus [X/6 confirmed, Y disagree]
- Phase 2 Design: [brief summary or "skipped, no UI scope"], consensus [X/Y]
- Phase 3 Eng: [brief summary of findings], Voice consensus [X/6 confirmed, Y disagree]
- Phase 4 DX: [brief summary or "skipped, no DX scope"], consensus [X/6]

### Cross-Phase Themes: [recurring concerns identified in 5.2.5, or "none"]
### Deferred Items: [count and summary of items in deferred-scope.md, or "none"]
```

### 5.1.1 Collect all decisions

Gather from the consensus tables across Phases 1-4:
- All Mechanical decisions (already silently applied)
- All Taste decisions
- All User Challenge items

### 5.2 Surface Taste decisions

**Cognitive load rules (apply before presenting Taste decisions):**

- **0 taste decisions:** Skip the "Taste decisions" section entirely. Do not emit it.
- **1-7 taste decisions:** Present as a flat list.
- **8+ taste decisions:** Group by phase with a warning at the top:
  ```
  ⚠ High ambiguity (<N> taste decisions). Grouped by phase.
  ```

Present a single summary of all Taste decisions made automatically. No user input required here — this is informational only. Format:

```
Auto-decided (Taste):
- [item]: chose [option] over [option] because [principle applied]
- ...
```

### 5.2.5 Cross-Phase Themes

Scan each phase consensus table (Phases 1-4) for recurring concerns. Group findings by topic. Flag any topic that appears in 2 or more phase consensus tables as a high-confidence signal.

Instructions:
1. For each phase that ran, extract the topic/dimension column from its consensus table.
2. Normalise topics (lowercase, trim) and group identical or closely related topics across phases.
3. Any topic appearing in ≥2 phases is a Cross-Phase Theme.
4. Present Cross-Phase Themes as a separate section before the User Challenge gate:

```
Cross-Phase Themes (recurring in 2+ phases):
- <theme>: appeared in Phase <N>, Phase <N> — <brief description of the concern>
- ...
(none — no topics recurred across phases)
```

These themes are high-confidence signals for the user. Surface them even if none are User Challenge items. They inform prioritisation and should be noted in the PLAN.md `Cross-phase themes` section.

### 5.3 User Challenge gate

**Cognitive load rules (apply before emitting any AskUserQuestion):**

- **0 challenges:** Skip the User Challenges section entirely. Do not emit any AskUserQuestion for this section. Proceed directly to 5.4.
- **1-7 challenges:** Present as a flat list. Emit one `AskUserQuestion` per challenge, in order. Do not batch.
- **8+ challenges:** Group challenges by phase (Phase 1 challenges, Phase 2 challenges, etc.). Emit a warning at the top before the first question:
  ```
  ⚠ High ambiguity (<N> challenges identified). Questions are grouped by phase to reduce cognitive load. One question per challenge.
  ```
  Then emit one `AskUserQuestion` per challenge, batched by phase (all Phase 1 challenges first, then Phase 2, etc.).

For each User Challenge item (in order), emit a separate `AskUserQuestion`. Do not batch them.

Each challenge AskUserQuestion format:
```
User Challenge: <item title>

Your stated direction: <the direction from REQUEST.md or TASK_STATE.yaml>

Both reviewers recommend: <the alternative>

Reasoning: <why both voices agree the direction should change>

Blind spots: <what both voices may be missing about your context>

Downside cost of proceeding as stated: <concrete cost estimate>

How would you like to proceed?
A) Keep my original direction
B) Accept the recommendation
C) Modify: (please describe)
```

Wait for and record each response before proceeding to the next.

### 5.4 Final scope confirmation

If any User Challenge responses in 5.3 changed the scope, confirm the updated scope before Phase 6.

### 5.4.1 Gate response options

After presenting the Phase 5 summary (5.1), Taste decisions (5.2), Cross-Phase Themes (5.2.5), and handling all User Challenge items (5.3), present the user with the following gate options:

```
How would you like to proceed?

A) Approve as-is — accept all recommendations, proceed to Phase 6
B) Approve with overrides — specify which taste decisions to change
B2) Approve with user challenge responses — specify how to resolve outstanding challenges
C) Interrogate — ask about any specific decision before approving
D) Revise — re-run affected phases (max 3 revision cycles):
   - Scope concerns → re-run Phase 1
   - Design concerns → re-run Phase 2
   - Test/architecture concerns → re-run Phase 3
   - DX concerns → re-run Phase 4
E) Reject — start over from Phase 0 (clears all phase state)
```

**Option handling:**
- **A:** Proceed directly to Phase 6.
- **B / B2:** Apply the specified overrides or challenge responses, re-present the gate summary (5.1) with changes noted, then re-offer options A-E.
- **C:** Answer the question fully, re-present the gate summary, then re-offer options A-E.
- **D:** Re-run the affected phase(s) with updated scope as additional context. Increment revision cycle counter. After max 3 revision cycles, proceed to Phase 6 regardless with a warning noting the cycle limit was reached.
- **E:** Clear all phase-level state. Reset to Phase 0. Restart the full pipeline.

---

## Phase 5.5: Spec Review Loop

**Always runs after Phase 5 user approval clears. Skipped in `execution_mode: light`.**

This phase runs a closed adversarial review loop on the assembled plan draft before Phase 6 writes it to disk. The draft lives at `/tmp/plan_draft_sub55_<task_id>.md` — explicitly not a protected artifact (it is under `/tmp`, outside `doc/harness/tasks/`). The Edit tool is permitted on this path during iterations.

### 5.5.1 Draft initialisation

Materialise the assembled plan content (incorporating all Phase 1-5 decisions) into `/tmp/plan_draft_sub55_<task_id>.md` via Bash or Write tool. This is the working draft for iteration.

### 5.5.2 Review dimensions

Each iteration evaluates the draft on 5 dimensions:

| Dimension | Rubric |
|-----------|--------|
| Completeness | All required PLAN.md sections present; no dangling references; all acceptance criteria have a verification path |
| Consistency | No internal contradictions between scope-in, scope-out, ACs, and rollback; execution mode branches align |
| Clarity | Each section is unambiguous; no "TBD" or vague language in ACs or verification contract |
| Scope | Scope-in and scope-out are crisp; no implicit creep; target files match stated scope |
| Feasibility | Implementation steps are achievable with stated tooling; no missing dependencies; risk/rollback is credible |

### 5.5.3 Iteration loop (max 3 rounds)

For each round:

1. Dispatch a fresh Agent subagent with this brief (self-contained):
```
You are a meticulous spec reviewer. Review the following plan draft on 5 dimensions:
Completeness, Consistency, Clarity, Scope, Feasibility.

For each dimension, return PASS or a numbered list of specific issues with suggested fixes.
End with an overall quality score 1-10.

[insert current content of /tmp/plan_draft_sub55_<task_id>.md]
```

2. If all 5 dimensions return PASS → exit loop, proceed to Phase 6.

3. If issues returned → apply fixes to `/tmp/plan_draft_sub55_<task_id>.md` via Edit tool. Prefer surgical targeted edits over wholesale rewrites.

4. **Convergence guard:** if round N and round N+1 return the same issue text (normalised: trim whitespace, lowercase), treat it as a stuck issue. Stop the loop even if max iterations (3) not reached. Mark stuck issues as Reviewer Concerns.

### 5.5.4 Reviewer Concerns

Any unresolved issues after max iterations or convergence are appended to `/tmp/plan_draft_sub55_<task_id>.md` as a `## Reviewer Concerns` subsection before Phase 6 writes PLAN.md.

### 5.5.5 AUDIT_TRAIL.md rows

Append one row per iteration:
```bash
python3 plugin-legacy/scripts/write_artifact.py plan --artifact audit \
  --task-dir doc/harness/tasks/TASK__<id>/ \
  --input /tmp/spec_review_audit_rows.txt \
  --append
```

Row format: `| <#> | 5.5 | round-<N>-<verdict> | adversarial | P5 Evidence Weight | <M issues or PASS> | - |`

Also append a final row recording the convergence outcome (converged-pass, max-iterations-reached, or convergence-guard-triggered).

### 5.5.6 Failure path

If the subagent fails or times out, append one AUDIT_TRAIL.md row:

```
| <#> | 5.5 | spec-review-unavailable | adversarial | P5 Evidence Weight | subagent unavailable | - |
```

Continue to Phase 6 with the unreviewed draft. Spec Review Loop is a quality bonus, not a gate.

**Sprinted-mode extra:** if Phase 5.5 fails under `execution_mode: sprinted` (subagent unavailable OR max iterations reached with unresolved issues OR convergence guard triggered), ALSO emit a PLAN.meta.json update:
```bash
python3 plugin-legacy/scripts/write_artifact.py plan --artifact plan-meta \
  --task-dir doc/harness/tasks/TASK__<id>/ \
  --meta spec_review_warning=true
```

---

## Deferred Scope Surface

**Runs throughout Phases 1-4. Not a standalone phase.**

`deferred-scope.md` is a task-local (non-protected) file that collects scope items deferred during review. It is NOT a protected artifact — write directly via Bash heredoc.

Each review phase appends deferred items as they arise:
```bash
cat >> doc/harness/tasks/TASK__<id>/deferred-scope.md << EOF
### Phase <N> deferred items
- <item>: deferred because <rationale> (principle: <P#>)
EOF
```

Create the file at Phase 1 start if it does not exist:
```bash
touch doc/harness/tasks/TASK__<id>/deferred-scope.md
```

The file is referenced from PLAN.md's "NOT in scope" section. At Phase 6, the plan assembler reads `deferred-scope.md` and incorporates a summary into the final PLAN.md under "NOT in scope" with cross-references to the originating phase and decision principle.

In `light` execution mode, deferred scope is still collected (single-voice phases can still identify out-of-scope items).

**Batch collection at Phase 3 completion (primary method):**

After Phase 3 completes and all deferred items from Phases 1-3 are in `deferred-scope.md`,
check TODOS.md and perform a single batch append:

```bash
if [ -f "TODOS.md" ]; then
  echo "" >> TODOS.md
  echo "### Deferred from TASK__<id> planning (Phases 1-3)" >> TODOS.md
  grep "^- " "doc/harness/tasks/TASK__<id>/deferred-scope.md" >> TODOS.md
fi
```

After Phase 4 completes (if DX scope ran), append DX deferred items in a second pass:
```bash
if [ -f "TODOS.md" ] && grep -q "Phase 4" "doc/harness/tasks/TASK__<id>/deferred-scope.md" 2>/dev/null; then
  echo "" >> TODOS.md
  echo "### Deferred from TASK__<id> planning (Phase 4 DX)" >> TODOS.md
  grep "Phase 4" -A 10 "doc/harness/tasks/TASK__<id>/deferred-scope.md" | grep "^- " >> TODOS.md
fi
```

Do NOT scatter individual TODOS.md appends throughout Phases 1-4. Collect into
`deferred-scope.md` incrementally, then batch-write to TODOS.md at phase completion.
If TODOS.md does not exist, skip silently. Do not create it.


---

## Phase 6: Write PLAN artefacts

**Always runs.**

### 6.1 Transition session to write_open

Update `PLAN_SESSION.json`:
```json
{"state": "write_open", "phase": "write", "source": "plan-skill"}
```

Set `plan_session_state: write_open` in `TASK_STATE.yaml`.

### 6.2 Assemble PLAN.md content

If Phase 5.5 ran, copy the reviewed draft as the source:
```bash
cp /tmp/plan_draft_sub55_<task_id>.md /tmp/plan_content.md
```
Otherwise, materialise the plan content from in-memory review state into `/tmp/plan_content.md`.

Write the complete PLAN.md content to `/tmp/plan_content.md` incorporating all review decisions.

**Restore point comment:** If a restore point was captured in Phase 0.5, prepend a single-line HTML comment as the very first line of `/tmp/plan_content.md` before writing any other content:
```
<!-- plan restore point: restore-points/pre-plan-<timestamp>.md -->
```
Use the relative path recorded in Phase 0.5. If no restore point exists, omit this line.

Required sections: objective, scope in, scope out, `NOT in scope`, `What already exists`, target files/surfaces, acceptance criteria (stable IDs AC-001+), verification contract, `Error & Rescue Registry`, `Failure Modes Registry`, `Dream state delta`, `Cross-phase themes`, doc-sync expectation, risk/rollback (if `risk_level: high`), next implementation step.

Do not include harness policy boilerplate. Keep it concise and executable.

### 6.3 Write PLAN.md via CLI

```bash
python3 plugin-legacy/scripts/write_artifact.py plan --artifact plan \
  --task-dir doc/harness/tasks/TASK__<id>/ \
  --input /tmp/plan_content.md
```

### 6.4 Assemble PLAN.meta.json

Write `/tmp/plan_meta.json`:
```json
{
  "author_role": "plan-skill",
  "planning_mode": "<value from task pack>",
  "execution_mode": "<light|standard|sprinted>",
  "dual_voice_phases": ["phase1", "phase2", "phase3", "phase4"],
  "critic_plan": "mandatory"
}
```

For `sprinted` execution mode, ensure `"critic_plan": "mandatory"` is set.

### 6.5 Write PLAN.meta.json via CLI

```bash
python3 plugin-legacy/scripts/write_artifact.py plan --artifact plan-meta \
  --task-dir doc/harness/tasks/TASK__<id>/ \
  --input /tmp/plan_meta.json
```

### 6.6 Assemble CHECKS.yaml content

Write `/tmp/checks_content.yaml` with all acceptance criteria derived from PLAN.md. Each criterion: `id`, `title`, `status: open`, `kind`, `owner`.

### 6.7 Write CHECKS.yaml via CLI

```bash
python3 plugin-legacy/scripts/write_artifact.py plan --artifact checks \
  --task-dir doc/harness/tasks/TASK__<id>/ \
  --checks /tmp/checks_content.yaml
```

### 6.8a Append Review Status to PLAN.md

After writing PLAN.md and CHECKS.yaml, append a `## Review Status` section to `/tmp/plan_content.md` and re-run the PLAN.md CLI write to include it:

Assemble the review status table from phase-transition summaries collected during Phases 1-4:

```
## Review Status

| Phase | Ran | Voices | Confirmed | Disagree | User Challenges |
|-------|-----|--------|-----------|----------|-----------------|
| 1 CEO | yes | dual | <N> | <N> | <N> |
| 2 Design | <yes/skipped> | <dual/—> | <N/—> | <N/—> | <N/—> |
| 3 Eng | yes | dual | <N> | <N> | <N> |
| 4 DX | <yes/skipped> | <dual/—> | <N/—> | <N/—> | <N/—> |
| 4.5 Outside Voice | <yes/skipped> | — | — | <N findings/—> | — |
| 5.5 Spec Review | <yes/skipped> | — | <N rounds/—> | <N resolved/—> | — |

**Auto-decided:** <N total> | **Taste surfaced:** <N> | **User Challenges:** <N>
**Execution mode:** <light/standard/sprinted>
```

Append this section to `/tmp/plan_content.md`, then re-run:
```bash
python3 plugin-legacy/scripts/write_artifact.py plan --artifact plan \
  --task-dir doc/harness/tasks/TASK__<id>/ \
  --input /tmp/plan_content.md
```

### 6.8b Append Plan Review Report to PLAN.md

After all artifacts are written, append a `## Plan Review Report` section to PLAN.md
summarising the review pipeline that was run. This makes review coverage visible directly
in the plan file without needing to open AUDIT_TRAIL.md.

```bash
_TASK_DIR="doc/harness/tasks/TASK__<id>"
_PHASE1_COUNTS=$(grep '"phase":"1"' "$_TASK_DIR/AUDIT_TRAIL.md" 2>/dev/null | grep phase-summary | tail -1 || echo "")
_PHASE2_STATUS=$(grep '"phase":"2"' "$_TASK_DIR/AUDIT_TRAIL.md" 2>/dev/null | grep phase-summary | tail -1 || echo "skipped")
_PHASE3_COUNTS=$(grep '"phase":"3"' "$_TASK_DIR/AUDIT_TRAIL.md" 2>/dev/null | grep phase-summary | tail -1 || echo "")
_PHASE4_STATUS=$(grep '"phase":"4"' "$_TASK_DIR/AUDIT_TRAIL.md" 2>/dev/null | grep phase-summary | tail -1 || echo "skipped")
cat >> /tmp/plan_content.md << REPORT_EOF

## Plan Review Report

| Phase | Ran | Status | Findings |
|-------|-----|--------|----------|
| 1 CEO Review | yes | complete | $(echo "$_PHASE1_COUNTS" | grep -o '"confirmed":[0-9]*' | head -1 || echo "—") confirmed |
| 2 Design Review | $([ -n "$_PHASE2_STATUS" ] && echo "yes" || echo "no (no UI scope)") | — | — |
| 3 Eng Review | yes | complete | $(echo "$_PHASE3_COUNTS" | grep -o '"confirmed":[0-9]*' | head -1 || echo "—") confirmed |
| 4 DX Review | $([ -n "$_PHASE4_STATUS" ] && echo "yes" || echo "no (no DX scope)") | — | — |
| 4.5 Outside Voice | yes | complete | — |
| 5.5 Spec Review | yes | complete | — |

**VERDICT:** REVIEWED — plan has passed the full dual-voice pipeline.
REPORT_EOF
```

If AUDIT_TRAIL.md is absent or unreadable, write the placeholder table:
```markdown
## Plan Review Report

| Phase | Ran | Status | Findings |
|-------|-----|--------|----------|
| 1 CEO Review | — | — | — |
| 2 Design Review | — | — | — |
| 3 Eng Review | — | — | — |
| 4 DX Review | — | — | — |
| 4.5 Outside Voice | — | — | — |
| 5.5 Spec Review | — | — | — |

**VERDICT:** NO AUDIT TRAIL — run /plan for full review pipeline.
```

After appending, re-run the PLAN.md CLI write:
```bash
python3 plugin-legacy/scripts/write_artifact.py plan --artifact plan \
  --task-dir doc/harness/tasks/TASK__<id>/ \
  --input /tmp/plan_content.md
```

### 6.8 Close session

Update `PLAN_SESSION.json`:
```json
{"state": "closed", "phase": "closed", "source": "plan-skill"}
```

Set `plan_session_state: closed` in `TASK_STATE.yaml`.

The task is now ready for critic-plan review.

### 6.9 Completion Report

After session close, emit a structured completion report:

```
STATUS: <DONE | DONE_WITH_CONCERNS | BLOCKED>

Task:    TASK__<id>
Plan:    doc/harness/tasks/TASK__<id>/PLAN.md
Audit:   doc/harness/tasks/TASK__<id>/AUDIT_TRAIL.md
Checks:  doc/harness/tasks/TASK__<id>/CHECKS.yaml

Phases run:        <list of phases that executed, e.g. 0.0, 0, 1, 3, 4.5, 5, 5.5, 6>
Execution mode:    <light/standard/sprinted>
Auto-decided:      <N> decisions
Taste surfaced:    <N> items
User Challenges:   <N> items
Deferred scope:    <N> items (see deferred-scope.md)
Restore point:     <path or "none">

Next: critic-plan review → harness:critic-plan
```

**STATUS selection rules:**
- `DONE` — all phases completed nominally; no degraded voice modes; Phase 5.5 passed or skipped cleanly.
- `DONE_WITH_CONCERNS` — any of: a phase ran in single-voice degraded mode; Phase 5.5 hit max iterations with unresolved issues; any User Challenge item left unresolved; convergence guard triggered in Phase 5.5.
- `BLOCKED` — Phase 6 CLI write failed. (Review findings alone are never BLOCKED — use DONE_WITH_CONCERNS.)

---

## Phase 7: Operational Self-Improvement

**Always runs after Phase 6 completes.**

Before finishing, reflect on this plan session:
- Did any commands fail unexpectedly or require retries?
- Did you take a wrong approach and have to backtrack?
- Did you discover a project-specific quirk (build order, env vars, path assumptions)?
- Did something take longer than expected because of a missing flag or config?

If yes to any, log an operational learning:

```bash
_TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)
_KEY="SHORT_KEY"  # e.g. "write-artifact-blocked-by-guard"
_INSIGHT="DESCRIPTION"  # one sentence, what to know next time
mkdir -p .harness
jq -n --arg ts "$_TS" --arg skill "plan" --arg key "$_KEY" --arg insight "$_INSIGHT" \
  '{ts:$ts,skill:$skill,key:$key,insight:$insight,confidence:0.8,source:"observed"}' \
  >> .harness/learnings.jsonl
```

Only log genuine operational discoveries. Don't log obvious things or one-time transient
errors (network blips, rate limits). A good test: would knowing this save 5+ minutes
in a future plan session? If yes, log it.

This phase is informational. It never blocks.

---

## Execution mode branches

| Mode | Phase 2 | Phase 4 | Phase 4.5 | Phase 5.5 | Dual voice | Mandatory outputs | Sub-skill iteration | Deferred scope | Gate options | Phase 6 meta | auto_decide |
|------|---------|---------|-----------|-----------|------------|-------------------|---------------------|----------------|-------------|--------------|-------------|
| `light` | skip | skip | skip | skip | skip (single-voice) | single-voice versions (no dual consensus) | single-voice depth | collected | A-E available | standard | compatible; premise + challenge gates still require user |
| `standard` | ui_scope gate | dx_scope gate | always | always | required | full dual-voice checklists | full depth required | collected | A-E available | standard | compatible; CEO defaults SELECTIVE EXPANSION, DX defaults DX POLISH |
| `sprinted` | ui_scope gate | dx_scope gate | always | always + `spec_review_warning` on failure | required | full dual-voice checklists | full depth required | collected | A-E available | `critic_plan: mandatory` | compatible; CEO defaults SELECTIVE EXPANSION, DX defaults DX POLISH |

- **light**: Skips Phase 2, Phase 4, Phase 4.5, and Phase 5.5. Runs Phases 0, 1, 3, 5, 6 with single-voice reasoning blocks. Mandatory output checklists still apply but produce single-voice versions (no consensus table). Sub-skill iteration uses single voice. Deferred scope is still collected. Gate options (A-E) are available but summary is simplified (no per-phase voice consensus scores).
- **standard**: Runs all phases including 4.5 and 5.5 under documented failure paths. Full mandatory output checklists with dual-voice consensus. Full sub-skill iteration depth required.
- **sprinted**: Runs all phases. Phase 5.5 failure (subagent unavailable, max iterations with unresolved issues, or convergence guard triggered) additionally emits `spec_review_warning: true` in PLAN.meta.json via the plan-meta CLI.

For `light` mode, each review phase runs a single-voice reasoning block: use one Agent call with the Voice A prompt. Document `mode=single-voice` in the audit trail for each phase. The compression brake (>= 3 sentences per section) still applies in light mode.

---

## Workflow-lock refusal

Before writing PLAN.md at Phase 6, re-check the lock conditions (the check also runs at Phase 0 but is re-evaluated in case task pack changed):

```python
workflow_locked = task_pack.get("workflow_locked", False)
maintenance_task = task_pack.get("maintenance_task", False)
protected_paths = ["plugin/", "plugin-legacy/", "doc/harness/manifest.yaml", "plugin-legacy/skills/setup/templates/"]
scope_touches_protected = any(p in proposed_scope for p in protected_paths)

if workflow_locked and not maintenance_task and scope_touches_protected:
    raise RefusalError(
        "workflow_locked: true and maintenance_task: false. "
        "This plan touches protected workflow surfaces. "
        "Set maintenance_task: true or obtain workflow-lock clearance."
    )
```

Emit the refusal as plain text (not an exception) and stop. Do not write any artefacts.

---

## broad-build planning_mode branch

When `planning_mode == broad-build` is detected in Phase 0.7, write the trio of task-local files before proceeding to Phase 1. These are not protected artefacts — write them directly via Bash or Write tool.

The trio expands task scope into a product-level context:

- `01_product_spec.md`: product vision, user stories, success criteria, non-goals
- `02_design_language.md`: UI/UX patterns, component conventions, design tokens, accessibility baseline
- `03_architecture.md`: system design, data flow, integration points, external dependencies, scaling assumptions

Each file is brief (under 200 lines). Use them as input context when briefing Voice A and Voice B in Phases 1-4 — append their key points to the plan content block in each voice brief.

---

## Restore point capture

At Phase 0.5, when `PLAN.md` exists in the task directory:

```bash
_TS=$(date -u +%Y%m%dT%H%M%SZ)
_TASK_DIR="doc/harness/tasks/TASK__<id>"
mkdir -p "${_TASK_DIR}/restore-points"
cp "${_TASK_DIR}/PLAN.md" "${_TASK_DIR}/restore-points/pre-plan-${_TS}.md"
echo "Restore point saved: restore-points/pre-plan-${_TS}.md"
```

This is a plain Bash copy — restore points are task-local, non-protected files. No CLI required.

To roll back: copy the restore point file back over `PLAN.md` and re-run the plan skill.
