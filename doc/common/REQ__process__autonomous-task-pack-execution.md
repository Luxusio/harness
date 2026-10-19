# REQ - Process Autonomous WBS Outcome Execution

## Intent

When a user gives a multi-step product, project, or roadmap request, Harness
should add ordered outcomes to one WBS and execute them without
asking the user how to split or sequence the work. Task decomposition and
ordering are harness operating details, not user decisions.

## Observable Behavior

- Harness must add WBS outcomes when the request clearly contains
  multiple sequential stages, roadmap items, or follow-up tasks.
- Harness must queue known outcome IDs up front when enough information exists to
  name the work and its acceptance boundary, even before task directories exist.
- Harness must choose the next child deterministically from the
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
- Closing one WBS outcome is a checkpoint, not completion of the user's overall
  request. Harness must run self-improvement, learning promotion, and hygiene
  scheduling before selecting the next child with `wbs(action="next")`. It then
  continues the ready outcome unless the WBS
  is done, blocked, stopped by the user, or a configured budget/cap is reached.
- Status updates should report the chosen next task and reason as execution
  context, not present it as a question.

## Acceptance Signals

- A roadmap request with stages 1, 2, and 3 produces ordered WBS outcome
  records for the
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

- Add tests covering ordered WBS outcomes, dependency-ready outcomes,
  `wbs(action="next")`, post-close continuation, and suppression of scope-partition
  questions.
- Verify WBS close refuses unfinished or unverified outcomes.
- Verify run instructions preserve `task_close -> self-improvement/promotion/
  hygiene -> wbs(next)` and do not bypass automatic learning.
- Verify WBS state survives interruption and resume.

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
