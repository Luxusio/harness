# REQ process receipt watcher fail closed

summary: Receipt watcher readiness must be proven before verification agents are spawned, and registration failure must surface at task start rather than at close
status: partially-implemented
updated: 2026-08-26
freshness: current
confidence: high
kind: process
source: User directive 2026-08-26 after a Codex session ran 3 review agents plus a 1,559-test QA pass whose results were never recorded as receipts.

Receipt-backed verification is fail-closed by design (C-14, C-04). The defect is
not the gate — it is that registration failure is discovered only *after* all
verification has been paid for.

## Observed incident

A Codex session was not registered with the harness watcher.

- 3 review agents each returned `VERDICT: PASS`
- QA passed the full 1,559-test suite
- `RECEIPTS.jsonl` was never created, so `task_verify` stayed `PENDING`
- both watcher registration directories were empty
- Codex 0.149.1 — not a stale-version problem
- hook configuration itself was installed
- the registration function's return value is not checked by its caller
- the watcher start exception is swallowed without a message at
  `harness_server.py:983` (installed path
  `~/.codex/harness/plugins/harness/mcp/harness_server.py`)

The resulting asymmetry:

```
watcher registration: best-effort, hides failure
        -> review/QA: run normally, consuming time and money
                -> task_verify: fail-closed, no receipts
```

The same failure class is reachable on the Claude side: a loaded plugin tree
missing `SubagentStart`/`SubagentStop` produces subagent verdicts that are never
recorded. `plugin/scripts/hook_tree_health.py` already announces that case at
session start; Codex has no equivalent pre-check.

## Requirements

1. **Force a watcher status check before review starts.** Immediately before
   `collaboration.spawn_agent`, verify: watcher manager running, current root
   thread registered, active task/run linkage present, receipt file writable.
   `hook_pre_tool_use.py:63` originally attempted registration and ignored the
   result; it must now consume and report it.

   **Superseded on 2026-08-26 — the check must not block the spawn.** The
   original wording said "on failure, do not spawn the agents." The user reversed
   that: a watcher error must never stop an agent from running. Surfacing the
   condition is the requirement; gating execution on it is not. The hook reports
   and exits 0, and the spawn proceeds.
2. **Expose the registration failure cause.** `harness_server.py:983` swallows
   all exceptions. These must be queryable via `task_context` or a dedicated
   `watcher_status` tool: `manager_running`, `registration_present`,
   `root_thread_id`, `active_task_id`, `active_run_id`, `rollout_offset`,
   `last_registration_error`, `last_watcher_error`.
3. **Warn early at `task_start`.** When the watcher is not ready, the harness
   must not instruct the agent to spawn review subagents, and must say so with
   explicit recovery text: "현재 세션에는 receipt watcher가 등록되지 않았습니다.
   새 Codex 세션을 시작한 뒤 task를 resume하십시오."

   **Amended on 2026-08-26 — warn, do not refuse.** The original wording required
   an immediate `BLOCKED_ENV`. The user rejected that: the task is still created
   or resumed, and only the spawn instruction is withheld.
4. **Receipt handshake per verification stage.** reviewer spawn confirmed ->
   reviewer completion recognized -> only then QA is allowed. If the review
   receipt failed to record, an 18-minute full QA run must not start.
5. **Best-effort registration may remain, but failure must propagate.**
   `codex_hook_registration.py:157` retries within a short deadline and returns
   `False`. The hook must consume that return value and reflect it to the user
   and to harness task state.
6. **Do not silently change `run_id` on an ordinary resume.** A repeat
   `task_start` changed the run id in this incident. A new run invalidates
   existing receipts, so it must warn: "새 evidence run이 생성되었습니다. 이전
   review/QA 결과는 사용할 수 없으며 모두 다시 실행해야 합니다."

## Explicit non-goals

- **Never synthesize receipts retroactively** by scanning past conversation.
  That destroys attestation trust.
- Manual receipt authoring and guard bypass are not acceptable recoveries.
- The correct posture is: prepare the watcher before work, abort immediately on
  failure.

## Recovery for an already-stuck task

Open the repo in a new session/thread, resume the task, confirm watcher
registration, and re-run review and QA. Do not backfill receipts from the
previous PASS results.

## Implementation status

| Requirement | Status | Where |
|---|---|---|
| 1 — check before spawn | done, as decided | `hook_pre_tool_use.py` checks and reports; it deliberately exits 0 so the spawn proceeds. `harness_server._gate_next_action` stops the harness from *instructing* a further spawn it cannot attest. Blocking the spawn is explicitly rejected — see Settled decisions. |
| 2 — expose failure cause | done | `harness_server._watcher_status`, returned by `task_start` and `task_context`; `_start_codex_watchers` records `last_watcher_error` instead of swallowing it |
| 3 — warn at task_start | done, as decided | `task_start` warns `RECEIPT_HOOKS_UNAVAILABLE` and suppresses the spawn instruction, and still creates or resumes the task. Refusing outright is explicitly rejected — see Settled decisions. |
| 4 — per-stage receipt handshake | not implemented | needs the Codex collaboration surface and a live Codex session to verify |
| 5 — propagate registration failure | done | `hook_pre_tool_use.py` consumes the `restore_watcher_registration` result, records it, and writes a user-visible stderr line; still exits 0 per C-12 |
| 6 — run_id change warning | done | `task_start` emits `EVIDENCE_RUN_SUPERSEDED` naming both run ids |
| 7 — no retroactive receipts | held | enforced by `tests/test_receipt_watcher_fail_closed.py::TestNoReceiptSynthesis` |

`watcher_status` reports fields it cannot determine as `null` rather than
guessing. A fabricated "ready" would be worse than an admitted unknown. No field
in it can authorize a PASS; the close gate still reads only hook-owned
`RECEIPTS.jsonl` entries.

### Readiness must be evaluated per runtime

`receipt_capability_warning()` resolves the plugin registration from
`~/.claude/plugins/installed_plugins.json`. That is a **Claude-only** signal: on a
Codex session it finds nothing to indict and returns `""`. The originating
incident was on Codex, so readiness keyed on that warning alone would have stayed
silent through three review agents and a 1,559-test QA pass — the exact failure
this REQ exists to prevent.

`receipts_recordable` is therefore false when **any** of these hold, and
`receipts_unrecordable_reason` names which one fired:

| Signal | Runtime | Source |
|---|---|---|
| `receipt_capability_warning` non-empty | Claude | hook tree missing SubagentStart/SubagentStop |
| `registration_present is False` | Codex | `hook_pre_tool_use.py` recorded a failed `restore_watcher_registration` |
| `last_registration_error` non-empty | Codex | same |
| `last_watcher_error` non-empty | Codex | `_start_codex_watchers` caught a start failure |

Two fields, `root_thread_id` and `rollout_offset`, are declared but no writer
populates them yet; they always report `null`. They stay listed because
requirement 2 names them, and an always-null field is honest where a guess would
not be.

## Settled decisions

**A watcher error never blocks an agent.** Decided by the user on 2026-08-26.
`hook_pre_tool_use.py` must report a failed registration and exit 0; it must not
deny `collaboration.spawn_agent`. Rationale: attestation is a recording concern,
and losing the ability to record is not a reason to stop the work from running.
Degrading to "you got the result but no receipt" is strictly better than "you got
neither." This also keeps the hook aligned with C-12 fail-safe behavior.

Consequence, accepted knowingly: agents spawned in the same message as the
failing one still run unattested, so a multi-lens batch can complete with no
receipts. The recovery is to repair receipt capability and re-run the lenses —
never to hand-author receipts.

**`task_start` warns and stays open.** Decided by the user on 2026-08-26.
Requirement 3's original wording asked for an immediate `BLOCKED_ENV` when the
watcher is unready. That is superseded: `task_start` emits the
`RECEIPT_HOOKS_UNAVAILABLE` warning, suppresses the instruction to spawn
verification agents, and still creates or resumes the task. Rationale is the same
as above and confirmed in practice — the session that produced this REQ planned
and implemented five tasks under the advisory warning; refusing at the door would
have prevented all of that work for a condition that only affects recording.

Both decisions reduce to one rule: **warn, do not obstruct.** Receipt capability
is reported honestly at every surface, and nothing about its absence stops
planning, implementation, or agent execution. What absence does stop is `close` —
`task_verify` still requires ordered hook-owned PASS receipts, and that gate is
unchanged.

## Verification

- `tests/test_receipt_watcher_fail_closed.py` — a raising watcher start records
  the cause without raising; `watcher_status` keeps undeterminable fields null;
  a failed registration is recorded while the hook still exits 0; the spawn
  instruction is replaced when receipts are unrecordable and preserved when they
  are not; no path writes a receipt.
- Not covered locally: the Codex `restore_watcher_registration` path against a
  real rollout, which needs a live Codex session.
