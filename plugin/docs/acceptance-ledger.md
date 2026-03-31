# Acceptance Ledger (CHECKS.yaml)

updated: 2026-03-30

CHECKS.yaml is a machine-readable companion to PLAN.md that tracks the lifecycle of each acceptance criterion from planning through verification. It enables structured evidence tracking, reopen counting, delta verification focus, and calibration mining.

---

## Purpose

- Provide a stable, addressable ID per acceptance criterion (`AC-001`, `AC-002`, ...)
- Track criterion status as agents progress through the harness loop
- Record which critic artifacts provided evidence for each criterion
- Count how many times a criterion was reopened after an initial pass
- Surface open criteria at task completion as a non-blocking advisory
- Enable fix-round delta verification (focus-first + guardrail-second)
- Trigger local calibration case generation when reopen_count ≥ 2

---

## Schema

```yaml
close_gate: standard | strict_high_risk
checks:
  - id: AC-001
    title: "User can do X"
    status: planned
    kind: functional
    evidence_refs: []
    reopen_count: 0
    last_updated: "2026-03-30T00:00:00Z"
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
- On regression (`passed` → `failed`): increments `reopen_count`

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

A criterion with `reopen_count >= 2` is a signal that the plan or implementation has a recurring issue in that area. This also triggers calibration case generation (see below).

---

## Delta Verification (Focus/Guardrail Sets)

In fix rounds (after a prior runtime FAIL), verifying all criteria from scratch is wasteful. CHECKS.yaml enables a focused verification strategy:

### Focus set
Criteria requiring immediate attention:
- `status: failed`
- `status: implemented_candidate`
- `status: blocked`

### Open set
All criteria not yet `passed` (superset of focus).

### Guardrail set
Criteria currently `passed` — verify lightly for regression. Source: `status: passed` in CHECKS.yaml, or `last_known_good_checks` in `SESSION_HANDOFF.json` if present.

### When to use delta vs. full sweep

| Situation | Strategy |
|-----------|----------|
| Fix round (Round 2+), standard/light task | Focus first → guardrail sweep |
| Round 1 (no prior FAIL) | Full sweep |
| `execution_mode: sprinted` | Full sweep |
| `roots_touched` ≥ 2 | Full sweep |
| `risk_tags`: structural/migration/schema/cross-root | Full sweep |
| No CHECKS.yaml | Full sweep |

The focus/guardrail sets are computed by `plugin/scripts/checks_focus.py` and surfaced in prompt memory (as a short summary) during fix rounds.

### Prompt memory injection

During fix rounds, `prompt_memory.py` injects a short checks summary (max 120 chars):

```
Checks: focus AC-002, AC-005 | guardrails AC-001
```

This is injected only when:
- An active task has `runtime_verdict: FAIL` or `SESSION_HANDOFF.json` present
- CHECKS.yaml has focus-status criteria
- Prompt is not casual

---

## Calibration Mining

When a criterion has `reopen_count >= 2`, it is a candidate for local calibration case generation. The `calibration_miner.py` script generates a short case file in `plugin/calibration/local/critic-runtime/` describing:

- Why the previous PASS was wrong
- What the critic must check next time
- Evidence refs

`session_end_sync.py` reports the count of calibration candidates (read-only). Actual case files are only written by `/harness:maintain` or explicit `calibration_miner.py` invocation.

---

## Integration with HANDOFF.md

CHECKS.yaml is a task-local artifact. Its path is `doc/harness/tasks/<task_id>/CHECKS.yaml`.

When summarizing task state in HANDOFF.md, agents may reference open criteria by ID:

```
open_checks: [AC-003, AC-005]  # not yet passed
```

This is optional — HANDOFF.md does not require explicit CHECKS.yaml references.

---

## Task completion advisory

At task completion (`TaskCompleted` hook), `task_completed_gate.py` reads CHECKS.yaml and prints a warning for any criterion not in `passed` status, grouped by status:

```
WARN: 2 open acceptance criteria in CHECKS.yaml:
  [failed] AC-002
    - AC-002: User can reset password via email
  [planned] AC-005
    - AC-005: Rate limiting applied to auth endpoints
```

This warning is **non-blocking** — it does not prevent task completion (no `exit 2`). It is advisory only.

---

## Backward compatibility

CHECKS.yaml is optional. All agents check for its existence before reading or writing:

- If CHECKS.yaml does not exist → skip silently, no error
- If CHECKS.yaml is malformed → log a warning, skip silently
- Tasks created before CHECKS.yaml was introduced work exactly as before

The `close_gate` field is backward-compatible:
- Absent field → treated as `standard` (existing behavior preserved)
- `close_gate: standard` → only `failed` criteria block completion
- `close_gate: strict_high_risk` → ALL non-`passed` criteria block completion

---

## Close gate policy

The `close_gate` top-level field in CHECKS.yaml controls how strictly non-passed criteria are enforced at task close.

### standard (default)

Only `failed` criteria block task completion. Other non-passed statuses (`planned`, `implemented_candidate`, `blocked`) are advisory warnings.

### strict_high_risk

ALL criteria must be `passed` before the task can close. Any criterion in `planned`, `implemented_candidate`, `failed`, or `blocked` status blocks completion.

The blocker message groups criteria by status with actionable descriptions:
- `failed` — critic FAIL
- `implemented_candidate` — implementation claimed but not critic-verified
- `planned` — not yet implemented or verified
- `blocked` — env/dependency blocker unresolved

### When strict_high_risk is set

The plan skill sets `close_gate: strict_high_risk` when ANY of:
- `execution_mode: sprinted`
- `review_overlays` contains `security` or `performance`
- `risk_tags` contains `structural`, `migration`, `schema`, or `cross-root`

### Removing criteria from strict tasks

To close a strict task when a criterion is no longer relevant:
1. Update PLAN.md acceptance criteria (remove the criterion)
2. Re-sync CHECKS.yaml (remove the entry)
3. Re-pass critic-plan

This ensures the contract is updated rather than bypassed at close time.

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
    last_updated: "2026-03-30T14:32:00Z"
    notes: ""
    runtime_required: true

  - id: AC-002
    title: "Form submission is rate-limited to 5 requests per minute"
    status: failed
    kind: functional
    evidence_refs:
      - CRITIC__runtime.md
    reopen_count: 1
    last_updated: "2026-03-30T15:10:00Z"
    notes: "Rate limiting middleware not applied to /contact route"
    runtime_required: true

  - id: AC-003
    title: "API endpoint documented in docs/api-reference.md"
    status: planned
    kind: doc
    evidence_refs: []
    reopen_count: 0
    last_updated: "2026-03-30T10:00:00Z"
    notes: ""
    doc_sync_required: true
```
