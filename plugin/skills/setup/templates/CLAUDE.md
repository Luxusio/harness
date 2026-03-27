# CLAUDE.md
tags: [root, harness, bootstrap]
summary: repo entrypoint and durable root registry
always_load_paths: [doc/common/CLAUDE.md]
registered_roots: [common]
updated: {{SETUP_DATE}}

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

# Durable knowledge rules
- REQ: explicit human requirements only
- OBS: directly observed facts only
- INF: unverified AI inferences only
- Never silently rewrite INF into fact
- When INF is verified, create OBS and link with superseded_by
- Notes track freshness: status (active/stale/archived), confidence, last_verified_at
