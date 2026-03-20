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

- [2026-03-20] [plugin] Does plugin settings.json `"agent"` field actually set the main agent prompt? — resolved: 2026-03-20 — outcome: No. Use `"agent": "harness:harness-orchestrator"` in project `.claude/settings.json` instead. Setup skill now adds this automatically.
- [2026-03-20] [plugin] How does Claude Code resolve skill paths in nested plugin structures? — resolved: 2026-03-20 — outcome: `"source": "./plugin"` in marketplace.json has a known bug (issue #11278). Skills are resolved relative to plugin root. Agents are auto-discovered from `agents/` directory.
- [2026-03-20] [plugin] Skill paths in plugin.json are resolved relative to the plugin root directory — resolved: 2026-03-20 — outcome: Confirmed. `"skills": "./skills/"` in plugin.json resolves relative to plugin root, not `.claude-plugin/`.
