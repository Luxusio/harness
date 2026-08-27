# REQ process subagent receipt binding

summary: every hook-observed subagent stop for the active task records exactly one completion receipt
status: accepted
updated: 2026-08-27
freshness: current
confidence: high
kind: process
invalidated_by_paths:
  - plugin/scripts/subagent_lifecycle.py
  - plugin/scripts/background_hook.py
  - plugin/hooks/hooks.json

## Expected behavior

A subagent that starts and stops under an active harness task produces exactly
two `RECEIPTS.jsonl` entries: one `started`, one `completed`. Starts and
completions pair one-to-one. An unpaired `started` means either the agent is
still running or the harness declined the stop — never that a completed agent
went unrecorded.

This is load-bearing rather than cosmetic. `task_verify` derives
`runtime_verdict` from ordered hook-owned start/completion pairs (C-14), so a
completion the harness declines is indistinguishable from a review that never
happened. A loss rate anywhere above zero makes PASS a matter of luck, and at
the observed rate it made PASS unreachable.

## Observed defect (2026-08-27)

`TASK__bash-guard-unresolved-path-allow` accumulated **41 starts, 32
completions, 9 unpaired starts, 0 orphan completions**, across seven review
rounds and two days. Five reviewer agents delivered complete findings that were
acted on and committed; none recorded a completion.

Root cause: `subagent_lifecycle.py` scanned the subagent transcript for a start
attachment with `hookName == "SubagentStart"` carrying
`content == ["Agent <type> started (<agentId>)"]`, and skipped
`SubagentStart:<matcher>` attachments as duplicates "written alongside the
canonical attachment".

**That banner is not the harness's.** Both start attachments come from a
third-party plugin — `oh-my-claudecode`'s `subagent-tracker.mjs`, registered on
`SubagentStart` (`node "$CLAUDE_PLUGIN_ROOT"/scripts/run.cjs …
subagent-tracker.mjs start`). The canonical banner is that hook's
`hookSpecificOutput.additionalContext`, and the plugin **intermittently omits
it**, returning a bare `{"continue":true}` while still exiting 0. The harness's
own `SubagentStart` hook (`background_hook.py --event start`) writes no
attachment at all.

So C-14 receipt provenance was anchored to an optional field of an unrelated
plugin's output. Measured across all 47 subagent transcripts of the affected
session: **40 carry both shapes, 7 carry only the matcher-qualified record.**
Those 7 are the declined completions.

Falling back to the hook-execution record removes the dependency on that
**optional field** — not on the plugin. Both start attachments are still omc's:
across every session of this project, 71 canonical `hook_additional_context`
lines and 79 `hook_success` records, all from `subagent-tracker.mjs`. The
harness's own `SubagentStart` hook (`background_hook.py --event start`) emits no
attachment at all when it succeeds.

**The harness therefore still owns no start attachment** — with one perverse
exception. When the harness's own 3 s start hook *times out*, the runtime
records a `hook_cancelled` attachment for it, and `_bind` accepts that as a
binding line because attachment `type` is never inspected. So the only
self-owned start attachment the harness has today is the one it writes when it
fails. Semantically that is still sound — the record proves the runtime
dispatched `SubagentStart` for this agentId — but it is load-bearing for the
follow-up below and should not be mistaken for coverage.

On an install without oh-my-claudecode a subagent transcript contains zero
`SubagentStart` attachments, `_bind` rejects at `no-canonical-start-attachment`,
and PASS is unreachable permanently rather than intermittently. This is a live
gap for every downstream user of the plugin, and it is not closed by this task.
Closing it means emitting a harness-owned identity banner from
`background_hook.py --event start` — see the requirement below, which this
document does not yet satisfy.

### Why the accepted shape is trustworthy

"Matcher-qualified" is a misleading name, and the distinction carries the whole
trust argument for the widening. oh-my-claudecode registers `SubagentStart`
with `"matcher": "*"`, yet the runtime writes `SubagentStart:<agent-type>` —
608 records with ~30 distinct suffixes for that one command. **The suffix is
resolved by the runtime, not chosen by the plugin.**

That makes the newly accepted line *less* plugin-controlled than the one already
accepted. Its `agentId`, `sessionId`, `timestamp` and agent type are all
runtime-authored; the canonical banner's `content` is entirely omc-authored. A
malicious plugin therefore has less leverage over the shape this change adds
than over the shape that was accepted before it, and any agent type it did forge
is caught downstream by the started/completed identity match, which yields no
receipt at all rather than a mistyped one.

The residual risk from the omc dependency is **availability, not integrity**.

Note the sampling error that produced the first version of this paragraph:
three transcripts were probed, all three happened to be among the seven, and
"the runtime emits only the qualified form" was written as though it were the
rule. It is the exception — which is why the outage looked intermittent rather
than total. Counting the whole population is what distinguishes the two, and
this document asserted the opposite until a reviewer counted.

## Requirements

- Provenance checks are written against the transcript shape the runtime
  **actually emits**, verified against a captured transcript. An assumed schema
  is not evidence, and a comment asserting what a runtime emits is a claim that
  needs checking like any other.
- Verify such a claim against the **whole population**, not a sample. An
  intermittent shape looks like the only shape if every transcript you open is
  one of the failing minority, and the resulting rule is confidently wrong.
- Receipt validity never depends on a field owned by another plugin. If a
  signal the harness relies on is produced by software the harness does not
  ship, the harness must degrade to something it does own.
  **Not yet satisfied.** Both accepted start shapes are written by
  oh-my-claudecode; the harness owns neither. Until
  `background_hook.py --event start` emits its own identity banner, this
  requirement is aspirational and the gap above is real.
- A repeated signal is not a forgery signal. One start pair is written per
  registered `SubagentStart` hook, so identical repeats are expected; only
  *conflicting* claims (two agent types for one `agentId`) are a conflict.
  Rejecting on count declined honest stops.
- Both start shapes bind: the canonical identity banner, and a matcher-qualified
  `SubagentStart:<agent-type>` hook record. Accepting an additional *shape* is
  not accepting an unverified one — the binding line must still carry this
  `agentId`, this `sessionId`, and a timestamp at or after the task run start.
- The identity bar attaches to whichever line actually binds. A qualified line
  may omit its own `agentId` while a canonical attachment supplies identity;
  when the qualified line *is* the binding one, its `agentId` is mandatory.
  (The tolerance and the bar were both fixes for real outages, in opposite
  directions.)
- Rejections stay fail-closed and keep their `provenance_reason`. Every reason
  code keeps a test, so widening acceptance cannot silently retire a check.
- A declined stop is logged with its reason. `background_hook:binding-miss`
  records in `doc/harness/learnings.jsonl` are the diagnostic surface —
  note the field is `source`, not `key`.

## The three observed rejection reasons

- **`no-canonical-start-attachment` (5 of 8)** — fixed. The qualified
  hook-execution record now binds when the omc banner is absent.
- **`duplicate-canonical-start` (2 of 8)** — fixed. Both transcripts carried
  *two identical* start pairs for one `agentId` and one agent type, and the
  check rejected on count. It now rejects only on conflicting agent types, so
  a repeat is accepted and a contradiction is not.
- **`transcript-changed-during-read` (1 of 8)** — correct as designed, not
  fixed. The runtime appends the final assistant message around the instant
  `SubagentStop` fires, so a read that starts mid-append sees the file change
  under it. Failing closed on a torn read is right; the alternative is binding
  against a transcript whose contents are indeterminate.

  The prohibition is **never accept a changed transcript** — which is narrower
  than "leave this code alone". A bounded re-read (re-`fstat`, re-read from the
  same fd up to N times, then fail closed) closes the race without accepting
  anything indeterminate, and stays open to the follow-up. Worth doing: the cost
  is not one lost receipt but an entire reviewer or QA agent that must be re-run
  before PASS is reachable, and it fired twice in one session.

## Diagnosis notes

`RECEIPTS.jsonl` has three states, not two. Absent means hooks are not running
at all; partial means some path is failing; **declined** means the hooks ran,
saw the stop, and refused to bind it. Declined is the only one that leaves a
`binding-miss` breadcrumb, and it is invisible if you only compare receipt
counts to expectations.

Do not diagnose from `hook_tree_health.py` alone: it reads the registered
`installPath` from `installed_plugins.json`, which can point at a cache tree
(`plugins/cache/harness/harness/<version>`) while the live hooks run from a
different directory. It reported `RECEIPT_HOOKS_UNAVAILABLE` throughout this
outage while receipts were in fact being written and then declined.

## Never

Receipts are never hand-written or retroactively created, under any
circumstances, including to unblock a task whose work is genuinely complete. A
fabricated receipt makes `task_verify` pass while proving nothing, which is the
precise failure C-14 exists to prevent. If receipts cannot be recorded, the task
does not close.

See [[REQ__receipt-capability-diagnosis]] for the wider diagnosis flow.
