# harness

Claude Code plugin for AI-assisted software work with durable knowledge, mandatory critic gates, and executable QA.

## What it does

- **REQ/OBS/INF durable memory** — structured notes that separate requirements, observations, and inferences
- **Mandatory critic gates** — plan, runtime, and document critics validate every change
- **Execution-first QA** — browser-first verification, smoke scripts, health checks, persistence checks
- **Contract-based task lifecycle** — request → contract plan → plan critic → implement → runtime QA → persistence → docs sync → document critic → close
- **Doc roots** — expandable knowledge domains with critic-governed structure changes
- **Task state model** — TASK_STATE.yaml tracks every task through its lifecycle
- **Manifest-based initialization** — `.claude/harness/manifest.yaml` as the single initialization marker

## Install

Add this plugin to your Claude Code configuration.

## Usage

After installing, run `/harness:setup` in your project to bootstrap the durable knowledge structure and executable QA scaffolding.

Then work in plain language — the harness routes requests through the appropriate lanes automatically.

## Plugin structure

```
plugin/
  .claude-plugin/plugin.json     # plugin manifest (v3.0.0)
  CLAUDE.md                      # plugin instructions
  settings.json                  # default agent config
  agents/                        # 5 agent definitions
    harness.md                   # main orchestrator
    developer.md                 # code implementation
    writer.md                    # REQ/OBS/INF note writer
    critic-plan.md               # plan contract validation
    critic-runtime.md            # runtime verification
    critic-document.md           # doc/note/structure governance
  skills/                        # 3 skills
    plan/SKILL.md                # create task PLAN.md contract
    maintain/SKILL.md            # periodic doc hygiene
    setup/SKILL.md               # bootstrap target project
  hooks/hooks.json               # plugin hooks
  scripts/session-context.sh     # session start context loader
```

## Setup outputs

When `/harness:setup` runs in a target project, it creates:

```
CLAUDE.md                        # root entrypoint + registry
doc/common/CLAUDE.md             # always-loaded root index
doc/common/REQ__|OBS__|INF__*    # initial durable notes
.claude/harness/manifest.yaml    # initialization marker + runtime config
.claude/harness/critics/         # plan.md, runtime.md, document.md
.claude/settings.json            # hook configuration
.claude/hooks/*.sh               # 5 gate scripts
scripts/harness/                 # verify.sh, smoke.sh, healthcheck.sh, reset-db.sh
```

## Task artifacts

Every repo-mutating task produces:

| Artifact | Purpose |
|----------|---------|
| `REQUEST.md` | Original user request |
| `PLAN.md` | Contract document with scope, acceptance criteria, verification |
| `TASK_STATE.yaml` | Machine-readable state (created → planned → implemented → closed) |
| `HANDOFF.md` | Developer handoff notes and blockers |
| `QA__runtime.md` | Executable verification evidence |
| `DOC_SYNC.md` | Durable note/index update record |
| `CRITIC__plan.md` | Plan critic verdict |
| `CRITIC__runtime.md` | Runtime critic verdict |
| `CRITIC__document.md` | Document critic verdict |
| `RESULT.md` | Task outcome summary |

## Skills

| Skill | Description |
|-------|-------------|
| `/harness:setup` | Bootstrap harness structure and executable QA scaffolding |
| `/harness:plan` | Create or refresh a task-local PLAN.md contract |
| `/harness:maintain` | Run doc hygiene and structure maintenance |
