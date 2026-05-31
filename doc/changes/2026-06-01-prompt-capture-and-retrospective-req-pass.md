# 2026-06-01 — Retrospective REQ extraction at task close (C-101)

tags: [harness, critic-document, retrospective-req, c-101, contracts]
freshness: current
task: TASK__prompt-capture-and-retrospective-req-pass

## What changed (operator-visible)

At every task close, `critic-document` agent now reads the task's
`USER_FEEDBACK.jsonl` (populated all along by `prompt_memory.py`) and writes
any missed REQ documents via `write_req_doc` with `status: candidate`. The
candidate REQ lands on disk for user review without claiming acceptance — it
does not block the close, and the user (or a follow-up task) promotes it to
`status: accepted` when confirmed.

The SessionStart banner reminder shipped in commit `659c771` was the upfront
net. This is the retrospective net: even if the operator ignores the banner,
the requirement gets captured before the task closes.

## What changed under the hood

- `plugin/agents/critic-document.md` gains a `## Retrospective REQ pass (C-101)`
  section. Invariant updated with explicit carve-out for write_req_doc calls.
- `write_req_doc` MCP tool + `req_scaffold.py` CLI gain optional `status`
  field (default `accepted`). REQ frontmatter has `status: <value>` directly
  under the title.
- `plugin/skills/develop/SKILL.md` Phase 8.6 trigger: REQ/GUIDE/ADR/POLICY
  changes OR USER_FEEDBACK.jsonl non-empty. `plugin/CLAUDE.md` artifact-writes
  block reflects the same.
- `CONTRACTS.local.md` C-101 codifies the rule (references C-100 bug → REQ).

## Why this exists

User identified the friction:

> 야 사용자가 입력한 프롬프트는 자동으로 어디 기록됐다가, document-critic이
> 나중에 사용자가 입력한 프롬프트까지 읽어서 REQ문서로 적어야하는지 한번 더
> 판단하도록 시키는게 어떰?

Banner reminder is upfront; retrospective check at close is the safety net.

## Major scope simplification during plan

Phase 3 Voice B found `prompt_memory.py:331-376` already captures
`USER_FEEDBACK.jsonl` per task with richer schema than the proposed new file.
User confirmed reuse over build. Cancelled: `prompt_capture.py`,
`prompts.jsonl`, offset tracking, truncate-on-PASS, .gitignore extension.

## Tests

15 new tests across 4 files. Full suite: 729 passed, 0 failed.

## Migration

None. `status` defaults to `accepted`; absent status frontmatter remains
valid (readers treat as accepted).

## Follow-ups

- Trigger logic in develop close is text-only; needs machine enforcement.
- qa-cli subagent runtime did not expose `write_critic_qa` in session
  (verdict bubbled to parent).
- `prompt_memory.py` did not populate USER_FEEDBACK.jsonl for this task —
  audit needed before C-101 can be runtime-validated.
