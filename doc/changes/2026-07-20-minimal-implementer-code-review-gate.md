---
date: 2026-07-20
task: TASK__minimal-implementer-code-review-gate
tags: [developer, code-review, security, qa, subagent, receipts]
---

# Minimum-sufficient implementation now has an independent review gate

Source-changing harness tasks now use three distinct responsibilities:

```text
minimum-sufficient implementer
  -> read-only code/security review
  -> runtime QA
  -> close
```

The code reviewer is always required for source changes. The security reviewer
is added when changed paths or diff content identify a security-sensitive trust
boundary. Docs-only/non-code tasks receive an explicit exemption. Review and QA
have separate hook-owned receipt streams; review must PASS the current HEAD and
worktree fingerprint before QA starts, and any later source edit invalidates the
evidence.

## Open-source behavior provenance

| Harness behavior | Reference | Adaptation |
|------------------|-----------|------------|
| Implementer reads/traces first, then tries no change → reuse → stdlib → platform → installed dependency → minimum new code | Ponytail `skills/ponytail/SKILL.md` | Applied to `developer` and the actual parallel `ac-worker`; called minimum sufficient rather than minimum LOC |
| Implementer never removes trust-boundary validation, security, accessibility, or data-loss prevention for brevity | Ponytail `skills/ponytail/SKILL.md` | Extended to current auth, transactions, concurrency, cleanup, and error propagation |
| Minimality review detects deletion, stdlib/native reuse, and speculative abstractions | Ponytail `skills/ponytail-review/SKILL.md` | Incorporated as one lens of the balanced reviewer, not used as a correctness/security verdict |
| Always-on attacker/chaos posture covers races, leaks, silent corruption, swallowed errors, and trust boundaries | gstack `ship/sections/adversarial.md` | Required for every source diff and made fail-closed when unavailable |
| Findings require exact motivating code plus severity/confidence; specialists route from diff scope | gstack `review/checklist.md` and `ship/sections/review-army.md` | Added excess/missing direction, FIX_NOW/INVESTIGATE/OPTIONAL disposition, and diff-content routing |
| Security and migration specialists remain insurance controls despite low historical hit rates | gstack `ship/sections/review-army.md` | Security remains scope-conditional but can never be adaptive-hit-rate gated |
| Architect, code reviewer, security reviewer, simplifier, and QA have separate responsibilities; reviewers are read-only | oh-my-claudecode `agents/*.md` and `skills/autopilot/SKILL.md` | Uses one balanced code reviewer plus conditional security reviewer; avoids duplicate always-on agents and mutating simplification |
| Spec compliance precedes quality and every finding has file:line, severity, confidence, fix, and verdict | oh-my-claudecode `agents/code-reviewer.md` | Removed generic SOLID/function-length thresholds that can force project-inappropriate abstractions |
| Security reviewer prioritizes applicable OWASP/trust boundaries by exploitability and blast radius | oh-my-claudecode `agents/security-reviewer.md` | Excludes style/general refactoring and runs only when path or content signals apply |
| Parent persona is explicitly propagated or scoped to subagents | Ponytail `hooks/ponytail-subagent.js` | Claude uses named roles; Codex spawn prompts explicitly point to methodology files |

The references are behavioral inputs, not runtime dependencies. Harness owns
the resulting prompts, routing, lifecycle correlation, fingerprinting, and
close semantics.

## Gate contract

- `REVIEW_RECEIPTS.jsonl` stores only code/security reviewer starts and matched
  completions.
- `SUBAGENT_RECEIPTS.jsonl` remains the runtime QA lifecycle stream.
- Starts, unmatched waits/stops, missing verdicts, FAIL, and BLOCKED_ENV never
  count as PASS.
- Codex follows `wait_agent → list_agents`; the latter exposes each completed
  runtime agent name and final response for exact start/completion correlation.
- Reviewer output requires an exact first-line verdict and exact second-line
  `FINDING_COUNTS`; missing, duplicate, misplaced, or contradictory counts stay
  PENDING.
- Code review is always routed for source changes; security review is
  conditional from both paths and changed content, including dependency
  manifests, migrations, authorization/configuration signals, and large files.
- Only QA started after the latest required review PASS is valid.
- Any source change alters the canonical worktree fingerprint and invalidates
  review; QA must consequently run again after fresh review.
- Reviewers are read-only. Only FIX_NOW findings return to the original
  minimum-sufficient implementer; OPTIONAL findings never auto-grow code.

## Harness development install propagation

Harness-source changes now run `plugin/scripts/install_verified.py`
automatically after the final fresh review+QA PASS and before `task_close`.
The helper verifies the canonical manifest+origin, review/QA freshness, and a
fingerprint-scoped success marker under a lock before invoking
`python3 install.py --force`. Previously installation
was deferred until after a successful close, which deadlocked when the loaded
runtime was too old to emit the new receipt format. Installer failure blocks
completion, while an already-running process may still require a new session to
load the installed MCP server and hooks.
