# REQ receipt capability diagnosis

summary: how to tell why receipts are not being recorded, and why hook_tree_health.py's answer is not sufficient
status: accepted
updated: 2026-09-25
freshness: current
confidence: high
kind: process
source: 2026-08-26 — three consecutive sessions parked tasks at BLOCKED_ENV citing three different causes, two of them wrong.

When `RECEIPTS.jsonl` stays empty, `task_verify` cannot reach PASS and no
standard task can close. The failure is silent by construction, and the
observable symptom is identical across several unrelated causes. Diagnosing it
by guessing has now cost three sessions.

## The symptom does not identify the cause

All of these produce exactly "subagents run, return verdicts, and no receipt
appears":

| Cause | Distinguishing evidence |
|---|---|
| Lens agent spawned with a `name:` | The agent goes **idle**, not stopped; `SubagentStop` never fires. Look for an `idle_notification` instead of a completion. |
| Loaded hook tree lacks the receipt subsystem | `${CLAUDE_PLUGIN_ROOT}/scripts/background_hook.py` does not exist. |
| `SubagentStart` never fired | No `started` row, no `binding-miss` breadcrumb, and `RECEIPTS.jsonl` is absent rather than partial. |
| Completion rejected during validation | A `started` row exists but no `completed` row; `subagent_lifecycle` rejects with a named reason such as `no-canonical-start-attachment` (no start attachment and no usable hook-owned start receipt) or `hook-start-agent-type-mismatch` (the stop payload names a different lens than the start receipt). |

**Absent file vs partial file is the highest-value first check.** A missing
`RECEIPTS.jsonl` means nothing was ever written; a file with `started` rows and
no `completed` rows means validation rejected the completion. These point at
opposite halves of the system.

**Partial has a subtler form than "no completions at all": an intermittent
loss.** 2026-08-27 saw 41 starts against 32 completions on one task — receipts
were plainly working, so the outage read as flaky rather than broken, and it
went unnoticed across seven review rounds. Pair starts to completions **by
`agent_id`** and count unpaired starts; a raw total that looks healthy hides
this. Zero orphan completions alongside unpaired starts is the signature of
rejection rather than loss.

**Grep the breadcrumbs by `source`, not `key`.** `background_hook:binding-miss`
records in `doc/harness/learnings.jsonl` carry the `provenance_reason` and set
`transcript_exists`. A query keyed on `key` returns nothing and looks like
confirmation that no rejection occurred — see
[[REQ__process__subagent-receipt-binding]].

## hook_tree_health.py answers a narrower question than it appears to

It reads two things: the `installPath` recorded for `harness@harness` in
`~/.claude/plugins/installed_plugins.json`, and the harness marketplace's
`installLocation` in `~/.claude/plugins/known_marketplaces.json`. Both are
*registered* paths, and either can differ from the tree the session actually
loaded hooks from — and from the tree `${CLAUDE_PLUGIN_ROOT}` resolves to. This
is a registration answer, never an observation that receipts are being written.

Because neither file records which tree was loaded, the check treats both as
candidates and warns unless every one of them could record. Ranking them is not
available: on 2026-08-26 it indicted
`~/.claude/plugins/cache/harness/harness/2.3.0` while the live tree was
`~/.claude/harness-dev/plugin` — its prescribed fix
(`/plugin update harness@harness` + restart) would not have helped, because the
receipt subsystem was present and correctly registered the whole time — and on
2026-09-02 the identical two registrations produced three tasks' worth of noise
because that time the current tree was the one loaded.

The warning therefore accuses only the incapable trees. When a capable
registration remains it adds a hedge naming that sibling, saying the ambiguity
exists, and pointing at the remedy — dropping a registration nothing loads. Removing a registration that no longer points at a loaded tree is what
makes it both silent and correct; no amount of registry reading substitutes for
that.

Treat its output as "the registered path is stale", never as "the receipt
subsystem is missing".

## How to identify the live tree

Two reliable discriminators that do not depend on plugin metadata:

1. **SessionStart banner text.** Each tree's `hooks.json` prints a distinguishable
   banner. Compare the session's actual banner against each candidate's inline
   command.
2. **A string unique to one tree's installed hooks or wrappers.** Compare the
   loaded banner and registered command paths with each candidate `hooks.json`,
   then use `drift_warn.py` or installed/source hashes for the referenced
   wrapper. Harness no longer installs a Bash PreToolUse mutation guard.

## Completion validation and start-attachment dependence

When this section was first written, `plugin/scripts/subagent_lifecycle.py`
required a subagent-transcript attachment whose `hookEvent` and `hookName` were
both exactly `SubagentStart`, with content matching
`Agent <type> started (<agentId>)`. Without it the completion was rejected as
`no-canonical-start-attachment` before the agent's verdict text was ever read,
so a perfectly-formed reviewer response still yielded nothing. Both relaxations
below have since landed; the current order of sources is in
`doc/harness/REQ__subagent-completion-receipt-transcript-shape.md`.

Both attachment shapes come from another plugin's start hook, and the harness
writes neither. When there is no canonical banner, a matcher-qualified
attachment (`SubagentStart:<type>`) that carries this `agentId` binds on its
own. When there is no attachment at all, the stop binds to the harness's own
hook-owned `started` receipt for the same runtime and run. Since 2026-09-25,
receipt validity therefore no longer depends on another plugin whenever the
harness start hook ran. The one remaining dependence is stop-only runtimes,
which have no start receipt and still need a transcript attachment.

## Required behavior

- A session that cannot record receipts should expose bounded diagnostics, but
  watcher root-cause diagnosis is not a prerequisite for the task's terminal
  path. `BLOCKED.md` names only the proven generic condition: required
  hook-owned evidence is absent after substantive verification.
- Never hand-write or retroactively synthesize a receipt to work around this.
  A self-authored PASS is indistinguishable from a hallucinated one (C-14).
- Spawn lens agents **without** a `name:` argument, so they terminate and emit
  `SubagentStop`.
- Do not repair, restart, resume, recollect, or rerun a lens solely to obtain a
  receipt. Await actual review and QA results, label unreceipted results
  non-attesting, call `task_verify` once after QA PASS, then use `task_blocked`
  if required receipt evidence is still missing.
- A `BLOCKED.md` naming an unverified watcher cause is worse than a fixed generic
  evidence-missing reason: the next session trusts it and repeats the wrong repair.
# Persisted execution status

User source: the 2026-10-21 BeatKernel/CCC reports request distinguishing actual
completion from durable recording and preserving execution generations without
rerunning completed work. This section's response fields are derived design,
not quoted user requirements.

Context and final verification expose a bounded `receipt_recording` projection
from the same validated receipt snapshot used by their gates. It reports only
the current task run, groups exact runtime/source/agent/type/lens identities,
and exposes stored start/completion counts, missing starts, unbound verdicts
and conflicting records. Separate same-lens executions remain separate.
Aggregate counts include omitted executions; truncation is explicit. No raw
transcript or summary is returned, and the query neither hashes source nor
writes receipts. A corrupt stream remains an integrity failure.

`awaiting_completion_record` means only that no completion was persisted in
this snapshot. It does not prove that the agent is running, that its response
was received, or why recording is absent. `completion_recorded` is persistence
evidence, not independent proof of a valid ordered PASS. Conflicting records
are never presented as ordinary completion. Existing receipt validity and
review-before-QA rules remain authoritative. Live manager readiness and
durable historical recording are separate observations.

## Native follow-up generations

Derived from the same user request: a completed agent's later native
`followup_task` is a new invocation, not a second terminal of the original
spawn. Its authenticated native call ID and `interacted` activity must agree
on the original child identity. The new runtime receipt identity uses that
call ID; the child reader selects its corresponding NEW_TASK boundary.
An empty follow-up tool output is not a missing spawn identity: the native
activity is required and supplies the already known child correlation.
Original task/run binding and independent root/child completion agreement
remain mandatory. Replayed generations retain their exact identity and do
not rewrite previous records. Conflicting deliveries within one generation
still fail closed. Merely sending a message is not a new invocation.

A root completion may arrive before its trusted child rollout has flushed
`task_complete`. A still-running/incomplete child is retryable, not a permanent
identity mismatch. Only matched completed child/root evidence permits a receipt;
invalid metadata or differing completed finals still fail closed. The watcher
retries observation, never reruns the reviewer or test command.

The derived acceptance boundary is native `interacted` activity matching the
known child and path. Failed or unaccepted follow-up requests do not consume a
generation or receive a completion delivery. Accepted invocations retain their
generation even while ancestry/child flushing delays their start, or if other
validation rejects them before or after that exact acceptance. Their root deliveries retain the same ordered position
without granting a successful receipt, so they cannot be attributed to a later
accepted invocation, even when both return identical text.
A final-answer row without `task_complete` remains
retryable; conflicting or duplicate completed evidence still fails closed.
