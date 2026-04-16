# 2026-04-16 — Remove harness orchestrator agent; inject skill-based routing

task: TASK__remove-harness-agent-inject-routing
kind: refactor
risk: medium

## Why

"Default agent is harness" was a fiction. Claude Code plugins cannot switch
the running agent on their own; the real entrypoint has always been skill
invocation. The bootstrap output and README previously told users the
harness *agent* would take over — in practice it never did. This change
aligns runtime docs with how the harness actually operates and makes the
routing rules explicit and idempotent.

## What changed

- `plugin/agents/harness.md` deleted. The doc had no dispatch consumers.
- `CLAUDE.md` (repo root): replaced the "Default agent is harness" line
  with a `## Harness routing` block (marker
  `<!-- harness:routing-injected -->`) that routes intents to skills.
- `plugin/CLAUDE.md`: reframed the intro — harness rules apply to any
  caller running the canonical loop; no orchestrator agent.
- `plugin/skills/setup/bootstrap.md` §3.4: expanded with the idempotent
  routing-block bash snippet so setup appends the routing block to the
  host project's CLAUDE.md (and skips re-inject if the marker is present).
- `plugin/skills/setup/SKILL.md`: routing-injection step now references
  `bootstrap.md` §3.4 and the marker.
- `plugin/skills/setup/verify-report.md`: adds a routing-block presence
  check after the manifest check.
- `plugin/skills/maintain/SKILL.md`: reworded "the harness agent" →
  "SessionStart hook".
- Tests updated: `tests/test_plugin_agent_contracts.py`,
  `tests/test_prompt_budget.py`, `tests/test_workflow_surface_lock.py`
  no longer assert the existence or contents of `plugin/agents/harness.md`.
  `test_plugin_agent_contracts.py` gains a `test_no_harness_orchestrator_agent`
  assertion to guard against reintroduction.

## Skill routing block (source of truth)

```markdown
## Harness routing
<!-- harness:routing-injected -->
- Run the full cycle (plan → develop → verify → close) → `Skill(harness:run)`
- Plan only → `Skill(harness:plan)`
- Implement an approved PLAN.md → `Skill(harness:develop)`
- Bootstrap harness in a new project / repair existing → `Skill(harness:setup)`
- Contract drift / post-upgrade cleanup → `Skill(harness:maintain)`
- Read-only question or explanation → answer directly, no skill
```

## Migration for existing hosts

Hosts already set up with harness2 will continue to work — the old
"Default agent is harness" line has no runtime effect. Running
`Skill(harness:setup)` on an existing project with `A) Repair` appends
the routing block idempotently (the `harness:routing-injected` marker
guards against duplication).

## Pre-existing test-suite notes (not part of this change)

Several tests in `tests/` reference infrastructure that does not exist
in the current `feature/v3` tree (`plugin/settings.json`,
`plugin/agents/critic-runtime.md`, `plugin/scripts/hctl.py`,
`plugin/docs/orchestration-modes.md`). Those failures pre-date this task
and are out of scope.

## Env note

During this task, pip was bootstrapped via `get-pip.py` with
`--break-system-packages` (PEP 668 override) because no pip shim was
available. pytest 9.0.3 installed to `~/.local`. Documented for future
sessions.
