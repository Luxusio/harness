# CLAUDE.md
updated: 2026-03-28

# Operating mode
- Default agent is harness — a completion firewall with verdict invalidation.
- `.claude/harness/manifest.yaml` is the initialization marker.
- Every repo-mutating task follows: plan -> critic-plan PASS -> implement -> critic-runtime PASS -> close.
- The only hard gate is at task completion: critic verdicts must PASS. Stale PASS (after file changes) does not count.
- Work in plain language. The harness routes requests and gates completion.
