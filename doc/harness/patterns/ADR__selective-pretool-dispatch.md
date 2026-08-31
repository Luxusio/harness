# ADR: Selective PreToolUse dispatch

Status: accepted

## Context

Harness previously registered `prewrite_gate.py` and
`qa_delegation_gate.py` without tool matchers. Both processes therefore started
for every Claude tool call and inspected the payload before deciding that most
calls were irrelevant. Read-only inspection, browser interaction, and unrelated
MCP tools paid the same fixed process-start cost as repository writes.

The browser gate protected an optimization rather than repository integrity.
Delegating browser work can keep large DOM snapshots, screenshots, and script
results out of the orchestrator context, but an inline call does not bypass the
plan, protected-artifact, review, QA-receipt, or close gates.

## Decision

PreToolUse dispatch follows the tool surface it protects:

```text
direct write tools (`Write|Edit|MultiEdit|apply_patch`) -> prewrite_gate.py
all other tools    -> no repository-mutation PreToolUse process
```

Browser and heavy full-suite delegation remains C-18 workflow guidance. The
orchestrator should prefer an applicable `qa-*` lens when isolation materially
reduces context or process load, but may run browser tools inline when
delegation is unavailable or inline work is materially simpler. Harness does
not inspect every tool call to enforce that preference.

Review and QA freshness, receipt ownership, and `task_verify`/`task_close`
requirements are unchanged. New direct-write tool names must be added to the
selective matcher when a supported runtime introduces them. Bash/shell
mutation is deliberately outside Harness PreToolUse enforcement.

## Consequences

- Read-only, browser, and unrelated tool calls no longer fork Python gate
  processes that immediately self-filter.
- Direct writes retain plan-first and protected-artifact enforcement. Bash
  commands run without a Harness mutation parser or deny decision.
- Inline browser output can consume substantial orchestrator context. This is
  an accepted developer-owned availability and throughput risk, not an
  integrity guarantee supplied by Harness.
- A newly introduced write-capable tool can escape the direct-write gate until
  the matcher is updated. Runtime integration tests must enumerate supported
  direct-write tool names.

## Alternatives rejected

- A separate Lite mode would duplicate hook policy and allow the default path
  to remain inefficient.
- Generic self-filtering hooks preserve automatic discovery of unknown tool
  names but charge every tool call for a rare mutation surface.
- Continuing to block inline browser calls treats context isolation as a hard
  security property and retains the highest-frequency generic hook.
