# Project docs index

## Core operating files
- `CLAUDE.md` — project instructions for Claude
- `harness/manifest.yaml` — project shape and commands
- `harness/router.yaml` — intent routing configuration
- `harness/policies/approvals.yaml` — approval gates / ask-first rules
- `harness/policies/memory-policy.yaml` — memory classification rules

## State
- `harness/state/recent-decisions.md` — chronological decision log
- `harness/state/recent-decisions-archive.md` — archived older decision entries
- `harness/state/unknowns.md` — open questions and hypotheses
- `harness/state/current-task.yaml` — runtime loop state (gitignored)
- `harness/state/last-session-summary.md` — previous session summary (gitignored)

## Knowledge
- `harness/docs/constraints/project-constraints.md` — confirmed project rules
- `harness/docs/decisions/ADR-0001-harness-bootstrap.md` — bootstrap decision record
- `harness/docs/domains/README.md` — domain knowledge index
- `harness/docs/architecture/README.md` — architecture boundaries and patterns
- `harness/docs/runbooks/development.md` — development procedures and debugging notes

## Requirements
- `harness/docs/requirements/README.md` — requirement specifications index

## Scripts
- `harness/scripts/validate.sh` — validation checks
- `harness/scripts/smoke.sh` — smoke tests
- `harness/scripts/arch-check.sh` — architecture guardrail checks
- `harness/scripts/check-approvals.sh` — deterministic approval gate checker
- `harness/scripts/build-memory-index.sh` — deterministic memory index compiler
- `harness/scripts/build-memory-index.py` — memory index build logic (Python 3)
- `harness/scripts/check-memory-index.sh` — memory index staleness checker
- `harness/scripts/query-memory.sh` — memory index query prefilter
- `harness/scripts/query-memory.py` — query logic (Python 3)

## Memory Index
- `harness/memory-index/README.md` — shared compiled memory index documentation
- `harness/memory-index/VERSION` — index schema version
- `harness/memory-index/manifest.json` — index metadata (generated)
- `harness/memory-index/source-shards/` — per-source compiled records (generated)
- `harness/memory-index/active/` — active records indexed by subject/domain/path (generated)
- `harness/memory-index/timeline/` — temporal evolution of records (generated)
