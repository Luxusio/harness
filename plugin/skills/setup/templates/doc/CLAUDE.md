# doc registry
tags: [root-registry, doc, active]
summary: durable knowledge root registry. common은 항상 우선 로드하고 나머지는 필요 시 읽는다.
always_load_roots: [common]
registered_roots: []
updated: {{SETUP_DATE}}

@doc/common/CLAUDE.md

# Root registry
(no additional roots registered yet)

# Durable knowledge rules
- REQ is only for explicit human requirements.
- OBS is only for directly observed facts.
- INF is only for unverified AI inferences.
- Never silently rewrite INF into fact.
- When INF is verified, create OBS and link with superseded_by.
