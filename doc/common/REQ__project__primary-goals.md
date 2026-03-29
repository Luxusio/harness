# REQ project primary-goals
tags: [req, root:common, source:inferred, status:active]
summary: Harness plugin development — plan/implement/verify execution loop for Claude Code
source: repo scan on 2026-03-30
updated: 2026-03-30

## Goals
- Maintain and evolve the harness execution loop (plan -> critic -> implement -> verify -> sync)
- Provide reliable verdict gating with evidence bundles
- Support multiple execution modes (light/standard/sprinted) and orchestration modes (solo/subagents/team)
- Keep hook scripts, agent definitions, and critic calibration packs in sync
