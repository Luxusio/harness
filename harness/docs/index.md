# Project docs index

## Core operating files
- `CLAUDE.md` — project instructions for Claude
- `harness/manifest.yaml` — project shape and commands
- `harness/router.yaml` — intent routing configuration
- `harness/policies/approvals.yaml` — risk zone approval rules
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
