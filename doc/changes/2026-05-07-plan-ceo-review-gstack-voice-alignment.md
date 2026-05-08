# 2026-05-07 — plan-ceo-review gstack voice alignment

**Task:** TASK__align-ceo-voice-with-gstack

Five additive content inserts in `plugin/skills/plan-ceo-review/SKILL.md`, closing the remaining gstack-vs-harness content gaps in the CEO review skill while preserving harness's deliberate divergences.

## What changed

- **Voice section.** Appended AI-vocabulary blocklist (`delve`, `crucial`, `robust`, `comprehensive`, `nuanced`, `multifaceted`, `furthermore`, `moreover`, `additionally`, `pivotal`, `landscape`, `tapestry`, `underscore`, `foster`, `showcase`, `intricate`, `vibrant`, `fundamental`, `significant`), "no em dashes" rule, "Lead with the point / be concrete / tie technical to user outcomes / sound like a builder, not a consultant" rules, and a paired Good/Bad example.
- **Confusion Protocol.** New top-level section. STOP-on-high-stakes-ambiguity (architecture, data model, destructive scope, missing context); 2-3 options + AskUserQuestion; "do not use for routine coding decisions" guard.
- **0C-bis equal-weight clause.** Implementation Alternatives Rules block gains a bullet stating "minimal viable" and "ideal architecture" have equal weight; do not default to minimal viable just because the diff is smaller; if rewrite is right, invoke Prime Directive #9.
- **0D-prelude — Expansion Framing.** New subsection before `### 0D. Mode-Specific Analysis`. FLAT vs EXPANSIVE example; "evocative, not promotional" guard. Applies to SCOPE EXPANSION and SELECTIVE EXPANSION modes.
- **Anti-shortcut clause.** Appended to **Anti-skip rule:** paragraph in Review Sections intro. Names the May 2026 transcript bug failure mode (model finds issues, dumps into PLAN.md, signals completion without firing AskUserQuestion); explicitly routes finding-to-PLAN.md path THROUGH AskUserQuestion.

## Excluded from scope (with rationale)

- **AskUserQuestion `D<N>/ELI10/✅❌/Net line` format.** Conflicts with parent `plugin/skills/plan/decision-principles.md` which owns harness's deliberately different `[Re-ground]/[Simplify]/[Recommend]/[Options]` shape. SPEC.md "gstack infra stripped" semantics extend to design choices, not just code.
- **Writing Style + curated jargon glossary (~80 lines).** Adds weight without proportionate value; existing Voice section already covers tone (CONTRACTS C-13 weight budget).
- **gstack infra (preamble bash, telemetry, gstack-config, ~/.gstack/, gbrain, vendoring, question-tuning, plan-mode safe ops, continuous checkpoint, model-specific behavioral patch, artifacts sync).** Stripped by design per SPEC.md.

## Impact

- Anyone running `Skill(plan-ceo-review)` standalone or as a sub-skill in `Skill(harness:plan)` Phase 1 now sees: a stricter voice rule set with concrete blocklist, a STOP protocol for high-stakes ambiguity, a corrective against "default to minimal viable", craft instruction for expansion proposals, and an explicit guard against the dump-to-PLAN.md failure mode.
- File grew 1273 → 1297 lines (+24). No deletions, no reordering, no script API changes, no hook changes, no contract changes.

## Cross-skill follow-up (deferred)

Sibling skills (`plan-eng-review`, `plan-design-review`, `plan-devex-review`) all share the Anti-skip rule pattern but lack the Anti-shortcut clause. The May 2026 bug applies symmetrically to all four review skills. Filed for a future task — not in this scope.

## References

- PLAN.md: `doc/harness/tasks/TASK__align-ceo-voice-with-gstack/PLAN.md`
- HANDOFF.md: `doc/harness/tasks/TASK__align-ceo-voice-with-gstack/HANDOFF.md`
- gstack source: `https://github.com/garrytan/gstack` (plan-ceo-review/SKILL.md, 2108 lines)
- Prior parity tasks: `doc/changes/2026-04-09-*`, `doc/changes/2026-04-11-*`, `doc/changes/2026-04-12-*`, `doc/changes/2026-04-23-plan-skills-office-hours-and-outside-voice-polish.md`
