# harness — execution harness for AI-assisted repository work

Version 4.0.0

## What it does

Harness is an execution harness that orchestrates plan-implement-verify loops for AI-assisted repository work. It enforces critic verdicts at task closure, invalidates stale verdicts when files change, and coordinates specialist agents through the full task lifecycle.

## The loop

```
receive → classify → plan contract → critic-plan PASS → implement → self-check breadcrumbs → runtime QA (browser-first when supported) → writer / DOC_SYNC → critic-document (when doc surface changed) → close
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

## Agents

| Agent | Role |
|-------|------|
| `harness` | Orchestrating harness — classifies requests, drives the loop, gates completion |
| `developer` | Code generator — implements changes, updates HANDOFF.md |
| `writer` | Doc generator — creates/updates notes, writes DOC_SYNC.md |
| `critic-plan` | Evaluator — validates plan contract before implementation |
| `critic-runtime` | Evaluator — runtime verification with evidence (browser-first for web projects) |
| `critic-document` | Evaluator — doc validation, DOC_SYNC accuracy |

## Browser-first QA

When the project manifest declares `browser_qa_supported: true`, the runtime critic prioritizes browser interaction over text-based checks. This applies to web frontend projects where visual and functional verification is best performed in a real browser session.

## Durable memory

The harness maintains durable memory in `doc/common/` using three note types:

- **REQ** — requirements and constraints from the project or user
- **OBS** — observations from repo scans, test runs, or runtime checks
- **INF** — inferences and conclusions derived from REQ/OBS evidence

Notes are created during setup from real repo scan results, not placeholder templates.

## DOC_SYNC sentinel

All repo-mutating tasks produce a `DOC_SYNC.md` file. This sentinel records which documentation surfaces were affected and confirms they are consistent with the code changes. The document critic validates DOC_SYNC accuracy before task close.

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
| `/harness:plan` | Create task contract (PLAN.md) |
| `/harness:maintain` | Doc and task cleanup — auto-fixes indexes, stale tasks, orphaned notes |

## Plugin structure

```
plugin/
  .claude-plugin/plugin.json     # plugin manifest
  CLAUDE.md                      # plugin instructions
  settings.json                  # default agent config
  hooks/hooks.json               # completion gate hook
  agents/                        # 6 agent definitions
    harness.md                   # orchestrating harness
    developer.md                 # generator — code
    writer.md                    # generator — durable notes
    critic-plan.md               # evaluator — plan validation
    critic-runtime.md            # evaluator — runtime verification
    critic-document.md           # evaluator — doc validation
  skills/
    plan/SKILL.md                # create task contract (PLAN.md)
    maintain/SKILL.md            # optional cleanup tool
    setup/SKILL.md               # bootstrap target project
  scripts/
    session-context.sh           # session start context
    task-created-gate.sh         # task init (no blocking)
    subagent-stop-gate.sh        # agent reminders (no blocking)
    task-completed-gate.sh       # THE completion gate
```

## Setup outputs

When `/harness:setup` runs, it creates the minimum:

```
CLAUDE.md                        # root entrypoint
.claude/settings.json            # agent config
.claude/harness/manifest.yaml    # initialization marker
.claude/harness/critics/         # plan.md, runtime.md playbooks
doc/common/                      # initial notes from repo scan (OBS/REQ/INF)
```

Setup also creates initial notes (`doc/common/`) from repo scan results — real observations, not placeholder templates.

Additional structure (constraints, QA scripts) is created only when the project needs it and actual commands are known.

## Manifest schema

The `.claude/harness/manifest.yaml` file declares the project shape:

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
