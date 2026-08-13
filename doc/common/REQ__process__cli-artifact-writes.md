# REQ process controlled artifact writes
summary: Agents must use the owning control surface for protected task artifacts
status: accepted
updated: 2026-08-13
freshness: current
confidence: high
kind: process
source: User directive stated 2026-03-31; updated 2026-08-13 for the minimal current artifact set.

Protected harness artifacts must be written only by their owning control
surface. Agents must not hand-author files that the close gate treats as
provenance.

## Current Ownership

| Artifact | Owner |
|---|---|
| `PLAN.md` | MCP `write_plan` |
| exact four-field `TASK.json` | harness MCP task tools; `write_plan` may update required lenses |
| `RECEIPTS.jsonl` | Codex/Claude review and QA lifecycle hooks |
| `doc/<area>/REQ__*.md` and other durable docs | normal committed doc edits or `plugin/scripts/req_scaffold.py` |

## Requirements

- The MCP server must not expose manual evidence writers; durable docs remain
  normal committed repository edits.
- A PASS verdict must be backed by ordered hook-observed review and QA start
  and completion receipts, not by self-authored narrative evidence.
- User corrections that affect acceptance must be reflected directly in
  `PLAN.md` or promoted to durable documentation before close.
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
