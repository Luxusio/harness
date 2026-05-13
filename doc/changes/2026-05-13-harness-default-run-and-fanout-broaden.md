# 2026-05-13 — harness default = `Skill(harness:run)`; parallel fanout from N≥2

## What changed for users

Two related changes so harness stops asking which flow to use and starts using parallel agents more often.

**Routing.** Saying "make this feature", "fix this bug", "refactor X" no longer prompts a "do you want plan-only / run / direct edit" question. The harness goes straight to `Skill(harness:run)` (the full plan → develop → verify → close cycle). Narrower flows (`harness:plan`, `harness:develop`) fire only when you explicitly name them ("plan only", "implement PLAN.md").

**Parallel fanout.** The develop skill's Phase 3.0 now spawns parallel sub-agents when PLAN.md has 2 or more component-independent ACs (was: 3 or more disjoint ACs). New explicit triggers:
- API ↔ frontend changes in one task → contract-first sequential prelude, then parallel consumers.
- Helper-extract pattern → run the extract AC first, then parallel-fanout the consumers (guard: the extract must already be a declared AC in PLAN, not "while I'm here" creep).
- Phase 7 multi-lens QA (browser + api + cli + desktop as applicable) → all spawned in one assistant message with `lens="<lens>"` for lens-aware merge.
- Phase 7.7 dogfooder → batches with the Phase 7 final-PASS-cycle QA spawn.
- Phase 4.5-4.8 quality audit → all 4 calls in one message.

The 4-agent cap is unchanged. Every fanout decision logs one row to `learnings.jsonl` with `type:"parallel-trigger"` so the 6-month retro can verify the threshold earns its keep.

## What didn't change

- The OMC `/team` skill is referenced but not ported into harness — `TeamCreate` / `Task` primitives are used as-is.
- The `Skill(harness:plan)` and `Skill(harness:develop)` routes still exist; they just aren't the default anymore.
- The 4-agent cap stays at 4 per batch (broader triggers produce more batches, not larger ones).

## What to do if a regression happens

- Routing question reappears: re-check `CLAUDE.md` "Harness routing" section + `plugin/CLAUDE.md` §6 — the `Default = Skill(harness:run)` directive must be at the top of both.
- Parallel fanout fires too often: read `doc/harness/learnings.jsonl | grep 'type":"parallel-trigger'` for trigger distribution. Threshold tuning is a follow-up task.
- Helper-extract trigger fires without a declared AC: that's the guard misfiring. Open a bug — the guard at `plugin/skills/develop/SKILL.md` Phase 3.0 should block it.

## Follow-up

- Encode the trigger as a script (`should_fanout.py`) instead of prose. Prose rules rot; the Audit hook makes drift visible but doesn't prevent it.
- Plan-skill should auto-set `maintenance_task: true` when ACs target `plugin/CLAUDE.md` or `plugin/skills/*/SKILL.md` — this task had to add the `MAINTENANCE` marker mid-develop.
- `omc ask codex` died with `mise ERROR: Argument list too long` during the plan phase. Cross-model Voice B unavailable for this plan. Logged 2026-05-13T13:03Z.
