# harness v4 — Execution Harness

You are running with harness, an execution harness for AI-assisted repository work.

The plugin orchestrates plan-implement-verify loops, enforces critic verdicts at task closure, invalidates stale verdicts when files change after a PASS, prevents premature stop when tasks are open, and coordinates specialist agents through browser-first QA and DOC_SYNC enforcement.

## The loop

```
receive → classify (answer | mutate-repo) → plan contract → critic-plan PASS → implement → self-check breadcrumbs → runtime QA (browser-first when supported) → writer / DOC_SYNC → critic-document (when doc surface changed) → close
```

## Hook gates

| Hook | Behavior |
|------|----------|
| `SessionStart` | Load context, show open tasks |
| `TaskCreated` | Initialize TASK_STATE.yaml, HANDOFF.md, REQUEST.md |
| `TaskCompleted` | **BLOCK** (exit 2) unless all required verdicts PASS |
| `SubagentStop` | Warn if expected artifacts missing |
| `Stop` | **BLOCK** (exit 2) if open tasks remain |
| `FileChanged` | Invalidate PASS verdicts to pending |
| `PostCompact` | Re-inject open task summary |
| `SessionEnd` | Record final session state |

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

## Verdict invalidation

When files change after a critic PASS:
- `runtime_verdict` resets to `pending`
- `document_verdict` resets to `pending` (if doc files changed)

This prevents stale PASS from allowing task closure after code changes.

## Browser-first QA

When the project manifest declares `browser_qa_supported: true`, the runtime critic prioritizes browser interaction over text-based checks:

1. Open the entry URL declared in the manifest
2. Perform functional verification in the browser session
3. Capture evidence (screenshots, console output, network requests)
4. Record pass/fail verdict with browser-sourced evidence

Browser-first QA applies to web frontend projects. For non-browser projects, the runtime critic falls back to command-line verification using the playbook.

## DOC_SYNC sentinel

All repo-mutating tasks must produce a `DOC_SYNC.md` file before close. This sentinel:

- Records which documentation surfaces were touched
- Confirms doc content is consistent with code changes
- May declare "none" if no doc surfaces were affected

The document critic validates DOC_SYNC accuracy. A missing DOC_SYNC.md blocks task closure for any repo-mutating task.

## Durable memory

The harness maintains durable memory in `doc/common/` using three note types:

| Type | Purpose |
|------|---------|
| `REQ` | Requirements and constraints from the project or user |
| `OBS` | Observations from repo scans, test runs, or runtime checks |
| `INF` | Inferences and conclusions derived from REQ/OBS evidence |

Notes are created during setup from real repo scan results. The writer agent creates and updates notes; other agents reference them for context.

## Runtime playbooks

Critic agents follow playbooks in `.claude/harness/critics/`:

- `plan.md` — steps for plan contract validation
- `runtime.md` — steps for runtime verification, including browser interaction when supported

Playbooks are scoped to the project shape declared in the manifest.

## Specialist agents

| Agent | Role |
|-------|------|
| `harness` | Orchestrating harness — classifies requests, drives the loop, gates completion |
| `harness:developer` | Generator — code implementation, updates HANDOFF.md |
| `harness:writer` | Generator — creates/updates notes, writes DOC_SYNC.md |
| `harness:critic-plan` | Evaluator — plan contract validation |
| `harness:critic-runtime` | Evaluator — runtime verification with evidence (browser-first for web projects) |
| `harness:critic-document` | Evaluator — doc validation, DOC_SYNC accuracy |

## Task state model

```yaml
task_id: TASK__<slug>
status: created | planned | plan_passed | implemented | qa_passed | docs_synced | closed | blocked_env | stale | archived
lane: build | debug | verify | refactor | docs-sync | investigate
mutates_repo: true | false | unknown
plan_verdict: pending | PASS | FAIL
runtime_verdict: pending | PASS | FAIL | BLOCKED_ENV
document_verdict: pending | PASS | FAIL | skipped
browser_required: true | false
doc_sync_required: true | false
doc_changes_detected: true | false
touched_paths: []
roots_touched: []
verification_targets: []
blockers: []
updated: <ISO 8601>
```

## Lanes

| Lane | When |
|------|------|
| `answer` | Pure question — short-circuit, no task folder |
| `build` | Feature addition |
| `debug` | Bug investigation + fix |
| `verify` | Test/QA/validation |
| `refactor` | Structural change |
| `docs-sync` | Documentation update only |
| `investigate` | Research, may transition |

## Manifest schema reference

```yaml
project:
  name: <project name>
  type: <web-frontend | api | cli | library | ...>
runtime:
  test_command: <command>
  build_command: <command>
qa:
  browser_qa_supported: true | false
browser:
  entry_url: <url>
```

## Core rules

- No implementation without PLAN.md + critic-plan PASS
- No close without required critic PASS
- DOC_SYNC.md is mandatory for all repo-mutating tasks
- `blocked_env` leaves task open — never closes
- Verdict invalidation on file changes — stale PASS does not count
- If `.claude/harness/manifest.yaml` is missing, recommend `/harness:setup`
- Browser-first QA is default for web frontend projects when manifest declares `browser_qa_supported: true`
