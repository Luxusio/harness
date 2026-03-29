# Acceptance Ledger (CHECKS.yaml)

updated: 2026-03-29

CHECKS.yaml is a machine-readable companion to PLAN.md that tracks the lifecycle of each acceptance criterion from planning through verification. It enables structured evidence tracking and reopen counting without replacing the human-readable PLAN.md checklist.

---

## Purpose

- Provide a stable, addressable ID per acceptance criterion (`AC-001`, `AC-002`, ...)
- Track criterion status as agents progress through the harness loop
- Record which critic artifacts provided evidence for each criterion
- Count how many times a criterion was reopened after an initial pass
- Surface open criteria at task completion as a non-blocking advisory

---

## Schema

```yaml
checks:
  - id: AC-001
    title: "User can do X"
    status: planned
    kind: functional
    evidence_refs: []
    reopen_count: 0
    last_updated: "2026-03-29T00:00:00Z"
    notes: ""
    # optional fields:
    # owner_hint: ""          # which agent is expected to satisfy this criterion
    # related_paths: []       # file paths most relevant to this criterion
    # overlay_tags: []        # review overlay tags if relevant (security, performance, etc.)
    # runtime_required: false # true if runtime execution is needed to verify
    # doc_sync_required: false # true if documentation must be updated
```

### Required fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Stable criterion ID, format `AC-NNN` (zero-padded, sequential) |
| `title` | string | Criterion text verbatim from PLAN.md |
| `status` | string | Current lifecycle status (see Status Lifecycle below) |
| `kind` | string | Criterion category: `functional`, `verification`, `doc`, `risk` |
| `evidence_refs` | list | Paths to critic artifacts that support this criterion's current status |
| `reopen_count` | integer | Number of times criterion regressed from `passed` to `failed` |
| `last_updated` | string | ISO 8601 timestamp of last status change |
| `notes` | string | Free-text notes; empty string if none |

### Optional fields

| Field | Type | Description |
|-------|------|-------------|
| `owner_hint` | string | Agent expected to satisfy this criterion |
| `related_paths` | list | File paths most relevant to verifying this criterion |
| `overlay_tags` | list | Review overlay tags that apply (`security`, `performance`, `frontend-refactor`) |
| `runtime_required` | boolean | True if runtime execution is required to verify (default: false) |
| `doc_sync_required` | boolean | True if a doc surface must be updated (default: false) |

### Kind values

| Kind | Meaning |
|------|---------|
| `functional` | User-visible behavior or feature outcome |
| `verification` | A test, check, or validation step |
| `doc` | Documentation or comment requirement |
| `risk` | Safety, rollback, or constraint condition |

---

## Status Lifecycle

```
planned
  │
  │  developer implements
  ▼
implemented_candidate
  │
  │  critic-plan / critic-runtime / critic-document evaluates
  ▼
passed ◄──────────────────────────────── (re-evaluated)
  │                                              ▲
  │  subsequent critic finds criterion not met   │
  ▼                                              │
failed ────────────────────────────────────────► (reopen_count++)
  │
  │  environment/dependency issue blocks evaluation
  ▼
blocked
```

### Status values

| Status | Set by | Meaning |
|--------|--------|---------|
| `planned` | plan skill | Criterion extracted from PLAN.md; not yet implemented |
| `implemented_candidate` | developer | Developer believes this criterion is addressed |
| `passed` | any critic | Critic evidence confirms criterion is met |
| `failed` | any critic | Critic evidence shows criterion is not met |
| `blocked` | any agent | Cannot be evaluated due to environment or dependency issue |

---

## How critics update criteria

Each critic updates only the criteria relevant to its domain:

### critic-plan
- Updates all criteria based on whether the plan adequately addresses them
- Evidence ref: `CRITIC__plan.md`
- Typically transitions: `planned` → `passed` or `planned` → `failed`

### critic-runtime
- Updates criteria where `runtime_required: true` or where the criterion clearly needs execution
- Skips `kind: doc` criteria (those belong to critic-document)
- Evidence ref: `CRITIC__runtime.md`
- Typically transitions: `implemented_candidate` → `passed` or `implemented_candidate` → `failed`

### critic-document
- Updates criteria where `kind: doc` or `doc_sync_required: true`
- Skips purely functional criteria
- Evidence ref: `CRITIC__document.md`
- Typically transitions: `implemented_candidate` → `passed` or `implemented_candidate` → `failed`

---

## Reopen tracking

`reopen_count` increments each time a criterion regresses from `passed` back to `failed`. This provides a signal for:

- Identifying flaky or underspecified acceptance criteria
- Detecting recurring failures in a specific area
- Prioritizing additional scrutiny during review

A criterion with `reopen_count >= 2` is a signal that the plan or implementation has a recurring issue in that area.

---

## Integration with HANDOFF.md

CHECKS.yaml is a task-local artifact. Its path is `.claude/harness/tasks/<task_id>/CHECKS.yaml`.

When summarizing task state in HANDOFF.md, agents may reference open criteria by ID:

```
open_checks: [AC-003, AC-005]  # not yet passed
```

This is optional — HANDOFF.md does not require explicit CHECKS.yaml references.

---

## Task completion advisory

At task completion (`TaskCompleted` hook), `task_completed_gate.py` reads CHECKS.yaml and prints a warning for any criterion not in `passed` status:

```
WARN: 2 open acceptance criteria in CHECKS.yaml: AC-003, AC-005
  - AC-003 [failed] User can reset password via email
  - AC-005 [planned] Rate limiting applied to auth endpoints
```

This warning is **non-blocking** — it does not prevent task completion (no `exit 2`). It is advisory only.

---

## Backward compatibility

CHECKS.yaml is optional. All agents check for its existence before reading or writing:

- If CHECKS.yaml does not exist → skip silently, no error
- If CHECKS.yaml is malformed → log a warning, skip silently
- Tasks created before CHECKS.yaml was introduced work exactly as before

The presence or absence of CHECKS.yaml does not affect any hard gate in the harness.

---

## Example

```yaml
checks:
  - id: AC-001
    title: "User can submit the contact form and receives a confirmation email"
    status: passed
    kind: functional
    evidence_refs:
      - CRITIC__runtime.md
    reopen_count: 0
    last_updated: "2026-03-29T14:32:00Z"
    notes: ""
    runtime_required: true

  - id: AC-002
    title: "Form submission is rate-limited to 5 requests per minute"
    status: failed
    kind: functional
    evidence_refs:
      - CRITIC__runtime.md
    reopen_count: 1
    last_updated: "2026-03-29T15:10:00Z"
    notes: "Rate limiting middleware not applied to /contact route"
    runtime_required: true

  - id: AC-003
    title: "API endpoint documented in docs/api-reference.md"
    status: planned
    kind: doc
    evidence_refs: []
    reopen_count: 0
    last_updated: "2026-03-29T10:00:00Z"
    notes: ""
    doc_sync_required: true
```
