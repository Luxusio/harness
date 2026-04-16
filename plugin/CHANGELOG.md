# harness plugin changelog

All notable changes to the harness Claude Code plugin.

## [2.2.0] — 2026-04-16

### Removed
- `plugin/agents/harness.md` — the orchestrator agent is gone. The main Claude session now invokes `Skill(harness:run)` and sub-skills directly. No more agent-switching.

### Changed
- `plugin/skills/setup/bootstrap.md` §3.4 — setup now emits an idempotent `## Harness routing` block (marker: `<!-- harness:routing-injected -->`) into the user's CLAUDE.md that maps intents to `Skill(harness:run|plan|develop|setup|maintain)`.
- `plugin/skills/setup/bootstrap.md` §3.4 — added migration step that strips the legacy `Default agent is harness` line from existing CLAUDE.md on Repair/Upgrade runs.
- `plugin/skills/setup/SKILL.md` — routing-injection now references the bootstrap §3.4 template with idempotency marker.
- `plugin/skills/setup/verify-report.md` — verifies routing block presence and (new) pytest availability for CLI/library projects with pytest-based test_command.
- `plugin/CLAUDE.md` — reframed intro: harness rules apply to any caller running the canonical loop (skills, MCP clients), not a specific orchestrator agent.
- `CLAUDE.md` (repo root) and `CLAUDE_CODE_HARNESS_BLUEPRINT.md` — aligned with the routing-first wording.

### Migration (existing users)
Run `/harness:setup` and choose Repair or Upgrade. Setup will:
1. Strip any legacy `Default agent is harness` line from your CLAUDE.md
2. Inject the new `## Harness routing` block (idempotent — safe to re-run)
3. Stamp `doc/harness/.version` with 2.2.0

### Fixed
- `plugin/skills/setup/SKILL.md` — `_HARNESS_VERSION` was stuck at 2.0.0 despite plugin.json being 2.1.0. Now synced to 2.2.0.
- Removed 3 stale test files (`tests/test_plugin_agent_contracts.py`, `tests/test_prompt_budget.py`, `tests/test_workflow_surface_lock.py`) that referenced non-existent paths (`plugin/settings.json`, `plugin/agents/critic-runtime.md`, `plugin/scripts/hctl.py`, `plugin/docs/orchestration-modes.md`) that never existed on `feature/v3`.
