# REQ receipt capability diagnosis

summary: how to tell why receipts are not being recorded, and why hook_tree_health.py's answer is not sufficient
status: accepted
updated: 2026-09-01
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
| Completion rejected during validation | A `started` row exists but no `completed` row; `subagent_lifecycle` rejects with a named reason such as `no-canonical-start-attachment`. |

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

It reads one thing: the `installPath` recorded for `harness@harness` in
`~/.claude/plugins/installed_plugins.json`. That is the *registered* path, which
can differ from the tree the session actually loaded hooks from — and from the
tree `${CLAUDE_PLUGIN_ROOT}` resolves to.

On 2026-08-26 it indicted `~/.claude/plugins/cache/harness/harness/2.3.0` while
the live tree was `~/.claude/harness-dev/plugin`. Its prescribed fix
(`/plugin update harness@harness` + restart) would not have helped, because the
receipt subsystem was present and correctly registered the whole time.

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

## Completion validation depends on an attachment harness does not emit

`plugin/scripts/subagent_lifecycle.py` requires a subagent-transcript attachment
whose `hookEvent` and `hookName` are both exactly `SubagentStart`, and whose
content matches `Agent <type> started (<agentId>)`. Absent it, the completion is
rejected as `no-canonical-start-attachment` — before the agent's verdict text is
ever read, so a perfectly-formed reviewer response still yields nothing.

The matcher-qualified duplicate (`SubagentStart:<matcher>`) is already tolerated
and skipped. The canonical form is not produced by harness itself, which makes
receipt validity dependent on another plugin's hook remaining registered and
healthy. That coupling is a known defect, not a design intent: it should accept
any attachment carrying the identity payload rather than one exact string. Fixing
it is a separate maintenance task.

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
