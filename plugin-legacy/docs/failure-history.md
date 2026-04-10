# Failure history and similar-case retrieval

The harness keeps repeated-failure retrieval intentionally lightweight.

## Goals

- Surface the most relevant prior failures during fix rounds
- Preserve task-local evidence already produced by the harness
- Avoid large prompt expansion or external memory infrastructure

## `FAILURE_CASE.json`

Each task may contain a derived `FAILURE_CASE.json` sidecar. It is a compact index over existing task artifacts:

- `TASK_STATE.yaml`
- `CHECKS.yaml`
- `CRITIC__runtime.md`
- `CRITIC__document.md`
- `HANDOFF.md`
- `SESSION_HANDOFF.json`

The sidecar stores only summary metadata:

- task id and lane
- best artifact to inspect first
- failure signal count
- runtime/document verdict summaries
- failing or reopened check ids
- path focus tokens and examples
- a short excerpt from the most relevant critic or handoff artifact

It is not a source of truth. The source of truth remains the original task artifacts.

## Retrieval policy

`failure_memory.py` scores prior failures with a conservative weighted overlap:

- path overlap: 0.45
- keyword overlap: 0.30
- check-id overlap: 0.15
- same-lane bonus: 0.05
- failure-confidence bonus: 0.05

Only cases above a minimum threshold are surfaced.

## Runtime surfacing

- `prompt_memory.py` still injects only the **single** best similar-failure hint to keep the user-prompt hook bounded.
- `emit_compact_context()` may surface up to the top **3** similar cases in fix rounds.
- `hctl` exposes the full small-CLI workflow for selective inspection instead of pushing more history into prompts.

## CLI

- `hctl history [--tasks-dir DIR] [--lane LANE] [--limit N]`
- `hctl top-failures --task-dir DIR [--tasks-dir DIR] [--limit N]`
- `hctl diff-case --case-a TASK__... --case-b TASK__... [--tasks-dir DIR]`

These commands are meant for targeted inspection and diffing, not automatic prompt stuffing.
