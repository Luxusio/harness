---
tags: [harness, lifecycle, task_blocked, turn-end, contracts]
summary: task_blocked 와 BLOCKED.md 는 미완료 태스크의 durable park 기록으로 유지한다. park 는 종결이 아니며 close 는 여전히 C-04 PASS 만 인정한다. "PASS 만 턴을 끝낸다" 방향(2026-09-02)은 Stop 훅 제거(2026-09-23)로 소멸했고 복원하지 않는다.
updated: 2026-09-25
freshness: suspect
invalidated_by_paths:
  - plugin/mcp/harness_server.py
  - plugin/hooks/hooks.json
  - CONTRACTS.md
  - plugin/skills/setup/templates/CONTRACTS.md
freshness_updated: 2026-10-19T06:06:13Z
---

# REQ — task_blocked is the park record, not a turn-end escape

## Requirement

- The `task_blocked` MCP tool and the `BLOCKED.md` it writes stay in the
  runtime. They are the durable record of *why* an open task stopped and *what*
  unblocks it.
- Parking is not completion. A parked task grants no PASS, does not satisfy or
  bypass C-04, and does not close the task. It stays in `blocked` status,
  which `task_close` refuses, until plain `task_start` resumes it with the run
  and its review/QA receipts preserved.
- Close stays PASS-only: `task_close` requires receipt-backed
  `runtime_verdict: PASS` (C-04, C-14).
- No Claude `Stop` hook gates turn-end. A turn may end while a task is open;
  continuation across turns is native `/goal` with the close condition in the
  goal condition (C-17, `doc/harness/patterns/auto-loop.md`).

The normative text is the C-17 **Parking clause** in `CONTRACTS.md` and the
setup template. This note records how the decision was reached.

## History

1. **2026-09-02.** The user directed "턴 종료 정당 사유: PASS 만" — only a PASS
   runtime verdict may end a turn on an open task. At that point the Claude Stop
   hook (`stop_gate.py`) blocked turn-end on open tasks, and the stop-judge →
   `task_blocked` path was the only non-PASS way out. In the session that
   prompted the direction, receipts could not be recorded at all, so the pause
   path was the only exit and was used. `TASK__remove-blocked-env-pause-path`
   was opened to delete stop-judge, `task_blocked`, `BLOCKED.md`, and the stop
   gate's BLOCKED_ENV turn-end branch.
2. **2026-09-23.** A different task removed the Claude Stop hook registration
   and deleted `stop_gate.py`, because the hook produced repeated empty turns
   while the coordinator waited on background reviewers. The stop-judge agent
   files were already gone.
3. **2026-09-25.** With no turn-end gate left, `task_blocked` no longer lets a
   turn escape anything. Its only remaining effect is recording the unfinished
   state. The user confirmed the Stop hook removal was intended and decided to
   keep `task_blocked` / `BLOCKED.md` and align the docs only. The 2026-09-02
   PASS-only-turn-end direction is withdrawn, not reinstated.

## Accepted cost

A turn can end on an open task without PASS and without a park record. That is
the same state as before this decision, because the Stop hook is already gone.
Nothing can close such a task except C-04 PASS, so the risk is an abandoned open
task, not a false completion. `/goal` is the intended way to keep working until
close.

## Verification

- `tests/test_task_blocked_is_retained.py` checks five things:
  - the C-17 Parking clause is in both CONTRACTS copies;
  - the template copy of the clause carries no dates or repo doc paths;
  - `task_blocked` is registered in `plugin/mcp/harness_server.py`;
  - the `plugin/CLAUDE.md` §4a park line is present;
  - the stop-gate sentences that read as live are gone.
- `tests/test_plan_single_reviewer_and_no_stop_hook.py` pins that no Claude
  `Stop` hook is registered and that `stop_gate.py` stays deleted.
