---
name: batch
description: Execute declared independent requests through the shared batch pool with Codex worktree coordinators.
user-invocable: false
---

# Codex batch

Read the shared batch procedure at
`${HARNESS_PLUGIN_ROOT}/internal-skills/batch/shared-procedure.md` once. Its
preflight, reservation, S1 selected-module preparation, result validation,
serial finish, recovery and retention rules apply. This file replaces only
Claude's native isolation/spawn and continuation calls. Shared preflight b.2's
Claude settings/baseRef requirement does not apply: explicit worktree creation
below pins the exact spawn HEAD. Do not execute Claude
`Agent`, `SendMessage` or `Skill` examples on Codex.

For Goal dispatch use the exact returned batch ID and request list; existing
state is resumed, never replaced with another ID. The canonical Goal child is
the later integration task, not a lead. No main-checkout task may be open
while leads run. Independent declared requests automatically use this route
when the host exposes spawn and same-agent continuation capabilities.

For a WBS-owned pack, pass its returned task_id as `--owner-task-id <task_id>`
when initializing the exact batch. This immutable pool owner must match the
parent binding. Standalone batches may omit it. Preserve it on every retry;
never attach a different root to an initialized pool.

## Capacity and capability

Use the exposed `spawn_agent`, same-agent continuation (`followup_task` or the
host equivalent), and live-agent inventory. Count the coordinator, running
leads, nested workers and unreleased finished agents. Leave at least one slot
for lead review/QA; cap lead admission to the smaller of `batch.max_leads` and
free slots minus that verification reserve. Pass the nonnegative result as
`claim --available-slots N`. Assign nested workers capacity from that same
inventory, never a new per-lead host limit. At a host capacity too small for
one coordinator, one lead and an independent reviewer, report the capability
blocker before reservations rather than creating an unfinishable lead.

The shared MCP and receipt watcher must support authenticated worktree task
bindings. An old installed runtime rejecting workspace, missing native
ancestry, or unavailable independent evidence is a concrete blocker. Do not
drop `workspace` or manufacture receipts to get around it. If no pool has been
declared yet, dependent or unavailable work uses the ordinary sequential task
route. Once a Goal work pack is declared, preserve its intent and report its
specific capability blocker instead of silently marking the pack complete.

## Bootstrap and bind

1. Write the exact requests to an ignored JSON file and run shared `init` and
   preflight. On every refill, `claim` reserves capacity before spawning.
2. For each claim, create a unique branch and registered worktree under
   `<main>/.claude/worktrees/` at the returned `spawn_head`, using plain
   `git worktree add -b <branch> <W> <spawn_head>`. Never change process-wide
   cwd: all shell calls pass an explicit working directory or `git -C`.
   Recheck the returned branch/HEAD and the worktree registration.
3. Spawn a bootstrap-only agent with a unique name; prompt it to read
   `${HARNESS_PLUGIN_ROOT}/agents/task-lead.md`, name its exact W/branch,
   batch ID, slug, exact claim `task_id`, scope, off-limits paths, selected modules and spawn HEAD.
   Bootstrap writes only the shared helper's retention marker and returns.
   Record the actual native worker identity from the host result.
4. Run shared `bind` with that identity, W and branch. S1 preparation belongs
   only to bind. Continue the same stopped agent only after successful binding,
   with explicit mutation permission and prepared-module handoff. Use
   `followup_task` for an idle agent when that is the exposed continuation tool;
   a message-only operation that does not start a turn is insufficient.
5. A failure before spawn does not prove no external work exists. Reconcile
   reservations with actual created resources, preserve unknown workers and
   worktrees, and use shared release/resume rules. Never blindly re-spawn.

## Collect and refill

Accept only actual host completion from the bound worker, after its nested
writers have stopped. Pass its final JSON to shared `result`, then run `finish`
serially. `finish` owns rebase, fast-forward, evidence harvest and disposal.
Preserve its conflict/error/recovery behavior, including S1 proof. Recompute
host slots and refill immediately while unrelated leads remain live.

After all requests integrate and cleanup succeeds, close the pool. A WBS-owned pack reopens the same WBS root;
standalone batches start the ordinary integration task. Review the full batch
base-to-integrated diff and run combined QA. Leads skip source auto-install;
the integration task owns verified delivery and final completion reporting.
Abandoned, blocked or retained required work remains unfinished.
