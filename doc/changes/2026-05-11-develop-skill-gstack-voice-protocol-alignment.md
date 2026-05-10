# 2026-05-11 — develop/ orchestrator gstack voice + protocol alignment

**Task:** TASK__align-develop-skill-with-gstack-voice-protocols

Five orchestrator-level content additions in `plugin/skills/develop/SKILL.md` (the parent develop-skill that runs the implement → audit → verify → handoff loop), aligning with the gstack alignment that landed for `plan/SKILL.md` (commit 31bc43a, doc/changes/2026-05-08-plan-orchestrator-gstack-alignment.md). Closes the develop-orchestrator gap left after the 5-task plan-orchestrator series.

## What changed

**plugin/skills/develop/SKILL.md (+40 lines / -1 line, 381 → 420):**

- **AC-001 — Voice block expanded.** Replaced 1-line "Direct, terse" stub with full develop-orchestrator voice rules. Lead-with-the-point, concrete file/line/AC-id refs, AI-vocab blocklist (`delve`, `crucial`, etc.), no-em-dashes rule, builder-to-builder framing, paired Good/Bad example using a develop-orchestration scenario.
- **AC-002 — `## Confusion Protocol` (new section).** STOP rule for high-stakes implementation ambiguity (blast radius >5 files, 3-strike hypothesis exhaustion, T2 vs T3 test-failure ambiguity, Phase 2 EUREKA flagging PLAN.md as wrong, Phase 5 scope creep mid-fix-loop). Cross-references `plan/decision-principles.md` § AskUserQuestion Format.
- **AC-003 — `## Context Health` (new section).** Soft directive — `[PROGRESS]` checkpoint summaries at phase boundaries when phases run >5 min (Phase 3 + Phase 4.5-4.8 are the longest); loop detection (3 occurrences of the same fix-cycle pattern → STOP and reassess via premise re-confirm / fresh adversarial cross-model agent / user check-in). Never mutates git state.
- **AC-004 — `## Anti-shortcut clause` (new section).** Names CHECKS.yaml `passed` as evidence (not substitute for fresh runtime verification — reinforces C-04), PROGRESS.md as scope-lock (not substitute for HANDOFF.md narrative), and the May 2026 `update_checks.py` indent bug as the canonical failure-mode reference.
- **AC-005 — `## Premise Gate / User Challenge` (new section).** Replaces silent-override prose at original SKILL.md:75 with structured AskUserQuestion `[Re-ground / Simplify scope / Proceed as planned / Other]` for Phase 2 EUREKA, and `[Revert / Add to scope / Defer to new task / Other]` for Phase 5 scope-expansion. Mirrors the plan-orchestrator User Challenge gate at §5.3.

## Excluded from scope (with rationale)

- **`decision-principles.md` centralization (Tier 2).** Requires extraction from sub-files, not additive — separate task.
- **`quality-audit-pipeline.md` split (Tier 3).** Currently 504 lines, breaches C-13 weight ceiling but split is restructuring not additive.
- **Phase 8.5 Operational Self-Improvement concrete develop-tooling examples (Tier 4).** Depends on accumulated learnings; bundle with sub-file Voice cascade.
- **Sub-file Voice cascade (verification-gate.md, fix-first-pattern.md, etc.).** Voice rules cascade once parent lands; planned for Tier 4.
- **Completion Status Protocol DONE / DONE_WITH_CONCERNS / BLOCKED / NEEDS_CONTEXT (Tier 2).** Requires schema decision: how does DONE_WITH_CONCERNS map to `runtime_verdict`?
- **AskUserQuestion `D<N>/ELI10/Net line` format.** Deliberate divergence — harness uses `[Re-ground]/[Simplify]/[Recommend]/[Options]`.
- **All gstack infra.** Telemetry, Preamble, `~/.gstack/`, designer binary, comparison-board — stripped by design per `doc/harness/SPEC.md`.

## Impact

The develop-skill orchestrator is now the 6th of 10 harness skills carrying the full voice + protocol set (after plan/, plan-ceo-review/, plan-design-review/, plan-eng-review/, plan-devex-review/). Anyone running `Skill(harness:develop)` now sees orchestrator-level voice rules at the top of SKILL.md, an explicit STOP-rule contract for implementation ambiguity (Confusion Protocol), a Context Health soft directive for long phases, an Anti-shortcut clause that names artefact-substitution as the failure mode, and a structured Premise Gate / User Challenge that replaces silent-override prose. The 5 sections add ~40 lines and stay well under the C-13 weight ceiling (420 ≤ 500).

## Cross-skill alignment matrix (post-task)

| Section | plan/ orchestrator | develop/ orchestrator |
|---------|:-:|:-:|
| Voice (full) | full ✓ | full ✓ (was 1 line) |
| Anti-shortcut clause | explicit ✓ | explicit ✓ |
| Confusion Protocol | yes ✓ | yes ✓ |
| Context Health (soft directive) | yes ✓ | yes ✓ |
| Premise Gate / User Challenge | yes (§5.3) ✓ | yes (Phase 2 EUREKA + Phase 5 scope-expansion) ✓ |
| Operational Self-Improvement (concrete examples) | yes ✓ | DEFERRED (Tier 4) |
| Decision Principles (centralized doc) | yes ✓ | DEFERRED (Tier 2) |
| Completion Status Protocol | yes ✓ | DEFERRED (Tier 2) |
| Sub-file Voice cascade | yes (4 sub-skills) ✓ | DEFERRED (Tier 4) |
| AskUserQuestion Format | harness-native | harness-native (cross-refs plan/decision-principles.md) |

## Tier 1 closes — Tier 2-4 follow-ups

- **Tier 2:** `align-develop-skill-decision-principles` — centralize the 6 Decision Principles + AskUserQuestion Format + Completion Status Protocol.
- **Tier 3:** `develop-quality-audit-pipeline-split` — split `quality-audit-pipeline.md` (504 → ≤200 lines per file).
- **Tier 4:** `develop-skill-ops-self-improvement` — Phase 8.5 concrete develop-tooling friction examples + sub-file Voice cascade across all 11 sub-files.

## References

- PLAN.md: `doc/harness/tasks/TASK__align-develop-skill-with-gstack-voice-protocols/PLAN.md`
- HANDOFF.md: `doc/harness/tasks/TASK__align-develop-skill-with-gstack-voice-protocols/HANDOFF.md`
- Pattern source: `plugin/skills/plan/SKILL.md` (commit 31bc43a)
- Prior series doc: `doc/changes/2026-05-08-plan-orchestrator-gstack-alignment.md`
- Commit: `0ede784 docs(develop): align orchestrator voice + protocol blocks with plan-skill`
