---
freshness: current
status: accepted
owner: harness
---

# Native Goals

Harness treats native `/goal` as the explicit-goal orchestration entry point.
Users do not choose orchestration modes; Goal owns explicit broad requests and
the harness state decides whether one child task is enough or whether the
child-task queue must grow. Plain repo-mutating requests do not need to be
re-issued as `/goal`; the agent may open or resume a harness task directly when
the canonical loop is needed.

## Model

A Goal is a durable container stored under `doc/harness/goals/`. It has one
objective and a list of child harness tasks. A focused request can stay as one
child task. A broad request can append more child tasks as bugs, pages, domains,
or follow-up slices are discovered.

The Goal state and child tasks are the durable source of truth. The optional
Goal queue runner is an implementation helper for long-running child-task
queues; it is not a separate user-facing mode.

When no native Goal is active, task state is still valid for plain requests.
Those tasks follow the same plan, develop, verify, and close gates, but are not
attached to a Goal unless the user or agent later establishes one.

## Runtime Split

Claude exposes `/goal ...` text through `UserPromptSubmit`, so hooks can sync a
harness Goal automatically when the user invokes `/goal` or `/골`.

Codex native goal objective should be read by the agent from native goal
context/tooling, then synced with `goal_start`. Codex hook payloads are not the
authoritative objective source, but hooks still inject the short runtime
procedure that tells the agent to run `get_goal -> goal_start`, inspect
`goal_context`, create/attach a child task when needed, continue via
`goal_next_task`, and call `goal_finish` only after child tasks prove the
objective.

## Task Relationship

Harness tasks remain the execution unit for plan, develop, verify, and close.
Goal tools attach those tasks as children. This keeps existing task artifacts
and close gates intact while making Goal the public control surface.

## Existing Repo Migration

Repair/Upgrade setup runs `plugin/scripts/goal_queue_migrate.py`. The script is
idempotent and handles the two pre-native artifacts that can remain in user
repositories:

- `doc/harness/autopilot.yaml` is converted to `doc/harness/goal-queue.json`.
  Legacy `TASK__autopilot-*` child IDs are preserved when the corresponding
  `doc/harness/tasks/<task_id>/` directory exists, so migrated state does not
  point at missing task artifacts. Missing task-dir references are rewritten to
  `TASK__goal-queue-*`. Migration metadata records which policy was used, and
  the old state file is archived under `doc/harness/legacy/`.
- A marked `## Harness routing` block in `CLAUDE.md` is replaced with the
  current Goal-or-direct-task routing block. Stale `Default agent is harness`
  lines are removed.
