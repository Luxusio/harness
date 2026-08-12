# ADR: Receipt gates without source snapshots

Status: accepted  
Date: 2026-08-11  
Task: `TASK__remove-automatic-git-change-detection`

## Context

Harness previously derived task scope and verification freshness from Git HEAD,
dirty paths, committed paths, gitlinks, submodules, linked worktrees, and file
fingerprints. That made lifecycle calls slow and fragile in workspaces that
contain ignored nested repositories or independently managed services. It also
turned a workflow coordinator into a local repository-integrity monitor.

## Decision

Automatic Git change detection is removed from `task_start`, `task_context`,
`task_verify`, and `task_close`.

The plan supplies applicable review and QA lenses in `TASK.json`. Lifecycle
receipts prove only:

- the Harness task identity;
- the runtime child-agent identity;
- the declared review or QA lens;
- a matched start and explicit completion verdict, including an exact runtime
  event/session/thread/agent-path tuple when the runtime supplies one; and
- required ordering, including review PASS before QA start.

Receipts do not prove HEAD, diff contents, touched paths, source scope, or that
the repository stayed unchanged after verification. Post-review/post-QA edits,
concurrent mutations, and scope drift are developer-owned risks.

Explicit operations may still inspect Git or fingerprint their own concrete
payload. Examples include setup discovery, a verified installer, release
packaging, and an opt-in diagnostic command. Such checks are local to that
operation and are not lifecycle gates.

For the verified installer, review/QA receipts authorize the current task run;
the payload fingerprint proves only that the bytes copied and executed stayed
stable during that explicit install transaction. It does not claim that those
bytes are the same bytes seen by the reviewers. This is the same intentional
developer-owned post-QA mutation risk, including for `install.py` itself.

Receipt streams remain a transaction boundary. Each lifecycle generation gets
a random identity in `TASK.json`, copied into the session marker and receipts;
events from an earlier generation cannot authorize a resumed task, including
when a watcher replays its rollout. Terminal resume rotates receipt streams
under the hardened no-follow receipt lock, and close holds the same lock from
verdict evaluation through attestation/state publication. Late watcher events
cannot append after the task becomes terminal. Crossing an ignored nested Git
boundary likewise requires a session-specific ancestor task binding; the
legacy repository-wide marker is insufficient.

## Consequences

- Submodules, ignored nested repositories, and linked worktrees no longer need
  special change-tracking support.
- Lifecycle latency and failure modes are independent of repository size and
  Git metadata layout.
- Harness still rejects missing, malformed, mismatched, or incorrectly ordered
  review/QA receipts.
- Harness cannot automatically detect edits after QA or work performed outside
  the planned scope. Developers rerun review/QA when those changes warrant it.

This tradeoff is intentional: Harness coordinates evidence and ordering; it
does not police the developer's local Git workspace.
