# Unknowns

<!-- Items here are hypotheses or open questions — NOT confirmed facts. -->
<!-- Move items to harness/docs/constraints/, harness/docs/decisions/, or harness/docs/runbooks/ once confirmed. -->

## Open questions
<!-- Format: - [YYYY-MM-DD] [scope] question — status: open -->

## Hypotheses
<!-- Format: - [YYYY-MM-DD] [scope] hypothesis — confidence: low|medium|high — status: open -->

## Resolved
<!-- Move resolved items here. Do not delete — keep for history. -->
<!-- Format: - [YYYY-MM-DD] [scope] item — resolved: YYYY-MM-DD — outcome: <what happened> -->

- [2026-03-20] [plugin] Does plugin `settings.json` `"agent"` set the main agent prompt? — resolved: 2026-03-22 — outcome: Yes for shipped plugin defaults. `plugin/settings.json` can set the plugin's default main-thread agent. Project `.claude/settings.json` remains useful when this repository wants to activate `harness:harness-orchestrator` explicitly during development.
- [2026-03-20] [plugin] How does Claude Code resolve plugin paths in marketplace-relative installations? — resolved: 2026-03-22 — outcome: Relative plugin sources such as `./plugin` are supported for Git-based marketplaces and resolve from the marketplace root. Direct URL-based marketplace catalogs should use external plugin sources instead of relative paths.
- [2026-03-20] [plugin] Skill paths in plugin.json are resolved relative to the plugin root directory — resolved: 2026-03-20 — outcome: Confirmed. `"skills": "./skills/"` in plugin.json resolves relative to plugin root, not `.claude-plugin/`.
