# CLAUDE.md
tags: [root, harness, bootstrap]
summary: repo entrypoint and durable root registry
always_load_paths: [doc/common/CLAUDE.md]
registered_roots: [common]
updated: 2026-03-27

@doc/common/CLAUDE.md

# Operating mode
- Default operating agent is harness.
- Every substantial repo-mutating task follows:
  request -> contract plan -> plan critic -> implement -> runtime QA -> persistence -> docs sync -> document critic -> close.
- New durable roots or durable structure changes go through critic-document.
- `.claude/harness/manifest.yaml` is the initialization marker.

# Plugin agents
- `harness` — main orchestrator, routes work through lanes
- `developer` — implements approved plans
- `writer` — writes/updates durable notes (REQ/OBS/INF) and documentation
- `critic-plan` — validates PLAN.md before implementation
- `critic-runtime` — runtime verification for code changes
- `critic-document` — validates documentation, note hygiene, and durable structure changes

# Plugin skills
- `/harness:plan` — create or refresh task-local PLAN.md as a contract
- `/harness:maintain` — periodic doc hygiene and structure maintenance
- `/harness:setup` — bootstrap harness structure and executable QA scaffolding in target project

# Durable knowledge rules
- REQ: explicit human requirements only
- OBS: directly observed facts only
- INF: unverified AI inferences only
- Never silently rewrite INF into fact
- When INF is verified, create OBS and link with superseded_by

# Doc structure (created by /harness:setup in target projects)
- `doc/common/` — always-loaded shared context
- `doc/<root>/` — domain-specific roots (auth, billing, etc.)
- Each root has its own CLAUDE.md with note index
- Notes use prefix convention: REQ__, OBS__, INF__
