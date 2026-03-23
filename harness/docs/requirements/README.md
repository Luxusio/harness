# Requirements

Persistent requirement specifications for this project.

## Naming convention

Files follow the pattern `REQ-NNNN-<slug>.md` where:
- `NNNN` is a zero-padded sequential number
- `<slug>` is a short kebab-case description

## Status lifecycle

```
draft → accepted → implemented → verified
```

- **draft**: requirements-curator produced the spec, not yet confirmed by user
- **accepted**: requirements-curator completed the conflict check and user confirmed scope, criteria, and non-goals
- **implemented**: feature-workflow marked implementation complete
- **verified**: feature-workflow recorded passing validation evidence and completed acceptance checks

## Ownership rules

- Only requirements-curator creates new REQ files and assigns sequential numbers.
- Only requirements-curator transitions a requirement from `draft` to `accepted` (after conflict check and user confirmation).
- docs-sync does NOT modify REQ status or history.

## Conflict resolution

Before a requirement moves from `draft` to `accepted`, the requirements-curator checks all existing `accepted` and `implemented` requirements for conflicts:
- Contradictory acceptance criteria
- Overlapping scope with incompatible goals
- Non-goals of one requirement conflicting with goals of another

Conflicts must be resolved by the user before development begins.
