# Codex Run Subagent Capability Routing

Codex run/develop guidance now routes by current session capability instead of treating old v1.5 no-Agent notes as absolute. When `spawn_agent` is available, Codex should use it for independent QA/review and bounded worker tasks; inline role execution is fallback only.

The docs include concrete `spawn_agent { ... }` call shapes for QA, worker, and explorer roles. Runtime fallback reporting is exception-only: do not add routine routing logs to every HANDOFF; record a short `Runtime Fallbacks` section only when an expected independent review/QA path is replaced by inline verification or a required tool is unavailable.

User writing feedback was recorded separately as a Tier 2 pattern: document only reusable conditional behavior rules in readable prose, not raw YAML/JSON candidates or incident narratives.
