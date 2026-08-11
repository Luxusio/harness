# REQ process controlled artifact writes
summary: Agents must use the owning control surface for protected task artifacts
status: accepted
updated: 2026-08-12
freshness: current
confidence: high
kind: process
source: User directive stated 2026-03-31; updated 2026-06-22 after removal of manual evidence writers.

Protected harness artifacts must be written only by their owning control
surface. Agents must not hand-author files that the close gate treats as
provenance.

## Current Ownership

| Artifact | Owner |
|---|---|
| `PLAN.md` / `PLAN.meta.json` / optional `AUDIT_TRAIL.md` | MCP `write_plan` |
| `RECEIPTS.jsonl` | Codex/Claude review and QA lifecycle hooks |
| `CONVERSATION.md` | Codex/Claude UserPromptSubmit/Subagent hooks |
| `TASK_STATE.yaml` lifecycle fields | harness MCP task tools and runtime scripts |
| `doc/<area>/REQ__*.md` and other durable docs | normal committed doc edits or `plugin/scripts/req_scaffold.py` |

## Requirements

- The MCP server must not expose manual evidence writers, critic writers,
  handoff writers, or REQ writers.
- A PASS verdict must be backed by ordered hook-observed review and QA start
  and completion receipts, not by a narrative critic file.
- Task-local conversation history is human-readable Markdown. Close gates may
  read only explicit `<!-- item: ... status=open -->` markers from it, never
  infer requirements from free-form prose.
- Durable user requirements and reusable discoveries must be promoted to
  committed docs, skills, patterns, scripts, or tests. Task-local notes and
  transient `learnings.jsonl` rows are staging only.
- Agents must not output protected artifact file bodies inline in chat when a
  tool/script owner exists.

## Verification

- `tests/test_harness_mcp_server.py` checks the exposed MCP tool set and asserts
  removed writer tools are not callable.
- `plugin/scripts/prewrite_gate.py` blocks direct writes to protected task
  artifacts.
- `plugin/scripts/mcp_bash_guard.py` blocks shell mutation bypasses for the same
  protected task artifacts.
