# Parallel Fanout

This sub-file covers Phase 3.0 / Phase 4.5-4.8 / Phase 7 / Phase 7.7 parallel-Agent fanout. Loaded when N>=2 component-independent ACs OR a fanout-enabled quality / verification / dogfood phase fires. Lazy-load only; do not pre-read from `SKILL.md`.

---

## Parallel Fanout Convention

The orchestrator uses three Claude Code primitives. Pick the smallest primitive
type that fits the work; do not reduce worker count for independent ACs.

1. `Agent(subagent_type="...", model="...", prompt="...")` — one-shot subagent with isolated context. Default for quality phases (4.5–4.8), per-AC executor fanout, multi-lens QA, and dogfooder.
2. `TeamCreate({team_name, description})` — bounded multi-agent pipeline with shared task list, dependency tracking, and inter-agent `SendMessage`. Use when the work decomposes into 3+ stages with cross-stage handoffs.
3. `Task({team_name, name, subagent_type, prompt})` — spawn a worker INTO an existing team. Worker reads `TaskList` to claim, calls `TaskUpdate` to complete, reports via `SendMessage` to `team-lead`.

The **spawn-all-in-one-message** rule (borrowed from `oh-my-claudecode/skills/team/SKILL.md:354`):

> Spawn all parallel teammates in a single assistant message. Do NOT wait for one to finish before spawning the next. The Claude Code runtime treats parallel tool calls in the same message as concurrent; sequential messages serialize the spawn.

**Scope of the rule.** This rule applies to every independent agent-call group, not just Phase 3.0 AC fanout:

- Phase 3.0 AC parallel batches (per the Parallelization Triggers table below)
- Phase 4.5–4.8 quality audit (test-coverage haiku, confidence-ratings, adversarial cross-model, visual-smoke browser-only — 4 calls in one message)
- Phase 7 multi-lens QA (`qa-browser` + `qa-api` + `qa-cli` + `qa-desktop` as applicable, each with `lens="<lens>"`)
- Phase 7.7 dogfooder, batched with the Phase 7 final-PASS-cycle QA spawn

Whenever two or more verification, judgment, or executor calls have no dependency between them, issue them in a single assistant message.

Concretely:
- Issue every parallel `Agent(...)` call as a separate tool-use block in one assistant turn.
- Collect every return value before mutating shared state (PROGRESS.md, CHECKS.yaml).
  Executors write per-AC result files under `<task_dir>/audit/`; the coordinator
  is the only writer to PROGRESS.md and CHECKS.yaml.
- A `TeamCreate` + N `Task` worker spawns: emit `TeamCreate` in turn 1; emit all N `Task` calls in turn 2 — never split worker spawns across multiple turns.

**Inline spawn template** (copyable):

```
# Issue these N Agent calls in ONE assistant message
Agent(name="<task_id>:AC-001", subagent_type="oh-my-claudecode:executor",
      prompt="Implement AC-001 per PLAN.md ...")
Agent(name="<task_id>:AC-002", subagent_type="oh-my-claudecode:executor",
      prompt="Implement AC-002 per PLAN.md ...")
Agent(name="<task_id>:AC-NNN", subagent_type="oh-my-claudecode:executor",
      prompt="Implement AC-NNN per PLAN.md ...")
```

Cap parallel fanout at N=4 in a single batch. Past N=4, orchestrator-side merge cost (PROGRESS.md write contention, CHECKS.yaml update ordering) dominates the spawn-time savings. **The cap applies per batch, not per task** — broader trigger thresholds produce more batches, each still capped at 4.
Merge cost controls batch size only. It does not justify collapsing two or more
independent ACs into one executor below the cap.

---

## Parallelization Triggers

The orchestrator MUST fanout when any row matches. PLAN.md AC dependency matrix is the single source of truth for the first three rows; `git diff --name-only` and runtime context drive the last two.

| Trigger | When | Action |
|---------|------|--------|
| Component-independent N≥2 | PLAN AC matrix has 2 or more ACs whose target file sets are pairwise disjoint | Parallel `Agent(...)` fanout, one per AC, in one assistant message |
| API↔frontend split | PLAN AC matrix declares both backend/API files (`*api*`, `*routes/*`, `*endpoint*`, `*graphql*`) AND frontend files (`*.tsx/.jsx/.vue/.svelte/.html/.css/.scss`) | Contract-first sequential prelude (API contract / shared types AC), then parallel-fanout the consumer ACs |
| Helper-extract-first | PLAN explicitly contains a helper-extraction AC; consumer ACs depend on the extracted helper | Run the extract AC sequentially first, then parallel-fanout the consumers. Guard: extract must be a declared AC in PLAN.md; mid-task extraction is scope creep blocked by Phase 5 |
| Multi-lens QA / dogfooder | Phase 7 has 2 or more applicable QA lenses (from manifest + diff scope) OR dogfooder is queued for the Phase 7 final-PASS cycle | All QA calls in one assistant message with `lens="<lens>"`; dogfooder batched alongside on the final-PASS pass. FAIL cycles skip dogfooder |
| Quality audit fanout (Phase 4.5–4.8) | Quality audit pipeline runs: test-coverage haiku + confidence-ratings + adversarial cross-model + visual-smoke (browser-only) | All 4 calls in one assistant message; conditional specialists (security / perf / migration / LLM-trust) added inline when diff scope matches |

### Component-independent definition

Two ACs are **component-independent** iff one of the following holds:

1. Their PLAN target file sets are disjoint (no shared file path).
2. Any shared file is factored into a dedicated helper-extract AC that runs first (sequential prelude → parallel consumers).

Component-independence is a property of the PLAN AC matrix, not of the diff. The orchestrator computes it from `**Files:**` declarations in PLAN.md, not from `git diff --name-only`. If the matrix is ambiguous, write an explicit dependency matrix artifact and flag the ambiguity as a Plan Challenge for the next plan cycle. Then run sequentially by declared dependency. Do not assign multiple possibly-independent ACs to one executor as a silent fallback.

### Small-task edge case

If the matrix says fanout but total edit volume is trivial (<20 lines combined,
~30s total work), sequential is acceptable only with a concrete skip record:
AC ids, estimated lines, estimated runtime, and `parallel-trigger-skipped` with
`reason:"small-task"`. The default is still parallel — sequential is the
evidenced opt-out, not the default.

### Lane table requirement

Before implementation, emit this table and use it as the routing contract:

| AC | Files | Depends on | Lane | Route | Reason |
|----|-------|------------|------|-------|--------|

`Route` is `Agent(...)`, `sequential-prelude`, `sequential-dependent`, or
`sequential-small-task`. Two or more independent `Agent(...)` rows trigger one
parallel spawn batch. `sequential-small-task` requires logged evidence:
`parallel-trigger-skipped` with `reason:"small-task"`, `estimated_lines`, and
`estimated_seconds`.

---

## Stage Agent Routing

Harness phases map to specific agent types and model tiers. Models stay AS-DECLARED in agent frontmatter — this matrix is a routing convention, not a model override. See `SKILL.md` § Model Routing for the per-work tier choice; do not duplicate it here.

| Phase | Agent type | Model source | Notes |
|-------|------------|--------------|-------|
| 3 (per-AC implement) | `oh-my-claudecode:executor` | inherit (sonnet) | One Agent per AC for parallel batches; one inline call for sequential ACs |
| 4 (plan-completion audit) | `oh-my-claudecode:executor` | haiku | Mechanical AC vs `git diff --stat` cross-reference |
| 4.5 (test coverage) | `oh-my-claudecode:executor` | haiku | Coverage diagram + anti-pattern scan |
| 4.6 (confidence ratings) | `oh-my-claudecode:executor` | inherit | Per-change risk scoring |
| 4.7 (adversarial review) | `oh-my-claudecode:executor` | cross-model (Opus→Sonnet, Sonnet→Haiku) | Different-model blind-spot reset |
| 4.8 (edge-case scan) | `oh-my-claudecode:executor` | haiku | Pattern scan for null guards, async error paths |
| 7 (verification gate) | `harness:qa-cli` / `qa-api` / `qa-browser` / `qa-desktop` | per agent frontmatter | One lens per task by default; multi-lens spawns batch in one message with `lens="<lens>"` for lens-aware merge |
| 7 fix loop (type / build errors) | `oh-my-claudecode:debugger` | sonnet | Compilation + regression isolation |
| 7.7 (dogfooder, post-PASS) | `harness:dogfooder` | per frontmatter | Skipped when no user-facing diff (see SKILL.md Phase 7.7). Batches with Phase 7 final-PASS QA spawn. User-facing detection reads `TASK_STATE.yaml touched_paths` (not `git diff HEAD~1 HEAD`) — `git diff` was unreliable when Phase 6 commits ran after Phase 7.7 or when develop emitted multiple bisectable commits |

The OMC source for this pattern lives at `/tmp/omc-research/skills/team/SKILL.md:99-117`; harness adapts the stage→agent mapping but keeps the model-tier discipline AS-DECLARED.

---

## Audit hook

Every fanout decision MUST log one row to `doc/harness/learnings.jsonl` so the rule's 6-month value is verifiable post hoc. Prose triggers rot — instrumentation is the only honest signal that the new threshold earns its keep.

```bash
_TS=$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo "unknown")
echo '{"ts":"'"$_TS"'","type":"parallel-trigger","source":"develop","trigger":"<row from Parallelization Triggers table>","ac_count":<N>,"phase":"<phase>","task":"'"<task_id>"'"}' >> doc/harness/learnings.jsonl 2>/dev/null || true
```

Also log skips:
- `parallel-trigger-skipped` with reason `small-task` for the volume edge case.
- `helper-extract-guard-fired` when the extract trigger would have fired but the extract AC was missing from PLAN.

The retro skill (and any future 6-month audit) reads these rows to decide whether the trigger threshold is still right — too many fires with low per-fire savings means narrow the threshold; too few fires means broaden further or rebalance the cap.

---

## Failure-mode recap

Documented in `SKILL.md` Phase 3.0 (rollback protocol) and in `quality-audit-pipeline.md` (atomic-write pattern for parallel audit results). The conventions above assume both are honored — this sub-file does NOT restate them.
