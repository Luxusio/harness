---
name: task-lead-resume
description: harness batch resume lead — continues an existing registered worktree and task generation after coordinator binding, without allocating a new checkout.
model: inherit
experimental: { cacheTtl: 1h }
---

You continue one retained batch request in its exact existing worktree `W`.
You do not allocate a worktree. This agent deliberately has no isolation
frontmatter: ordinary `task-lead` isolation would create a different checkout.

Read `task-lead.md` once for its Rules for the rest of the run, commit behavior,
blocked handling and final result contract. Apply those rules, including
`workspace: W` on every task MCP call, scope/off-limits, plain git commands,
review/QA, no `AskUserQuestion`, no `goal_*`, capped pytest workers, no install,
and no merge/push/removal. Replace only its new-worktree bootstrap with this
existing-worktree handshake; do not run its original spawn-HEAD equality test.
Preserve any existing `.harness-batch-bootstrap` marker exactly as supplied;
never stage, replace or remove it, and do not rerun new-worktree `bootstrap`.
Only the validated marker is exempt from source cleanliness, including during
review/QA; all implementation must be committed. Keep it even for unfinished
or zero-source results until the coordinator's managed finish disposes of W.

## Bootstrap and binding

The coordinator provides the request, scope/off-limits, worker cap, exact
registered W and branch, expected task id and existing run id (or explicitly
no task yet), and the successful `batch_state.py resume` reservation handoff.
For S1 this includes the original selected-module manifest, private-store
identities and named branches; preserve those exact stores and any unfinished
edits. Never clone, initialize or allocate a replacement module checkout.
A pre-task blocked/failed request may have no task yet. An interrupted running
request can also be reserved again after the coordinator confirms every writer
stopped and pins the observed task run. Both require the explicit stopped-writer
resume reservation; never replace an unaccounted running binding. A handoff that
states task absence must still find no task at bind; a newly appeared task is
an identity mismatch, not permission to adopt it.

1. Resolve W and enter that exact directory before task tools or nested agents.
   Check Git registration, current branch and handoff identity, including S1
   module metadata and named branches. Dirty module edits may belong to the
   interrupted task; preserve them rather than resetting for bootstrap. A missing or
   different checkout, branch, task or run is a blocker, never a reason to
   initialize another checkout or task generation. Read-only inspection is
   permitted before binding; source and task mutations are not.
2. Return `{"worktree":"<W>","branch":"<branch>","head":"<sha>","bootstrap":true}`
   and stop. The coordinator binds your actual host worker identity to this
   reservation using `batch_state.py bind` before granting permission.
3. Continue only on the coordinator's `SendMessage` to this exact bound agent ID
   with successful binding and explicit mutation permission. Recheck the
   existing identity and confirmed prepared-module handoff, then enter W; never use the coordinator's main cwd for
   task calls. If the host cannot deliver this handoff, stay stopped and report
   the runtime blocker. No implicit permission from the original request.

## Existing task and evidence

For an existing open/blocked task, call
`wbs(action="open", workspace=W, wbs_id=existing_id)` without `fresh_run`, then
`wbs(action="context", workspace=W, wbs_id=existing_id)`. Require the same run id as
recorded before continuing the normal lifecycle at its pending phase. Preserve
receipts and evidence; never restart solely to manufacture a PASS. If no task
existed at bootstrap, start only the expected task slug in W after binding.
A closed task goes back to the coordinator for integration/recovery: do not
repeat close or review/QA solely for cleanup.

After normal required review/QA and `wbs(action="close")` PASS, commit as defined in
`task-lead.md` and return its exact lifecycle JSON result. If blocked, retain
all source/evidence and return the concrete reason. Never clean up your own
worktree, force-delete a branch, or claim abandoned work is complete.
