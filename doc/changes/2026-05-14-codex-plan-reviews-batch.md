---
date: 2026-05-14
task: TASK__codex-plan-reviews-batch
type: feature
---

# Codex plan-* review skills batch-ported

Final batch of plan-* review skills lands in the Codex tree under MCP-only-sharing (spike-report §3.6): `plan-ceo-review` (1335L), `plan-design-review` (910L), `plan-devex-review` (1105L). Parallel-fanned-out per Phase 3.0 rule — 3 component-independent ACs, 3 executor agents in one assistant message. Each port applies the established pattern: AskUserQuestion → conversational prose, Agent fan-out → single-voice degraded, MCP tool names bare, `HARNESS_PLUGIN_ROOT` env. plan-design-review additionally degrades browser MCP to ASCII wireframes + `open file://...` (Playwright MCP deferral to v2). After this task all 9 user-facing harness skills are ported to plugin-codex: setup, run, plan, develop, maintain, plan-ceo-review, plan-eng-review, plan-design-review, plan-devex-review. Codex users can now run the full canonical loop end-to-end with feature parity to Claude side, modulo single-voice degradation on dual-voice review phases and deferred Playwright MCP visual verification.
