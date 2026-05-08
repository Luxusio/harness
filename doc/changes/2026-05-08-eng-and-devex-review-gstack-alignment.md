# 2026-05-08 — plan-eng-review + plan-devex-review gstack alignment (4-gap pattern)

**Task:** TASK__align-eng-and-devex-review-with-gstack

Eight additive content edits across two files, applying the same 4-gap voice-and-gating backfill pattern that landed on plan-ceo-review (2026-05-07) and plan-design-review (2026-05-08). After this task, all 4 sibling sub-skills under `plugin/skills/plan/` share consistent voice rules, the Confusion Protocol, the Anti-shortcut clause, and tight STOP-rule wording (with one small carryover noted below).

## What changed

**plan-eng-review/SKILL.md (+23 lines):**
- New `## Voice` section. Engineer-builder voice: file/line/test/exception-class refs; AI-vocab blocklist; Good/Bad example with `billing.ts:142`.
- New `## Confusion Protocol` section. STOP on architecture / data-model migration / destructive scope / missing context.
- `**Anti-shortcut clause:**` appended to Anti-skip rule. References May 2026 transcript bug; finding-to-PLAN.md goes through AskUserQuestion.
- Escape-hatch wording tightened from `"obvious fix with no real alternatives → don't waste a question"` to gstack form `"a finding with an 'obvious fix' is still a finding"`.

**plan-devex-review/SKILL.md (+22 lines):**
- New `## Voice` section. DX-builder voice: developer pain, TTHW, error-message specifics; Good/Bad example with `POST /v1/messages` error.
- New `## Confusion Protocol` section. STOP on public API / CLI / SDK / breaking-change / destructive-scope ambiguity.
- `**Anti-shortcut clause:**` appended to Anti-skip rule (numbered for 1-8 passes).
- Escape-hatch wording tightened (was a 2-line variant; now matches gstack tight form).

## Excluded from scope (with rationale)

- Other content gaps in gstack vs harness for these two files (~810 lines for plan-eng-review, ~1045 lines for plan-devex-review). User scoped this task to the 4-gap pattern only.
- AskUserQuestion `D<N>/ELI10/✅❌/Net line` format. Parent decision-principles.md owns harness format.
- Writing Style + jargon glossary. Weight budget (CONTRACTS C-13).
- gstack infra (preamble bash, telemetry, plan-mode safe-ops, gbrain, vendoring, question-tuning, gstack-config, ~/.gstack/ paths). Stripped by design.

## Impact

- Cross-skill voice consistency under `plugin/skills/plan/` reaches 3-of-4 fully aligned (was 2-of-4 pre-task; plan-ceo-review still has a minor STOP-wording carryover that didn't get tightened in the original 2026-05-07 task).
- 8 edits, 2 files. +48 lines / -3 lines (the 3 deletions are escape-hatch wording replacements: 1 line in plan-eng-review, 2 lines in plan-devex-review). No section structure deletions.

## Cross-skill consistency snapshot

| Skill | Voice | Confusion Protocol | Anti-shortcut clause | STOP wording |
|-------|:-:|:-:|:-:|:-:|
| plan-ceo-review | ✓ | ✓ | ✓ | ⚠ weak (carryover) |
| plan-design-review | ✓ | ✓ | ✓ | tight |
| plan-eng-review | ✓ | ✓ | ✓ | tight |
| plan-devex-review | ✓ | ✓ | ✓ | tight |

## Cross-skill follow-up (deferred)

- Tighten plan-ceo-review escape-hatch STOP wording (carryover from TASK__align-ceo-voice-with-gstack). One-line edit to fully unify all 4 siblings.
- Per-skill content alignment (the bigger gstack content gaps for plan-eng-review and plan-devex-review). Separate per-file tasks if desired.

## References

- PLAN.md: `doc/harness/tasks/TASK__align-eng-and-devex-review-with-gstack/PLAN.md`
- HANDOFF.md: `doc/harness/tasks/TASK__align-eng-and-devex-review-with-gstack/HANDOFF.md`
- gstack source: `https://github.com/garrytan/gstack`
- Prior tasks in this series: `doc/changes/2026-05-07-plan-ceo-review-gstack-voice-alignment.md`, `doc/changes/2026-05-08-plan-design-review-gstack-alignment.md`
