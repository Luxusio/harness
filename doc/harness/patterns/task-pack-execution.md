---
tags: [harness, task-pack, orchestration, continuation]
summary: Ordered task packs let harness queue known roadmap tasks up front and continue without asking users to choose internal work sequence.
freshness: current
updated: 2026-05-29
---

# Task Pack Execution

Task packs are the default harness shape for user requests that already contain
multiple sequential stages, roadmap items, or follow-up tasks. The user owns the
goal and any real product decisions. Harness owns task decomposition, ordering,
queue state, and next-task selection.

## Behavior

When the known stages can be named, create `doc/harness/task-packs/current.json`
with:

```bash
python3 plugin/scripts/task_pack_runner.py init \
  --goal "Toss redesign" \
  --task "stage-4:Admin density spec and application" \
  --task "stage-5:Admin verification and cleanup"
```

Use declared roadmap order first. If the user gives no order, use dependency
order, then highest-risk/highest-value order. Report that order as execution
context.

Before starting the next task:

```bash
python3 plugin/scripts/task_pack_runner.py next
python3 plugin/scripts/task_pack_runner.py claim-next
```

After closing a task:

```bash
python3 plugin/scripts/task_pack_runner.py close --task stage-4
```

If the close command prints another `next:` item, start or queue that task. Do
not ask the user which task to do next.

## Ask Boundary

Ask for user input only when the next step crosses a user-owned boundary:

- go/no-go at an agreed batch boundary,
- product, architecture, billing, auth, data, privacy, or destructive-operation
  decisions,
- environment or credential blockers,
- contradictions where continuing would likely implement the wrong intent.

Do not ask for internal execution mechanics: whether to split a task, combine
tasks, do only a subset, defer a known stage, or choose the next stage from an
already ordered roadmap.

## Prompt Hook

`prompt_memory.py` emits `[harness-task-pack]` while
`doc/harness/task-packs/current.json` is active. This reminder prevents task
close from being treated as completion of the user's overall request.

## Related Requirement

- `doc/common/REQ__process__autonomous-task-pack-execution.md`
