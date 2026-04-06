# Artifact Schema Versioning

The harness now carries explicit schema metadata for the core task-local control-plane artifacts:

- `TASK_STATE.yaml`
- `CHECKS.yaml`
- `SESSION_HANDOFF.json`

## Current schema versions

- `TASK_STATE.yaml`: `schema_version: 1`
- `CHECKS.yaml`: `schema_version: 1`
- `SESSION_HANDOFF.json`: `schema_version: 1`

Legacy artifacts without an explicit `schema_version` are treated as **schema version 0** and can be upgraded in place.

## TASK_STATE revisions

`TASK_STATE.yaml` also carries lightweight state lineage fields:

```yaml
schema_version: 1
state_revision: 0
parent_revision: null
```

Rules:

- newly scaffolded tasks start at `state_revision: 0`
- each state mutation bumps `state_revision`
- `parent_revision` points at the immediately previous revision number
- legacy task states are migrated to the versioned layout before future mutations continue

This is intentionally simple: it gives the control plane a durable notion of state lineage without introducing a heavier transactional store.

## Migration

Preview a task-local migration:

```bash
python3 plugin/scripts/hctl.py migrate --task-dir doc/harness/tasks/TASK__example
```

Apply it in place:

```bash
python3 plugin/scripts/hctl.py migrate --task-dir doc/harness/tasks/TASK__example --write
```

Emit machine-readable output:

```bash
python3 plugin/scripts/hctl.py migrate --task-dir doc/harness/tasks/TASK__example --write --json
```

## Writer behavior

- task scaffolding writes versioned `TASK_STATE.yaml`
- `set_task_state_field()` preserves schema metadata and bumps revisions
- `write_artifact.py` preserves task-state revisions and backfills `CHECKS.yaml` schema metadata
- `handoff_escalation.py` writes versioned `SESSION_HANDOFF.json`

The migration path is deliberately conservative: existing files remain readable, missing schema fields are treated as legacy, and upgrades only add version metadata plus task-state revision fields.
