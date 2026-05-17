---
date: 2026-05-14
task: TASK__codex-develop-port-and-parity-check
type: pivot
---

# Codex develop port + sync-engine policy reversal

The dual-runtime trajectory pivots from v1.5's canonical-YAML sync engine to MCP-only sharing. Empirical v1.5 spike data (60% weighted-mean mechanical-portable across setup/run/plan, with 100% of restructure burden on control-flow primitives — Agent fan-out, Skill chain, AskUserQuestion) showed ~600 LOC of sync infrastructure was a negative-ROI bet to share 60% of authoring effort while leaving the interesting parts (the 40%) unsynced. This task reverts the sync-engine infra (canonical_schema, transform_skill, corpus, AC-005 test), documents the reversal in spike-report.md §3.6, and lands the first develop-tree port under the new policy (plugin-codex/skills/develop/SKILL.md, hand-authored). Shared substrate remains: MCP server, hook payload schemas, gate scripts, contract artifacts (PLAN.md/CHECKS.yaml/HANDOFF.md/CRITIC__qa.md), and the Codex config emitter now folded into root `install.py`. SKILL.md trees are now independent per runtime — Claude consumes Skill chain + Agent fan-out + AskUserQuestion natively; Codex consumes sequential execution + read-inline sub-skills + conversational asks. Both trees consume the same shared substrate, so behavior remains contract-equivalent; only the orchestration surface differs.
