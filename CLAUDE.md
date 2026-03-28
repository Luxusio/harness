# CLAUDE.md
updated: 2026-03-28

# Operating mode
- Default agent is harness — a thin completion firewall.
- `.claude/harness/manifest.yaml` is the initialization marker.
- The only hard gate is at task completion: critic verdicts must PASS.
- Work in plain language. The harness routes requests and gates completion.
