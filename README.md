# harness — execution harness for AI-assisted repository work

Version 4.1.0

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

## Agents

| Agent | Role |
|-------|------|
| `harness` | Orchestrating harness — classifies requests, selects execution mode, drives the loop, gates completion |
| `developer` | Code generator — implements changes, populates touched_paths/verification_targets, updates HANDOFF.md |
| `writer` | Doc generator — creates/updates notes with freshness metadata, writes DOC_SYNC.md |
| `critic-plan` | Evaluator — validates plan contract using mode-matched rubric |
| `critic-runtime` | Evaluator — runtime verification with mandatory evidence bundle |
| `critic-document` | Evaluator — doc validation, DOC_SYNC accuracy, sprinted-mode structural checks |

## Browser-first QA

When the project manifest declares `browser_qa_supported: true`, the runtime critic prioritizes browser interaction over text-based checks. This applies to web frontend projects where visual and functional verification is best performed in a real browser session.

Browser-first is auto-detected during setup using a 4-signal process: framework/package detection, structure detection, executability check, and server-only exclusion rules.

## Freshness-aware memory

The harness maintains durable memory in `doc/common/` using three note types:

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

The `FileChanged` hook marks notes `suspect` when their `invalidated_by_paths` are modified. The writer agent restores `suspect → current` after re-verification.

See `plugin/docs/note-freshness.md` for the full freshness lifecycle specification.

## Hidden review overlays

The harness conditionally activates domain-specific review overlays based on task signals — prompt keywords, predicted file paths, and lane classification. Three overlays are available: **security**, **performance**, and **frontend-refactor**. When active, critics apply additional checks specific to that domain. No new commands or workflows are needed — overlays are selected automatically during planning and stored in `TASK_STATE.yaml`.

## Performance evidence

Performance tasks (those with `performance_task: true` or a `performance` overlay) require numeric before/after evidence for runtime critic PASS. Qualitative claims alone ("it's faster") are not sufficient. The plan must include a performance contract with baseline metrics, target metrics, and a reproducible benchmark command. The runtime critic requires a Performance Comparison section in the evidence bundle.

## Memory retrieval

The prompt memory system uses freshness-weighted relevance scoring when selecting notes for context injection. Notes marked `current` receive full weight, `suspect` notes are included with a caution label, `stale` notes are used only as a last resort with a re-verification flag, and `superseded` notes are excluded entirely. The selection budget targets the top 2 relevant notes, 1 active task, and 1 recent verdict within a 600-character context limit.

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

Doc paths: `doc/*`, `docs/*`, `*.md`, `README*`, `CHANGELOG*`, `LICENSE*`, `.claude/harness/critics/*`, `DOC_SYNC.md`

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

## Maintain-lite

At session end and post-compaction, the harness automatically runs maintain-lite: a read-only entropy scan with no writes or auto-fixes.

| Check | What it detects |
|-------|----------------|
| Stale tasks | `updated` > 7 days, status not closed/archived/stale |
| Orphan notes | Files in `doc/common/` not referenced in any CLAUDE.md index |
| Broken supersede chains | `superseded_by:` pointing to non-existent file |
| Dead artifacts | `CRITIC__*.md` in closed task folders |

Results include an entropy health score: `LOW` / `MEDIUM` / `HIGH`. Run `/harness:maintain` when entropy is MEDIUM or HIGH.

## Runtime playbooks

Critic agents follow playbooks stored in `.claude/harness/critics/`. These define the verification steps for plan validation (`plan.md`) and runtime checks (`runtime.md`), scoped to the project shape declared in the manifest.

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
    harness.md                   # orchestrating harness (mode selection, signal matrix)
    developer.md                 # generator — code + touched_paths population
    writer.md                    # generator — notes with freshness metadata
    critic-plan.md               # evaluator — mode-aware plan validation
    critic-runtime.md            # evaluator — runtime verification + evidence bundle
    critic-document.md           # evaluator — doc validation + sprinted-mode checks
  skills/
    plan/SKILL.md                # create task contract (mode-aware formats)
    maintain/SKILL.md            # cleanup tool + maintain-lite docs
    setup/SKILL.md               # bootstrap target project
  scripts/
    _lib.sh                      # shared helpers (extract_roots, is_doc_path, find_tasks_with_verification_targets)
    session-context.sh           # session start context
    task-created-gate.sh         # task init (no blocking)
    subagent-stop-gate.sh        # agent reminders (no blocking)
    task-completed-gate.sh       # completion gate (auto-populates touched_paths on close)
    file-changed-sync.sh         # precise invalidation (doc vs runtime, note freshness)
    session-end-sync.sh          # session end summary + maintain-lite entropy
    post-compact-sync.sh         # post-compaction context + maintain-lite entropy
  docs/
    execution-modes.md           # execution mode reference
    note-freshness.md            # note freshness lifecycle reference
    evidence-bundle.md           # evidence bundle format reference
```

## Setup outputs

When `/harness:setup` runs, it creates the minimum:

```
CLAUDE.md                        # root entrypoint
.claude/settings.json            # agent config
.claude/harness/manifest.yaml    # initialization marker
.claude/harness/critics/         # plan.md, runtime.md, document.md playbooks
doc/common/                      # initial notes from repo scan (OBS/REQ/INF)
```

Setup also creates initial notes (`doc/common/`) from repo scan results — real observations, not placeholder templates.

Additional structure (constraints, QA scripts, browser config) is created only when the project needs it and actual commands are known.

## Manifest schema

The `.claude/harness/manifest.yaml` file declares the project shape:

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
```

## Hook behavior

| Hook | Behavior |
|------|----------|
| `SessionStart` | Load context, show open tasks |
| `TaskCreated` | Initialize TASK_STATE.yaml, HANDOFF.md, REQUEST.md |
| `TaskCompleted` | **BLOCK** (exit 2) unless all required verdicts PASS; auto-populates touched_paths from git diff if empty |
| `SubagentStop` | Warn if expected artifacts missing |
| `Stop` | **BLOCK** (exit 2) if open tasks remain |
| `FileChanged` | Precise invalidation: runtime_verdict for runtime paths, document_verdict for doc paths; marks affected notes suspect |
| `PostCompact` | Re-inject open task summary + maintain-lite entropy indicators |
| `SessionEnd` | Record final session state + maintain-lite entropy summary |

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
