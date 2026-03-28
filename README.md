# harness

Thin completion firewall for AI-assisted repository work.

## What it does

Harness prevents false "done" claims. When a task completes, the plugin checks that required critic verdicts exist and pass. That's it.

**Not a workflow OS.** No memory sync, no entropy control, no document management by default.

## How it works

```
request → context → plan → execute → independent critic → close
```

The only hard gate is at **task completion**:

| Requirement | When needed |
|-------------|-------------|
| TASK_STATE.yaml | Always |
| PLAN.md + plan critic PASS | Always |
| HANDOFF.md | Always |
| Runtime critic PASS | Code changes |
| Document critic PASS | Doc changes |

Tasks with `blocked_env` status cannot close.

## Install

Add this plugin to your Claude Code configuration.

## Usage

Run `/harness:setup` in your project to bootstrap. Then work in plain language.

## Plugin structure

```
plugin/
  .claude-plugin/plugin.json     # plugin manifest
  CLAUDE.md                      # plugin instructions
  settings.json                  # default agent config
  hooks/hooks.json               # completion gate hook
  agents/                        # 6 agent definitions
    harness.md                   # loop controller
    developer.md                 # generator — code
    writer.md                    # optional — documentation
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
```

Additional structure (docs, constraints, QA scripts) is created only when the project needs it and actual commands are known.

## Skills

| Skill | Description |
|-------|-------------|
| `/harness:setup` | Bootstrap harness in target project |
| `/harness:plan` | Create task contract (PLAN.md) |
| `/harness:maintain` | Optional cleanup tool |
