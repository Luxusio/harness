# REQ - Subagent Receipt Session Binding

## Intent
A lens subagent that runs to completion under an open harness task must always produce ordered `started`/`completed` entries in that task's `RECEIPTS.jsonl`, regardless of whether the MCP host process receives the runtime session id. Receipts are the only evidence `task_verify` accepts, so a binding failure is indistinguishable from "no verification was performed" and blocks `task_close` permanently. This REQ captures the expected normal behavior surfaced by the 2026-08-12 receipt outage: Claude Code passes no session id into the MCP server environment, so `current_session_id()` resolved to `default` and `task_start` wrote `.active_sessions/default.json`, while `SubagentStart`/`SubagentStop` hooks resolved the real session id and read only `<sid>.json`. The two never met, `resolve_session_task_binding` returned `{}`, and every receipt was silently dropped for 12 days.

## Observable Behavior
- After `task_start` binds a task in a Claude session, `doc/harness/tasks/.active_sessions/` contains a marker whose filename is the real runtime session id, not `default.json`, and whose `run_id` field equals the `run_id` in that task's `TASK.json`.
- Spawning any lens subagent (for example `harness:code-reviewer` or `harness:qa-cli`) appends a `started` entry to `RECEIPTS.jsonl` at `SubagentStart`, and a `completed` entry carrying the parsed verdict at `SubagentStop`.
- `task_context` reports the resulting counts in `subagent_receipts.count` / `review_receipts.count`, and `task_verify` derives review and runtime verdicts from those entries.
- The marker filename is supplied by a session hint recorded by a hook that receives the real session id; `plugin/scripts/_lib.py::read_session_hint` returns it and `task_start` passes it to `write_active_marker` as an explicit `session_id`.
- When no usable hint exists (Codex, hookless installs, first turn before any prompt hook fired), the marker falls back to `default.json`. This is the documented degraded mode, not an error — Codex binds its own marker explicitly via the root thread id.
- A hint value that is empty, literally `default`, or that does not survive `sanitize_session_id` unchanged is rejected and never becomes a marker filename.

## Acceptance Signals
- Exact-session isolation is preserved: a subagent whose session id does not match the marker records nothing. Promoting or accepting a `default` marker for an arbitrary session is not an acceptable fix, because a concurrent session's subagents would be attributed to this task.
- A subagent that runs but produces no receipt leaves a diagnosable trace in `doc/harness/learnings.jsonl` whenever a receipt was actually owed — a matching `started` receipt exists for the run, or the payload names an agent type. Agent classes that never write a subagent transcript and never record a start owe no completion; logging them buried the real failures under noise on 2026-08-25. See `doc/harness/REQ__subagent-completion-receipt-transcript-shape.md` for the narrowed rule and its rationale. Silent `{}` returns from `register_subagent_start` / `mark_subagent_stop` are what made the original outage untraceable; the lifecycle must stay fail-safe but must not stay invisible where a receipt was expected.
- No verdict is inferred, forged, or defaulted to compensate for a missing receipt. A binding failure surfaces as a blocked close, never as a synthesized PASS.

## Verification Cues
- `tests/test_session_hint_marker_binding.py` covers hint validation, the round trip, `task_start` binding the marker to the hinted session, foreign-session isolation, and the no-hint fallback.
- Manual: start a fresh session, run `task_start`, confirm `.active_sessions/<real-sid>.json` exists with a `run_id` key, spawn `harness:code-reviewer`, then confirm `RECEIPTS.jsonl` gains `started` followed by `completed`, and that `task_context` reflects the count.
- Regression signature to watch for: a task directory containing only a 0-byte `.receipts.lock` after subagents have run means binding failed again.

## Non-Goals
- This REQ does not require the MCP host to learn the session id by any particular mechanism; the hint file is the current implementation, and an environment-supplied session id would satisfy the same contract.
- It does not change what a verdict means or how `normalize_receipt_completion` grades one. It governs only whether receipts get recorded at all.
- It does not promise receipts for subagents spawned outside an open task, nor for sessions that never bound a task.
- It does not cover marker schema migration. Markers written by an older in-memory MCP build (`task_run_id` / `run_started_at` instead of `run_id` / `updated`) are a stale-process artifact resolved by reinstall plus a session restart, not a schema the current code accepts.

## Source
- created: 2026-08-24
- source: C-100 (CONTRACTS.local.md): bug report -> REQ doc for expected normal behavior. Bug: total `RECEIPTS.jsonl` non-recording from 2026-08-12 (commit 18a8023 introduced `resolve_session_task_binding`, which reads only the exact `<sid>.json` and rejects `default`). Reproduced live 2026-08-24: `task_start` produced `.active_sessions/default.json` with `"session_id": "default"` and a 0-byte `.receipts.lock`.
