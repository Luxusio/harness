---
name: plan
description: Create a task contract — PLAN.md with scope, acceptance criteria, verification contract, doc sync, and rollback.
argument-hint: <task-slug>
user-invocable: true
allowed-tools: Read, Glob, Grep, Write, Edit, AskUserQuestion
---

Create a task contract for this request.

Task slug from user: `$ARGUMENTS`

## Procedure

### 0. Open plan session token

**Before any context gathering**, create the plan session token to authorize reads and writes:

Create `doc/harness/tasks/TASK__$ARGUMENTS/PLAN_SESSION.json`:

```json
{
  "task_id": "TASK__$ARGUMENTS",
  "state": "open",
  "phase": "context",
  "source": "plan-skill",
  "opened_at": "<ISO 8601>",
  "expires_at": "<ISO 8601 +15min>"
}
```

Also update `TASK_STATE.yaml` field `plan_session_state: context_open`.

This token authorizes reading guarded paths (source/script/agent files) during planning.

### 1. Load context
- Read root `CLAUDE.md`
- Read `doc/harness/manifest.yaml` if it exists (check `browser.enabled`, `qa.default_mode`)
- Scan relevant existing docs if any
- Understand what the user is asking for

### 2. Clarify requirements
- Separate explicit user requirements from inferred assumptions
- If requirements are ambiguous, ask the user (max 3 questions)

### 3. Select execution mode

Before creating TASK_STATE.yaml, determine execution mode using these signals:

**Mode A — light**: Lane is `docs-sync`, `answer`, or `investigate`; single-file change; small predicted diff; no API/DB/infra surfaces.

**Mode B — standard**: Normal feature/bugfix, single-root change. Default when light and sprinted signals are absent.

**Mode C — sprinted**: 2+ roots estimated; multi-surface change (app+api, app+db, etc.); destructive/structural flag (migrations, schema changes, dependency major upgrades); prior `blocked_env`; ambiguous spec requiring significant assumptions.

Tie-break: higher mode wins when signals conflict.

### 3.3 Select planning mode

After execution mode selection, determine planning mode. This is orthogonal to execution mode.

```yaml
planning_mode: standard | broad-build
```

**Select `broad-build` when ALL of:**
- Lane is `build`
- Request is a broad product/build request

**Plus 2+ of these additional signals:**
- Request is short and high-level (1–4 sentences)
- Greenfield / new app / dashboard / site / tool / workspace / platform
- No specific file paths or low-level implementation anchors
- Browser/UI emphasis
- 2+ roots estimated
- Writing PLAN.md immediately would require too many assumptions

**Do NOT select `broad-build` when ANY of:**
- Clear bugfix
- Single endpoint/component work
- Performance optimization / enforcement / refactor
- Request already contains detailed technical spec with file anchors

**Default:** `planning_mode: standard` (field absence = standard for backward compatibility).

#### broad-build artifacts

When `planning_mode: broad-build`, generate these task-local artifacts BEFORE writing PLAN.md:

**`01_product_spec.md`** — problem definition, target users, primary flows, must/should/nice-to-have, out of scope.

**`02_design_language.md`** — interaction model, layout principles, UI/UX constraints, visual priorities, patterns to avoid.

**`03_architecture.md`** — high-level modules, data flow, integration points, persistence boundaries, risk/constraint summary.

These are NOT low-level code specs. Function/class/file micromanagement is prohibited. The purpose is to narrow a broad request into a clear execution contract for PLAN.md.

PLAN.md is still required and must reference these specs:
- Link to `01_product_spec.md` for scope justification
- State which tranche this task delivers
- State what is explicitly NOT in this task

### 3.5 Select review overlays

After mode selection, conservatively select review overlays based on prompt signals and predicted file paths. Overlays add domain-specific review criteria without changing the workflow.

**Selection rules (apply only when signals are clear — when in doubt, do not select):**

**Security overlay** — select when:
- Prompt contains: auth, login, session, token, permission, role, cors, csrf, secret, cookie, middleware, sql, injection, header, password, encrypt
- Predicted paths touch: auth/, api/auth, middleware/, security/, session/

**Performance overlay** — select when:
- Prompt contains: performance, latency, slow, benchmark, query, cache, memory, cpu, throughput, p95, p99, optimize, bottleneck
- Predicted paths touch: hot path code, DB query files, caching layer

**Frontend-refactor overlay** — select when:
- Prompt contains: component, ui, layout, hook, state, a11y, responsive, refactor (with frontend context), architecture (with frontend context)
- Predicted paths touch: app/, pages/, components/, src/ui/, src/components/, hooks/, stores/
- Lane is `refactor` with frontend files

**Observability overlay** — select when ALL required conditions are met:
- `tooling.observability_ready: true` in manifest
- Project kind is web/api/fullstack/worker

AND at least ONE additional signal:
- `performance` overlay is also selected
- `runtime_verdict_fail_count >= 2` in TASK_STATE.yaml (re-plan / recovery context)
- Request or failure context contains investigation keywords: intermittent, flaky, cross-service, latency spike, p95, p99, trace, log correlation

Do NOT select observability overlay when:
- Project kind is library/cli
- `tooling.observability_ready` is false or absent
- Task is a simple docs/refactor/single-pass bugfix with no investigation signals

Record selected overlays in TASK_STATE.yaml `review_overlays` field. Set `performance_task: true` when performance overlay is selected.

If no signals match, leave `review_overlays: []` — this is the common case for normal tasks.

### 3.7 Select orchestration mode

After selecting review overlays (step 3.5), determine orchestration mode using these signals:

**Select solo when:**
- Single-file or small-diff task
- Steps have sequential dependencies (B must follow A)
- Same-file conflict risk
- Lane is `docs-only`, `answer`, or `investigate`

**Select subagents when:**
- Helper tasks needed (research, search, verify) with no cross-talk
- No team readiness confirmed but some parallelism is useful
- Workers do not need to write files concurrently

**Select team when:**
- Cross-layer work (app + api + tests) with clearly disjoint file ownership
- 2+ independent roots estimated from request + manifest
- Parallel exploration or review across non-overlapping areas
- Team provider available and readiness probe passes

**Prohibition rules for team:** do NOT select if multiple workers would edit the same file, steps are sequentially dependent, or the task is a small bugfix.

**Escalation:** solo → subagents OK; subagents → team OK; team → fallback-subagents or fallback-solo if provider unavailable.

When `orchestration_mode: team`, set `team_plan_required: true`, `team_synthesis_required: true`, and record a brief `team_reason`.

### 4. Create TASK_STATE.yaml

Create `doc/harness/tasks/TASK__$ARGUMENTS/TASK_STATE.yaml`:

```yaml
task_id: TASK__$ARGUMENTS
status: planned
lane: <selected sub-lane: build|debug|verify|refactor|docs-sync|investigate>
execution_mode: <light|standard|sprinted>
planning_mode: <standard|broad-build>
mutates_repo: <true|false>
qa_required: <true|false>
qa_mode: <auto|tests|smoke|browser-first>
browser_required: <true|false>
doc_sync_required: <true|false>
touched_paths: []
roots_touched: []
verification_targets: []
plan_verdict: pending
runtime_verdict: pending
document_verdict: pending
blockers: []
review_overlays: []
risk_tags: []
performance_task: false
orchestration_mode: <solo|subagents|team>
team_provider: none | native | omc | fallback-subagents | fallback-solo
team_status: n/a | planned | running | degraded | fallback | complete | skipped
team_size: 0
team_reason: ""
team_plan_required: <true|false>
team_synthesis_required: <true|false>
fallback_used: none | subagents | solo
workflow_mode: compliant
compliance_claim: strict
artifact_provenance_required: true
result_required: <true if lane=investigate, false otherwise>
plan_session_state: write_open
capability_delegation: unknown
collapsed_mode_approved: false
collapsed_reason: ""
directive_capture_state: clean
pending_directive_ids: []
updated: <ISO 8601>
```

Set `browser_required: true` and default `qa_mode: browser-first` when `manifest.browser.enabled: true`.
Set `doc_sync_required: true` for all repo-mutating tasks.
Set `result_required: true` for investigate-lane tasks.

### 5. Write REQUEST.md

Create `doc/harness/tasks/TASK__$ARGUMENTS/REQUEST.md`:

```markdown
# Request: TASK__$ARGUMENTS
created: <date>

<capture the original user request in their words>
```

### 5.5 Transition plan session to write phase

Before writing PLAN.md, update the session token:

Update `PLAN_SESSION.json` field `phase` to `"write"`.
Update `TASK_STATE.yaml` field `plan_session_state: write_open`.

### 6. Write PLAN.md

Create `doc/harness/tasks/TASK__$ARGUMENTS/PLAN.md` using the format matching the selected `execution_mode`.

**If `planning_mode: broad-build`:** write `01_product_spec.md`, `02_design_language.md`, and `03_architecture.md` BEFORE writing PLAN.md (see step 3.3).

**After writing PLAN.md**, create the provenance sidecar `PLAN.meta.json`:

```json
{
  "artifact": "PLAN.md",
  "task_id": "TASK__$ARGUMENTS",
  "author_role": "plan-skill",
  "author_agent": "harness:plan",
  "workflow_mode": "compliant",
  "created_at": "<ISO 8601>"
}
```

#### Mode A (light) — compact format

Required sections only: Scope in, Acceptance criteria, Verification contract, Required doc sync. Skip risk matrix and rollback (unless genuinely risky).

Performance contract is not required for light mode, but include if the change is performance-sensitive.

```markdown
# Plan: <task title>
created: <date>
task_id: TASK__$ARGUMENTS
execution_mode: light
mutates_repo: <true|false>

## Scope in
<what this task will do>

## Acceptance criteria
- [ ] <specific, testable criterion 1>

## Verification contract
- commands: <exact commands to run>
- expected outputs: <what success looks like>

## Required doc sync
<which doc surfaces need updating, or "none">
```

#### Mode B (standard) — full format

All sections required. This is the current default format.

```markdown
# Plan: <task title>
created: <date>
task_id: TASK__$ARGUMENTS
execution_mode: standard
mutates_repo: <true|false>

## Scope in
<what this task will do>

## Scope out
<what this task will NOT do>

## User-visible outcomes
<what changes from the user's perspective — observable behavior, not implementation details>

## Touched files / roots
- <file or directory path>
- <file or directory path>

## QA mode
<tests | smoke | browser-first>

## Acceptance criteria
- [ ] <specific, testable criterion 1>
- [ ] <specific, testable criterion 2>

## Verification contract
- commands: <exact commands to run>
- routes: <URLs or API endpoints to hit, or "n/a">
- persistence checks: <database or file state to verify, or "n/a">
- expected outputs: <what success looks like>

## Required doc sync
<which doc surfaces need updating, or "none">

## Hard fail conditions
<conditions that would mean this task has failed>

## Risks / rollback
<what could go wrong, how to undo>

## Open blockers
<known blockers before implementation, or "none">
```

#### Conditional: Performance contract (when `performance_task: true` or `performance` overlay selected)

When this task involves performance optimization, add this section to PLAN.md:

## Performance contract
- baseline metrics: <current measurements — must be numeric>
- target metrics: <goals — must be numeric>
- workload: <dataset size / user count / request rate / scenario>
- benchmark command: <exact reproducible command>
- warmup policy: <warmup runs before measurement, or "none">
- guardrail metrics: <metrics that must not regress — must be numeric>
- unacceptable regressions: <explicit fail conditions>

#### Mode C (sprinted) — enhanced format

All standard sections plus sprint contract, detailed risk matrix, explicit rollback steps, and dependency graph.

```markdown
# Plan: <task title>
created: <date>
task_id: TASK__$ARGUMENTS
execution_mode: sprinted
mutates_repo: <true|false>

## Scope in
<what this task will do>

## Scope out
<what this task will NOT do>

## User-visible outcomes
<what changes from the user's perspective — observable behavior, not implementation details>

## Touched files / roots
- <file or directory path>
- <file or directory path>

## QA mode
<tests | smoke | browser-first>

## Sprint contract
- surfaces: <list of repo surfaces: app | api | db | infra | docs>
- roots: <list of repo roots to be touched>
- rollback trigger: <condition that requires immediate rollback>
- staged delivery: <yes | no — whether changes can be delivered incrementally>

## Acceptance criteria
- [ ] <specific, testable criterion 1>
- [ ] <specific, testable criterion 2>

## Verification contract
- commands: <exact commands to run>
- routes: <URLs or API endpoints to hit, or "n/a">
- persistence checks: <database or file state to verify, or "n/a">
- expected outputs: <what success looks like>

## Required doc sync
<which doc surfaces need updating, or "none">

## Hard fail conditions
<conditions that would mean this task has failed>

## Risk matrix
| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| <risk> | low/med/high | low/med/high | <step> |

## Rollback steps
1. <explicit ordered step>
2. <explicit ordered step>

## Dependency graph
- <component A> depends on <component B>
- <migration X> must run before <service Y> restarts>

## Open blockers
<known blockers before implementation, or "none">
```

#### Conditional: Performance contract (when `performance_task: true` or `performance` overlay selected)

When this task involves performance optimization, add this section to PLAN.md:

## Performance contract
- baseline metrics: <current measurements — must be numeric>
- target metrics: <goals — must be numeric>
- workload: <dataset size / user count / request rate / scenario>
- benchmark command: <exact reproducible command>
- warmup policy: <warmup runs before measurement, or "none">
- guardrail metrics: <metrics that must not regress — must be numeric>
- unacceptable regressions: <explicit fail conditions>

#### Broad-build tasks

When `planning_mode: broad-build`, the longform spec artifacts (`01_product_spec.md`, `02_design_language.md`, `03_architecture.md`) must already exist before PLAN.md is written (see step 3.3).

PLAN.md for broad-build tasks must additionally include:
- Reference to `01_product_spec.md` for scope justification
- Which tranche/phase this task delivers from the broader spec
- What is explicitly NOT in this task (scope out from the broader vision)

The supporting documents do NOT replace PLAN.md — they provide context. PLAN.md remains the single executable contract.

If PLAN.md alone is genuinely insufficient (10+ files, cross-domain, high ambiguity) and `planning_mode` is NOT `broad-build`, add ONE supporting document — SPEC.md, DESIGN.md, or TASKS.md. Do not create a hierarchy by default.

### 7. Generate CHECKS.yaml

After writing PLAN.md, extract each acceptance criterion and write `doc/harness/tasks/TASK__$ARGUMENTS/CHECKS.yaml`.

Parse the `## Acceptance criteria` section from PLAN.md. Each `- [ ] <criterion text>` line becomes one entry.

Assign stable IDs sequentially: `AC-001`, `AC-002`, etc.

Infer `kind` from the criterion text using these heuristics:
- `functional` — describes user-visible behavior or a feature outcome
- `verification` — describes a test, check, or validation step
- `doc` — describes documentation or comment requirements
- `risk` — describes a safety, rollback, or constraint condition

Set optional fields when inferable:
- `runtime_required: true` if the criterion requires running the application
- `doc_sync_required: true` if the criterion requires documentation changes

#### close_gate policy

Determine the `close_gate` value based on task risk signals:

Set `close_gate: strict_high_risk` when ANY of:
- `execution_mode: sprinted`
- `review_overlays` contains `security` or `performance`
- `risk_tags` contains `structural`, `migration`, `schema`, or `cross-root`

Otherwise set `close_gate: standard`.

The `close_gate` field is a top-level field in CHECKS.yaml (not inside criteria).

```yaml
close_gate: <standard | strict_high_risk>
checks:
  - id: AC-001
    title: "<criterion text verbatim>"
    status: planned
    kind: functional
    evidence_refs: []
    reopen_count: 0
    last_updated: "<ISO 8601 timestamp>"
    notes: ""
    # optional fields (omit if not applicable):
    # owner_hint: ""
    # related_paths: []
    # overlay_tags: []
    # runtime_required: false
    # doc_sync_required: false
```

**Status values:** `planned` | `implemented_candidate` | `passed` | `failed` | `blocked`

If the PLAN.md has no acceptance criteria (e.g., blank or malformed), write an empty `checks: []` list and note the omission.

### 8. Close plan session

After all plan artifacts are written, close the session:

Update `PLAN_SESSION.json`:
```json
{
  "task_id": "TASK__$ARGUMENTS",
  "state": "closed",
  "phase": "done",
  "source": "plan-skill",
  "opened_at": "<original>",
  "closed_at": "<ISO 8601>"
}
```

Update `TASK_STATE.yaml` field `plan_session_state: closed`.

**HANDOFF.md is NOT created here.** It is a developer-owned artifact created after implementation with verification breadcrumbs.

## Guardrails

- Every acceptance criterion must be testable (no "works correctly")
- Verification contract must have concrete executable commands or endpoints — prose alone is not sufficient
- For browser-first projects: QA mode must be `browser-first`, not `smoke` or `tests` alone
- `execution_mode` must be set in TASK_STATE.yaml and reflected in PLAN.md header
- **Light mode**: Hard fail conditions and Risks/rollback sections may be omitted unless the change is genuinely risky
- **Standard mode**: All mandatory PLAN.md fields must be present (critic-plan will FAIL plans missing required fields)
- **Sprinted mode**: Sprint contract, risk matrix, and rollback steps are mandatory (critic-plan will FAIL if missing)
- **Broad-build mode**: `01_product_spec.md`, `02_design_language.md`, `03_architecture.md` must exist before PLAN.md. These must be high-level product context — NOT low-level code specs.
- **Template sync awareness (plugin repos only)**: When `Touched files / roots` includes `plugin/` paths, note in PLAN.md that template sync will be checked after runtime validation (harness step 6.5). No action needed at plan time — just awareness that the check will happen.
- **Investigate lane**: Set `result_required: true` in TASK_STATE.yaml — investigate tasks cannot close without RESULT.md
