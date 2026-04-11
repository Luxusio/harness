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

## Decision Classification

Every contested item between Voice A and Voice B is classified before it is acted on.

**Mechanical** — Objectively correct answer exists (wrong import, broken reference, missing required field). Auto-decide silently. Append one audit row per item.

**Taste** — Two reasonable approaches with different tradeoffs (naming, structure, sequencing). Auto-decide using the applicable principles. Surface at Phase 5.2 for user awareness.

**User Challenge** — Both voices independently recommend changing a direction the user has stated. Never auto-decide. Surface at Phase 5.3 with full framing: the user's stated direction, the dual-model recommendation, reasoning, identified blind spots, and estimated downside cost of proceeding as stated.

If voices disagree on the classification itself, escalate to the higher tier (e.g. Taste vs. User Challenge escalates to User Challenge).

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
phase | item | voice_a | voice_b | decision | classification | rationale
```

---

## Phase 0: Intake + Context

**Always runs.**

### 0.1 Open session

Write `PLAN_SESSION.json` in the task directory:
```json
{"state": "context_open", "phase": "context", "source": "plan-skill"}
```

Set `plan_session_state: context_open` in `TASK_STATE.yaml` via Bash.

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

### 0.5 Restore point

If a prior `PLAN.md` exists in the task directory, capture a restore point:
```bash
_TS=$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p doc/harness/tasks/TASK__<id>/restore-points/
cp doc/harness/tasks/TASK__<id>/PLAN.md \
   doc/harness/tasks/TASK__<id>/restore-points/pre-plan-${_TS}.md
```

### 0.6 Scope detection

```bash
grep -rn "ui_scope\|frontend\|component\|css\|html\|react\|vue" \
  doc/harness/tasks/TASK__<id>/ 2>/dev/null | head -5
grep -rn "dx_scope\|api\|cli\|sdk\|devex\|developer experience" \
  doc/harness/tasks/TASK__<id>/ 2>/dev/null | head -5
```

Set `ui_scope=true` if matches found (or if task pack already sets it). Set `dx_scope=true` similarly.

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
[insert plan content]
```

**Voice B brief:**
```
[same prompt as Voice A]

## Prior phase findings
[terse bullet summary of any prior consensus — empty for Phase 1]
```

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
[insert plan content]
```

**Voice B brief:**
```
[same prompt as Voice A]

## Prior phase findings
[terse bullet summary from completed phases]
```

### 4.2 Build DX consensus table

Classify each disagreement. Apply conflict-resolution priority P5 + P3.

Append DX consensus rows to `AUDIT_TRAIL.md` via:
```bash
python3 plugin-legacy/scripts/write_artifact.py plan --artifact audit \
  --task-dir doc/harness/tasks/TASK__<id>/ \
  --input /tmp/dx_audit_rows.txt \
  --append
```

---

## Phase 5: Final Approval Gate

**Always runs.**

### 5.1 Collect all decisions

Gather from the consensus tables across Phases 1-4:
- All Mechanical decisions (already silently applied)
- All Taste decisions
- All User Challenge items

### 5.2 Surface Taste decisions

Present a single summary of all Taste decisions made automatically. No user input required here — this is informational only. Format:

```
Auto-decided (Taste):
- [item]: chose [option] over [option] because [principle applied]
- ...
```

### 5.3 User Challenge gate

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

Write the complete PLAN.md content to `/tmp/plan_content.md` incorporating all review decisions.

Required sections: objective, scope in, scope out, target files/surfaces, acceptance criteria (stable IDs AC-001+), verification contract, doc-sync expectation, risk/rollback (if `risk_level: high`), next implementation step.

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

### 6.8 Close session

Update `PLAN_SESSION.json`:
```json
{"state": "closed", "phase": "closed", "source": "plan-skill"}
```

Set `plan_session_state: closed` in `TASK_STATE.yaml`.

The task is now ready for critic-plan review.

---

## Execution mode branches

| Mode | Phase 2 | Phase 4 | Dual voice | Phase 6 meta |
|------|---------|---------|------------|--------------|
| `light` | skip | skip | skip (single-voice reasoning blocks in Ph 1, Ph 3) | standard |
| `standard` | ui_scope gate | dx_scope gate | required | standard |
| `sprinted` | ui_scope gate | dx_scope gate | required | `critic_plan: mandatory` |

For `light` mode, each review phase runs a single-voice reasoning block: use one Agent call with the Voice A prompt. Document `mode=single-voice` in the audit trail for each phase.

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
