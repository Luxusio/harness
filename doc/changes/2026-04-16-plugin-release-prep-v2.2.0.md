---
date: 2026-04-16
task: TASK__plugin-release-prep-v2.2.0
status: shipped
freshness: suspect
invalidated_by_paths:
  - plugin/.claude-plugin/plugin.json
  - plugin/.claude-plugin/marketplace.json
  - plugin/skills/setup/bootstrap.md
  - plugin/skills/setup/SKILL.md
  - plugin/skills/setup/verify-report.md
  - plugin/CHANGELOG.md
freshness_updated: 2026-04-16T07:23:23Z
---

# plugin-release-prep v2.2.0

Five-item release bundle preparing the harness plugin for a 2.2.0 tag after the
orchestrator-agent removal landed in v2.1.x.

## Items

1. **Legacy CLAUDE.md line migration** (Repair/Upgrade path). `plugin/skills/setup/bootstrap.md` §3.4 now strips pre-existing `- Default (operating) agent is harness …` lines before the `## Harness routing` block is injected. Portable (no sed -i divergence), idempotent. `plugin/skills/setup/SKILL.md` routing-injection step references the new cleanup so ordering is explicit.

2. **Version bump**. `plugin.json`, `marketplace.json`, and `SKILL.md _HARNESS_VERSION` are all synced to `2.2.0`. The `_HARNESS_VERSION` variable was stuck at `2.0.0` — drift fixed in the same pass.

3. **CHANGELOG.md**. New file at `plugin/CHANGELOG.md` with Removed / Changed / Migration / Fixed sections for 2.2.0.

4. **Stale test cleanup**. Deleted `tests/test_plugin_agent_contracts.py`, `tests/test_prompt_budget.py`, `tests/test_workflow_surface_lock.py`. All three imported paths/symbols absent from the current tree (`plugin/settings.json`, `plugin/agents/critic-runtime.md`, `plugin/scripts/hctl.py`, `plugin/docs/orchestration-modes.md`, symbol `_is_workflow_control_surface`). They were failing pytest collection and poisoning verify gates. Removing them cuts collection errors 31 → 30.

5. **pytest detection in verify-report**. `plugin/skills/setup/verify-report.md` now detects pytest availability when `manifest.yaml` declares a pytest-based `test_command`, and prints actionable install guidance (`pip --user`, `--break-system-packages`, or `pipx`) when the runner is missing. Catches the failure at setup time instead of during the first verify gate.

## Migration note for existing users

Run `/harness:setup` and pick Repair or Upgrade. The setup flow now:
1. Strips the legacy `Default agent is harness` line if present.
2. Injects the `## Harness routing` block (idempotent).
3. Stamps `doc/harness/.version` with 2.2.0.

## Known follow-up (out of scope)

30 pre-existing pytest collection errors remain (renamed `_lib` APIs etc.).
This task reduced them by 1; the rest belong to a separate test-surface refresh task.
