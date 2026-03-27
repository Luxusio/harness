# CLAUDE.md
tags: [root, harness, bootstrap]
summary: repo entrypoint and durable root registry
always_load_paths: [doc/common/CLAUDE.md]
registered_roots: [common]
updated: 2026-03-27

@doc/common/CLAUDE.md

# Operating mode
- Default operating agent is harness — a universal loop runtime.
- Every request enters the same loop:
  receive → gather context → select lane → plan/spec → execute → evaluate → sync memory → maintain → escalate (if needed) → close.
- Lane selection is based on intent + repo state, not just intent alone.
- Generators (developer, writer) produce. Evaluators (critics) independently verify.
- New durable roots or durable structure changes go through critic-document.
- `.claude/harness/manifest.yaml` is the initialization marker.

# Lanes
- `answer` — direct response, no repo mutation
- `spec` — spec hierarchy for large/ambiguous work
- `build` — feature addition, new code
- `debug` — bug investigation + fix
- `verify` — test/QA/validation
- `refactor` — structural improvement
- `docs-sync` — documentation and note management
- `investigate` — research, exploration (may transition)
- `maintain` — entropy control, hygiene

# Plugin agents
- `harness` — loop controller, lane router, escalation boundary
- `developer` — generator: implements approved plans
- `writer` — generator: REQ/OBS/INF notes and documentation
- `critic-plan` — evaluator: validates contracts before implementation
- `critic-runtime` — evaluator: runtime verification for code changes
- `critic-document` — evaluator: doc/note hygiene and structure governance

# Plugin skills
- `/harness:plan` — create or refresh task contract (PLAN.md or spec hierarchy)
- `/harness:maintain` — entropy control and structure maintenance
- `/harness:setup` — bootstrap harness structure and executable QA scaffolding

# Durable knowledge rules
- REQ: explicit human requirements only
- OBS: directly observed facts only
- INF: unverified AI inferences only
- Never silently rewrite INF into fact
- When INF is verified, create OBS and link with superseded_by
- Notes track freshness: status (active/stale/archived), confidence, last_verified_at

# Doc structure (created by /harness:setup in target projects)
- `doc/common/` — always-loaded shared context
- `doc/<root>/` — domain-specific roots (auth, billing, etc.)
- Each root has its own CLAUDE.md with note index
- Notes use prefix convention: REQ__, OBS__, INF__
