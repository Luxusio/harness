---
tags: [harness, receipts, subagent, lifecycle]
summary: 영수증은 실제 spawn 된 서브에이전트의 생애에만 대응한다. 재개는 새 영수증을 만들지 않고, 중단은 고아 started 레코드를 남긴다.
updated: 2026-09-26
freshness: superseded
invalidated_by_paths:
  - plugin/scripts/subagent_lifecycle.py
  - plugin/scripts/background_hook.py
  - plugin/hooks/hooks.json
freshness_updated: 2026-09-18T08:10:49Z
---

> **Historical — superseded 2026-10-28.** [Receipt-free execution and completion](REQ__receipt-free-execution-and-completion.md) replaces this document’s receipt collection, attestation, naming, storage and completion instructions. The body below is retained history, not active workflow guidance. Actual independent review PASS before actual QA and current required WBS checks remain required.


# REQ — receipts track a spawn, not a conversation

## Expected behavior

`RECEIPTS.jsonl` rows are written by the `SubagentStart` / `SubagentStop` hooks,
so a row exists for a subagent *process lifecycle* — not for a verdict, a
conversation, or an amount of work performed. Two consequences follow, and both
are load-bearing for anyone driving the loop:

1. **A verdict without a receipt cannot close a task, however good it is.**
   Ordered hook-owned receipts are the close gate (C-14). Nothing about the
   thoroughness of a lens report changes that.
2. **A receipt pair exists only if the hooks observed both ends.** A start
   without a stop is a live agent — or a dead one that never reported.

## Observed (2026-09-03)

Both of these cost time in one session because neither is written down anywhere.

### Resuming an agent produces no new receipt

`SendMessage` to a finished agent resumes it from its transcript and returns a
final response, but writes no `started` / `completed` pair. A QA lens that
returned FAIL, was resumed after the findings were fixed, and reported PASS
therefore left the receipt trail ending at FAIL. `task_verify` correctly refused.

The symptom is confusing precisely because the work was real: *"QA says PASS but
close keeps refusing."* That is the same surface presentation as the 2026-08-26
stale-hook-tree outage, which is why it is worth naming — the causes are
unrelated and the remedies are opposite.

**Consequence for the loop:** re-verification after fixing review or QA findings
must spawn a fresh lens, not resume the previous one. Resuming is useful for
asking an agent to explain or extend its reasoning; it can never advance the
gate.

### A lens that ends its turn to wait (2026-09-26)

A qa-cli lens ran the full suite as a background command and ended its turn to
wait for it. That turn end fired `SubagentStop`, and its interim text ("the
suite is still running") was recorded as the lens's `completed` row with
verdict `PENDING`. The completion notification then resumed the agent:

- its second `SubagentStart` for the same agent id used to raise
  `duplicate or conflicting Claude lifecycle start`, logged as a `gate-crash`;
- its real final (`PASS`) could not replace the recorded completion, and was
  reported as a retryable publication failure that nothing would retry.

The first turn end is the completion; that does not change (see "Why this is a
REQ" below). What changed:

- **Prevention.** The Claude lens agents (`code-reviewer`, `security-reviewer`,
  `qa-cli`, `qa-api`, `qa-browser`, `qa-desktop`) are told to run verification
  commands in the foreground and never end a turn while their own command or
  subagent is still running. In the two reviewers the rule sits outside the
  role core, so the Claude/Codex core parity is unchanged.
- **Naming.** A second start after a recorded completion writes nothing and
  logs a `background_hook:binding-miss` breadcrumb with
  `provenance_reason=resumed-after-completion`. A later, different final writes
  nothing and logs `provenance_reason=completion-already-recorded`. These are
  expected refusals, not binding failures, even though the breadcrumb text reads
  "produced no receipt". Any other conflicting start still raises.

The 2026-09-03 "resume writes no pair" observation above is very likely this
same mechanism; it now surfaces as these two reasons instead of a crash.

**Remedy:** spawn the lens fresh. `task_verify`'s "Recorded but unusable" next
action already says so, but its stated cause (a misplaced verdict block) and the
`receipts:verdict-unbound` breadcrumb's remedy ("re-deliver … with exactly one
verdict block") named only the malformed-report causes. Both texts now also
list this case as a possible cause, with its remedy (spawn fresh, run in the
foreground); they enumerate rather than assert, because the receipt row alone
cannot tell an interim stop from a malformed report
(`tests/test_unbound_verdict_names_interim_stop.py`, including the written
ledger line for a realistic task name, which the 400-character cap must not
cut).

Verification: `tests/test_subagent_lifecycle.py`
(`test_a_resumed_lens_is_named_and_cannot_replace_its_completion`,
`test_a_resume_with_a_different_agent_type_still_raises`,
`test_background_hook_names_a_resumed_lens_without_a_gate_crash`) and
`tests/test_feedback_rule_skill_docs.py::test_claude_lens_agents_stay_in_the_foreground`.

### Killing an agent leaves an orphan `started` row

Stopping a running subagent leaves its `started` row with no completion. The
stop gate (removed 2026-09-23; historical) read those rows as live background work and reported the agent as
still active — in one observed case for ~1450 seconds after it had been killed —
until `HARNESS_BACKGROUND_STALE_SECS` (default 1800) ages it out.

Nothing is corrupted and the gate degrades safely: `subagent_lifecycle`'s stale
handling is exactly why the row eventually stops counting. But between the kill
and the timeout, "background work still running" names a process that no longer
exists.

**Consequence for the loop:** after killing a lens, expect the gate to mention
it for a while. Do not interpret it as a second agent still running, and do not
try to clear it by hand — `RECEIPTS.jsonl` is hook-owned (C-05), and a
hand-written or hand-deleted row is exactly what the receipt protocol exists to
prevent.

## Why this is a REQ and not a code change

Neither behaviour is wrong. Receipts describing a spawn is the property that
makes them unforgeable; if a resume could append a completion, a coordinator
could manufacture one. The stale-timeout path is the fail-safe design working.

What was missing is the written expectation, so the observable behaviour reads
as a defect the first time it is met. If a future task does change this — for
instance by writing a cancellation row when an agent is killed — that row must
still come from a lifecycle hook observing the event, never from the caller.
