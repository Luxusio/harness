# CLAUDE.md
updated: {{SETUP_DATE}}

# Operating mode
- Default agent is harness — a thin loop controller with completion gates.
- `.claude/harness/manifest.yaml` is the initialization marker.
- Every repo-mutating task follows: plan -> critic-plan PASS -> implement -> critic-runtime PASS -> close.
- Work in plain language. The harness routes requests and gates completion.
