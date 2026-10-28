---
freshness: superseded
invalidated_by_paths:
  - plugin/scripts/subagent_lifecycle.py
  - plugin/scripts/background_hook.py
  - plugin/hooks/hooks.json
freshness_updated: 2026-09-25T14:50:00Z
---

> **Historical — superseded 2026-10-28.** [Receipt-free execution and completion](REQ__receipt-free-execution-and-completion.md) replaces this document’s receipt collection, attestation, naming, storage and completion instructions. The body below is retained history, not active workflow guidance. Actual independent review PASS before actual QA and current required WBS checks remain required.


# REQ — subagent completion receipts survive runtime transcript shape

tags: [harness, receipts, verification]
summary: A subagent that completes normally must always produce a `completed` receipt; provenance tolerates duplicate hook attachments but never forgeries.
updated: 2026-09-25

## Expected normal behavior

When a harness subagent (reviewer, QA lens, or any lifecycle-tracked agent)
finishes on a task run:

1. `RECEIPTS.jsonl` contains an ordered `started` → `completed` pair for that
   agent's `runtime_id`, bound to the current `task_run_id`.
2. `task_verify` can therefore reach a receipt-backed `runtime_verdict: PASS`
   once required review lenses PASS before required QA lenses.
3. `task_close` succeeds on a standard task whose lenses have all completed.

If any of these does not hold for an agent that visibly ran and returned a final
response, the harness is defective — not the agent.

## Transcript provenance requirements

`_trusted_stop_provenance` in `plugin/scripts/subagent_lifecycle.py` derives the
completion's authority from the subagent transcript. It must:

- Take the agent type from exactly one source, in this order:
  1. The **canonical** attachment (`hookName == "SubagentStart"`, content a
     one-element list matching `Agent <type> started (<agent_id>)`). Identical
     repeats are one start; two different agent types are a hard failure.
  2. Only when no canonical attachment exists, the **matcher-qualified**
     attachment (`hookName == "SubagentStart:<type>"`). The suffix is the
     type, and that line must carry this `agentId` and post-date the run
     start. Different suffixes are a hard failure.
  3. Only when the transcript has neither, the single hook-owned `started`
     receipt for this runtime and run (§ "No start attachment at all").
- While a canonical attachment is present, **tolerate** matcher-qualified
  attachments beside it. Claude 2.1.x emits one per matched hook, with empty
  content, *before* the canonical attachment. Skip them then, and take no
  agent type, timestamp authority, or completion identity from them.
- **Reject** any other `hookName` under that `hookEvent`, and reject any
  transcript item whose `agentId` or `sessionId` names someone other than the
  stopping agent and session. That guard is loop-wide and applies to qualified
  attachments too.
- Treat a qualified attachment's *own* fields as non-load-bearing. Its
  `agentId` may be absent, and its `content`/`stdout` may repeat or even
  contradict the identity string; none of it is read. Requiring any field of a
  line the validator otherwise ignores would recreate the coupling that caused
  the 2026-08-25 outage.
- Keep every existing path, ownership, `nlink`, mtime-stability, session-marker,
  and run-id check unchanged. Tolerance applies only to duplicate hook
  bookkeeping, never to identity.

Together these establish the property that matters: **a real subagent of the
recorded type actually started in this task run.** That is what makes a
completion receipt something the orchestrator cannot simply assert.

## No start attachment at all

Both attachment shapes above come from other plugins' `SubagentStart` output
(oh-my-claudecode). The harness start hook prints none. On an install with only
the harness plugin, a subagent transcript has **zero** `SubagentStart`
attachments. Observed 2026-09-25 (Claude Code 2.1.282): all 8 stops in one
session were declined at `no-canonical-start-attachment`, even though the
harness `SubagentStart` hook had written a `started` receipt for each. No
completion was ever written, so `task_verify` could not reach PASS on any task.

When the transcript has no start attachment of either shape, provenance binds
the stop to the agent type recorded in the **single hook-owned `started`
receipt** (`source: claude_hook`) for this exact `runtime_id`
(`claude:<session>:<agent>`) and the current `task_run_id`. Everything else
still holds:

- official session and agent identity, the session→task binding, the
  transcript path shape, ownership, permissions, `nlink` and a stable read;
- every transcript line must belong to this agent and session;
- a present start attachment always wins, and one that conflicts with the
  start receipt fails closed (`receipt_pending`); a malformed `SubagentStart:`
  name still rejects with `unrecognized-start-hook-name`;
- zero or several matching start receipts, a start receipt from an earlier
  run, or one from another source give no fallback, and the stop is rejected
  with `no-canonical-start-attachment` as before;
- if the stop payload names a different supported lens than the start receipt,
  the stop is rejected with `hook-start-agent-type-mismatch`; a payload with
  no agent type is accepted;
- stop-only runtimes, which have no start receipt, still need a transcript
  attachment (C-14).

This does not widen the trust boundary. `RECEIPTS.jsonl` is guarded against
Write/Edit but not against Bash. A shell writer can already append a
`completed` row directly, which is outside the integrity boundary. A forged
`started` row would only unlock a completion whose verdict still comes from the
runtime stop payload.

Verification: `tests/test_subagent_lifecycle.py`. The attachment-less tests
cover the fallback, the stop-only refusal, a prior-run start, a lens mismatch,
a payload without a type, replay, attachment precedence, the malformed name,
and a `background_hook.py` end-to-end run. The `_hook_start_agent_type` test
includes the `MappingProxyType` snapshot entries.

## What provenance deliberately does not check

Provenance does **not** compare the transcript's final assistant text against
the payload's `last_assistant_message`, and must not be "hardened" to do so.

The runtime appends that text around the same instant `SubagentStop` fires, so
a hook reading immediately sees a trailing `thinking`/`tool_use` record (empty
extracted text) or no file at all. Requiring a match rejected genuine stops
roughly as often as it passed them — during the 2026-08-25 session only one of
five real lens agents produced a receipt.

What the check bought was resistance to a deliberate multi-step forgery: spawn
one trivial subagent so a genuine transcript and `started` receipt exist, then
replay a hand-written payload claiming `VERDICT: PASS` against that same session
and agent id. That is not the failure mode receipts exist for. Receipts guard
against an orchestrator *confabulating* that a review happened — not against one
deliberately staging a decoy and hand-crafting hook input. An agent able to do
the latter can also fabricate the transcript outright, so the check was not a
real boundary either.

A sharper form of the same gap: a crafted stop delivered against a genuine,
still-running lens agent would write `completed` before the real stop arrives,
and the real stop then degrades to `receipt_pending` — so a real reviewer's FAIL
would never land. Both variants require hand-crafted input to
`background_hook.py`. Bash/shell mutation and direct lifecycle-script
invocation are outside Harness PreToolUse enforcement, so this is an explicitly
accepted hostile-shell exposure rather than a receipt-model boundary.

Restoring it would require either a retry loop inside the stop hook or a
deferred-reconciliation state machine, plus a raised hook timeout. That is a
large, permanently-load-bearing apparatus bought for a threat model that does
not apply. The verdict comes from the runtime-supplied payload, which the
orchestrator cannot author.

## Not every stop owes a receipt

Agent classes other than lifecycle-tracked lenses stop without writing a
subagent transcript and without a `started` receipt. They owe no completion,
and reporting them as binding misses buries real failures — during the
2026-08-25 diagnosis roughly 25 such entries masked the single entry that
mattered. A miss is logged when a matching `started` receipt exists for the
run, or when the payload names an agent type; otherwise silence is correct.

## Fail-closed property

A provenance regression must block close, never fabricate a PASS. This is the
system's most important safety property and the reason the 2026-08-25 defect was
recoverable: the bug suppressed every completion receipt, so tasks became
unclosable rather than falsely closing. Any future change that makes provenance
failure produce a PASS — or that lets a `completed` receipt be written without a
verified transcript — is a strictly worse failure than the outage it prevents.

Because the failure is silent by construction, `background_hook.py` logs a
`background_hook:binding-miss` entry to `doc/harness/learnings.jsonl` whenever a
subagent stops without producing a receipt **that was owed** — see "Not every
stop owes a receipt" above for exactly when that is. That breadcrumb is the
intended diagnostic entry point; preserve it, and preserve its narrowing.

## Test obligation

Fixtures must reproduce the **runtime** transcript shape, including the
matcher-qualified duplicate with its real field set (`type: "hook_success"`,
`stdout`, `exitCode`, `toolUseID`, …) rather than a stylized approximation. The 2026-08-25 defect passed 827 tests because
`tests/test_subagent_lifecycle.py` emitted only the canonical attachment. A
green unit suite is not evidence that receipts work end to end; a live
spawn-and-verify pass is.

## History

- **2026-08-25** — the canonical-`hookName` check in `_trusted_stop_provenance`
  hard-failed on the
  matcher-qualified `SubagentStart:<agent-type>` attachment that Claude 2.1.220
  writes first. No subagent could record a completion, so `task_verify` never
  reached PASS and no standard task could close; four tasks were stranded open.
  Diagnosed from the `binding-miss` breadcrumbs.
- The same session had an approved plan to delete the receipt subsystem
  outright (`TASK__remove-receipt-system`), on the premise that the verification
  gate was not earning its complexity. That plan was **cancelled** once the
  cause was shown to be this one-line provenance bug rather than a design
  failure. Independent receipt-backed verification is retained deliberately;
  reversing it again should require new evidence, not a repeat of this outage.
