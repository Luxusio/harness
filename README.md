# harness

Claude Code plugin for AI-assisted software work with durable knowledge and mandatory critic gates.

## What it does

- **REQ/OBS/INF durable memory** — structured notes that separate requirements, observations, and inferences
- **Mandatory critic gates** — plan, runtime, write, and structure critics validate every change
- **Task lifecycle** — plan → plan-critic → developer/writer → critic → sync
- **Doc roots** — expandable knowledge domains with critic-governed structure changes

## Install

Add this plugin to your Claude Code configuration.

## Usage

After installing, run `/harness:setup` in your project to bootstrap the durable knowledge structure.

Then work in plain language — the harness routes requests through the appropriate lanes automatically.

## Plugin structure

```
plugin/
  .claude-plugin/plugin.json     # plugin manifest
  CLAUDE.md                      # plugin instructions
  settings.json                  # default agent config
  agents/                        # 7 agent definitions
    harness.md      # main orchestrator
    developer.md                 # code implementation
    writer.md                    # REQ/OBS/INF note writer
    critic-plan.md               # plan validation
    critic-runtime.md            # runtime verification
    critic-write.md              # doc/note hygiene
    critic-structure.md          # structure governance
  skills/                        # 3 skills
    plan/SKILL.md                # create task PLAN.md
    maintain/SKILL.md            # periodic doc hygiene
    setup/SKILL.md               # bootstrap target project
  hooks/hooks.json               # plugin hooks
  scripts/session-context.sh     # session start context loader
```

## Skills

| Skill | Description |
|-------|-------------|
| `/harness:setup` | Bootstrap doc/ structure and critic playbooks in a project |
| `/harness:plan` | Create or refresh a task-local PLAN.md |
| `/harness:maintain` | Run doc hygiene and structure maintenance |
