# 2026-05-08 — plan-design-review gstack alignment

**Task:** TASK__align-design-review-with-gstack

Nine additive content inserts/edits in `plugin/skills/plan-design-review/SKILL.md`, closing the gstack-vs-harness content gaps in the design review skill while preserving harness's deliberate divergences. Companion to TASK__align-ceo-voice-with-gstack from 2026-05-07.

## What changed

**Plan-ceo-review parity (4):**
- **Voice section** (new). Designer-builder voice: lead with point, concrete file/component/viewport refs, AI-vocabulary blocklist (`delve`, `crucial`, `robust`, etc.), no-em-dashes rule, paired Good/Bad example. Mirrors plan-ceo-review for cross-skill consistency.
- **Confusion Protocol** (new). STOP-on-design-ambiguity (IA restructure, navigation pattern, destructive UI scope, missing brand context); 2-3 options + AskUserQuestion; routine-fix guard.
- **Anti-shortcut clause** (appended to Anti-skip rule). Names May 2026 transcript bug; finding-to-PLAN.md path goes THROUGH AskUserQuestion.
- **STOP-rule reconciliation**. CRITICAL RULE escape hatch tightened from `"obvious fix → don't waste a question"` (which contradicted the new Anti-shortcut clause) to gstack's tighter `"a gap with an 'obvious fix' is still a gap and still needs user approval"`.

**Design-specific (5):**
- **UX Principles: How Users Actually Behave** (new section, ~85 lines). Krug's Three Laws of Usability (Don't make me think; Clicks don't matter, thinking does; Omit, then omit again), behavior observations (scan/satisfice/muddle/skip-instructions), Billboard Design (conventions/hierarchy/clickability/noise/clarity-trumps-consistency), Navigation as Wayfinding (trunk test), Goodwill Reservoir (deplete vs replenish), Mobile Same Rules Higher Stakes.
- **Cognitive Patterns key references** extended with Steve Krug, Ginny Redish, Caroline Jarrett.
- **AI Slop blacklist #11**: `system-ui or -apple-system as the PRIMARY display/body font — the "I gave up on typography" signal.`
- **Universal rules +4**: small/low-contrast type rule, placeholder-as-label rule, visited-link distinction rule, floating-heading rule.
- **Next Steps — Review Chaining** (new section). Recommends `/plan-eng-review` (required gate); `/plan-ceo-review` selectively when fundamental product gaps surface.

## Excluded from scope (with rationale)

- **AskUserQuestion `D<N>/ELI10/✅❌/Net line` format.** Parent `plugin/skills/plan/decision-principles.md` owns harness's deliberately different `[Re-ground]/[Simplify]/[Recommend]/[Options]` shape.
- **Writing Style + curated jargon glossary (~80 lines).** Weight budget breach (CONTRACTS C-13).
- **gstack designer binary integration.** Tool not present in harness; existing Step 0.5 ASCII wireframe approach kept.
- **Comparison-board HTTP server feedback loop.** Tied to gstack designer binary.
- **gstack infra (preamble bash, telemetry, gstack-config, ~/.gstack/, gbrain, vendoring, plan-mode safe-ops, continuous checkpoint, model-specific behavioral patch, question-tuning, artifacts sync, review-log binary).** Stripped by design per SPEC.md.

## Impact

- Anyone running `Skill(plan-design-review)` standalone or as a sub-skill in `Skill(harness:plan)` Phase 2 now sees: a stricter voice rule set with concrete blocklist, a STOP protocol for high-stakes design ambiguity, a corrective against the dump-to-PLAN.md failure mode, ~85 lines of usability-grounded UX principles, expanded AI-slop and universal rule coverage, and an explicit next-review chaining recommendation.
- File grew 725 → 853 lines (+128 net; +130 inserts, -2 line-replacement refactors). No deletions of substantive content.

## Cross-skill follow-up (deferred)

`plan-eng-review` and `plan-devex-review` still lack Voice / Confusion Protocol / Anti-shortcut clause and carry the same weak STOP wording. Consistency snapshot:

| Skill | Voice | Confusion Protocol | Anti-shortcut | STOP wording |
|-------|-------|-------------------|---------------|--------------|
| plan-ceo-review | ✓ | ✓ | ✓ | tight |
| plan-design-review | ✓ | ✓ | ✓ | tight |
| plan-eng-review | ✗ | ✗ | ✗ | weak |
| plan-devex-review | ✗ | ✗ | ✗ | weak |

Filed for a future cross-skill alignment task.

## References

- PLAN.md: `doc/harness/tasks/TASK__align-design-review-with-gstack/PLAN.md`
- HANDOFF.md: `doc/harness/tasks/TASK__align-design-review-with-gstack/HANDOFF.md`
- gstack source: `https://github.com/garrytan/gstack` (plan-design-review/SKILL.md, 1828 lines)
- Companion task: `doc/changes/2026-05-07-plan-ceo-review-gstack-voice-alignment.md`
