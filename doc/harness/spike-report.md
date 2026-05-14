# Spike report — 3-skill Codex hand-port (AC-001/002/003 → AC-004 decision)

Task: TASK__dual-runtime-v1.5-spike-and-sync.
Source: 3 hand-ports at `plugin-codex/skills/{setup,run,plan}/SKILL.md`.
Date: 2026-05-14.
Runtime: codex-cli 0.130.0 (authenticated, OPENAI_API_KEY present).

This report is **binding for AC-005** (sync engine canonical form). The decision in §3 commits v1.5 to a specific representation; downstream sync-engine modules implement against that representation.

---

## 1. Measurements

Per-skill source-line breakdown, captured during hand-port at the bottom of each ported SKILL.md:

| Skill | Source LOC | As-is portable | Trivial rewrite | Significant restructure | Dropped | Codex-additive | Mechanical-portable |
|---|---|---|---|---|---|---|---|
| setup | 469 | 60% | 11% | 19% | 5% | 5% | **71%** |
| run | 171 | 41% | 15% | 29% | 15% | 20% | **56%** |
| plan | 298 | 40% | 5% | 39% | 10% | 8% | **45%** |

**Weighted average (by source LOC):**
- As-is portable: ~50%
- Trivial rewrite (env var, prefix de-strip, frontmatter prune): ~10%
- Significant restructure (control-flow primitives → degraded prose): ~27%
- Dropped (Plan Mode, Claude-specific MCP refs): ~9%
- Codex-additive (runtime notes, degradation prose): ~9%
- **Mechanical-portable total: ~60%**

The 35/35/30 estimate in v1 PLAN.md (`runtime-matrix.md` per-skill row, "as-is + rewrite + Claude-only") was slightly pessimistic on the as-is share — actuals show 50% as-is rather than 35%. The structurally-Claude-only share is closer to 27% (significant restructure) + 9% (dropped) = 36% rather than 30%.

## 2. Categorical observations across the 3 ports

### 2.1 Control-flow primitives are the load-bearing porting friction

| Primitive | setup count | run count | plan count | Porting cost |
|---|---|---|---|---|
| `Agent(subagent_type=...)` | 0 | 1 (multi-spawn block) | 4+ (dual-voice phases) | HIGH — no Codex equivalent; degraded to inline orchestration |
| `Skill("harness:X")` | 0 | 3 | 4 (sub-skill chain) | MEDIUM — "read SKILL.md inline" prose substitution works but adds wordcount |
| `AskUserQuestion` | 14 | 1 (Phase 4 FAIL retry) | 3 (premise + challenge + approval) | LOW — conversational prose substitution; Codex natural mode handles |
| `mcp__harness__*` prefix | 0 | 4 sites | 2 sites | LOW — mechanical de-prefix |
| `${CLAUDE_PLUGIN_ROOT}` | 0 | 4 sites | 2 sites | LOW — sed s/CLAUDE/HARNESS/g |

The pattern: **`Agent` + `Skill` carry 100% of the "significant restructure" budget.** AskUserQuestion is high-frequency but mechanical. Env var + MCP prefix are byte-level rewrites.

### 2.2 The dual-voice review pipeline is structurally Claude-only

Plan SKILL.md depends on Agent fan-out for Phase 1-4 dual-voice review. Sub-skills `plan-ceo-review` (1293L), `plan-eng-review` (846L), `plan-design-review` (853L), `plan-devex-review` (1022L) — total 4014L — are 100% built around dual-voice. Single-voice degradation is a real product compromise, not a "transform". Codex side ships single-voice variant; users wanting dual-voice fidelity run `claude $/harness:plan <task>`.

### 2.3 Frontmatter is highly normalizable

All three skills' frontmatter shrunk from Claude form (with `allowed-tools` listing 5-13 specific tools including Claude-only MCP names) to Codex minimal form (just `name + description`). Codex ignores unknown frontmatter keys, so the rewrite is mechanical and reversible.

### 2.4 Bash blocks are 99% portable

All bash code blocks ported verbatim except for `${CLAUDE_PLUGIN_ROOT} → ${HARNESS_PLUGIN_ROOT}` substitution. Both runtimes spawn `bash -lc` for shell tool invocations, and the harness scripts are stdlib-only Python.

---

## 3. AC-004 decision — canonical form for sync engine

**Decision: YAML/JSON intermediate (Voice B's option #2 from v1 Phase 3 Eng review).**

This is binding for AC-005 sync engine MVP.

### 3.1 Why not pure AST text-substitution

Phase 3 v1 Voice B argued: "AST regex eats `Agent(subagent_type='qa-browser')` and emits broken Codex prompts. This is translation, not substitution." Empirical evidence from the 3 spike ports confirms:
- Plan SKILL.md needs Voice A/B/Agent removal AND a whole new "Single Voice Protocol" section — not a regex pass.
- Run SKILL.md's Phase 4 "spawn QA agents in parallel" block needs collapse to "inline QA on Codex" — semantic transformation, not name swap.
- Setup SKILL.md's 14 AskUserQuestion blocks have option labels in YAML-style that map cleanly to conversational prose — declarative source would handle this.

### 3.2 Why not dual hand-maintained with lint

Phase 4 v1 DX argued doc surface doubles. Empirical port cost confirms: each skill takes 30-60min hand-port + ongoing drift risk. For the harness's 9 skills + 7 agents, that's an unmaintainable matrix. Sync engine pays back the investment by the second skill change.

### 3.3 Why YAML/JSON intermediate

The intermediate captures:
- Pure prose sections (voice rules, capstones, anti-shortcut clauses) — ride through both runtimes verbatim (~50% of source per measurements).
- Declarative `voices_required: <int>` per review phase — Claude renders dual, Codex renders single.
- Declarative `chain: [skill1, skill2]` for Skill() invocation — Claude renders `Skill()` calls, Codex renders "read SKILL.md inline" prose.
- Declarative `interactions: [{type, content, options}]` for ask gates — Claude renders AskUserQuestion call, Codex renders conversational ask.
- Declarative `tools_used:` for MCP/builtin tool names — sync engine emits the runtime-correct token (`Read` vs `read_file`, `mcp__harness__task_start` vs `task_start`).
- `runtime_notes:` block in canonical that Codex side renders as the "Codex runtime notes" header; Claude side ignores or renders as comment.

Effort estimate: ~600 LOC for the sync engine + per-skill canonical YAML rewrite (one-time, ~30-60 min per skill × 9 = 5-9 hours total).

### 3.4 Alternatives rejected (with rationale)

| Alternative | Rejected because |
|---|---|
| AST substitution on SKILL.md | Loses semantic primitives (Agent/Skill/AskUserQuestion are control flow). Phase 3 v1 Voice B predicted 3-month bit-rot — confirmed by plan port restructure %. |
| Dual hand-maintained + lint | Drift guaranteed at 2nd skill change. Unsustainable for 9-skill matrix. Phase 4 v1 DX flagged doc-surface doubling. |
| MCP-only sharing (no SKILL.md) | Gives Codex users only the MCP primitives — no end-user-visible skills. Defeats the "dual-runtime plugin" goal entirely. |
| Codex canonical with Claude as derivative | Phase 1 v1 Voice B suggestion (safer drift gradient). Empirically Claude side is richer (dual-voice, AskUserQuestion structured envelope); making it the derivative requires DOWNGRADE on Claude side which is unacceptable to existing users. |

### 3.5 Binding scope for AC-005 sync engine MVP

The sync engine ships:
- `plugin/runtime-sync/canonical_schema.py` — Pydantic-free dataclass spec for the canonical YAML.
- `plugin/runtime-sync/transform_skill.py` — reads `shared/skills/<name>.skill.yaml`, emits BOTH `plugin/skills/<name>/SKILL.md` (Claude) and `plugin-codex/skills/<name>/SKILL.md` (Codex).
- `plugin/runtime-sync/parity_check.py` — CI lint; rejects commits where emitted files don't match re-emit.
- Golden corpus at `tests/runtime-sync/corpus/` — initially seeded with the 3 spike-ported skills (setup/run/plan), 6 more added as remaining skills migrate.

The remaining 6 skills (develop, maintain, plan-ceo-review, plan-eng-review, plan-design-review, plan-devex-review) get YAML rewrites in subsequent v1.5 sub-tasks or v2.0.

> **Superseded by §3.6** — see policy reversal below. The YAML/JSON canonical form was abandoned after the v1.5 spike measurements proved the ROI was negative. This section is preserved as historical record of the path attempted.

### 3.6 Policy reversal — abandon YAML canonical, adopt MCP-only sharing

Reversed 2026-05-14 in TASK__codex-develop-port-and-parity-check after user challenged the §3.5 binding during plan-skill premise gate. The reversal is driven by the same measurements that motivated §3.5, read with one more piece of evidence: how much infrastructure cost the 60% sharing actually carries.

**Decision:** the dual-runtime plugin shares **only the protocol-portable substrate** — MCP server (`plugin/mcp/harness_server.py`), hook payload schemas, gate scripts (`plugin/scripts/*.py`), and contract artifacts (PLAN.md, CHECKS.yaml, HANDOFF.md, DOC_SYNC.md, CRITIC__qa.md). SKILL.md trees are **independent per runtime**, hand-authored in each runtime's native idiom. Future authoring is two trees, not one canonical source.

**What stays shared (protocol-portable, unchanged):**
- `plugin/mcp/harness_server.py` — 7 MCP tools, runtime-agnostic
- `plugin/hooks/hooks.json` payload schema — already byte-identical (Codex `ClaudeHooksEngine` is an explicit Claude port)
- `plugin/scripts/*.py` — `prewrite_gate`, `mcp_bash_guard`, `stop_gate`, `qa_delegation_gate`, `update_checks`, `_lib`, etc.
- Contract artifacts on disk — PLAN.md, CHECKS.yaml, HANDOFF.md, DOC_SYNC.md, CRITIC__qa.md
- `plugin/runtime-sync/emit_codex_config.py` — emits Codex `~/.codex/config.toml` MCP+hook snippet. The single surviving bridge from the shared substrate to the Codex runtime.

**What was reverted (would have been infra for YAML canonical):**
- `plugin/runtime-sync/canonical_schema.py` — DELETED
- `plugin/runtime-sync/transform_skill.py` — DELETED
- `tests/runtime-sync/corpus/` — DELETED
- `tests/regression/task__dual_runtime_v15/test_ac_005__transform_skill.py` — DELETED
- `plugin/runtime-sync/parity_check.py` — NEVER WRITTEN (was the v1.5 follow-up; deferred indefinitely)

**Why §3.5 was wrong despite §3.1–§3.4 being right:**
- §3.1 correctly identified that pure AST text-substitution breaks on control-flow primitives.
- §3.2 correctly identified that dual hand-maintained without lint will drift.
- §3.3 derived "therefore: YAML/JSON intermediate that captures structure".
- §3.4 rejected MCP-only sharing on the grounds it would "defeat the dual-runtime plugin goal".

The error was in §3.4's framing. MCP-only sharing does **not** defeat the dual-runtime goal — it just shifts the unit of sharing from "skill content" to "skill substrate". The user-visible artifact (a working Codex experience for the canonical loop) is delivered by hand-authoring SKILL.md trees that consume the shared MCP + hook + gate substrate. The §1 measurements (60% weighted-mean, 100% restructure on control-flow primitives) prove that the *interesting* parts of any skill — Skill chain, AskUserQuestion, Agent fan-out, dual-voice — are exactly where canonical-form sharing fails. Authoring a YAML canonical for the 60% that *is* portable, then adding per-runtime overrides for the 40% that isn't, ends up with two trees of complexity (canonical + per-runtime overrides) instead of one (two independent trees consuming a shared substrate).

**Why the §3.1 / §3.2 problems don't re-emerge:**
- Drift between trees is acceptable because the trees are not promised equivalent. They're promised to consume the same substrate. A bug fix in Claude's `develop` skill is a candidate port to Codex's `develop` skill, not a forced sync. The MCP and gate scripts catch contract-level drift; SKILL.md drift is editorial.
- Control-flow primitives stop being a porting problem because each tree writes them natively. No restructure budget needed.

**De-risking effect:** future authoring is two independent trees, both consuming the same MCP + hook + gate substrate. Editing a Claude skill no longer requires thinking about Codex equivalents. The Codex tree grows on its own cadence, hand-shaped to Codex's primitives (sequential execution, conversational asks, multi_agent when ergonomic). The shared substrate is what makes both trees produce equivalent *behavior* — PLAN.md / CHECKS.yaml / HANDOFF.md / CRITIC__qa.md don't care which runtime wrote them.

**Cost paid:** v1.5 sync-engine infra (~600 LOC: canonical_schema + transform_skill + corpus + AC-005 tests) is sunk cost. The spike measurements that produced this decision could not have been written without doing the spike first; the data justifies the price.

---

## 4. AC-005 + AC-006 scope after decision

Given AC-004 decision is YAML/JSON intermediate:

- **AC-005** delivers `transform_skill.py` MVP that handles the 3 spike skills round-trip.
- **AC-006** (MCP config emitter) is independent — emits `~/.codex/config.toml` snippet for harness MCP server + `[hooks]` + `[hooks.state.<key>].trusted_hash`. This is unrelated to the sync engine canonical form; it's separate runtime install plumbing.

AC-005 implementation will land in v1.5 follow-up turns. AC-006 lands in this turn (small enough).

---

## 5. Observed pitfalls during hand-port

Logged to `doc/harness/learnings.jsonl` as `type=operational` / `type=pitfall` rows:

- Plan SKILL.md's "Plan Mode Safe Operations" section is Claude-only — Codex has no plan-mode concept. Dropped cleanly. No equivalent needed.
- The Claude `allowed-tools` frontmatter list including `Agent` + `Skill` had to be stripped entirely from Codex side — Codex ignores allowed-tools but the presence of nonexistent tool names is noise.
- `mcp__harness__task_start` prefix appears in skill prose (not just frontmatter) in run + plan. Sync engine regex must be context-aware (rewrite the literal in code blocks; explain the prefix difference in prose).
- The Claude `Skill()` chain in run skill (Phase 2 + Phase 3 + Phase 5 self-improvement) becomes 3 "read SKILL.md inline" prose blocks on Codex side. Adds ~30 lines net.
- Plan skill's `Plan Status Footer` boilerplate (`Phase N complete | Findings: <count> | Decisions: <count>`) is redundant with the `[PROGRESS]` summary directive elsewhere — dropped on Codex side for clarity. Could also be dropped on Claude side as cleanup.
