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

- **draft**: requirements-curator has produced the spec, not yet confirmed by user
- **accepted**: user has confirmed scope, criteria, and non-goals; no conflicts with existing requirements
- **implemented**: feature-workflow has completed the implementation
- **verified**: validation-loop has confirmed acceptance criteria are met

## Conflict resolution

Before a requirement moves from `draft` to `accepted`, the requirements-curator checks all existing `accepted` and `implemented` requirements for conflicts:
- Contradictory acceptance criteria
- Overlapping scope with incompatible goals
- Non-goals of one requirement conflicting with goals of another

Conflicts must be resolved by the user before development begins.
