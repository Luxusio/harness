# REQ - Process Autonomous Task Pack Execution

## Intent

When a user gives a multi-step product, project, or roadmap request, harness should
turn the request into an ordered task pack and execute the tasks one by one without
asking the user how to split or sequence the work. Task decomposition and ordering
are harness operating details, not user decisions.

## Observable Behavior

- Harness must derive a task pack from the user's stated goal when the request
  clearly contains multiple sequential stages, roadmap items, or follow-up tasks.
- Harness must create or queue the task records up front when enough information
  exists to name the work and its acceptance boundary.
- Harness must choose the next task from the task pack deterministically from the
  declared roadmap order, dependency order, or highest-risk/highest-value order
  when no explicit order is supplied.
- Harness must not ask the user which task to do next, whether to split the work,
  whether to combine tasks, or whether to defer a subset when the user's goal is
  already clear. Those are internal planning choices.
- Harness may ask the user only for:
  - go/no-go at an agreed batch boundary,
  - a real product, architecture, billing, auth, data, privacy, or destructive
    operation decision,
  - an environment or credential blocker,
  - a contradiction where continuing would likely implement the wrong intent.
- Closing one task in a task pack is a checkpoint, not completion of the user's
  overall request. Harness must start or queue the next task unless the task pack
  is done, blocked, stopped by the user, or a configured budget/cap is reached.
- Status updates should report the chosen next task and reason as execution
  context, not present it as a question.

## Acceptance Signals

- A roadmap request with stages 1, 2, and 3 produces queued task records for the
  known stages before implementation starts, or records why a stage cannot be
  named yet.
- After stage 1 closes, harness proceeds to stage 2 without asking "stage 2 or
  review?" unless the user explicitly requested a review gate.
- Scope-partition prompts such as "split this task?", "do only cluster A?", or
  "which remaining stage next?" are absent when the original goal already orders
  the work.
- `USER_DECISION_REQUIRED` appears only for genuine user-owned decisions, not for
  harness workflow mechanics.

## Verification Cues

- Add tests or golden transcripts covering task-pack creation, next-task
  selection, post-close continuation, and suppression of scope-partition
  questions.
- Verify the run/autopilot prompt surfaces and prompt-memory text say "start or
  queue the next task" rather than asking the user to choose task order.
- Verify task-pack state survives interruption and resume.

## Non-Goals

- This does not remove plan approval, premise challenge, or genuine blocker
  escalation gates.
- This does not require unattended execution past an explicit user go/no-go
  boundary.
- This does not let harness invent product intent that the user did not provide.

## Source

- created: 2026-05-29
- source: user feedback in harness dogfooding session: task split and sequence
  questions should be handled by harness automatically.
