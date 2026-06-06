# Close Gate Final-Defense Guidance

Date: 2026-06-05

## Context

Dogfood feedback showed close gates were acting too often as a discovery loop:
agents wrote nearly-correct HANDOFF content, hit a format-sensitive gate, inferred
the missing shape, retried, and then hit the next close condition. That protects
correctness, but it wastes development time and makes predictable formatting
requirements surface too late.

## Change

- `write_handoff` now appends default close-gate sections when callers omit them:
  `User Feedback Disposition`, `Commit-backed Learnings`, and
  `Self-Healing Candidates`.
- When `USER_FEEDBACK.jsonl` has event ids, `write_handoff` includes disposition
  stubs that name each event id.
- Close context keeps the existing generic blockers for compatibility, but adds
  more precise HANDOFF diagnostics such as missing `Status:` lines and missing
  artifact paths.
- Close context exposes unresolved feedback ids and includes them in
  `next_action`.

## Rationale

The gate should remain the final defense. Predictable close requirements belong
in artifact writers and preflight context so agents can satisfy them before the
final gate, while the gate remains responsible for catching malformed or
unresolved evidence.

## Deferred

Verification tiers, a broader micro-harness close path, and AC linting for
input/route examples remain separate improvements.
