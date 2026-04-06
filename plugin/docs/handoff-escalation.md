# Handoff Escalation Reference

updated: 2026-03-29

This document describes the SESSION_HANDOFF.json mechanism — a structured recovery context generated for long-running or repeatedly failing tasks.

---

## Purpose

Normal successful tasks see zero additional ceremony. SESSION_HANDOFF.json is only generated when a task exhibits signals that indicate a new session or agent will need structured context to continue safely without regressing previously passing work.

It is a compact, machine-readable recovery brief — not a verbose retrospective.

---

## Trigger Conditions

The handoff is generated when ANY of these conditions is met:

### 1. `runtime_fail_repeat`
`runtime_verdict` FAIL count >= 2.

A task that has failed runtime QA twice or more needs structured recovery context so the next attempt knows what was tried, what failed, and what was previously working.

Detection: `runtime_verdict_fail_count` in `TASK_STATE.yaml`, or count of `verdict: FAIL` lines in `CRITIC__runtime.md`.

### 2. `criterion_reopen_repeat`
Any criterion in `CHECKS.yaml` has `reopen_count >= 2`.

A repeatedly reopened acceptance criterion indicates a systemic issue — the fix keeps breaking or the criterion keeps being reopened. Recovery context helps identify the pattern.

Detection: `reopen_count` field in any criterion entry in `CHECKS.yaml`. If `CHECKS.yaml` is absent, this trigger never fires.

### 3. `sprinted_compaction`
`execution_mode == "sprinted"` AND a compaction event occurred.

Sprinted tasks involve multiple surfaces and complex state. After compaction, context is reduced and recovery structure is important to avoid regressing completed work.

Detection: `execution_mode: sprinted` in `TASK_STATE.yaml` + `compaction_just_occurred=True` passed by the post-compact hook.

### 4. `blocked_env_reentry`
Task `status == "blocked_env"` OR `was_blocked_env: true` in `TASK_STATE.yaml`.

A task that was previously blocked by an environment issue has demonstrated environmental complexity. On re-entry, structured context guides the recovery.

Detection: `status: blocked_env` or `was_blocked_env: true` in `TASK_STATE.yaml`.

### 5. `roots_exceeded_estimate`
`roots_touched` grew significantly beyond the original `roots_estimate`.

If the task touched more roots than planned, scope has expanded. Recovery context helps calibrate expectations and may trigger mode escalation to sprinted.

Detection: `len(roots_touched) > roots_estimate + 1` where `roots_estimate` is stored in `TASK_STATE.yaml`.

---

## SESSION_HANDOFF.json Schema

```json
{
  "task_id": "TASK__example",
  "trigger": "runtime_fail_repeat",
  "created_at": "2026-03-29T00:00:00Z",
  "open_check_ids": ["AC-002", "AC-004"],
  "last_fail_evidence_refs": ["CRITIC__runtime.md"],
  "last_known_good_checks": ["AC-001"],
  "next_step": "Reproduce the persistence failure and verify database write completes",
  "roots_in_focus": ["common"],
  "paths_in_focus": ["src/api/users.py", "src/db/connection.py"],
  "do_not_regress": ["login flow remains functional", "existing API endpoints respond"],
  "files_to_read_first": ["PLAN.md", "TEAM_PLAN.md", "CRITIC__runtime.md", "HANDOFF.md"],
  "team_recovery": {
    "status": "running",
    "phase": "worker_summaries",
    "plan_ready": true,
    "synthesis_ready": false,
    "expected_workers": ["worker-a", "worker-b"],
    "summary_workers": ["worker-a", "worker-b"],
    "synthesis_workers": ["lead"],
    "ready_workers": ["worker-a"],
    "pending_workers": ["worker-b"],
    "missing_workers": ["worker-b"],
    "incomplete_workers": [],
    "pending_artifacts": ["team/worker-b.md", "TEAM_SYNTHESIS.md"],
    "worker_summary_artifacts": ["team/worker-a.md"],
    "pending_owned_paths": ["api/**"],
    "worker_summary_errors": [],
    "synthesis_semantic_errors": []
  }
}
```

### Field Definitions

| Field | Type | Description |
|-------|------|-------------|
| `task_id` | string | Task identifier matching `TASK_STATE.yaml` `task_id` |
| `trigger` | string | One of the five trigger condition names |
| `created_at` | ISO 8601 string | UTC timestamp when handoff was written |
| `open_check_ids` | string[] | IDs of criteria not yet PASS in CHECKS.yaml; empty if CHECKS.yaml absent |
| `last_fail_evidence_refs` | string[] | Filenames of CRITIC__*.md files with FAIL verdicts |
| `last_known_good_checks` | string[] | Criteria IDs that were PASS at last check; empty if CHECKS.yaml absent |
| `next_step` | string | Single clear sentence — the most important recovery action |
| `roots_in_focus` | string[] | `roots_touched` from TASK_STATE.yaml — repo roots involved |
| `paths_in_focus` | string[] | Most recently failed file paths (from critic content or touched_paths) |
| `do_not_regress` | string[] | Items that were passing and must remain passing |
| `files_to_read_first` | string[] | Ordered reading list for efficient session recovery |
| `team_recovery` | object | Present only for `orchestration_mode: team`; records which team phase is blocked, which worker artifacts / owned paths are still pending, and which synthesis owner(s) should refresh `TEAM_SYNTHESIS.md` / `HANDOFF.md` |

---

## When It Is Created

| Event | Condition | Action |
|-------|-----------|--------|
| Post-compact hook runs | Any open task matches a trigger (with `sprinted_compaction` most common) | `generate_handoff()` called; SESSION_HANDOFF.json written |
| Session-end hook runs | Any open task matches a trigger; or task already has a handoff | Existing handoff reported; new ones generated if trigger met |
| Harness / developer detects 2nd+ FAIL | runtime_verdict FAIL count reaches 2 | Handoff generated on next hook invocation |

---

## task_context evidence-first promotion

When `SESSION_HANDOFF.json` is present, a task has an active `CRITIC__runtime.md` / `CRITIC__document.md` FAIL, **or runtime verification is blocked by environment/setup issues**, `mcp__plugin_harness_harness__task_context` switches into an evidence-first posture:

1. it promotes the most relevant failing critic artifact into `must_read`
2. it keeps `SESSION_HANDOFF.json` near the front of `must_read` when present
3. on `blocked_env`, it promotes `ENVIRONMENT_SNAPSHOT.md` so recovery starts from actual sandbox facts rather than re-probing the repo
4. it emits a compact `review_focus` block with `trigger`, `critic_artifact`, `supporting_artifact`, and a short `evidence_excerpt`
5. it carries forward `focus_check_ids`, `paths_in_focus`, and `do_not_regress` when available
6. when a similar past failure exists, it also surfaces `prior_similar_task`, `prior_similar_artifact`, and `prior_similar_excerpt` as a single top-1 recovery hint
7. for team tasks, it also surfaces `team_recovery_phase`, `team_pending_workers`, and `team_pending_artifacts` so restart logic can resume the blocked team slice instead of re-scanning the whole repo

This is meant to stop fix rounds from reopening with only summaries while the actual failing evidence stays hidden deeper in the task folder.

For replay / dry-run checks, `handoff_escalation.preview_handoff(task_dir)` now builds the same payload without writing `SESSION_HANDOFF.json`, so golden replay can pin recovery behavior without mutating task fixtures.

---

## How It Is Consumed

### harness (orchestrator)

On session start or task re-entry:
1. Check for `SESSION_HANDOFF.json` in the active task directory.
2. If present: read it FIRST before other artifacts.
3. Use `next_step` as primary recovery directive.
4. Read `files_to_read_first` in order.
5. Pass `do_not_regress` to critic-runtime.
6. Focus on `open_check_ids` and `paths_in_focus`.
7. For team tasks, use `team_recovery.phase` plus `team_recovery.synthesis_workers` to decide whether the next move is `TEAM_PLAN.md`, contributor worker summaries, a synthesis-owner refresh of `TEAM_SYNTHESIS.md`, final runtime verification, or the documentation pass (`DOC_SYNC.md` / `CRITIC__document.md`) before `HANDOFF.md`. When `team_recovery.doc_sync_owners` or `team_recovery.document_critic_owners` are present, route those artifacts to those workers instead of treating the doc phase as anonymous role work.
8. After runtime PASS: leave handoff in place (historical record).

### developer (generator)

When `SESSION_HANDOFF.json` is present:
1. Read it before starting implementation.
2. Focus on `paths_in_focus`.
3. Avoid breaking `do_not_regress` items.
4. After 2nd+ FAIL: populate or update `do_not_regress` with criteria that passed in earlier critic runs.
5. On team tasks, prefer `team_recovery.pending_owned_paths` and the surfaced worker artifacts over broad repo exploration.

### post_compact_sync.py and session_end_sync.py

Both hook scripts:
- Scan all open task directories for trigger conditions.
- Generate SESSION_HANDOFF.json if trigger met and none exists.
- Report handoff presence in summary output with trigger and next_step.

---

## Backward Compatibility

If `CHECKS.yaml` is not present in a task directory:
- `open_check_ids` is always `[]`
- `last_known_good_checks` is always `[]`
- Triggers 1, 3, 4, 5 can still fire (they do not depend on CHECKS.yaml)
- Trigger 2 (`criterion_reopen_repeat`) never fires

All other fields are populated from `TASK_STATE.yaml` and `CRITIC__*.md` files, which are always present in active tasks.

---

## Output in Hook Summaries

When a handoff is created or present, hook scripts print:

```
[HANDOFF] TASK__example has SESSION_HANDOFF.json (trigger: sprinted_compaction)
  Next step: Resume from PLAN.md sprint contract — verify roots_in_focus and check do_not_regress items before continuing implementation.
  Read first: PLAN.md, TASK_STATE.yaml, CRITIC__runtime.md, HANDOFF.md
```

---

## Implementation

- `plugin/scripts/handoff_escalation.py` — `should_create_handoff()` and `generate_handoff()` functions
- `plugin/scripts/post_compact_sync.py` — calls handoff check after task summary
- `plugin/scripts/session_end_sync.py` — calls handoff check before maintain-lite section
- `plugin/agents/harness.md` — reading instructions for session start / task re-entry
- `plugin/agents/developer.md` — reading instructions and do_not_regress population rule


Team recovery now includes a `launch` phase between `dispatch` and `worker_summaries`. Use `team-launch` to auto-refresh stale bootstrap/dispatch artifacts, write `team/bootstrap/provider/launch.json`, and recover the default provider/implementer fan-out entrypoint before worker execution resumes. For native Claude teams, the same launch state now surfaces the frozen lead prompt plus an auto-execute fallback to the implementer dispatcher, so recovery can still continue from `team-launch --execute` when the provider path itself is interactive-only. After fan-out, `team-relaunch` can recover a specific worker/phase from the frozen dispatch pack — for example a missing implementer, lead synthesis, final runtime verification, documentation sync, document review, or handoff refresh — without rebuilding prompts by hand.
