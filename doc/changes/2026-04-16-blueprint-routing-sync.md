---
date: 2026-04-16
kind: doc-sync
scope: design-doc
related_task: TASK__blueprint-remove-harness-agent-refs
predecessor_task: TASK__remove-harness-agent-inject-routing
freshness: current
---

# 2026-04-16 — Blueprint routing sync

## Why
`TASK__remove-harness-agent-inject-routing` (closed 2026-04-16) removed the
standalone `plugin/agents/harness.md` orchestrator agent and replaced it
with a routing block injected into the host project's CLAUDE.md. Its
reviewer flagged a concern: the design document
`CLAUDE_CODE_HARNESS_BLUEPRINT.md` still presupposed the old orchestrator
agent in three places. This task closes that gap.

## What
Three surgical edits to `CLAUDE_CODE_HARNESS_BLUEPRINT.md`:

1. **Line 7 (top-level bullet)**: `main agent는 \`harness\`` →
   `main entrypoint는 \`harness\` plugin — main Claude session이
   \`Skill(harness:*)\`을 직접 호출한다 (별도 orchestrator agent 없음)`.
2. **Line 111 (Operating mode section)**: `Default operating agent is
   harness.` → `Repo-mutating work routes through harness skills
   (\`Skill(harness:run)\` / \`Skill(harness:plan)\` / \`Skill(harness:develop)\`
   / \`Skill(harness:setup)\` / \`Skill(harness:maintain)\`). No separate
   orchestrator agent — the main Claude session invokes skills directly.`
3. **Line 614 (setup 산출물 checklist item 12)**: `harness agent 활성화` →
   `CLAUDE.md에 \`## Harness routing\` 블록 주입 (마커: \`<!-- harness:routing-injected -->\`)`.

`git diff --stat` confirms exactly `1 file changed, 3 insertions(+), 3
deletions(-)`. No other lines modified.

## What was intentionally NOT changed
- Other legitimate uses of the word "agent" in the blueprint (e.g.
  `developer` / `writer` subagents, critic playbook scaffolding, general
  agent architecture commentary). The blueprint is a historical design doc
  and those references remain valid.
- `plugin-legacy/**`, `plugin/**`, test files, runtime CLAUDE.md. This task
  is doc-only.

## Verification
CLI-lens grep evidence, see `TASK__blueprint-remove-harness-agent-refs/CHECKS.yaml`
and `HANDOFF.md`:

| Check | Command | Expect | Got |
|-------|---------|--------|-----|
| AC-001 | `grep -c "main agent는 \`harness\`" CLAUDE_CODE_HARNESS_BLUEPRINT.md` | 0 | 0 |
| AC-002 | `grep -c "Default operating agent is harness" CLAUDE_CODE_HARNESS_BLUEPRINT.md` | 0 | 0 |
| AC-003 | `grep -c "harness agent 활성화" CLAUDE_CODE_HARNESS_BLUEPRINT.md` | 0 | 0 |
| AC-004 | `grep -c "harness:routing-injected" CLAUDE_CODE_HARNESS_BLUEPRINT.md` | ≥1 | 1 |
| AC-005 | `wc -l CLAUDE_CODE_HARNESS_BLUEPRINT.md` | 649 | 649 |
| AC-006 | `git diff --stat CLAUDE_CODE_HARNESS_BLUEPRINT.md` | 3 hunks, 3+ / 3- | 3+ / 3- |

## References
- Predecessor task: `doc/harness/tasks/TASK__remove-harness-agent-inject-routing/`
- Runtime rules: `plugin/CLAUDE.md` (especially §6 "Auto-routing")
- Setup routing injection: `plugin/skills/setup/bootstrap.md` §3.4
