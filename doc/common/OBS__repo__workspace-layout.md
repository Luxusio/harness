# OBS repo workspace-layout
tags: [obs, root:common, source:filesystem, status:active]
summary: Plugin repo layout — agents, scripts, hooks, calibration, docs, skills, templates
evidence: filesystem scan at setup time
updated: 2026-03-30

## Layout
- `plugin/agents/` — 7 agent definitions (harness, developer, writer, critic-plan, critic-runtime, critic-document, critic-intent)
- `plugin/scripts/` — 31 Python hook scripts + _lib.py shared utilities
- `plugin/hooks/hooks.json` — 8+ lifecycle hook definitions
- `plugin/calibration/` — 13 critic calibration packs by mode
- `plugin/docs/` — 8 reference documents (evidence bundles, execution modes, etc.)
- `plugin/skills/` — 3 skills (setup, plan, maintain) with templates
- `plugin/playbooks/` — 3 investigative playbooks
- `plugin/.claude-plugin/plugin.json` — plugin manifest v2.0.0
