# plan skill: autoplan parity full (9 features)
date: 2026-04-12
task: TASK__plan-skill-autoplan-parity-full
file: plugin/skills/plan/SKILL.md (1335 → 1540 lines)

## What changed

Nine additive insertions bringing the harness plan skill to full parity with gstack autoplan. Cross-model and Codex sections were excluded as not applicable. No existing content was removed.

| ID | Name | Location |
|----|------|----------|
| F1 | Context Recovery | Phase 0.0 / AUDIT_TRAIL welcome-back briefing (line 292) |
| F2 | Learnings load | Phase 0, step 0.1.5 — reads `.harness/learnings.jsonl` (last 5 entries) |
| F3 | Phase 7 Operational Self-Improvement | End-of-session reflection → appends to `.harness/learnings.jsonl` (line 1444) |
| F4 | Completeness X/10 scoring | AskUserQuestion Format — mandatory per-option score + effort compression table |
| F5 | Completion Status Protocol | DONE / DONE_WITH_CONCERNS / BLOCKED / NEEDS_CONTEXT with escalation format (line 191) |
| F6 | REPO_MODE ownership policy | solo / collaborative / unknown mode with issue-handling rules (line 216) |
| F7 | Plan Review Report in PLAN.md | Phase 6, step 6.8b — appends review summary table directly to PLAN.md |
| F8 | Sequential execution invariant | Invariants section — NEVER run phases in parallel (line 22) |
| F9 | Batch TODOS.md collection | Deferred Scope Surface — unified batch write at Phase 3 completion, not scattered appends |

## Why

These features close the remaining gaps between the harness plan skill and gstack autoplan:

- Prior sessions were invisible on resume; F1 surfaces a structured welcome-back briefing from AUDIT_TRAIL phase-summary rows.
- Accumulated learnings from previous runs were never fed forward; F2 loads them at Phase 0 and F3 writes new ones at session end.
- Option quality was hard to compare without a numeric signal; F4 adds a mandatory completeness score (X/10) plus an effort compression table to every AskUserQuestion.
- Session exit status was unstructured; F5 introduces machine-readable status codes with a defined escalation path.
- Collaborative repo scenarios had no ownership rules; F6 establishes REPO_MODE with explicit handling for solo, collaborative, and unknown states.
- Review outcomes were ephemeral (in conversation only); F7 persists the review summary table into PLAN.md via write_artifact.py.
- Parallel phase execution caused non-deterministic plan state; F8 codifies the sequential-only invariant.
- TODOS.md entries were appended piecemeal throughout Phase 3, making them easy to miss; F9 batches the write to Phase 3 completion.

## Key decisions

- Codex / cross-model sections from the gstack autoplan source were excluded — not applicable to the harness context.
- F1 (Context Recovery) is informational scaffolding only; unreadable AUDIT_TRAIL falls back to normal Phase 0 without blocking.
- F2 learnings load silently skips if `.harness/learnings.jsonl` does not exist; it never creates the file.
- F5 status DONE_WITH_CONCERNS covers: degraded voice during review, unresolved spec issues, or open user challenges at session close.
- F6 REPO_MODE is detected from git remote configuration and branch protection signals; unknown is the safe fallback.
- F7 review table is appended via a second write_artifact.py plan call; total call count increases by 1.
- F9 batch write mirrors the existing G1 pattern: appends only if TODOS.md already exists at project root.

## Caveats

- F3 writes to `.harness/learnings.jsonl`; this path must exist or be created by the skill before Phase 7 runs.
- F7 increases write_artifact.py plan call count by 1; any regression check tracking a fixed count must be updated.
- F8 sequential invariant is a documentation-level constraint; the skill has no runtime enforcement mechanism beyond the stated invariant.
