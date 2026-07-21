# harness Architecture Specification

tags: [harness, spec, architecture]
status: draft
created: 2026-04-09
updated: 2026-06-22
task_ref: TASK__harness-architecture

---

## Overview

harness is a goal-oriented control plane for repo-mutating agent work. Users
state work as a native Goal. Harness hooks and MCP tools sync that Goal into a
harness Goal, then create child tasks only as needed. Small requests may stay as
one child task; broad requests may grow additional child tasks as bugs, pages,
domains, or follow-up gaps are discovered.

The design goal is simple runtime behavior:

- one public Goal owns the user request
- child tasks hold scoped implementation work
- task-local planning is written through `write_plan`
- verification is receipt-backed through hook-observed subagent starts
- durable knowledge lands in committed docs, skills, patterns, scripts, or tests

## Goal Flow

```text
native Goal -> harness Goal -> child task(s) -> verify -> close task -> next task or finish Goal
```

The Goal is not a skill. Hooks inject the synchronization instruction and MCP
tools maintain the harness Goal/task queue. The agent chooses whether the Goal
needs another child task by inspecting the current task result, discovered gaps,
and pending user feedback.

## Task Loop

Every repo-mutating child task follows this loop:

```text
plan when needed -> minimum-sufficient develop -> independent review -> runtime QA -> verify -> close
```

No verification is skipped. If verification finds a gap, the task returns to
develop or creates a follow-up child task when the gap is separable.

## Protected Artifacts

| Artifact | Writer |
|---|---|
| `PLAN.md`, `PLAN.meta.json`, optional `CHECKS.yaml`, optional `AUDIT_TRAIL.md` | MCP `write_plan` |
| `CHECKS.yaml` status transitions after plan | `plugin/scripts/update_checks.py` |
| `SUBAGENT_RECEIPTS.jsonl` | Codex/Claude subagent lifecycle hooks, including the Codex SessionStart watcher |
| `REVIEW_RECEIPTS.jsonl` | Codex/Claude reviewer lifecycle hooks, including the Codex SessionStart watcher |
| `CONVERSATION.md` | Codex/Claude UserPromptSubmit/Subagent hooks |
| durable docs under `doc/<area>/<TYPE>__*.md` | normal committed doc edits or `plugin/scripts/req_scaffold.py` |

Manual evidence writers are intentionally absent. The MCP server does not expose
manual evidence writers, critic writers, handoff writers, or REQ writer tools.
`CONVERSATION.md` is readable task history; machine enforcement only reads
explicit `<!-- item: ... -->` markers.

## Verification

Verification has two responsibilities:

- operation check: the changed system works as implemented
- intent adequacy check: the result satisfies the user's request and durable
  requirements

A source-changing task first requires the always-on read-only code reviewer and,
when path or diff-content signals identify a security-sensitive boundary, the
read-only security reviewer. Their hook-owned `REVIEW_RECEIPTS.jsonl`
completions must explicitly PASS the current HEAD and uncommitted worktree
fingerprint. Docs-only/non-code tasks receive an explicit routing exemption.

Only QA started after the latest review PASS is eligible for runtime PASS. Every
applicable QA lens then needs a hook-owned `SUBAGENT_RECEIPTS.jsonl` completion
with explicit `VERDICT: PASS`. A start receipt proves delegation only. FAIL,
BLOCKED_ENV, missing verdicts, missing lenses, unmatched lifecycle events, and
source edits after review or QA prevent close. Self-authored PASS notes,
summaries, or narrative evidence do not close the task. Commands and inline
checks may still help debugging, but close authority comes from `task_verify`
reading task state and the two lifecycle streams.

Codex runtimes do not always forward collaboration tools to plugin
`PostToolUse`. On SessionStart, Harness therefore launches a watcher scoped to
the current `CODEX_THREAD_ID` and canonical repository. The watcher captures
HEAD and the task diff fingerprint only while a correlated child is still
running, then accepts completion only when the root-delivered message matches
the child rollout's final answer and task-complete record. Historical finals,
unrecognized lenses, cross-repository lineage, partial records, symlinks, and
schema ambiguity cannot create PASS. The watcher and classic PostToolUse path
share the same receipt owner and freshness gates.

## Durable Knowledge

Task-local files are not shared memory. User-stated durable requirements,
observable behavior contracts, significant decisions, reusable implementation
guidance, and surprising reusable discoveries must be promoted to committed
surfaces:

- `REQ__*.md` for observable behavior and contracts
- `GUIDE__*.md` for reusable guidance
- `ADR__*.md` for significant technical choices
- `POLICY__*.md` for external constraints
- skills, patterns, scripts, or tests when those are the right executable
  knowledge surface

`doc/harness/learnings.jsonl` is staging only. It helps identify promotion
candidates but does not satisfy the durable-memory requirement by itself.

## Runtime Boundaries

harness does not require users to choose between run/autopilot modes. A Goal may
remain small or expand child tasks dynamically. Hooks provide compact context
and record subagent starts; MCP tools own explicit state transitions.
