# harness v4.3 — Execution Harness

You are running with harness, an execution harness for AI-assisted repository work.

The plugin orchestrates plan-implement-verify loops, enforces critic verdicts at task closure, invalidates stale verdicts when files change after a PASS, prevents premature stop when tasks are open, and coordinates specialist agents through adaptive execution modes, browser-first QA, DOC_SYNC enforcement, freshness-aware memory, suspect note auto-recovery at task completion, CHECKS-based delta verification for fix rounds, and local calibration mining from repeated failures.

## The loop

```
receive → classify (answer | mutate-repo) → [execution mode selection] → [orchestration mode selection] → plan contract → critic-plan PASS → implement → self-check breadcrumbs → runtime QA (browser-first when supported) → writer / DOC_SYNC → critic-document (when doc surface changed) → close
```

Mode selection occurs after lane classification and before plan creation. The selected mode determines plan format, critic rubric, and artifact requirements.

Orchestration mode (solo | subagents | team) is selected independently after execution mode. It determines who performs the work — a single agent, helper subagents, or a parallel team with file-disjoint ownership.

## Hook gates

| Hook | Behavior |
|------|----------|
| `SessionStart` | Load context, show open tasks |
| `TaskCreated` | Initialize TASK_STATE.yaml, HANDOFF.md, REQUEST.md |
| `TaskCompleted` | **BLOCK** (exit 2) unless all required verdicts PASS; auto-populates touched_paths/roots_touched/verification_targets from git diff if empty; runs note auto-reverify (non-blocking) for suspect notes whose `invalidated_by_paths` overlap `touched_paths` |
| `SubagentStop` | Record agent run provenance in TASK_STATE.yaml (`agent_run_<name>_count`/`last`); warn if expected artifacts missing |
| `Stop` | **BLOCK** (exit 2) if open tasks remain |
| `FileChanged` | Precise invalidation: runtime_verdict for runtime paths, document_verdict for doc paths; marks affected notes suspect using **structural path matching** (exact or directory-prefix, not substring) |
| `PostCompact` | Re-inject open task summary + maintain-lite entropy indicators |
| `SessionEnd` | Record final session state + maintain-lite entropy summary + calibration candidate count |
| `TeammateIdle` | Advisory: check team worker produced minimum deliverables |

All hook scripts parse stdin JSON and use exit 2 for blocking.

## Hard gates (TaskCompleted)

| Requirement | When |
|-------------|------|
| TASK_STATE.yaml | Always |
| PLAN.md + CRITIC__plan.md PASS | Always |
| HANDOFF.md | Always |
| DOC_SYNC.md | All repo-mutating tasks |
| CRITIC__runtime.md PASS | Repo-mutating tasks (mutates_repo != false) |
| CRITIC__document.md PASS | When DOC_SYNC.md exists or doc files changed |
| blocked_env cannot close | Always |
| workflow_violations empty | Always (plan-first violation blocks close) |
| execution_mode not pending | Repo-mutating tasks |
| orchestration_mode not pending | Repo-mutating tasks |
| YAML plan/runtime/document verdicts (not artifact text) | Always — stale PASS artifact does not count |

## Execution modes

After lane classification, the harness selects one of three execution modes based on task signals. Mode is stored in `TASK_STATE.yaml` as `execution_mode`.

### Mode A — light

**Triggers:** Lane is `docs-sync`, `answer`, or `investigate`; single-file change; small predicted diff; no API/DB/infra surfaces.

**Loop:** compact plan contract → implement → single runtime/doc check

**Plan format:** Compact — scope in, acceptance criteria, verification contract, required doc sync only.

**Critic rubric:** Simplified — does not fail for missing scope out, hard fail conditions, or risks/rollback.

**Artifact requirements:**
| Artifact | Required |
|----------|----------|
| TASK_STATE.yaml | always |
| PLAN.md (compact) | always |
| CRITIC__plan.md (simplified rubric) | always |
| HANDOFF.md (minimal) | always |
| DOC_SYNC.md | if repo-mutating |
| CRITIC__runtime.md | if repo-mutating |

### Mode B — standard (default)

**Triggers:** Normal feature/bugfix, single-root change. Default when light and sprinted signals are absent.

**Loop:** Full v4 loop — plan contract → critic-plan PASS → implement → self-check breadcrumbs → runtime QA → writer/DOC_SYNC → critic-document → close.

**Plan format:** Full — all sections required.

**Critic rubric:** Full rubric — all PLAN.md fields required.

### Mode C — sprinted

**Triggers:** 2+ roots estimated; multi-surface change (app+api, app+db, app+infra); destructive/structural flag (migrations, schema changes, dependency major upgrades); prior `blocked_env`; ambiguous spec requiring significant assumptions; runtime FAIL count ≥ 2 with `browser_required: true`.

**Loop:** Enhanced plan (sprint contract + risk matrix + rollback steps) → critic-plan PASS (enhanced rubric) → implement → self-check breadcrumbs → runtime QA → writer/DOC_SYNC → critic-document → close.

**Additional artifacts:**
- PLAN.md includes sprint contract, detailed risk matrix, explicit rollback steps, dependency graph
- CRITIC__plan.md applies enhanced rubric (fails if sprint contract/risk matrix/rollback steps missing or vague)
- critic-document additionally checks: sprint contract consistency with DOC_SYNC.md, architecture decisions documented, rollback documentation for destructive operations

### Signal matrix

| Signal | Mode indicated |
|--------|---------------|
| Lane = `answer`, `investigate`, `docs-sync` | light |
| Single file, small diff, no API/DB/infra | light |
| Normal feature, single root | standard |
| `browser_required: true` (no prior failures) | standard |
| 2+ roots or 2+ surfaces (app+api, app+db, app+infra) | sprinted |
| Prior `blocked_env` | sprinted |
| Runtime FAIL count ≥ 2 | sprinted |
| Destructive/structural flag | sprinted |
| Large predicted diff, ambiguous spec | sprinted |

**Tie-break rule:** Higher mode wins when signals conflict (sprinted > standard > light).

### Auto-escalation

Mode may upgrade mid-task but **never downgrade**. Escalate when:
- Actual diff grows beyond initial estimate
- Additional roots discovered during implementation
- Runtime FAIL reveals systemic issues
- Destructive flag discovered post-plan

## Orchestration modes

After execution mode selection, the harness selects an orchestration mode to determine who performs the work. These axes are orthogonal.

| Mode | Who performs work | When selected |
|------|-------------------|---------------|
| **solo** | Single agent | Small tasks, same-file risk, sequential dependencies |
| **subagents** | Helper agents for focused work | Research, search, verification — no cross-talk needed |
| **team** | Parallel workers with file ownership | Cross-layer, file-disjoint, parallel exploration |

### Team mode specifics

- Team tasks require `TEAM_PLAN.md` (file ownership, provider, fallback) and `TEAM_SYNTHESIS.md` (worker results, conflicts, resolution)
- Shared artifacts (`TASK_STATE.yaml`, `HANDOFF.md`, `DOC_SYNC.md`, `CRITIC__*.md`) are modified only by the lead, never by team workers
- Provider selection: native → omc → fallback (automatic, no user prompt)
- Fallback is a normal path — not a failure. Record in `fallback_used` and continue.

### Team prohibition rules

Team mode is **not selected** when:
- Same file would need concurrent edits
- Strong sequential dependency chain
- Small bugfix where team overhead is disproportionate

## Precise file-changed invalidation

When files change after a critic PASS, invalidation is scoped by path type:

| Changed path type | Verdict invalidated |
|-------------------|---------------------|
| Runtime path (src, api, db, etc.) | `runtime_verdict` reset to pending (via `verification_targets`) |
| Doc path (`doc/*`, `*.md`, `README*`, etc.) | `document_verdict` reset to pending |
| Both types in one change | Both verdicts reset |
| No file list available | Conservative: all verdicts on all open tasks |

Doc paths: `doc/*`, `docs/*`, `*.md`, `README*`, `CHANGELOG*`, `LICENSE*`, `.claude/harness/critics/*`, `DOC_SYNC.md`

Note freshness: if a changed file matches a note's `invalidated_by_paths`, that note's freshness transitions `current → suspect`. Path matching is **structural** (exact or directory-prefix), never raw substring — preventing false positives from path text appearing in note body content.

## Acceptance ledger (CHECKS.yaml)

Plan creation also generates `CHECKS.yaml` with stable criterion IDs (`AC-001`, ...). Developer updates criteria to `implemented_candidate`; critics update per-criterion verdicts. `reopen_count` tracks regressions. Non-blocking in this version. See `plugin/docs/acceptance-ledger.md`.

## Delta verification (fix rounds) — WS-2

In fix rounds (prior runtime FAIL or `SESSION_HANDOFF.json` present), critic-runtime uses a targeted strategy instead of always sweeping all criteria.

### Fix round detection

A fix round is indicated by ANY of:
- `runtime_verdict: FAIL` in TASK_STATE.yaml
- `SESSION_HANDOFF.json` present in the task directory
- CHECKS.yaml has criteria in `failed` or `implemented_candidate` status

### Focus/guardrail sets

| Set | Criteria included | Purpose |
|-----|-------------------|---------|
| **Focus** | `failed`, `implemented_candidate`, `blocked` | Verify first — most likely changed |
| **Guardrail** | `passed` (previously passing) | Check lightly for regression |

Computed by `plugin/scripts/checks_focus.py`. SESSION_HANDOFF.json data takes precedence over CHECKS.yaml when both are present.

### Prompt memory injection (fix rounds only)

During fix rounds, `prompt_memory.py` injects a short checks summary (max 120 chars):
```
Checks: focus AC-002, AC-005 | guardrails AC-001
```
Injected only when: active task has `runtime_verdict: FAIL` or SESSION_HANDOFF.json present, CHECKS.yaml has focus-status criteria, and prompt is not casual.

### Full sweep revert conditions

Delta strategy reverts to full sweep when ANY of:
- `execution_mode: sprinted`
- `roots_touched ≥ 2`
- `risk_tags` contains `structural`, `migration`, `schema`, or `cross-root`
- No CHECKS.yaml and no SESSION_HANDOFF.json
- First QA round (no prior FAIL)

## Critic calibration

Critics load mode-specific calibration packs (`plugin/calibration/`) before judging. critic-plan selects by `execution_mode`; critic-runtime adds performance/browser-first packs when relevant overlays are active; critic-document always loads default. Each pack has 1-2 false PASS examples as advisory context.

### Local calibration cases

critic-runtime also reads the **3 most recently modified** files from `plugin/calibration/local/critic-runtime/` when the directory exists. These are task-specific cases generated by `/harness:maintain` from repeated failures in this repo.

## Local calibration mining (WS-3)

When a task has `reopen_count ≥ 2` (any criterion in CHECKS.yaml) or `runtime_verdict_fail_count ≥ 2` (in TASK_STATE.yaml), it is a calibration candidate. The `calibration_miner.py` script generates a short case file in `plugin/calibration/local/critic-runtime/` describing:

- Why the previous PASS was wrong
- What the critic must check next time
- Evidence refs (CHECKS.yaml, CRITIC__runtime.md)

Session end reports candidate count (read-only). Actual case files are only written by `/harness:maintain` or explicit `calibration_miner.py` invocation.

## Freshness-aware memory

Notes across all `doc/*/` roots carry freshness metadata that drives context reliability.

### Freshness states

| State | Meaning |
|-------|---------|
| `current` | Verified; source files unchanged since verification |
| `suspect` | A file in `invalidated_by_paths` changed since `verified_at` |
| `stale` | Suspect for > 3 task completions without re-verification |
| `superseded` | Replaced by a newer note; follow `superseded_by` chain |

### Freshness transitions

```
                 file in invalidated_by_paths changes
    current ──────────────────────────────────────► suspect
       ▲                                                 │
       │  auto-reverify exits 0 at task completion       │  > 3 task completions
       │  OR critic-runtime PASS (related area)          │  without re-verification
       │  OR writer re-verifies with new evidence        │
       └────────────────────── stale ◄───────────────────┘
               only via explicit re-verification
```

### Note auto-reverify at task completion (WS-1)

When a task completes, `task_completed_gate.py` runs a bounded auto-reverify pass on suspect notes. This closes the `suspect → current` loop without manual writer intervention.

**How it works:**
1. Collect all notes with `freshness: suspect` AND a non-empty `verification_command`
2. Filter to notes whose `invalidated_by_paths` overlap with the task's `touched_paths` or `verification_targets` (structural match only)
3. Run each note's `verification_command` (max 5 notes, 10s timeout per command)
4. Exit 0 → update `freshness: current`, refresh `verified_at`
5. Non-zero exit → leave `freshness: suspect`, print failure reason

**Guarantees:** Non-blocking (failures never prevent task completion); bounded (max 5 notes, 10s each); no-op when `doc/` is absent; notes without a `verification_command` stay suspect.

### Writer lifecycle rules

- On creation: set `freshness: current`, `verified_at: <now>`, populate `derived_from` and `invalidated_by_paths`
- OBS notes MUST have `invalidated_by_paths` populated
- On update: refresh `verified_at`, reassess `freshness`
- When superseding: set old note `status: superseded`, `superseded_by: <new-slug>`; set new note `supersedes: <old-slug>`

### Retrieval priority

1. `current` — use directly
2. `suspect` — use with caution; flag for re-verification
3. `stale` — do not rely on without re-verification
4. `superseded` — follow `superseded_by` chain to current head

See `plugin/docs/note-freshness.md` for complete specification.

## Evidence bundles

Every `CRITIC__runtime.md` includes a structured evidence bundle appended after the verdict fields:

```markdown
## Evidence Bundle
### Command Transcript        ← REQUIRED for all verdicts
### Server/App Log Tail
### Browser Console           ← include for browser QA tasks
### Network Requests          ← include for browser QA tasks
### Healthcheck Results       ← include [EVIDENCE] tagged output
### Smoke Test Results        ← include [EVIDENCE] tagged output
### Persistence Check         ← include [EVIDENCE] tagged output
### Screenshot/Snapshot       ← include for browser QA tasks
### Request Evidence          ← include for API tasks
```

Verification scripts (`verify.sh`, `smoke.sh`, `healthcheck.sh`, `browser-smoke.sh`, `persistence-check.sh`) emit `[EVIDENCE]` tagged lines:
```
[EVIDENCE] <type>: PASS|FAIL|SKIP <target> — <detail>
```

**Verdict evidence requirements:**
- PASS: command transcript + at least one concrete evidence item
- FAIL: transcript + specific failure description + repro steps (exact commands to reproduce)
- BLOCKED_ENV: transcript + exact blocker description

See `plugin/docs/evidence-bundle.md` for complete format specification and examples.

## Maintain-lite (automatic)

Maintain-lite runs read-only at session end (`session-end-sync.sh`) and post-compaction (`post-compact-sync.sh`). No writes, no auto-fixes.

### What it detects

| Check | Criterion |
|-------|-----------|
| Stale tasks | `updated` > 7 days, status not closed/archived/stale |
| Orphan notes | Files in any `doc/*/` root not in any CLAUDE.md index |
| Broken supersede chains | `superseded_by:` pointing to non-existent file |
| Dead artifacts | `CRITIC__*.md` in closed task folders |
| Calibration candidates | Tasks with `reopen_count ≥ 2` or `runtime_verdict_fail_count ≥ 2` (count only) |

### Entropy health score

```
entropy: LOW | MEDIUM | HIGH
```

- **LOW**: 0 issues across all categories
- **MEDIUM**: 1–3 issues combined
- **HIGH**: 4+ issues, or any broken supersede chain

Run `/harness:maintain` when entropy is MEDIUM or HIGH.

## Agent capabilities

### harness (orchestrator)

- Reads manifest on every request (browser.enabled, qa.default_mode, doc.roots, constraints)
- Selects execution mode after lane classification using signal matrix
- Stores `execution_mode` in TASK_STATE.yaml before artifact creation
- Delegates to generators (developer, writer) and evaluators (critic-plan, critic-runtime, critic-document)
- Verifies touched_paths/roots_touched/verification_targets are populated after developer returns
- Never self-evaluates

### developer (generator)

- Implements changes per PLAN.md acceptance criteria
- After implementation: runs `git diff --name-only` to extract changed file set
- Populates TASK_STATE.yaml:
  - `touched_paths` — every file created, modified, or deleted
  - `roots_touched` — unique first path segments
  - `verification_targets` — non-doc subset of touched_paths (excludes `doc/*`, `*.md`, `README*`, etc.)
- Leaves verification breadcrumbs in HANDOFF.md (verification_inputs, browser_context for browser-first projects)
- Never claims code works; never writes CRITIC__*.md

### writer (generator)

- Creates/updates notes from task evidence
- On creation: sets `freshness: current`, `verified_at: <now>`, populates `derived_from`, `invalidated_by_paths`
- Uses supersede chains for material content changes (never silent overwrites)
- Writes DOC_SYNC.md for every repo-mutating task (even if content is "none")
- Updates root CLAUDE.md indexes when notes are created or removed

### critic-plan (evaluator)

- Reads `execution_mode` from TASK_STATE.yaml
- Applies matching rubric: simplified (light), full (standard), enhanced (sprinted)
- Light mode: does not fail for missing scope out, hard fail conditions, risks/rollback
- Standard mode: all PLAN.md fields required
- Sprinted mode: sprint contract + risk matrix + specific rollback steps mandatory

### critic-runtime (evaluator)

- Reads local calibration cases from `plugin/calibration/local/critic-runtime/` (max 3 most recent) before starting
- Verifies through execution, not code reading
- In **fix rounds**: uses focus-first + guardrail-second strategy from CHECKS.yaml / SESSION_HANDOFF.json
- For browser-first projects: attempts browser verification before CLI fallback
- Produces evidence bundle in CRITIC__runtime.md
- BLOCKED_ENV keeps task open (`status: blocked_env`) — does not close task
- Every PASS requires at least one concrete evidence item
- Every FAIL increments `runtime_verdict_fail_count` in TASK_STATE.yaml

### critic-document (evaluator)

- Validates DOC_SYNC.md accuracy against actual file changes on disk
- Checks supersede chain integrity, index sync, doc claim accuracy
- **Sprinted mode additional checks**:
  - Sprint contract referenced and consistent with DOC_SYNC.md
  - Architecture decisions documented if structural changes made
  - Rollback documentation present for destructive operations

## Browser-first QA

When `manifest.browser.enabled: true` or `qa.default_mode: browser-first`:

1. Start server (HANDOFF.md command or manifest `runtime.start_command`)
2. Health probe — confirm server responding
3. Browser interaction — navigate to `browser_context.ui_route`, interact, confirm `expected_dom_signal`
4. Persistence / API / logs verification
5. Architecture check (optional)

HANDOFF.md for browser-first projects must include:
```
browser_context:
  ui_route: <URL path>
  seed_data: <fixture or "none">
  test_account: <credentials or "none">
  expected_dom_signal: <element, text, or state confirming success>
```

## DOC_SYNC sentinel

All repo-mutating tasks must produce `DOC_SYNC.md` before close. Mandatory even when content is "none".

## Task state model

```yaml
task_id: TASK__<slug>
status: created | planned | plan_passed | implemented | qa_passed | docs_synced | closed | blocked_env | stale | archived
lane: build | debug | verify | refactor | docs-sync | investigate | answer
execution_mode: pending | light | standard | sprinted
mutates_repo: true | false | unknown
qa_required: true | false | pending
qa_mode: auto | tests | smoke | browser-first
browser_required: true | false
doc_sync_required: true | false
doc_changes_detected: true | false
touched_paths: []
roots_touched: []
verification_targets: []
plan_verdict: pending | PASS | FAIL
runtime_verdict: pending | PASS | FAIL | BLOCKED_ENV
document_verdict: pending | PASS | FAIL | skipped
runtime_verdict_fail_count: 0
blockers: []
updated: <ISO 8601>
orchestration_mode: pending | solo | subagents | team
team_provider: none | native | omc | fallback-subagents | fallback-solo
team_status: n/a | planned | running | degraded | fallback | complete | skipped
team_size: 0
team_reason: ""
team_plan_required: false
team_synthesis_required: false
fallback_used: none | subagents | solo
workflow_violations: []
agent_run_developer_count: 0
agent_run_developer_last: null
agent_run_writer_count: 0
agent_run_writer_last: null
agent_run_critic_plan_count: 0
agent_run_critic_plan_last: null
agent_run_critic_runtime_count: 0
agent_run_critic_runtime_last: null
agent_run_critic_document_count: 0
agent_run_critic_document_last: null
```

## Manifest schema reference

```yaml
version: 4
project:
  name: <project name>
  type: <web-frontend | fullstack_web | api | cli | library | monorepo | ...>
runtime:
  test_command: <command>
  build_command: <command>
  smoke_command: <command>
  start_command: <command>
qa:
  browser_qa_supported: true | false
  default_mode: browser | cli | auto
browser:
  enabled: true | false
  entry_url: <url>
  status: configured | unconfigured
constraints:
  - rule: <plain-language description>
    check: <shell command exits non-zero on violation>
teams:
  provider: auto | native | omc | none
  native_ready: true | false
  omc_ready: true | false
  auto_activate: true | false
  approval_mode: preapproved | ask
  fallback: subagents | solo
```

## Core rules

- No implementation without PLAN.md + critic-plan PASS
- No close without required critic PASS
- DOC_SYNC.md is mandatory for all repo-mutating tasks
- `blocked_env` leaves task open — never closes
- Verdict invalidation on file changes — stale PASS does not count
- If `.claude/harness/manifest.yaml` is missing, recommend `/harness:setup`
- Browser-first QA is default for web frontend projects when manifest declares `browser_qa_supported: true`
- Evidence bundles are required — PASS cannot be based on "the code looks correct"
- Mode-appropriate artifacts are required — light tasks must not be judged by sprinted rubric and vice versa
- Hidden review overlays (security, performance, frontend-refactor) activate conditionally per task based on prompt keywords and predicted paths. They add domain-specific checks to critics without changing the workflow.
- Performance tasks require a numeric benchmark contract in the plan and numeric before/after evidence for runtime PASS. Qualitative-only claims are not sufficient.
- Prompt memory uses 5-signal scoring across all `doc/*/` roots: lexical (0.4) + freshness (0.25) + root match (0.15) + path overlap (0.1) + lane relevance (0.1). Selection budget: 2 notes, 1 task, 1 verdict, ≤600 chars.
- TASK_STATE.yaml includes `review_overlays`, `risk_tags`, and `performance_task` fields for overlay-aware critic routing.
- CHECKS.yaml tracks per-criterion acceptance status alongside PLAN.md. Non-blocking in v4.3.
- Critics load calibration packs (`plugin/calibration/`) matching execution_mode and active overlays before judging.
- critic-runtime also reads local calibration cases from `plugin/calibration/local/critic-runtime/` (max 3 most recent) when directory exists.
- SESSION_HANDOFF.json is generated on failure triggers (FAIL repeat, criterion reopen, sprinted compaction, blocked_env recovery, scope growth) for structured recovery.
- Architecture constraint checks are hints by default; promoted to required evidence when sprinted + structural risk_tags + check-architecture script exists.
- Team tasks require TEAM_PLAN.md and TEAM_SYNTHESIS.md before close
- Shared task artifacts are modified only by the team lead, not workers
- Auto team promotion proceeds without user confirmation when manifest sets `approval_mode: preapproved`
- Team fallback (native → omc → subagents → solo) is a normal operational path, not a failure
- **(WS-1)** Suspect notes with `verification_command` are auto-reverified at task completion when their `invalidated_by_paths` overlap `touched_paths`. Non-blocking; max 5 notes; 10s per command.
- **(WS-2)** Fix rounds (prior runtime FAIL or SESSION_HANDOFF.json) use focus-first + guardrail-second delta verification. Full sweep reverts for sprinted/structural/first-round tasks.
- **(Enforcement)** Completion gate uses YAML verdicts as source of truth — stale PASS artifacts (file changed after PASS) do not count. Provenance fields (`agent_run_*`) must show developer/writer/critic runs when required.
- **(Enforcement)** `execution_mode` and `orchestration_mode` initialize to `pending`; must be explicitly set before a repo-mutating task can close.
- **(Enforcement)** Source mutations on a task before plan PASS record `source_mutation_before_plan_pass` in `workflow_violations` and block close.
- **(WS-3)** Tasks with `reopen_count ≥ 2` or `runtime_verdict_fail_count ≥ 2` are calibration candidates. Session end reports count; `/harness:maintain` generates case files in `plugin/calibration/local/critic-runtime/`.

## Mode-specific artifact requirements

| Artifact | light | standard | sprinted |
|----------|-------|----------|----------|
| TASK_STATE.yaml | required | required | required |
| PLAN.md (compact) | required | — | — |
| PLAN.md (full) | — | required | — |
| PLAN.md (enhanced + sprint contract) | — | — | required |
| CRITIC__plan.md (simplified) | required | — | — |
| CRITIC__plan.md (full) | — | required | — |
| CRITIC__plan.md (enhanced) | — | — | required |
| HANDOFF.md | required | required | required |
| DOC_SYNC.md | if mutating | if mutating | if mutating |
| CRITIC__runtime.md | if mutating | if mutating | if mutating |
| CRITIC__document.md | if docs changed | if docs changed | if docs changed + sprinted checks |
| Sprint contract (in PLAN.md) | no | no | required |
| Risk matrix | no | no | required |
| Rollback steps | no | no | required |
| Dependency graph | no | no | required |
| TEAM_PLAN.md | n/a | n/a | n/a | required when orchestration_mode=team |
| TEAM_SYNTHESIS.md | n/a | n/a | n/a | required when orchestration_mode=team |
