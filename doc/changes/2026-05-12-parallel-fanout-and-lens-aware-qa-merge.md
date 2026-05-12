# 2026-05-12 — Parallel fanout convention + lens-aware QA merge

**Task:** TASK__speed-up-developer-qa-via-subagent-team

Four ACs land for the harness develop and run skills: a new lazy-loaded sub-file documenting Claude Code's parallel-Agent + `TeamCreate` + `Task` patterns (mirrored from oh-my-claudecode `skills/team/SKILL.md`), enforced parallel-AC executor fanout in develop Phase 3.0 at N>=3 disjoint ACs, a concrete user-facing glob list pinning Phase 7.7 dogfooder skip behavior, and a lens-aware merge in `plugin/mcp/harness_server.py` that ends the last-writer-wins race when multiple `qa-*` agents write `CRITIC__qa.md` concurrently. Models stay AS-DECLARED per the CEO premise gate — speedup comes from parallelism, not cheaper tiers.

## What changed

**plugin/skills/develop/parallel-fanout.md (NEW, 58 lines):**

- 2-line "When to load" header matching `verification-gate.md` pattern.
- Parallel Fanout Convention: names `Agent(subagent_type, model)`, `TeamCreate`, `Task(team_name, name)`, plus the spawn-all-in-one-message rule (cited to OMC `skills/team/SKILL.md:354`).
- Stage Agent Routing matrix: 9 rows mapping harness phases to agent types and model sources (Phase 3=executor sonnet, Phase 4=haiku audit, Phase 4.5-4.7=test-coverage haiku / confidence inherit / adversarial cross-model, Phase 7=qa-* per frontmatter, Phase 7 fix-loop=debugger sonnet, Phase 7.7=dogfooder). Models AS-DECLARED — links to `develop/SKILL.md` Model Routing table, does not duplicate.

**plugin/skills/develop/SKILL.md (420 → 469 lines, +49 lines, under C-13 ceiling):**

- Phase 3.0 now enforces parallel-AC fanout at N>=3 disjoint ACs with an inline 4-line copyable spawn template, file-overlap precheck on PLAN.md per-AC `**Files:**` lines, and a rollback protocol that calls `update_checks.py --status open --ac AC-NNN --note parallel-fallback` for siblings on any parallel agent failure.
- `parallel-fallback` added to the timeline event enum at SKILL.md:73 alongside `phase_start, phase_end, ac_start, ac_done, agent_spawn, agent_done, fix_cycle, blocked, resumed, finding`.
- Phase 7.7 dogfooder skip predicate replaced with a concrete user-facing glob list (`**/*.{tsx,jsx,vue,svelte,html,css,scss}`, `plugin/agents/**`, `plugin/skills/**`, `**/routes/**`, `**/api/**`, `bin/**`, `cli/**`, `README.md`, `doc/changes/**`) + an exact `git diff --name-only` intersection shell snippet returning `SKIP_DOGFOOD` or `RUN_DOGFOOD`.

**plugin/skills/run/SKILL.md (149 → 171 lines, +22 lines):**

- Phase 4 spawn template now has two variants. Single-lens (one type matches) keeps the legacy path with no `lens=` argument. Multi-lens fullstack (two or more types match) spawns ALL qa-* agents in a single assistant message AND passes `lens="<lens>"` so the MCP handler merges per-lens sections + computes worst-wins runtime_verdict.
- Inline note documents the MCP-reload caveat: the `lens` argument lands but only activates on the next Claude Code session restart per the 2026-05-08 mcp-server-reload learning.

**plugin/mcp/harness_server.py (+~36 lines):**

- New `_QA_SEVERITY` ordering: `PENDING < PASS < BLOCKED_ENV < FAIL`.
- New `_worst_verdict(current, new)` helper that returns the worse of two verdicts.
- New `_lens_merge_critic_qa(td, lens, verdict, summary, transcript)` helper: first lens writer creates `CRITIC__qa.md` with a global header and one section; subsequent lens writers append `## qa-<lens> verdict: <verdict>` sections (no truncation). `runtime_verdict` downgrades only when the new verdict is worse, never upgrades back.
- `handle_write_critic_qa` now reads optional `lens` arg. When set, routes to `_lens_merge_critic_qa`. When absent, legacy `_write_artifact` full-overwrite path is preserved exactly as before.
- `TOOL_DEFS` schema for `write_critic_qa` extended with optional `lens` property documented in the tool description.

**tests/regression/speed_up_developer_qa_via_subagent_team/test_ac_004__lens_merge.py (NEW, 5 tests):**

- `test_worst_verdict_helper` — verifies severity ordering.
- `test_first_lens_writer_creates_file` — verifies global header + section + runtime_verdict=PASS.
- `test_second_lens_appends_section` — verifies append + worst-wins downgrade (PASS→FAIL).
- `test_worst_wins_does_not_downgrade` — verifies FAIL stays FAIL when subsequent lens is PASS.
- `test_three_lens_merge_blocked_env` — verifies BLOCKED_ENV beats PASS, all three sections present.

All 5 pass via `python3 -m unittest tests.regression.speed_up_developer_qa_via_subagent_team.test_ac_004__lens_merge` (pytest unavailable in this env; stdlib unittest is sufficient evidence).

## Excluded from scope (with rationale)

- **Model tier changes** (opus → sonnet for any agent). User-rejected at the CEO premise gate. Speedup achieved through parallelism, not cheaper models.
- **Cross-model QA Voice B** (codex / gemini as adversarial QA reviewer). No precedent in OMC qa-* agents; deferred pending detection-rate measurement in a separate spike.
- **Test-suite hash cache for Phase 7 skip**. Conflicts with C-04 IRON LAW (PASS verdict must be fresh after last edit). Dropped entirely.
- **`quality-audit-pipeline.md` split** (504 lines, over C-13 ceiling). Already pending as a separate Tier 3 task — out of scope here.
- **Modifications to `qa-*.md` agent definitions, `developer.md`, or agent frontmatter**. All agents kept AS-DECLARED.
- **`update_checks.py` schema additions** (no `target_files:` field added). AC-002's file-overlap precheck operates on PLAN.md per-AC `**Files:**` lines instead — convention-based, not schema-enforced.

## Impact

The OMC ↔ harness alignment series adds an execution-shape layer (parallel-Agent fanout, lens-aware verdict merge) on top of the voice + protocol patterns landed in the prior 5 tasks. The harness can now fan out develop work where ACs are genuinely disjoint and merge QA verdicts safely when multiple lenses run concurrently. The MCP-reload caveat means the AC-004 fix is dormant until the next session restart; until then, single-lens spawns work normally and multi-lens fullstack remains advisory.

## References

- PLAN.md: `doc/harness/tasks/TASK__speed-up-developer-qa-via-subagent-team/PLAN.md`
- HANDOFF.md: `doc/harness/tasks/TASK__speed-up-developer-qa-via-subagent-team/HANDOFF.md`
- DOC_SYNC.md: `doc/harness/tasks/TASK__speed-up-developer-qa-via-subagent-team/DOC_SYNC.md`
- Pattern source: `oh-my-claudecode/skills/team/SKILL.md` (lines 99-117, 354)
- Prior gstack alignment doc: `doc/changes/2026-05-11-develop-skill-gstack-voice-protocol-alignment.md`
- MCP reload learning: `doc/harness/learnings.jsonl` 2026-05-08T08:40:34Z (mcp-server-reload pitfall)
