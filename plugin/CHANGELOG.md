# harness plugin changelog

All notable changes to the harness Claude Code plugin.

## [Unreleased]

### Changed

- Claude subagent lifecycle state now lives only in the task's unified
  `RECEIPTS.jsonl`. The separate background registry, lock, RMW/prune state
  machine, and diagnostic records were removed; Stop-hook active-work waiting
  derives unmatched current-run/current-session starts directly from receipts.
- Receipt rows use ten string fields with one namespaced `runtime_id`. Detailed
  completion text stays in the runtime transcript while receipts retain the
  validated verdict, review counts, and a detail digest. `task_context` and
  `task_verify` no longer embed receipt summaries or duplicate report paths.
- The paired develop and four plan-review skills now stay below the 500-line
  budget by deleting repeated orchestration prose while retaining role-specific
  gates, rubrics, output contracts, and runtime interaction differences.
- Old receipt schemas and side streams have no compatibility reader. Resume
  starts a fresh `TASK.json.run_id` and resets the unified stream.
- The MCP-hosted Codex watcher consumes direct collaboration spawn, output, and
  final events. Activity events are ignored; a uniquely discovered trusted
  child rollout supplies the child identity when the output event is the only
  rollout-bearing signal.
- Codex installation now packages every lazily loaded methodology file named by
  its internal skills, while preserving one canonical source copy in the Claude
  skill tree.

### Fixed

- Verified installation reinstalls the complete reviewed payload even from a
  clean worktree and prunes removed runtime files from the active payload.
- Nested-worktree micro tasks read `execution_mode` from the canonical
  four-field `TASK.json` without requiring `PLAN.md`.

## [2.2.0] — 2026-04-16

### Removed
- `plugin/agents/harness.md` — the orchestrator agent is gone. The main Claude session now routes through native Goal orchestration and internal sub-skills directly. No more agent-switching.

### Changed
- `plugin/skills/setup/bootstrap.md` §3.4 — setup emits an idempotent `## Harness routing` block (marker: `<!-- harness:routing-injected -->`) into the user's CLAUDE.md that maps normal use to native Goal orchestration plus `Skill(harness:setup)`; child-task execution, plan, develop, and review helpers are internal.
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
