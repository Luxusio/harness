# REQ - Subagent Receipt Session Binding

## Intent
A lens subagent that runs to completion under an open harness task must always produce ordered `started`/`completed` entries in that task's `RECEIPTS.jsonl`, regardless of whether the MCP host process receives the runtime session id. Receipts are the only evidence `task_verify` accepts, so a binding failure is indistinguishable from "no verification was performed" and blocks `task_close` permanently. This REQ captures the expected normal behavior surfaced by the 2026-08-12 receipt outage: Claude Code passes no session id into the MCP server environment, so `current_session_id()` resolved to `default` and `task_start` wrote `.active_sessions/default.json`, while `SubagentStart`/`SubagentStop` hooks resolved the real session id and read only `<sid>.json`. The two never met, `resolve_session_task_binding` returned `{}`, and every receipt was silently dropped for 12 days.

## Observable Behavior
- After `task_start` binds a task in a Claude session, `doc/harness/tasks/.active_sessions/` contains a marker whose filename is the real runtime session id, not `default.json`, and whose `run_id` field equals the `run_id` in that task's `TASK.json`.
- Spawning any lens subagent (for example `harness:code-reviewer` or `harness:qa-cli`) appends a `started` entry to `RECEIPTS.jsonl` at `SubagentStart`, and a `completed` entry carrying the parsed verdict at `SubagentStop`.
- `task_context` reports the resulting counts in `subagent_receipts.count` / `review_receipts.count`, and `task_verify` derives review and runtime verdicts from those entries.
- On Codex, successful `task_start` and `task_context` PostToolUse payloads combine the exact root `session_id` with the returned `task_dir`, `task_id`, and `run_id`. Only after those values match the canonical open `TASK.json` does the hook publish `.active_sessions/<session-id>.json` and register the root rollout.
- Codex MCP processes do not treat `.session-hint`, `default.json`, or legacy `.active` as current-session authority. When the MCP host lacks an exact thread environment, watcher readiness is unknown until a session hook can establish or disprove it.
- A hint value that is empty, literally `default`, or that does not survive `sanitize_session_id` unchanged is rejected and never becomes a marker filename.
- Harness tooling that runs **outside a hook** may still use the hint as a compatibility fallback for non-lifecycle operations. A genuine id from hook input or `HARNESS_SESSION_ID` wins. This narrower fallback does not grant receipt authority and is deliberately different from the MCP watcher surfaces, which never infer their caller from the repository-global hint.
  - The concrete case is `plugin/scripts/install_verified.py`, which `plugin/skills/develop/SKILL.md` Phase 7.8 requires before `task_close` in this repo. Without hint resolution it refused every invocation with `task is not the open active TASK.json generation`, making a contract-mandated pre-close step unrunnable unless the operator knew to prefix `HARNESS_SESSION_ID=<sid>` by hand (observed 2026-08-25, fixed 2026-08-26).

## Acceptance Signals
- Exact-session isolation is preserved: a subagent whose session id does not match the marker records nothing. Promoting or accepting a `default` marker for an arbitrary session is not an acceptable fix, because a concurrent session's subagents would be attributed to this task.
- A subagent that runs but produces no receipt leaves a diagnosable trace in `doc/harness/learnings.jsonl` whenever a receipt was actually owed — a matching `started` receipt exists for the run, or the payload names an agent type. Agent classes that never write a subagent transcript and never record a start owe no completion; logging them buried the real failures under noise on 2026-08-25. See `doc/harness/REQ__subagent-completion-receipt-transcript-shape.md` for the narrowed rule and its rationale. Silent `{}` returns from `register_subagent_start` / `mark_subagent_stop` are what made the original outage untraceable; the lifecycle must stay fail-safe but must not stay invisible where a receipt was expected.
- No verdict is inferred, forged, or defaulted to compensate for a missing receipt. A binding failure surfaces as a blocked close, never as a synthesized PASS.

## Verification Cues
- `tests/test_codex_hook_wrappers.py` covers exact task-result binding, default-marker non-promotion, foreign-session isolation, and fail-open PostToolUse behavior.
- `tests/test_codex_lifecycle_watcher.py` covers delayed replay from a pre-spawn checkpoint, including a child that completed before the watcher attached.
- `tests/test_install_verified.py` covers out-of-band resolution: a real id wins over the hint, the hint is used when the id is `default`, an unusable hint leaves `default`, and the resolved id reaches `active_task_binding_matches`.
- Manual: start a fresh session, run `task_start`, confirm `.active_sessions/<real-sid>.json` exists with a `run_id` key, spawn `harness:code-reviewer`, then confirm `RECEIPTS.jsonl` gains `started` followed by `completed`, and that `task_context` reflects the count.
- Regression signature to watch for: a task directory containing only a 0-byte `.receipts.lock` after subagents have run means binding failed again.

## Non-Goals
- This REQ does not require the MCP host itself to learn the session id. Codex currently joins exact hook identity to the successful structured MCP result in PostToolUse; an environment-supplied exact MCP identity would also satisfy the contract.
- It does not change what a verdict means or how `normalize_receipt_completion` grades one. It governs only whether receipts get recorded at all.
- It does not promise receipts for subagents spawned outside an open task, nor for sessions that never bound a task.
- It does not cover marker schema migration. Markers written by an older in-memory MCP build (`task_run_id` / `run_started_at` instead of `run_id` / `updated`) are a stale-process artifact resolved by reinstall plus a session restart, not a schema the current code accepts.

## Source
- created: 2026-08-24
- source: C-100 (CONTRACTS.local.md): bug report -> REQ doc for expected normal behavior. Bug: total `RECEIPTS.jsonl` non-recording from 2026-08-12 (commit 18a8023 introduced `resolve_session_task_binding`, which reads only the exact `<sid>.json` and rejects `default`). Reproduced live 2026-08-24: `task_start` produced `.active_sessions/default.json` with `"session_id": "default"` and a 0-byte `.receipts.lock`.
