# Parallel Fanout

This sub-file covers Phase 3.0 / Phase 4.5 parallel-Agent fanout. Loaded when N>=3 disjoint ACs OR a fanout-enabled quality phase fires. Lazy-load only; do not pre-read from `SKILL.md`.

---

## Parallel Fanout Convention

The orchestrator uses three Claude Code primitives. Pick the smallest that fits the work; do not escalate without reason.

1. `Agent(subagent_type="...", model="...", prompt="...")` — one-shot subagent with isolated context. Default for quality phases (4.5–4.8) and per-AC executor fanout.
2. `TeamCreate({team_name, description})` — bounded multi-agent pipeline with shared task list, dependency tracking, and inter-agent `SendMessage`. Use when the work decomposes into 3+ stages with cross-stage handoffs.
3. `Task({team_name, name, subagent_type, prompt})` — spawn a worker INTO an existing team. Worker reads `TaskList` to claim, calls `TaskUpdate` to complete, reports via `SendMessage` to `team-lead`.

The **spawn-all-in-one-message** rule (borrowed from `oh-my-claudecode/skills/team/SKILL.md:354`):

> Spawn all parallel teammates in a single assistant message. Do NOT wait for one to finish before spawning the next. The Claude Code runtime treats parallel tool calls in the same message as concurrent; sequential messages serialize the spawn.

Concretely:
- Issue every parallel `Agent(...)` call as a separate tool-use block in one assistant turn.
- Collect every return value before mutating shared state (PROGRESS.md, CHECKS.yaml).
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

Cap parallel fanout at N=4 in a single batch. Past N=4, orchestrator-side merge cost (PROGRESS.md write contention, CHECKS.yaml update ordering) dominates the spawn-time savings.

## Stage Agent Routing

Harness phases map to specific agent types and model tiers. Models stay AS-DECLARED in agent frontmatter — this matrix is a routing convention, not a model override. See `SKILL.md` § Model Routing (lines 60–67) for the per-work tier choice; do not duplicate it here.

| Phase | Agent type | Model source | Notes |
|-------|------------|--------------|-------|
| 3 (per-AC implement) | `oh-my-claudecode:executor` | inherit (sonnet) | One Agent per AC for parallel batches; one inline call for sequential ACs |
| 4 (plan-completion audit) | `oh-my-claudecode:executor` | haiku | Mechanical AC vs `git diff --stat` cross-reference |
| 4.5 (test coverage) | `oh-my-claudecode:executor` | haiku | Coverage diagram + anti-pattern scan |
| 4.6 (confidence ratings) | `oh-my-claudecode:executor` | inherit | Per-change risk scoring |
| 4.7 (adversarial review) | `oh-my-claudecode:executor` | cross-model (Opus→Sonnet, Sonnet→Haiku) | Different-model blind-spot reset |
| 4.8 (edge-case scan) | `oh-my-claudecode:executor` | haiku | Pattern scan for null guards, async error paths |
| 7 (verification gate) | `harness:qa-cli` / `qa-api` / `qa-browser` / `qa-desktop` | per agent frontmatter | One lens per task by default; multi-lens uses lens-aware merge |
| 7 fix loop (type / build errors) | `oh-my-claudecode:debugger` | sonnet | Compilation + regression isolation |
| 7.7 (dogfooder, post-PASS) | `harness:dogfooder` | per frontmatter | Skipped when no user-facing diff (see SKILL.md Phase 7.7) |

The OMC source for this pattern lives at `/tmp/omc-research/skills/team/SKILL.md:99-117`; harness adapts the stage→agent mapping but keeps the model-tier discipline AS-DECLARED.

## Failure-mode recap

Documented in `SKILL.md` Phase 3.0 (rollback protocol) and in `quality-audit-pipeline.md` (atomic-write pattern for parallel audit results). The conventions above assume both are honored — this sub-file does NOT restate them.
