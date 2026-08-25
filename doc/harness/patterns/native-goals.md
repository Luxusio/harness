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

The Goal state and child tasks are the sole durable orchestration source of
truth. There is no separate orchestration runner, heartbeat, event log, or
migration state.

When no native Goal is active, task state is still valid for plain requests.
Those tasks follow the same plan, develop, verify, and close gates, but are not
attached to a Goal unless the user or agent later establishes one.

## Runtime Split

UserPromptSubmit hooks are advisory and never write Goal authority. Claude and
Codex read the native objective from the current conversation or Goal tooling,
then sync it through the sole writer, `goal_start`. Hooks inject the short
runtime procedure that tells the agent to run `get_goal -> goal_start`, inspect
`goal_context`, create/attach a child task when needed, continue via
`goal_next_task`, and call `goal_finish` only after child tasks prove the
objective.

## Task Relationship

Harness tasks remain the execution unit for plan, develop, verify, and close.
Goal tools attach those tasks as children. This keeps existing task artifacts
and close gates intact while making Goal the public control surface.

## Ordered Children

For a request with multiple known stages, add children in the user's declared
roadmap order. If none is declared, use dependency order and then
highest-risk/highest-value order. A queued child may be recorded before its task
directory exists; `task_start` materializes it when selected.

`goal_next_task` returns the first queued or active child. Selecting, splitting,
and ordering these children are internal workflow decisions, so report the next
child and its reason as status. Ask only for a real product, architecture,
billing, auth, data, privacy, destructive-operation, environment, credential,
contradiction, or agreed go/no-go decision.

Each child should be the smallest thin vertical workflow that can produce
user-visible evidence. After it closes, compare the result with the Goal and
choose the next child by user value, risk, or learning. `goal_finish` remains
fail-closed until every child has canonical receipt-verified closed state.

## Learning Before Continuation

The transition order is fixed:

```text
task_close
-> self-improvement and learning promotion
-> goal_next_task
-> goal_finish when the objective is proven
```

This keeps automatic learning active after every child, including long Goals.
Runbook memory, `learnings.jsonl`, `promote_learnings.py`, and search are
independent of orchestration and remain unchanged.

## Removed State

Pre-native orchestration artifacts are unsupported. Setup does not read,
translate, archive, or advertise them; start a native Goal and add the remaining
children explicitly. Conversation history and discarded orchestration JSON are not
durable memory—reusable knowledge belongs in committed docs, patterns, tests,
skills, or approved runbooks through the normal self-improvement pipeline.
