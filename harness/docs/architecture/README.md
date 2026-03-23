# Architecture

<!-- Updated by harness during architecture-relevant work. -->
<!-- Entries should be marked: confirmed | inferred | hypothesis -->

## System Boundaries

- `plugin/` — all plugin runtime code (skills, agents, hooks, scripts) — confirmed
- `harness/` — repo-local operating state (generated per-project by setup) — confirmed
- `.claude-plugin/` — marketplace metadata — confirmed

## Key Patterns

- Hidden workflow SKILL.md files are internal procedure documents: SKILL.md contains step-by-step instructions read by the orchestrator, not executable code and not user-invocable commands — confirmed
- User-invocable command surface is limited to two public commands: `/harness:setup` (bootstrap) and `/harness:validate` (diagnostic). All other workflows are hidden internal procedures — confirmed
- Agent delegation: orchestrator reads SKILL.md procedure doc then delegates to specialist agents via Agent tool — confirmed
- Template-based setup: `skills/setup/templates/` contains all scaffolding templates with `{{PLACEHOLDER}}` syntax — confirmed
- Approval gate source of truth: `harness/policies/approvals.yaml` is the enforcement gate the orchestrator checks before touching sensitive areas. `harness/manifest.yaml` `risk_zones` is descriptive context only — confirmed

## Observed Facts

- [2026-03-20] Plugin skills are resolved relative to plugin root directory. Evidence: `"skills": "./skills/"` in plugin.json works when plugin root is set correctly.
- [2026-03-20] `plugin.json` does not support `"agents"` or `"hooks"` fields — validation error. Evidence: install failed with "agents: Invalid input".
- [2026-03-22] Relative plugin source (`./plugin`) is supported for Git-based marketplaces. Path resolves from the marketplace root. URL-based marketplace catalogs should use external plugin sources instead of relative paths. Evidence: Git-based marketplace add works with relative source.

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

- [2026-03-22] `plugin/settings.json` `"agent"` field sets the shipped plugin default main agent. Resolved: 2026-03-22. Outcome: `plugin/settings.json` sets the plugin's default main-thread agent for all users. Project `.claude/settings.json` can override this for local development (e.g., activating `harness:harness-orchestrator` explicitly). These are different configuration levels, not competing settings.
- [2026-03-20] `plugin.json` does not support `"agents"` or `"hooks"` fields. Resolved: 2026-03-20. Outcome: agents are auto-discovered from `agents/` directory without plugin.json declaration. Adding `"agents"` causes validation error.

## Open Questions

(none)
