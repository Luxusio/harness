# Codex public Harness run routing

## Decision

Codex exposes one public `$harness:run` skill for every repository-mutating
request. The public skill is a thin entry that loads the complete internal run
workflow; plan, develop, review, and QA prompts remain internal implementation
details.

This closes the gap where subagent requirements existed only inside an
internal skill: if Codex never selected that skill, those requirements could
not affect execution. Selection is now reinforced at four independent layers:

1. `skills/run/agents/openai.yaml` enables implicit invocation and describes
   the default repository-change workflow.
2. the Codex UserPromptSubmit wrapper injects the mutation/read-only routing
   rule even when no task is active, using only a parent-directory manifest
   check and never performing Git discovery;
3. setup writes the same durable rule into `AGENTS.md`;
4. prewrite and shell mutation gates direct an out-of-workflow edit back to
   `$harness:run`.

Setup finalization verifies the complete route rather than file presence alone:
the public skill policy must enable implicit invocation and the managed
`AGENTS.md` block must route repository mutation to `$harness:run`.

The hook and AGENTS rule are routing signals. The existing task verification
and close gates remain authoritative: code review is always independent,
security review is scope-conditional, QA is independent and later than review,
and only fresh completed verdicts for the reviewed revision can close a task.
The develop workflow is the single normal owner of that transaction. It performs
learning and durable-doc work before a final freshness check, reinstalls only
when the Harness install payload changed, and then closes exactly once; run-level
verify/close phases are recovery paths for interrupted or older flows.

Agent instruction files (`AGENTS.md` and `CLAUDE.md`) are behavioral review
inputs, not docs-only exemptions. Security routing compares the complete task
diff from its captured starting HEAD so committed deletions remain visible.

## Why one public skill

A single mutation entry keeps the trigger broad enough for implicit selection
without exposing internal phase prompts as competing user choices. It also
gives write gates one stable recovery action. Read-only questions, explanations,
and status checks explicitly bypass the run skill.

The implementation/reviewer behavior and its Ponytail, gstack, and
oh-my-claudecode sources remain documented in
`doc/designs/minimal-implementer-and-code-review-gate.md` and
`doc/changes/2026-07-20-minimal-implementer-code-review-gate.md`.

## Validation contract

- the public skill passes the Codex skill schema validator;
- setup packaging includes the skill and `agents/openai.yaml`;
- setup completion checks the installed public skill, implicit policy, and
  AGENTS route;
- hook and gate tests assert the public route is emitted;
- cachebuster installs retain prior version directories because active Codex
  sessions keep absolute hook paths until restart;
- the full repository test suite must pass before installation.
