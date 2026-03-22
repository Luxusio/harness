---
name: repo-memory-policy
description: Use when a user states a durable rule or when work reveals a lasting fact that should be captured in repo-local memory.
allowed-tools: Read, Glob, Grep, Write, Edit
user-invocable: false
---

## Trigger

Activate when:
- The user states a lasting project rule
- Work reveals a technical fact that will affect future work
- A decision needs to be persisted
- Memory quality needs to be reviewed

## Memory classification

Every candidate memory item must be classified:

| Type | Location | Example |
|------|----------|---------|
| `constraint` | `harness/docs/constraints/` | "retry max 2 times" |
| `decision` | `harness/docs/decisions/ADR-*.md` | "chose PostgreSQL over MongoDB" |
| `approval_rule` | `harness/policies/approvals.yaml` | "auth changes need confirmation" |
| `observed_fact` | `harness/docs/runbooks/` or `harness/docs/architecture/` | "service X calls Y synchronously" |
| `runbook_note` | `harness/docs/runbooks/` | "restart worker after config change" |
| `requirement` | `harness/docs/requirements/REQ-*.md` | "user wants CSV export with filtering" |
| `hypothesis` | `harness/state/unknowns.md` | "billing module may have race condition" |
| `open_question` | `harness/state/unknowns.md` | "who owns the legacy auth middleware?" |

## Recording rules

### Auto-record (no confirmation needed)
- Explicit user constraint: "from now on, always..."
- Explicit user approval rule: "ask me before changing..."
- Verified bug root cause (confirmed by test/evidence)
- Verified architecture fact (confirmed by code reading)
- Verified runtime fact (confirmed by logs/metrics)
- Repeated project pattern (seen in 3+ places)

### Ask first (needs confirmation)
- Business rule interpretation
- Architecture principle (not just observation)
- Ownership assignment
- External contract meaning
- Breaking behavior policy

### Never record
- Transient chat (one-off questions/comments)
- Emotional commentary
- Unverified guess presented as fact
- Duplicate of existing memory
- Repo-irrelevant preference

## Memory promotion ladder

```
hypothesis → observed_fact → confirmed → enforced
```

- **hypothesis → observed_fact**: requires code evidence or test/runtime evidence
- **observed_fact → confirmed**: requires user confirmation or explicit repo rule
- **confirmed → enforced**: requires test added, validation script added, or config assertion added

## Memory quality rules

Good memory is:
- **Specific**: "payment retry limit is 2" not "we have retry limits"
- **Scoped**: tied to a domain, file, or flow
- **Evidenced**: backed by code, tests, or user statement
- **Reusable**: helps future work, not just this session

Bad memory is:
- Vague generalities
- One-off phrasing from chat
- Duplicate of an existing entry
- Unverifiable guesses stored as facts

## Retrieval priority

When loading memory for a task:
1. Global constraints (always)
2. Approval rules (always)
3. Exact path match findings
4. Same domain findings
5. Same API surface findings
6. Recent related bugfixes
7. Hypotheses (load last, treat with caution)

## Cleanup

- Deduplicate on every sync
- Remove stale entries (superseded by newer decisions)
- Require `last_verified_at` for observed facts
- Move resolved unknowns out of unknowns.md

## Compaction

Compaction prevents unbounded growth of state files. Run during orchestrator Step 7 (sync), not on every turn.

### recent-decisions.md
- **Threshold**: 50 non-comment, non-blank entries
- **Rule**: count only lines matching `^- \[` (actual entries, not comments or blanks)
- **Compaction procedure** (in order):
  1. **Promote**: For each entry being removed, check if it is already reflected in a permanent document. If not, write it to the appropriate location:
     - `constraint` / `approval_rule` → `harness/docs/constraints/project-constraints.md` or `harness/policies/approvals.yaml`
     - `decision` → create `harness/docs/decisions/ADR-NNNN-*.md` if significant enough
     - `observed_fact` / `architecture` → `harness/docs/architecture/README.md` or `harness/docs/runbooks/`
     - `risk_zone` → `harness/manifest.yaml` risk_zones section
     - `requirement` → `harness/docs/requirements/REQ-NNNN-*.md` if not already captured
  2. **Archive**: Move promoted entries to `harness/state/recent-decisions-archive.md` (append-only, not loaded at session start)
  3. **Trim**: Remove archived entries from active file, keeping the most recent 50

### unknowns.md
- **Resolved items**: Items in the `## Resolved` section older than 30 days can be pruned (deleted entirely — they have served their purpose)
- **Active items**: Never automatically deleted. Only moved to Resolved by explicit confirmation.

### constraints/project-constraints.md
- **Never delete**: constraints are permanent unless the user explicitly revokes them.
- **Deduplicate**: merge entries that say the same thing in different words.
- **Group by scope**: when the file exceeds 30 entries, reorganize into scope-based sections (e.g., `## Repo structure`, `## Plugin UX`, `## Memory`, `## Workflows`).
- **Conflict resolution**: if two constraints conflict, flag to the user — do not silently remove either.

### decisions/ (ADR files)
- **Never delete**: ADRs are permanent historical records.
- **Superseded**: when a newer decision replaces an older one, mark the old ADR status as `superseded by ADR-NNNN`. Do not delete it.
- **No compaction**: ADR files do not compact or merge. Each decision stays as its own file.

### requirements/ (REQ files)
- **Never delete**: requirements are permanent records of what was asked for.
- **Status-only updates**: the only mutation is status progression (draft → accepted → implemented → verified) and history appends.
- **No compaction**: REQ files do not compact or merge. Each requirement stays as its own file.

### Trigger
Check file sizes during knowledge sync (orchestrator Step 7). Only compact when the threshold is exceeded — do not check on every turn.

## Guardrails

- Never store a guess as a fact
- Never duplicate existing memory
- Prefer executable memory (test > script > config > docs)
- Keep entries short and actionable
- When in doubt about classification, ask the user
