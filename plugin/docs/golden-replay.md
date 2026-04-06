# Golden Replay Reference

updated: 2026-04-06

This document describes the curated golden replay corpus used to detect behavioral drift in the harness control plane.

---

## Why this exists

Normal unit tests prove local logic, but harness regressions often appear as **behavior drift across multiple decisions**:

- a historical request routes to a different execution / orchestration mode
- a closed task no longer satisfies the current close gate
- a prompt retrieves a different primary note
- workflow status maps to a different next action
- recovery resumes from the wrong artifact or team phase after a failure / blocked-env round

The golden replay corpus makes those decisions explicit and replayable, including the more fragile team launch / relaunch recovery surfaces.

---

## Default corpus location

```text
doc/harness/replays/golden-corpus.json
```

The file is JSON (not YAML) so `plugin/scripts/golden_replay.py` stays stdlib-only.

Run it through the control plane:

```bash
python3 plugin/scripts/hctl.py replay
python3 plugin/scripts/hctl.py replay --kind routing
python3 plugin/scripts/hctl.py replay --kind handoff
python3 plugin/scripts/hctl.py replay --kind context
python3 plugin/scripts/hctl.py replay --kind team_launch
python3 plugin/scripts/hctl.py replay --kind team_relaunch
python3 plugin/scripts/hctl.py replay --case close_pass_cli_first_workflow
python3 plugin/scripts/hctl.py replay --json
```

Direct script entrypoint also works:

```bash
python3 plugin/scripts/golden_replay.py --kind prompt_notes
python3 plugin/scripts/golden_replay.py --kind handoff
python3 plugin/scripts/golden_replay.py --kind context
```

Exit codes:

- `0` → every selected case passed
- `2` → at least one replay failed
- `1` → corpus / argument / runtime error

---

## Supported case kinds

### 1. `routing`

Replays `compile_routing(task_dir)` for a historical task snapshot.

Use this to pin:

- `risk_level`
- `maintenance_task`
- `planning_mode`
- `execution_mode`
- `orchestration_mode`
- team fallback behavior

Example:

```json
{
  "id": "routing_harness_maintenance_sprinted_subagents",
  "kind": "routing",
  "task_dir": "doc/harness/tasks/TASK__cli-first-workflow-v1",
  "provider_probe": {"native_ready": false, "omc_ready": false},
  "expect": {
    "risk_level": "high",
    "maintenance_task": true,
    "execution_mode": "sprinted",
    "orchestration_mode": "subagents",
    "team_status": "fallback"
  }
}
```

### 2. `close_gate`

Replays `compute_completion_failures(task_dir)`.

Use this to pin:

- whether the task should be closable
- required blocking failure messages for known bad states

Example:

```json
{
  "id": "close_blocks_missing_runtime_and_doc_critic",
  "kind": "close_gate",
  "task_dir": "doc/harness/tasks/TASK__task-created-gate-prefix-filter",
  "expect": {
    "blocked": true,
    "required_substrings": [
      "repo-mutating task needs runtime critic verdict",
      "document_verdict is 'skipped'"
    ]
  }
}
```

### 3. `prompt_notes`

Replays `select_prompt_notes(prompt)` from the repo root.

Use this to pin:

- primary note selection
- optional second note selection
- note root
- note count

Example:

```json
{
  "id": "prompt_cli_artifact_writes_requirement",
  "kind": "prompt_notes",
  "prompt": "protected artifact writes should use CLI tool rather than inline docs writes",
  "expect": {
    "primary": "REQ__process__cli-artifact-writes.md",
    "primary_root": "common"
  }
}
```

### 4. `next_step`

Replays `stop_gate._next_step(status)`.

Use this to pin workflow guidance text such as:

- `created -> plan`
- `implemented -> critic-runtime`
- `docs_synced -> critic-document`

### 5. `handoff`

Replays `handoff_escalation.preview_handoff(task_dir)` without writing `SESSION_HANDOFF.json`.

Use this to pin:

- which trigger fires (`criterion_reopen_repeat`, `blocked_env_reentry`, etc.)
- recovery `next_step` wording
- `files_to_read_first` ordering for blocked-env rounds
- team recovery phase / pending artifacts / documentation owners

Example:

```json
{
  "id": "handoff_blocked_env_reads_snapshot",
  "kind": "handoff",
  "task_dir": "doc/harness/tasks/TASK__blocked-env-recovery",
  "expect": {
    "trigger": "blocked_env_reentry",
    "next_step_contains": "ENVIRONMENT_SNAPSHOT.md",
    "files_to_read_first_contains": ["ENVIRONMENT_SNAPSHOT.md", "PLAN.md", "TASK_STATE.yaml"]
  }
}
```

### 6. `context`

Replays `emit_compact_context(task_dir)` for the runtime control plane.

Use this to pin:

- `next_action` guidance for fragile recovery states
- nested `team.*` launch / relaunch metadata
- reviewer focus flags for team close-path recovery

This is the conservative way to catch drift where the underlying helper still works, but the surfaced operator guidance changes.

### 7. `team_launch`

Replays `team_launch_status(task_dir)`.

Use this to pin:

- provider-first vs implementer fallback launch target
- native interactive requirements
- stale launch-manifest detection
- execute fallback resolution wording

### 8. `team_relaunch`

Replays `select_team_relaunch_target(task_dir)`.

Use this to pin:

- which worker should resume next
- which phase (`implement`, `synthesis`, `final_runtime_verification`, `documentation_sync`, `documentation_review`, `handoff_refresh`)
- why that phase was chosen

---

## Provider probe stabilization

Routing/context/team cases accept an optional `provider_probe` block:

```json
"provider_probe": {
  "native_ready": false,
  "omc_ready": false,
  "claude_available": false
}
```

During replay, the runner temporarily patches the runtime provider probes so routing stays deterministic across machines. It can also patch synthetic Claude CLI availability for native team-launch auto-execute fallback branches.

Without this, a local environment that happens to expose native or OMC team support could change the expected orchestration result.

---

## Tie-break stability for prompt notes

Prompt-note replay depends on deterministic note ordering.

When scores tie, note selection now breaks ties in this order:

1. higher freshness
2. note type priority (`REQ__` before `OBS__` before `INF__`)
3. root name
4. path

This keeps replay results stable while still preferring requirement notes when two notes score equally.

---

## When to run replay

Run golden replay after edits to harness control surfaces such as:

- `plugin/scripts/*`
- `plugin/agents/*`
- `plugin/docs/*`
- `plugin/hooks/hooks.json`
- `doc/harness/manifest.yaml`
- prompt-memory / routing / close-gate logic

The intended use is conservative:

1. change the harness surface
2. run targeted unit tests
3. run `hctl replay`
4. only then treat the behavior shift as intentional

---

## Updating the corpus

Do **not** rewrite the corpus casually.

Update a case only when the behavior change is intentional and the new decision is now the preferred invariant. The corpus is supposed to be annoying when accidental drift slips in.
