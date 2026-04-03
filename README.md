# harness — execution harness for AI-assisted repository work

Version 2.0.0

## What it does

Harness is an execution harness that orchestrates plan-implement-verify loops for AI-assisted repository work. It enforces critic verdicts at task closure, invalidates stale verdicts when files change, scales ceremony to task complexity via adaptive execution modes, and coordinates specialist agents through the full task lifecycle.

## The loop

```
receive → classify → [mode selection] → plan contract → critic-plan PASS → implement → self-check breadcrumbs → runtime QA (browser-first when supported) → writer / DOC_SYNC → critic-document (when doc surface changed) → close
```

The only hard gate is at **task completion**: all required critic verdicts must PASS. A stale PASS (recorded before subsequent file changes) does not count.

| Requirement | When needed |
|-------------|-------------|
| TASK_STATE.yaml | Always |
| PLAN.md + critic-plan PASS | Always |
| HANDOFF.md | Always |
| Runtime critic PASS | Repo-mutating tasks |
| DOC_SYNC.md | All repo-mutating tasks |
| Document critic PASS | When doc surface changed |

Tasks with `blocked_env` status cannot close.

## Execution modes

The harness selects an execution mode after lane classification to scale ceremony to task complexity.

| Mode | When | Plan format | Critic rubric |
|------|------|-------------|---------------|
| **light** | Docs-only, single-file, answer/investigate lanes | Compact (scope + criteria + verification) | Simplified — does not fail for missing rollback |
| **standard** | Normal feature/bugfix, single-root change | Full — all sections required | Full rubric |
| **sprinted** | Cross-root, multi-surface, destructive operations, prior blocked_env | Enhanced — adds sprint contract, risk matrix, rollback steps, dependency graph | Enhanced rubric |

Mode is stored as `execution_mode` in `TASK_STATE.yaml`. Mode may escalate mid-task (`light → standard`, `standard → sprinted`) but never downgrade.

### Signal matrix (summary)

| Signal | Mode |
|--------|------|
| Lane = `docs-sync`, `answer`, `investigate` | light |
| Single file, small diff | light |
| Normal feature, single root | standard |
| 2+ roots or 2+ surfaces (app+api, app+db) | sprinted |
| Prior `blocked_env`, runtime FAIL ≥ 2 | sprinted |
| Destructive/structural flag (migration, schema, major dep upgrade) | sprinted |

See `plugin/docs/execution-modes.md` for the full signal matrix, artifact table, and auto-escalation rules.

## Planning modes

Planning depth is selected independently from execution mode and stored as `planning_mode` in `TASK_STATE.yaml`.

| Mode | When | Effect |
|------|------|--------|
| **standard** | Default for bugfixes, refactors, investigations, and already-detailed specs | Write `PLAN.md` directly |
| **broad-build** | Broad build-lane product requests with sparse specs and no strong file anchors | Write `01_product_spec.md`, `02_design_language.md`, `03_architecture.md` before `PLAN.md` |

`task_start` promotes `planning_mode: broad-build` conservatively. It only triggers before a plan exists, and it stays off for bugfixes, refactors, performance work, single-component requests, or detailed technical specs.

## Orchestration modes

The harness selects an orchestration mode independently from the execution mode. These are orthogonal axes: execution mode controls ceremony/verification depth, orchestration mode controls who performs the work.

| Mode | When | Artifacts |
|------|------|-----------|
| **solo** | Single-file, sequential dependencies, same-file conflict risk, small tasks | Standard artifacts only |
| **subagents** | Helper parallelism (research, search, verify), no cross-talk needed | Standard artifacts only |
| **team** | Cross-layer work (app+api+tests), file-disjoint ownership, parallel exploration | TEAM_PLAN.md + TEAM_SYNTHESIS.md |

Orchestration mode is stored as `orchestration_mode` in `TASK_STATE.yaml`. The two axes combine freely — for example, `execution_mode: sprinted` + `orchestration_mode: team` is valid for a cross-root, multi-surface task with parallel workers.

### Auto team promotion

The harness is now **team-first for non-trivial work**:
- `solo` is reserved for clearly small / low-overhead tasks
- `team` is preferred for broad-build, maintenance, multi-root, or multi-surface work when file ownership can be partitioned safely
- `subagents` is the default fallback for non-trivial work when safe team ownership or provider readiness is not established

Team mode is still **prohibited** when:
- Same file would need concurrent edits by multiple workers
- Work has strong sequential dependencies
- Task is a small bugfix that team overhead would dwarf

### Provider fallback

When team mode is selected, the harness probes for available providers:

1. **Native** Claude Code teams (if `native_ready: true` in manifest)
2. **OMC** teams (if `omc_ready: true` in manifest)
3. **Fallback** to subagents or solo (no user prompt — automatic)

The harness never asks the user for team permission when `approval_mode: preapproved`.

See `plugin/docs/orchestration-modes.md` for the full selection algorithm and provider policy.

## Agents

| Agent | Role |
|-------|------|
| `harness` | Orchestrating harness — classifies requests, selects execution and orchestration modes, drives the loop, gates completion |
| `developer` | Code generator — implements changes, populates touched_paths/verification_targets, updates HANDOFF.md |
| `writer` | Doc generator — creates/updates notes with freshness metadata, writes DOC_SYNC.md |
| `critic-plan` | Evaluator — validates plan contract using mode-matched rubric |
| `critic-runtime` | Evaluator — runtime verification with mandatory evidence bundle |
| `critic-document` | Evaluator — doc validation, DOC_SYNC accuracy, sprinted-mode structural checks |

## Browser-first QA

When the project manifest declares `browser_qa_supported: true`, the runtime critic prioritizes browser interaction over text-based checks. This applies to web frontend projects where visual and functional verification is best performed in a real browser session.

Browser-first is auto-detected during setup using a 4-signal process: framework/package detection, structure detection, executability check, and server-only exclusion rules.

## Acceptance ledger (CHECKS.yaml)

When `/harness:plan` creates a task, it also generates a machine-readable `CHECKS.yaml` alongside `PLAN.md`. Each acceptance criterion receives a stable ID (`AC-001`, `AC-002`, ...) and structured status tracking:

```yaml
checks:
  - id: AC-001
    title: "User can create account"
    status: planned | implemented_candidate | passed | failed | blocked
    kind: functional | verification | doc | risk
    evidence_refs: []
    reopen_count: 0
    last_updated: "2026-03-29T00:00:00Z"
```

The developer updates criteria to `implemented_candidate` after implementation. Critics update per-criterion verdicts (`passed`/`failed`) with evidence refs. The `reopen_count` tracks regression (re-failure after a previous pass). The ledger is informational — it does not block task completion in this version.

See `plugin/docs/acceptance-ledger.md` for the full schema and lifecycle.

## Critic calibration

Critics load mode-specific calibration packs before judging. These are short few-shot examples of common false PASS patterns, stored in `plugin/calibration/`:

| Critic | Calibration files |
|--------|-------------------|
| critic-plan | `light.md`, `standard.md`, `sprinted.md` — selected by `execution_mode` |
| critic-runtime | `default.md` (always), `performance.md` (when `performance_task`), `browser-first.md` (when browser QA) |
| critic-document | `default.md` (always) |

Calibration is advisory context for the critic, not a rigid checklist. Each file contains 1-2 examples of false PASS patterns and correct judgments.

## Freshness-aware memory

The harness maintains durable memory across `doc/*/` roots (default: `doc/common/`) using three note types:

- **REQ** — requirements and constraints from the project or user
- **OBS** — observations from repo scans, test runs, or runtime checks
- **INF** — inferences and conclusions derived from REQ/OBS evidence

Notes carry freshness metadata that transitions automatically:

| State | Meaning |
|-------|---------|
| `current` | Verified; source files unchanged since verification |
| `suspect` | A file in `invalidated_by_paths` has changed since verification |
| `stale` | Suspect for more than 3 task completions without re-verification |
| `superseded` | Replaced by a newer note; follow `superseded_by` chain |

The write-tool `PostToolUse` matcher (`Write|Edit|MultiEdit`) marks notes `suspect` when their `invalidated_by_paths` are modified. The writer agent restores `suspect → current` after re-verification.

See `plugin/docs/note-freshness.md` for the full freshness lifecycle specification.

## Hidden review overlays

The harness conditionally activates domain-specific review overlays based on task signals — prompt keywords, predicted file paths, and lane classification. Three overlays are available: **security**, **performance**, and **frontend-refactor**. When active, critics apply additional checks specific to that domain. No new commands or workflows are needed — overlays are selected automatically during planning and stored in `TASK_STATE.yaml`.

## Performance evidence

Performance tasks (those with `performance_task: true` or a `performance` overlay) require numeric before/after evidence for runtime critic PASS. Qualitative claims alone ("it's faster") are not sufficient. The plan must include a performance contract with baseline metrics, target metrics, and a reproducible benchmark command. The runtime critic requires a Performance Comparison section in the evidence bundle.

## Memory retrieval

The prompt memory system uses multi-signal relevance scoring across all registered doc roots (not just `doc/common/`). Five signals are combined in a linear scorer:

| Signal | Weight | Description |
|--------|--------|-------------|
| Lexical relevance | 0.40 | Keyword match ratio (Unicode-aware, supports Korean/CJK) |
| Freshness | 0.25 | current=1.0, suspect=0.5, stale=0.1, superseded=excluded |
| Root match | 0.15 | Bonus when note's root matches the active task root |
| Path overlap | 0.10 | Overlap between query paths and note's `path_scope` |
| Lane relevance | 0.10 | Bonus when note's `lane` matches current task lane |

Query tokenization handles: Unicode word splitting, file path segments, `snake_case`/`camelCase`/`kebab-case` decomposition, and 2-3gram fallback. The selection budget remains top 2 notes, 1 task, 1 verdict within 600 characters.

Notes may carry optional retrieval metadata (`root`, `lane`, `path_scope`, `topic_tags`) for better scoring. Notes without these fields use defaults and score normally.

See `plugin/docs/retrieval-selection.md` for the full algorithm specification.

## Design decisions

The following were evaluated and intentionally **not** included in this version:

- **Full multi-agent DAG**: Visible orchestration adds complexity without proportional benefit for the current single-loop model.
- **SendMessage-based agent communication**: File-based artifact contracts are preferred for reproducibility and debuggability.
- **Full knowledge-base-builder governance**: Only the relevance selection quality improvement was needed, not the full taxonomy/RACI framework.
- **ASMR ensemble / decision forest**: Experimental concept with insufficient certainty for production use.
- **Large domain catalog**: Would bloat setup output and maintenance surface beyond the "setup + plain language" philosophy.

## Precise invalidation

When files change after a critic PASS, verdict invalidation is path-scoped:

- **Runtime path change** → invalidates `runtime_verdict` only (via `verification_targets`)
- **Doc path change** → invalidates `document_verdict` only
- **Both** → invalidates both
- **No file list available** → conservative fallback: invalidates all verdicts on all open tasks

Doc paths: `doc/*`, `docs/*`, `*.md`, `README*`, `CHANGELOG*`, `LICENSE*`, `doc/harness/critics/*`, `DOC_SYNC.md`

## Evidence bundles

Every `CRITIC__runtime.md` includes a structured evidence bundle after the verdict fields:

```
## Evidence Bundle
### Command Transcript    ← required for all verdicts
### Server/App Log Tail
### Browser Console       ← required for browser QA tasks
### Network Requests
### Healthcheck Results
### Smoke Test Results
### Persistence Check
### Screenshot/Snapshot
### Request Evidence
```

Verification scripts emit `[EVIDENCE]` tagged lines for easy extraction. PASS verdict requires at minimum: command transcript + one concrete evidence item. FAIL verdict requires: transcript + specific failure description + repro steps.

See `plugin/docs/evidence-bundle.md` for the full format specification and examples.

## DOC_SYNC sentinel

All repo-mutating tasks produce a `DOC_SYNC.md` file. This sentinel records which documentation surfaces were affected and confirms they are consistent with the code changes. The document critic validates DOC_SYNC accuracy before task close.

## Handoff escalation

When specific failure triggers are detected, the harness generates a `SESSION_HANDOFF.json` in the task folder for structured recovery:

| Trigger | Condition |
|---------|-----------|
| `runtime_fail_repeat` | Runtime verdict FAIL count >= 2 |
| `criterion_reopen` | Same acceptance criterion reopen_count >= 2 |
| `sprinted_compaction` | Sprinted task undergoes context compaction |
| `blocked_env_recovery` | Re-entry after blocked_env resolution |
| `scope_growth` | Touched roots grew significantly beyond plan estimate |

The handoff includes: open check IDs, last fail evidence, next recovery step, paths in focus, and a do-not-regress list. For `orchestration_mode: team`, it also carries a `team_recovery` block describing whether resume should start from `TEAM_PLAN.md`, missing `team/worker-<name>.md` summaries, or `TEAM_SYNTHESIS.md`, plus the pending owned paths for the blocked workers. It now also records per-worker recovery details (artifact path, owned writable paths, handled paths, verification excerpt, residual-risk excerpt), documentation owners for `DOC_SYNC.md` / `CRITIC__document.md`, and whether `HANDOFF.md` has gone stale relative to the latest team artifacts. It is consumed by the harness on session re-entry and by the developer for recovery context.

Normal successful tasks produce no handoff artifact. See `plugin/docs/handoff-escalation.md`.

## Failure case history

Fix rounds now also emit a lightweight `FAILURE_CASE.json` sidecar in each task folder. It stores a compact summary of the failure surface already present in harness artifacts: lane, failing checks, path focus, verdict counts, and a short excerpt.

This stays intentionally conservative — no external DB, no embeddings, and no cross-repo writes. The index is only used to make repeated failures easier to compare.

`hctl` adds three inspection commands:

- `hctl history` — list indexed failure cases across task history
- `hctl top-failures --task-dir ...` — show the top similar past failures for the current task
- `hctl diff-case --case-a TASK__... --case-b TASK__...` — compare two cases by shared checks / paths / keywords

The runtime control plane still keeps prompt context small: fix rounds surface at most the top 3 similar cases, while the prompt hook continues to inject only the single best hint. See `plugin/docs/failure-history.md`.

## Architecture check promotion

Architecture constraint checks default to **hints only**. They are promoted to **required evidence** when all conditions are met:

1. `execution_mode` is `sprinted`
2. `risk_tags` contain structural / migration / schema / cross-root
3. `doc/harness/constraints/check-architecture.*` file exists

When promoted, the runtime critic requires the architecture check result in the evidence bundle. Script absence = skip (not fail). Normal/light tasks are never affected.

See `plugin/docs/architecture-promotion.md`.

## Maintain-lite

At session end and post-compaction, the harness automatically runs maintain-lite: a read-only entropy scan across all `doc/*/` roots with no writes or auto-fixes.

| Check | What it detects |
|-------|----------------|
| Stale tasks | `updated` > 7 days, status not closed/archived/stale |
| Orphan notes | Files in any `doc/*/` root not referenced in any CLAUDE.md index |
| Broken supersede chains | `superseded_by:` pointing to non-existent file |
| Dead artifacts | `CRITIC__*.md` in closed task folders |

Results include an entropy health score: `LOW` / `MEDIUM` / `HIGH`. Run `/harness:maintain` when entropy is MEDIUM or HIGH.

## Runtime playbooks

Critic agents follow playbooks stored in `doc/harness/critics/`. These define the verification steps for plan validation (`plan.md`) and runtime checks (`runtime.md`), scoped to the project shape declared in the manifest.

## Install

Add this plugin to your Claude Code configuration.

## Usage

Run `/harness:setup` in your project to bootstrap. Then work in plain language.

## Skills

| Skill | Description |
|-------|-------------|
| `/harness:setup` | Bootstrap harness in target project |
| `/harness:plan` | Create task contract (PLAN.md) with mode-appropriate format |
| `/harness:maintain` | Doc and task cleanup — auto-fixes indexes, stale tasks, orphaned notes |

## Plugin structure

```
plugin/
  .claude-plugin/plugin.json     # plugin manifest
  CLAUDE.md                      # plugin instructions
  settings.json                  # default agent config
  hooks/hooks.json               # hook configuration
  agents/                        # 6 agent definitions
    harness.md                   # orchestrating harness (mode selection, signal matrix, handoff reading)
    developer.md                 # generator — code + touched_paths + CHECKS.yaml updates
    writer.md                    # generator — notes with freshness + retrieval metadata
    critic-plan.md               # evaluator — mode-aware plan validation + calibration
    critic-runtime.md            # evaluator — runtime verification + evidence bundle + calibration + arch promotion
    critic-document.md           # evaluator — doc validation + sprinted-mode checks + calibration
  skills/
    plan/SKILL.md                # create task contract (mode-aware formats) + CHECKS.yaml generation
    maintain/SKILL.md            # cleanup tool + maintain-lite docs
    setup/SKILL.md               # bootstrap target project (multi-root support, arch constraints)
  scripts/
    _lib.py                      # shared helpers
    prompt_memory.py             # context injection (multi-root retrieval + single similar-failure hint)
    memory_selectors.py          # note scoring (5-signal, Unicode-aware)
    failure_memory.py            # FAILURE_CASE sidecar + top-k similar-failure retrieval
    hctl.py                      # task control plane + failure history/top-failures/diff-case CLI
    task_completed_gate.py       # completion gate + CHECKS.yaml warnings
    file_changed_sync.py         # precise invalidation (all doc/* roots)
    session_end_sync.py          # session end summary + maintain-lite (all roots) + handoff
    post_compact_sync.py         # post-compaction context + maintain-lite + handoff escalation
    handoff_escalation.py        # SESSION_HANDOFF.json trigger detection + generation
    stop_gate.py                 # stop gate (blocks if open tasks)
    capability_probe.py           # delegation + team provider detection
    teammate_idle_gate.py          # team worker deliverable check
  calibration/                   # critic calibration packs (few-shot examples)
    critic-plan/                 # light.md, standard.md, sprinted.md
    critic-runtime/              # default.md, performance.md, browser-first.md
    critic-document/             # default.md
  docs/
    execution-modes.md           # execution mode reference + arch check promotion
    note-freshness.md            # note freshness lifecycle + multi-root retrieval
    evidence-bundle.md           # evidence bundle format reference
    acceptance-ledger.md         # CHECKS.yaml schema and lifecycle
    retrieval-selection.md       # multi-signal retrieval algorithm reference
    handoff-escalation.md        # SESSION_HANDOFF.json triggers and schema
    failure-history.md           # FAILURE_CASE index and similar-failure CLI
    architecture-promotion.md    # conditional architecture check promotion
    orchestration-modes.md         # orchestration mode reference
```

## Setup outputs

When `/harness:setup` runs, it creates the minimum:

```
CLAUDE.md                        # root entrypoint
.claude/settings.json            # agent config
doc/harness/manifest.yaml    # initialization marker
doc/harness/critics/         # plan.md, runtime.md, document.md playbooks
doc/common/                      # initial notes from repo scan (OBS/REQ/INF)
```

Setup also creates initial notes (`doc/common/`) from repo scan results — real observations, not placeholder templates.

Additional structure (constraints, QA scripts, browser config) is created only when the project needs it and actual commands are known.

## Manifest schema

The `doc/harness/manifest.yaml` file declares the project shape:

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
  - rule: <plain-language rule>
    check: <shell command, exits non-zero on violation>
teams:
  provider: auto | native | omc | none
  native_ready: true | false
  omc_ready: true | false
  auto_activate: true | false
  approval_mode: preapproved | ask
  fallback: subagents | solo
  safe_only:
    require_disjoint_files: true | false
    forbid_same_file_edits: true | false
```

## Task state schema

`TASK_STATE.yaml` carries these fields per task:

```yaml
task_id: TASK__<slug>
status: created | planned | plan_passed | implemented | qa_passed | docs_synced | closed | blocked_env | stale | archived
lane: build | debug | verify | refactor | docs-sync | investigate | answer
execution_mode: light | standard | sprinted
mutates_repo: true | false | unknown
qa_required: true | false
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
blockers: []
updated: <ISO 8601>
orchestration_mode: solo | subagents | team
team_provider: none | native | omc | fallback-subagents | fallback-solo
team_status: n/a | planned | running | degraded | fallback | complete | skipped
team_size: 0
team_reason: ""
team_plan_required: false
team_synthesis_required: false
fallback_used: none | subagents | solo
```

## Team artifact lifecycle

For `orchestration_mode: team`, task state is now driven by the actual team artifacts:

- `task_start` scaffolds `TEAM_PLAN.md` and `TEAM_SYNTHESIS.md` when a team task is selected.
- Source writes stay blocked until `TEAM_PLAN.md` is fully completed: required headings present, no `TODO` / `TBD` placeholders, every worker has owned writable paths, and owned paths are non-overlapping.
- Once the team plan is ready, source writes are checked against declared ownership: shared read-only or unowned paths are blocked, and worker-specific runs may pin identity with `HARNESS_TEAM_WORKER` or a worker-suffixed `CLAUDE_AGENT_NAME`.
- After `team-bootstrap`, the lead can run `team-dispatch` to generate a provider launch pack under `team/bootstrap/provider/`: a provider prompt, launcher helper, per-phase worker prompts, and headless `run-*.sh` helpers. Synthesis owners now also get explicit `synthesis` / `handoff_refresh` phase helpers (for example `run-lead-synthesis.sh` and `run-lead-handoff_refresh.sh`) so the close path uses the same frozen launch surface as initial fan-out. Then `team-launch` can auto-refresh stale bootstrap/dispatch artifacts, write `team/bootstrap/provider/launch.json`, and expose the default provider/implementer fan-out command from one entrypoint. For native Claude teams, the frozen provider prompt is surfaced directly and `team-launch --execute` can automatically fall back to the implementer dispatcher when the preferred provider path is interactive-only. For recovery after fan-out, `team-relaunch` writes `team/bootstrap/provider/relaunch.json` and picks the best worker/phase pair (missing implementer, synthesis, runtime verification, docs sync, document review, or handoff refresh) before optionally spawning it in detached mode. `hctl context` now surfaces when the bootstrap, dispatch, or launch layer is missing or stale so fan-out happens from a frozen launch surface instead of ad-hoc copy/paste.
- `TEAM_PLAN.md` may optionally declare `## Documentation Ownership` (for example `- writer: reviewer`, `- critic-document: lead`) so the documentation pass is also worker-scoped instead of only role-scoped.
- Each planned worker is expected to leave `team/worker-<name>.md` before synthesis. Those summaries must record completed work, owned paths handled, verification, and residual risks.
- `hctl context` now surfaces the current worker (when `HARNESS_TEAM_WORKER` or a worker-suffixed `CLAUDE_AGENT_NAME` is present), their owned paths, summary artifact, and a worker-specific next action when their slice is still pending.
- `TEAM_SYNTHESIS.md` is required before `task_close`; it must be refreshed after the latest worker summary update, and once it is complete, `team_status` can resolve to `complete` without a manual YAML edit.
- For repo-mutating team tasks, the synthesis owner also owns the final runtime verification pass. If `CRITIC__runtime.md` is older than the newest `TEAM_SYNTHESIS.md`, close is blocked until final verification is rerun.
- After that final runtime verification, the writer must refresh `DOC_SYNC.md`. When `TEAM_PLAN.md` names documentation owners, `hctl context`, `SESSION_HANDOFF.json`, and the prewrite gate all route `DOC_SYNC.md` / `CRITIC__document.md` to those workers. When documentation review is required, `critic-document` must then rerun against the refreshed doc sync / final verification state before close can continue.
- `HANDOFF.md` now comes last for team close: if it becomes older than the latest worker summary, synthesis, final runtime verification, `DOC_SYNC.md`, or `CRITIC__document.md`, the context and session handoff will call that out so recovery resumes from a fresh global handoff instead of stale breadcrumbs.
- If a team round degraded, `TEAM_SYNTHESIS.md` must be refreshed after that degraded round before close succeeds.

## Hook behavior

| Hook | Behavior |
|------|----------|
| `SessionStart` | Load context, show open tasks |
| `TaskCompleted` | **BLOCK** (exit 2) unless all required verdicts PASS; auto-populates touched_paths from git diff if empty |
| `SubagentStop` | Warn if expected artifacts missing |
| `Stop` | **BLOCK** (exit 2) if open tasks remain |
| `PreToolUse (Bash)` | Route harness control commands to MCP aliases before shell execution |
| `PreToolUse` | Enforce the write gate and workflow lock before repo mutation; team tasks keep source writes blocked until `TEAM_PLAN.md` is semantically complete, then restrict writes to declared owned writable paths |
| `UserPromptSubmit` | Surface task/doc memory hints plus open directive/complaint summaries |
| `PostToolUse` | Emit tool-routing hints after tool execution |
| `PostToolUse (Write|Edit|MultiEdit)` | Precise invalidation: runtime_verdict for runtime paths, document_verdict for doc paths; marks affected notes suspect |
| `PostCompact` | Re-inject open task summary + maintain-lite entropy indicators |
| `SessionEnd` | Record final session state + maintain-lite entropy summary |
| `TeammateIdle` | Advisory check for team worker deliverables |


Optional bootstrap: `plugin/scripts/task_created_gate.py` still exists as a manual task-directory initializer, but it is **not** wired in `plugin/hooks/hooks.json`.

## Development

### Running evals

The `eval/` directory contains fixture-based tests that validate setup detection and manifest generation:

```bash
./eval/run.sh
```

Fixtures cover: `web-frontend`, `fullstack-web`, `api-only`, `cli-library`, `brownfield-mono`.

The eval suite validates:
- Browser detection accuracy (web-frontend and fullstack-web get `browser_qa_supported: true`)
- Manifest shape correctness per project type
- Fixture structure completeness

Add new fixtures by creating a subdirectory under `eval/fixtures/` with the minimum files that represent the project type.
