# 2026-05-31 — SessionStart REQ reminder + write_req_doc task-optional + install drift warn

tags: [harness, session-start, req-capture, drift-detection, contracts]
freshness: current
task: TASK__session-start-req-reminder-and-drift-warn

## What changed (operator-visible)

Every Claude Code (and Codex) session that opens the harness now prints one extra banner line at start:

```
REQ: capture requirements as doc/<area>/REQ__<slug>.md (write_req_doc).
```

That line is the only nudge harness gives to convert user-stated requirements into durable `REQ__*.md` files. There is no keyword detection, no UserPromptSubmit heuristic, no auto-classification. Always-on by design.

For developers of the harness itself (anyone with this repo checked out alongside an installed plugin under `~/.claude/harness-dev/`), each session start also runs a small SHA256 manifest diff between source `plugin/scripts/*.py` and the installed copies. When they drift apart, one extra line appears:

```
[drift] installed plugin behind source (N files differ) — run `python3 install.py --force`
```

Silent when in sync, silent in non-harness repos, silent when no install is detected.

## What changed under the hood

- `write_req_doc` MCP tool no longer requires `task_id`. Calls without it land in the same REQ file format, with `source` set to `adhoc:<ISO8601>` and an empty `task_dir` in the response. Existing task-scoped behavior is preserved bit-for-bit. JSON schema updated accordingly.
- `prewrite_gate.py` was confirmed (no code change) to allow `doc/<area>/REQ__<slug>.md` writes outside an active task — the gate's `SOURCE_EXTENSIONS` covers `.py/.ts/...` only, not `.md`.
- New `plugin/scripts/drift_warn.py` (90 lines, stdlib-only) does the SHA256 comparison above. Per-file `try/except` so a single unreadable file does not abort the loop; outer `try/except` so any unexpected failure is a silent no-op.
- New `CONTRACTS.local.md` C-100: "Bug report → REQ doc capturing expected normal behavior". Captures the user's mid-task standing rule that every reported bug must come with an expected-behavior REQ.
- Two REQ docs now anchor this task's behavior contracts:
  - `doc/harness/REQ__session-start-hooks-no-op-outside-harness.md` — every harness hook stays silent in non-harness-enabled repos. Pins the contract that the 2026-05-27 `0c5dd7b` commit established.
  - `doc/harness/REQ__req-capture-with-or-without-task.md` — the REQ-capture surface (banner + MCP + gate + CLI) works with or without an active task.

## Why this exists

Two friction classes drove the change, both surfaced in `doc/harness/OBS__design-planning-harness-friction.md` (Implementation-track friction section, 2026-05-31):

1. The user kept forgetting to write REQ docs when not invoking `harness:run`. Keyword/contains detection was explicitly rejected as "too hard-coded"; a standing reminder is the lightest honest fix.

2. On 2026-05-27 a commit landed a no-op-guard fix for hook scripts so non-harness repos would not get `learnings.jsonl` / `background.json` / `.lock` files written into them. The fix was correct but did not propagate: the user's installed plugin lagged source by six days, and the pollution continued in non-harness repos until the user happened to check git status. drift_warn closes that loophole on the next session start.

## Tests

15 new tests across 4 files; full suite 714 passed, 0 failed.

## Migration

None. Existing `write_req_doc` calls with `task_id` keep their exact prior behavior. After `install.py --force` runs on next session, the loosened MCP schema activates.

## Follow-ups

Tracked in OBS retrospective Implementation-track section, deferred to their own tasks:

- `doc/harness/runtime/*` paths should be in `.gitignore` (template + migration).
- `plugin/skills/plan` should emit MAINTENANCE marker when target files intersect prewrite_gate's WORKFLOW_CONTROL_SURFACE.
- Banner line-count meta-rule (when do reminders become wallpaper?).
- Lightweight `size: trivial` track for one-line config / cache changes.
- task_close auto-stage of durable harness outputs.
- Standing-constraint auto-capture detector (C-100 starts as convention).
- SLOP detector context awareness.
