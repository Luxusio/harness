# 2026-04-23 — plan-skills office-hours and outside-voice polish

**Task:** TASK__plan-skills-office-hours-and-outside-voice-polish

Closes the final three items from the gstack-vs-harness plan-skill comparison.

## What changed

- **Office-hours → setup realignment.** harness does not ship a separate office-hours skill. Setup fulfills the pre-planning / scope-sharpening role via its interactive intake (project-interview.md already labels the flow "office-hours style"). Realigned references in `plugin/skills/plan/intake.md` (Phase 0.4.5) and `plugin/skills/plan-ceo-review/SKILL.md` (Handoff note check + Mid-session scope-sharpening detection). Canonical mapping now lives at `plugin/CLAUDE.md:70` so future readers find it in one place.
- **Filesystem boundary prompt framing.** The existing Voice B boundary instruction at `plugin/skills/plan/review-phases.md:22-30` is now explicitly labeled "copy-paste verbatim" with a single-source-of-truth footer pointing callers back to this section instead of re-authoring the prefix elsewhere.
- **Security + Rollback rubrics (UC1 resolved → extraction).** Created `plugin/skills/plan-eng-review/rubrics-threat-rollback.md` (37L) as an imperative checklist with hybrid 3 STRIDE + 3 harness-native security questions (audit-trail preservation, protected-artifact provenance, contract-bypass vectors) and 4 plan-level rollback questions (blast radius, schema safety, feature-flag path, data-migration reversibility). `plan-eng-review/SKILL.md` got a 3-line MUST-READ stub (+3L, well under +8 ceiling), avoiding further weight-budget ratchet on an already-820L file.

## Impact

- Anyone running the plan skill will now see setup surfaced for scope-sharpening instead of a phantom office-hours skill.
- External Voice B briefs (codex/gemini) get a cleanly copy-pasteable filesystem boundary prefix.
- plan-eng-review Architecture reviews now load an imperative STRIDE + Rollback rubric — 10 harness-grounded questions that must be answered inline, with a skip-if-N/A escape clause and explicit `/cso` defer for runtime depth.
- The plan-eng-review weight-budget ratchet stopped. Further rubric additions land in the sub-file.

## References

- PLAN.md: `doc/harness/tasks/TASK__plan-skills-office-hours-and-outside-voice-polish/PLAN.md`
- HANDOFF.md: `doc/harness/tasks/TASK__plan-skills-office-hours-and-outside-voice-polish/HANDOFF.md`
- User Challenge UC1 (rubric location): resolved toward EXTRACTION
- Commits: 231198d (backfill), 7185b07 (boundary framing), 502180c (rubric sub-file + stub), 7ccc050 (canonical routing row)
