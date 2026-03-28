# CLAUDE.md
updated: {{SETUP_DATE}}

# Operating mode
- Default agent is harness — a thin loop controller with completion gates.
- `.claude/harness/manifest.yaml` is the initialization marker.
- Work in plain language. The harness routes requests and gates completion.
- Completion requires: PLAN.md + plan critic PASS, runtime critic PASS for code changes, HANDOFF.md.
