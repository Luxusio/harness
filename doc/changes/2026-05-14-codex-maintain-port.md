---
date: 2026-05-14
task: TASK__codex-maintain-port-and-run-stale-fix
type: feature
---

# Codex maintain skill ported + run.md stale reference fixed

5th skill landed in the plugin-codex tree under the MCP-only-sharing policy: `plugin-codex/skills/maintain/SKILL.md`. The Codex maintain mirrors the Claude version's methodology (REVIEW pile inspection, Tier C drift confirmation, batch hygiene-archive commit, atomic pending-file rewrite) with two AskUserQuestion gates rendered as conversational prose asks. Maintain has the highest portability ratio of any port to date (72% as-is) because it never invokes Skill chains or Agent fan-out. As a follow-up to the develop port, `plugin-codex/skills/run/SKILL.md` Phase 3 block was updated — the previous "develop NOT YET PORTED in v1.5" workaround is removed and replaced with the inline-read pattern matching the other ported skills.
