# Harness — Claude Code Plugin
tags: [root, harness, bootstrap, plugin]
summary: AI 운영 체계 플러그인. plan/critic/developer/writer 기반 task lifecycle을 제공한다.
updated: 2026-03-27

# Operating mode
- Default operating agent is `harness`.
- Every task follows: plan → plan-critic → developer/writer → critic → sync.
- No durable structure expansion without structure-critic approval.

# Plugin agents
- `harness` — main orchestrator, routes work through lanes
- `developer` — implements approved plans
- `writer` — writes/updates durable notes (REQ/OBS/INF) and documentation
- `critic-plan` — validates PLAN.md before implementation
- `critic-runtime` — runtime verification for code changes
- `critic-write` — validates documentation and note hygiene
- `critic-structure` — governs durable structure changes

# Plugin skills
- `/harness:plan` — create or refresh task-local PLAN.md
- `/harness:maintain` — periodic doc hygiene and structure maintenance
- `/harness:setup` — bootstrap doc/ structure and critic playbooks in target project

# Durable knowledge rules
- REQ: explicit human requirements only
- OBS: directly observed facts only
- INF: unverified AI inferences only
- Never silently rewrite INF into fact
- When INF is verified, create OBS and link with superseded_by

# Doc structure (created by /harness:setup in target projects)
- `doc/` — durable knowledge root
- `doc/common/` — always-loaded shared context
- `doc/<root>/` — domain-specific roots (auth, billing, etc.)
- Each root has its own CLAUDE.md with note index
- Notes use prefix convention: REQ__, OBS__, INF__
