# Architecture

<!-- Updated by harness during architecture-relevant work. -->
<!-- Entries should be marked: confirmed | inferred | hypothesis -->

## System Boundaries

- `plugin/` — all plugin runtime code (skills, agents, hooks, scripts) — confirmed
- `harness/` — repo-local operating state (generated per-project by setup) — confirmed
- `.claude-plugin/` — marketplace metadata — confirmed

## Key Patterns

- Skills as procedure documents: SKILL.md contains step-by-step instructions, not executable code — confirmed
- Agent delegation: orchestrator reads SKILL.md then delegates to specialist agents via Agent tool — confirmed
- Template-based setup: `skills/setup/templates/` contains all scaffolding templates with `{{PLACEHOLDER}}` syntax — confirmed

## Observed Facts

- [2026-03-20] Plugin skills are resolved relative to plugin root directory. Evidence: `"skills": "./skills/"` in plugin.json works when plugin root is set correctly.
- [2026-03-20] `plugin.json` does not support `"agents"` or `"hooks"` fields — validation error. Evidence: install failed with "agents: Invalid input".
- [2026-03-20] Marketplace `"source"` with nested subdirectory paths may not work reliably. Evidence: `"source": "./plugin"` did not load skills in testing.

## Validation Strategy

Validation escalates from cheapest to broadest — confirmed:
1. Fast static checks (format, lint, typecheck)
2. Scope-based tests (related unit/integration tests)
3. Smoke / key journey checks
4. Runtime evidence (browser, logs, metrics)
5. Documentation / constraint sync verification

## Memory Promotion Ladder

```
hypothesis → observed_fact → confirmed → enforced
```
- hypothesis: inferred, not yet verified
- observed_fact: verified by code/tests/logs
- confirmed: verified by user or explicit repo rule
- enforced: encoded as test, lint rule, config assertion, or validation script

## Resolved Questions

- [2026-03-20] `plugin/settings.json` `"agent"` field does NOT set the main agent. Resolved: 2026-03-20. Outcome: use `"agent": "harness:harness-orchestrator"` in PROJECT `.claude/settings.json` instead. The setup skill now adds this automatically.
- [2026-03-20] `plugin.json` does not support `"agents"` or `"hooks"` fields. Resolved: 2026-03-20. Outcome: agents are auto-discovered from `agents/` directory without plugin.json declaration. Adding `"agents"` causes validation error.

## Open Questions

(none)
