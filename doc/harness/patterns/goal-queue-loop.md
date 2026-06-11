# Goal Queue Loop

This policy defines how native `/goal` uses the Goal child-task queue for broad
product development. The runner executes child tasks, but the product loop is
Agile: each iteration
creates a thin vertical workflow, reviews the working product, rewrites the
backlog from evidence, and selects the next highest-value slice with a reason.
The quality-gate behavior is specified in
`doc/common/REQ__process-goal-queue-quality-gates.md`.

## Principles

- A backlog item is a user-value hypothesis, not a file/component task.
- A slice should be a thin vertical workflow a target user can experience.
- Each iteration must end in a runnable product state or an explicit blocker.
- QA/UX evidence can reorder the backlog.
- The next slice must have a stated reason tied to user value, risk, or learning.
- Quality gates should be preflight-first: surface likely collisions before the
  next command runs, and reserve hard blocking for missing review evidence or
  known failed/blocking evidence.
- `USER_DECISION_REQUIRED` remains a hard stop. Goal queue execution must not invent
  product, billing, auth, or architecture decisions.

## Backlog Item Shape

Each backlog item should carry enough product context for an agent to avoid
implementing isolated technical chunks:

```json
{
  "id": "slice-001",
  "title": "First usable workflow",
  "kind": "MVP",
  "user_value": "Target user can complete one real workflow",
  "hypothesis": "If this workflow works, the product becomes inspectable",
  "acceptance": ["User can complete the workflow", "QA passes"],
  "priority": 100,
  "status": "pending",
  "learned_from": ""
}
```

## Iteration Review

After each harness-closed slice, record a review:

- `demo_result`: `pass`, `fail`, or `partial`
- `user_workflow_status`: `complete`, `partial`, or `blocked`
- `qa_result`: `PASS`, `FAIL`, `BLOCKED_ENV`, or `not_run`
- `ux_result`: `PASS`, `FAIL`, or `not_applicable`
- `learnings`: what changed about the product understanding
- `backlog_changes`: what should change in scope/order
- `next_slice_id` and `next_slice_reason`

The runner appends quality fields to each review:

- `review_quality`: `complete`, `warning`, or `blocked`
- `quality_warnings`: pre-collision guidance such as missing learnings, missing
  backlog rationale, partial workflow evidence, missing next-slice rationale, or
  UX findings
- `quality_blockers`: evidence that continuing would build on a broken slice,
  such as failed demo, blocked workflow, QA `FAIL`, or QA `BLOCKED_ENV`

Warnings do not stop default execution. They should usually become backlog
changes, replan notes, or a targeted follow-up before the next slice. Blockers
should stop unattended execution until the failed workflow or missing evidence is
resolved.

## Continuation Gate

Closing one harness slice is an iteration checkpoint, not Goal completion.
After every closed slice, compare the current product against the locked product
goal, list remaining product gaps, choose the next highest-value thin vertical
slice, and start or queue the next harness task unless a valid stop condition
applies.

The final Goal response is allowed only when the product goal is fully
satisfied, a user/environment blocker prevents further progress, the user
explicitly stopped or narrowed the run, the configured budget/cap was reached,
or the next slice is already active/queued and the response is only a status
update. `MVP scaffold complete` is not `GOAL DONE` unless the locked goal
was only to build a scaffold.

## Preflight Gates

Use preflight before a long unattended loop or when resuming after interruption:

```bash
python3 plugin/scripts/goal_queue_runner.py preflight \
  --require-review-before-next
```

Preflight output is intentionally operational:

- `preflight: PASS` means the next slice has no known review-quality concerns.
- `preflight: WARN` means the next slice may proceed by default, but the warning
  should shape the next plan or backlog update.
- `preflight: BLOCK` means the next unattended run would continue without a
  required review or would ignore known failed evidence.

`--require-review-before-next` is the forced gate. It blocks when a completed
slice has no iteration review and when a completed slice's latest review records
quality blockers. Warning-only reviews continue, so the loop favors early
detection over walling off ordinary Agile learning.

## Replan Rules

- Replan after each review.
- Raise priority for work that unblocks a user-visible workflow.
- Lower or defer work that no longer supports the locked product direction.
- Add hardening slices only when they protect a shippable workflow.
- Do not mark the product done until the agreed done criteria and required
  harness QA/UX gates are satisfied.
