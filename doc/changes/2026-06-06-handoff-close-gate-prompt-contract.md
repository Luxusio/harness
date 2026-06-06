# HANDOFF Close-Gate Prompt Contract

Date: 2026-06-06

## Context

Agents were still learning HANDOFF requirements by colliding with close gates.
The canonical develop skills described most of the contract, but shorter
developer prompts and the `write_handoff` MCP tool description only named
Commit-backed Learnings. Close validation also enforced stricter conditions
than the short prompts exposed: captured/applied entries must name changed
commit-eligible artifacts, and deferred self-healing entries need explicit
decision fields.

## Change

- Adjacent `HANDOFF_CLOSE_GATE.md` guides now own the detailed HANDOFF
  close-gate template for both runtime trees: `plugin/agents/` for Claude and
  `plugin-codex/agents/` for Codex installs. The installed Codex plugin keeps
  the guide beside `agents/developer.md`.
- Claude and Codex developer prompts stay short and point developer agents to
  the adjacent guide before calling `write_handoff`.
- The shared guide names the strict close rules for feedback dispositions,
  captured learnings, applied/deferred self-healing candidates, and durable-doc
  or no-doc rationale.
- The `write_handoff` MCP tool description now exposes the same close-gate
  contract so agents that inspect only tool metadata still see the required
  shape.
- Close `next_action` guidance now reports the specific Commit-backed Learnings
  or Self-Healing Candidates missing reason instead of collapsing every failure
  into a generic section reminder.
- Regression tests lock the prompt/tool metadata contract.

## Expected Behavior

Agents should be able to write a closeable HANDOFF before calling `task_close`
without first failing close to discover the expected format. The close gate
remains the final defense for malformed or unsupported claims.
