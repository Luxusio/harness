# Quality audit and independent review gate

This file separates pre-review audit inputs from the final static review gate.
The final review runs after the last implementation commit/checkpoint and before
runtime QA. QA never substitutes for review.

## Phase 4.5: Pre-review audit inputs

Run independent inputs in parallel when applicable. They are advisory inputs,
not completion verdicts, and they do not write task artifacts.

1. **Test coverage trace**: map every changed path/branch and user flow to a
   focused test; identify genuinely uncovered behavior.
2. **Visual smoke**: browser-only, limited to the changed surface.
3. **Migration/contract specialist**: when schema, migration, config, or public
   API contracts change. This insurance specialist is never history-gated.
4. **LLM trust specialist**: when prompts, agent instructions, model output, or
   tool execution boundaries change.
5. **Performance specialist**: only when current request paths, queries,
   rendering, collections, or measured constraints are affected. Findings are
   advisory unless they demonstrate a current regression or requirement breach.

Apply necessary coverage or specialist fixes before the Phase 6 checkpoint.
Do not spawn the old generic adversarial, line-count Red Team, or quality
synthesis agents. Their responsibilities now belong to the balanced reviewer,
and duplicate reviewers create noisy fix churn.

## Phase 6.6: Mandatory independent review

After Phase 6 commits and the Phase 6.5 checkpoint, read `task_context` and use
its canonical `required_review_lenses` routing.

- `review-code`: spawn `harness:code-reviewer` whenever routed; mutating tasks
  default to this lens even when no path inventory exists.
- `review-security`: additionally spawn `harness:security-reviewer` when PLAN
  metadata or task routing requires it. Security is never inferred from a Git
  diff or adaptive-hit-rate gated.

When both lenses apply, spawn them in one message so they run independently in
parallel. Reviewers are read-only. They must read PLAN/REQUEST, linked durable
docs, complete changed files, relevant callers/callees, and nearby project
patterns.

Claude routing:

```text
Agent(subagent_type="harness:code-reviewer", prompt="Review <task_id> at the current diff. Return the exact VERDICT contract. Do not edit files.")
Agent(subagent_type="harness:security-reviewer", prompt="Security-review <task_id> at the current diff. Return the exact VERDICT contract. Do not edit files.")
```

Codex routing:

```text
ALL_TOOLS -> discover spawn_agent
spawn_agent(task_name="code_review", message="task_name: code_review\nRead plugin-codex/agents/code-reviewer.md and review <task_id>. Do not edit. Return exact VERDICT.")
spawn_agent(task_name="security_review", message="task_name: security_review\nRead plugin-codex/agents/security-reviewer.md and review <task_id>. Do not edit. Return exact VERDICT.")
wait for every required reviewer
prefer wait_agent status[agent_id].completed as the receipt completion signal
use list_agents once only when available and wait_agent omitted final identities
```

If the active spawn schema omits the structured `task_name` argument, omit only
that argument; the exact first-line marker in `message` remains required.

Lifecycle hooks record starts and matched completions in
`REVIEW_RECEIPTS.jsonl`. A start, unmatched wait, missing verdict, FAIL,
BLOCKED_ENV, a mismatched `TASK_RUN`, or invalid event ordering is not PASS. Do
not write or repair receipts manually.
On Codex, runtime capabilities vary. When `wait_agent` returns a structural
`status[agent_id].completed` map, the post-tool hook correlates those completed
agents directly and `list_agents` is unnecessary. If the wait response omits
identities or final transcripts, use `list_agents` once when available. Never
infer identity when multiple agents finish.

## Finding and fix loop

Each finding contains source evidence, present-day scenario, severity,
confidence, `excess|missing`, `FIX_NOW|INVESTIGATE|OPTIONAL`, and the smallest
safe correction.

- `FIX_NOW`: return only the required finding to the original minimum-sufficient
  implementer. Add/update the focused regression test, fix, and run it.
- `INVESTIGATE`: obtain the missing evidence. It cannot silently become PASS.
- `OPTIONAL`: report as advisory. Never send it into an automatic code-growth
  loop.

After fixing a reviewer finding, rerun that routed review before QA. Harness
does not detect later source edits; deciding whether an unrelated or post-QA
edit needs another review is developer-owned. QA must start after the latest review PASS
for every required lens in the current `TASK_RUN`; an early or
concurrent QA receipt cannot close the task.

## Phase 4.8: Near-zero-cost scan

Before the final checkpoint, scan only changed functions for immediately
reachable boundary, cleanup, and error-path gaps. Apply a change only when the
gap is demonstrated and the correction is smaller than leaving the defect.
Speculative null guards, retries, fallbacks, and impossible-state defenses are
not “free” and must not be added. The final balanced reviewer is authoritative.
