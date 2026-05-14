---
date: 2026-05-14
task: TASK__codex-plan-eng-review-port
type: feature
---

# Codex plan-eng-review skill ported

6th skill landed in the Codex tree: `plugin-codex/skills/plan-eng-review/SKILL.md`. Hand-ported from 846L Claude source to 912L Codex variant. 9 AskUserQuestion call sites converted to conversational prose asks, Agent adversarial subagent fan-out collapsed to single-voice degraded inline pass (v2 will revisit when Codex multi_agent ergonomics improve). Sub-file `rubrics-threat-rollback.md` not ported — references resolve to the Claude tree per the spike-report §3.6 sub-file fallback architecture. Methodology parity verified by qa-cli: Cognitive Patterns, Confidence Calibration, Failure Modes Registry, Worktree parallelization, Outside Voice section all preserved.
