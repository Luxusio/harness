# Parallel Fanout

This sub-file covers Phase 3.0 / Phase 4.5 / Phase 6.6 / Phase 7 / Phase 7.7 parallel-Agent fanout. Loaded when N>=2 component-independent ACs OR a fanout-enabled quality / verification / dogfood phase fires. Lazy-load only; do not pre-read from `SKILL.md`.

---

## Parallel Fanout Convention

The orchestrator uses three Claude Code primitives. Pick the smallest primitive
type that fits the work; do not reduce worker count for independent ACs.
Parallel is the default posture. Mandatory parallel delegation is
capability/task-shape based: when `Agent(...)` is available and the work has
independent lanes, spawn one worker per lane. This is mandatory
capability/task-shape routing; the user does not need to request delegation.
"User did not ask for delegation" is an invalid skip rationale. Sequential
execution needs a declared dependency, unavailable Agent tool, or the narrow
small-task exception below, and any sequential fallback after a matched fanout
trigger must be stated in the lane table before editing.
Do not wait for the user to request delegation. User request is not a condition
for parallel routing.

1. `Agent(subagent_type="...", model="...", prompt="...")` — one-shot subagent with isolated context. Default for the Phase 4.5 audit inputs, Phase 6.6 review lenses, per-AC worker fanout, multi-lens QA, and dogfooder. Use `harness:ac-worker` for Phase 3 per-AC implementation.
2. `TeamCreate({team_name, description})` — bounded multi-agent pipeline with shared task list, dependency tracking, and inter-agent `SendMessage`. Use when the work decomposes into 3+ stages with cross-stage handoffs.
3. `Task({team_name, name, subagent_type, prompt})` — spawn a worker INTO an existing team. Worker reads `TaskList` to claim, calls `TaskUpdate` to complete, reports via `SendMessage` to `team-lead`.

The **spawn-all-in-one-message** rule (borrowed from `oh-my-claudecode/skills/team/SKILL.md:354`):

> Spawn all parallel teammates in a single assistant message. Do NOT wait for one to finish before spawning the next. The Claude Code runtime treats parallel tool calls in the same message as concurrent; sequential messages serialize the spawn.

**Scope of the rule.** This rule applies to every independent agent-call group, not just Phase 3.0 AC fanout:

- Phase 3.0 AC parallel batches (per the Parallelization Triggers table below)
- Phase 4.5 pre-review audit inputs (coverage trace, visual smoke when browser-enabled, plus any conditional specialist matched in `quality-audit-pipeline.md` § Phase 4.5 — all in one message)
- Phase 6.6 independent review: batch only dependency-free calls. Selected
  defect hunters and `review-security` form the initial batch; `review-code`
  depends on attempted hunter finals at STANDARD/DEEP and starts afterward.
  At LIGHT, `review-code` and `review-security` may start together.
- Phase 7 multi-lens QA (`qa-browser` + `qa-api` + `qa-cli` + `qa-desktop` as applicable, each with `lens="<lens>"`)
- Phase 7.7 dogfooder, batched with the Phase 7 final-PASS-cycle QA spawn

Whenever two or more verification, judgment, or executor calls have no dependency between them, issue them in a single assistant message.

Concretely:
- Issue every parallel `Agent(...)` call as a separate tool-use block in one assistant turn.
- Collect every return value before mutating shared state (PROGRESS.md).
  Executors return status, changed paths, and blockers in their final response;
  the coordinator is the only writer to PROGRESS.md.
- A `TeamCreate` + N `Task` worker spawns: emit `TeamCreate` in turn 1; emit all N `Task` calls in turn 2 — never split worker spawns across multiple turns.

**Inline spawn template** (copyable):

```
# Issue these N Agent calls in ONE assistant message.
# Lane identity goes in the prompt, never in name= — see below.
Agent(subagent_type="harness:ac-worker",
      prompt="Lane <task_id>:AC-001. Implement AC-001 per PLAN.md ...")
Agent(subagent_type="harness:ac-worker",
      prompt="Lane <task_id>:AC-002. Implement AC-002 per PLAN.md ...")
Agent(subagent_type="harness:ac-worker",
      prompt="Lane <task_id>:AC-NNN. Implement AC-NNN per PLAN.md ...")
```

### Never pass `name=` to a spawned agent

Not just to lens agents. `name=` is **forbidden** for any agent whose receipt a
lens depends on — `harness:code-reviewer`, `harness:security-reviewer`, every
`qa-*` — because it loses the receipt outright. But it is also wrong for
lens-less lanes like `harness:ac-worker`, for a second reason: the lifecycle
cannot distinguish "named lane that legitimately owes nothing" from "named lens
agent whose type was destroyed", so a named AC worker writes a false
`named-spawn-shadows-agent-type` breadcrumb on every spawn. A 4-worker batch
writes four, which buries the signal the breadcrumb exists to carry.

The CLI puts the display name in the `agentType` position and drops the resolved
type from the payload entirely, so the lifecycle hook cannot infer the lens and
writes no receipt. Measured 2026-09-10, same session and same agent type: three
named spawns produced 0 receipt rows, one unnamed spawn produced its receipt
immediately. The review runs correctly and `task_close` still refuses, because
there is no evidence it ran.

Names that happen to encode the lens (`qa-cli-1` → `qa-cli`) survive, which is
why this failed intermittently rather than obviously. Do not rely on it.

```
# WRONG — the receipt is lost, PASS becomes unreachable
Agent(name="review-scoring", subagent_type="harness:code-reviewer", prompt=...)

# RIGHT — distinguish lanes in the prompt, not the name
Agent(subagent_type="harness:code-reviewer", prompt="Lane: scoring. ...")
```

An unnamed lens-less spawn is silent by design. A *named* lens-less spawn leaves
a `background_hook:binding-miss` entry in `doc/harness/learnings.jsonl` with
reason `named-spawn-shadows-agent-type` — up to one per lifecycle event, so
start and stop can each contribute. Grep for it when a lens ran but its receipt
never appeared; if the breadcrumb names a lane rather than a lens agent, the
spawn site is passing `name=` and should stop. See
`doc/harness/REQ__runtime-surfaces-name-the-actual-blocker.md`.

---

Cap parallel fanout at N=4 in a single batch. Past N=4, orchestrator-side PROGRESS.md merge cost dominates the spawn-time savings. **The cap applies per batch, not per task** — broader trigger thresholds produce more batches, each still capped at 4.
For N>4, spawn batches of up to 4 in successive assistant turns; do not
collapse remaining independent ACs into coordinator work.
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
| Quality audit fanout (Phase 4.5) | Quality audit pipeline runs: coverage trace + visual smoke (browser-only) | One assistant message; conditional specialists (security / perf / migration / LLM-trust) added when diff scope matches, per `quality-audit-pipeline.md` § Phase 4.5. Advisory inputs, not verdicts |
| Independent review fanout (Phase 6.6) | Selected discovery or `task_context` supplies 2 or more dependency-free calls | Initial batch: selected defect hunters plus routed `review-security`. Start dependent `review-code` only after attempted hunter finals; at LIGHT it may batch with security. Every required lens must PASS before Phase 7 QA starts. |

### Extra review breadth under a single declared lens (advisory)

Advisory, not a fanout trigger. The MUST rows above stay keyed to declared
lenses, because a second agent of a review type is not free: it writes a receipt.

When one review lens is declared but the diff has independent concern areas —
probe logic versus filesystem isolation versus measured doc claims are three
different jobs — extra breadth is worth buying, and running the areas as
successive rounds costs wall-clock without adding coverage. Diversity is the
real gain: in the round that produced this section, the agent that found a false
prose claim was not the one assigned to look for one, and the one assigned to
look for it reported it had searched and found nothing.

**Buy that breadth with agent types that produce no lens.** Any agent type whose
name tokenises to a {code, review} or {security, review} pair — `code-reviewer`
in every namespace — is assigned `review-code` or `review-security` by
`_infer_receipt_lens` and writes a receipt for it. Spawning several is not a
coordinator preference the runtime honors; it is several verdicts on one lens,
and the receipt layer keeps one:

- `_completed_review_by_lens` keys by lens, and `_effective_completion` returns
  the last readable completion. It was written for *reruns*, where newer
  genuinely supersedes older, and has no concept of concurrent siblings — so a
  sibling's `FAIL` is superseded by a later sibling's `PASS`, decided by
  wall-clock order.
- `_rerun_in_flight` tests only whether the *newest* event is a `started`, so a
  lens can be judged on the first completion while its siblings still run.

The lens vocabulary is closed (`review-code`, `review-security`) and
`_task_control_lenses` rejects duplicates, so declaring one lens per concern
area is not a workaround either. Exactly one receipt-producing review agent per
declared lens; give the additional concern areas to types that yield no lens
(`critic-document`, a general-purpose agent, `harness:documentation-review`),
read their findings yourself, and treat any FAIL they report as blocking
regardless of what the scoring lens said. A `PASS` that could be an artifact of
completion order is not a PASS.

### Re-review after remediation

After an affecting source edit, recompute review depth with the prior live
depth as a floor, rerun every fresh discovery attempt selected by that result,
then start one fresh formal reviewer. Do not reuse a previously clean hunter:
its evidence describes pre-edit source. Batch only the newly selected,
dependency-free discovery/security calls; the formal reviewer remains
dependent on attempted hunter finals.

### Component-independent definition

Two ACs are **component-independent** iff one of the following holds:

1. Their PLAN target file sets are disjoint (no shared file path).
2. Any shared file is factored into a dedicated helper-extract AC that runs first (sequential prelude → parallel consumers).

Component-independence is a property of the PLAN AC matrix, not of the diff. The orchestrator computes it from `**Files:**` declarations in PLAN.md, not from `git diff --name-only`. If the matrix is ambiguous, write an explicit dependency matrix artifact and flag the ambiguity as a Plan Challenge for the next plan cycle. Then run sequentially by declared dependency. Do not assign multiple possibly-independent ACs to one executor as a silent fallback.

### Small-task edge case

If the matrix says fanout but total edit volume is trivial (<10 lines combined,
~15s total work), sequential is acceptable only with a concrete lane-table
reason: AC ids, estimated lines, estimated runtime, and `reason:"small-task"`.
The default is still parallel. If the user explicitly asks for aggressive
subagent use or faster parallel execution, this opt-out is disabled.

### Invalid skip rationales

User-request-based routing is forbidden. The fanout decision comes from runtime
capability plus task shape: available `Agent(...)`, independent ACs, applicable
QA lenses, or independent audit workers. Do not use `user-did-not-ask`, "user
did not ask for delegation", "not requested", or equivalent wording to justify
inline execution. If the trigger matches and the run goes sequential, record
the concrete blocker in the lane table: declared dependency, unavailable Agent
tool, or small-task estimate.

### Lane table requirement

Before implementation, emit this table and use it as the routing contract:

| AC | Files | Depends on | Lane | Route | Reason |
|----|-------|------------|------|-------|--------|

`Route` is `Agent(...)`, `sequential-prelude`, `sequential-dependent`, or
`sequential-small-task`. Two or more independent `Agent(...)` rows trigger one
parallel spawn batch. `sequential-small-task` requires lane-table values:
`reason:"small-task"`, `estimated_lines`, and `estimated_seconds`.

---

## Stage Agent Routing

Harness phases map to specific agent types and model tiers. Models stay AS-DECLARED in agent frontmatter — this matrix is a routing convention, not a model override. See `SKILL.md` § Model Routing for the per-work tier choice; do not duplicate it here.

| Phase | Agent type | Model source | Notes |
|-------|------------|--------------|-------|
| 3 (per-AC implement) | `harness:ac-worker` | inherit (sonnet) | One Agent per AC for parallel batches; one inline call for sequential ACs only when dependency-bound |
| 4 (plan-completion audit) | `oh-my-claudecode:executor` | haiku | Mechanical AC vs `git diff --stat` cross-reference |
| 4.5 (pre-review audit inputs) | `oh-my-claudecode:executor` | haiku | Coverage trace, visual smoke, and the conditional migration/contract, LLM-trust, and performance specialists listed in `quality-audit-pipeline.md` § Phase 4.5. Advisory inputs, not verdicts |
| 6.6 (independent review) | `harness:code-reviewer` / `harness:security-reviewer` | per agent frontmatter | Routed from `task_context`; selected hunters/security batch first, then dependent code review. At LIGHT code/security may batch. Must PASS before Phase 7 QA. |
| 7 (verification gate) | `harness:qa-cli` / `qa-api` / `qa-browser` / `qa-desktop` | per agent frontmatter | Spawn every applicable lens in one message with `lens="<lens>"` for lens-aware merge |
| 7 fix loop (type / build errors) | `oh-my-claudecode:debugger` | sonnet | Compilation + regression isolation |
| 7.7 (dogfooder, post-PASS) | `harness:dogfooder` | per frontmatter | Routed from PLAN.md-declared user-facing surfaces or explicit `dogfood_required`; never inferred from Git. Batches with Phase 7 QA spawn. |

The OMC source for this pattern lives at `/tmp/omc-research/skills/team/SKILL.md:99-117`; harness adapts the stage→agent mapping but keeps the model-tier discipline AS-DECLARED.

---

## Learning capture

Do not log per-call routing history. `learnings.jsonl` is for reusable facts,
surprising discoveries, user corrections, and repeated friction, not usage stats
that a fanout trigger fired or was skipped. If fanout behavior reveals a durable
threshold problem, capture the smallest reusable lesson with observed impact and
promote it to a committed skill, test, or pattern doc before close.

Never accept a user-request reason for skipping delegation. Whether the user
asked for delegation is not evidence.

---

## Failure-mode recap

Documented in `SKILL.md` Phase 3.0 (rollback protocol) and in `quality-audit-pipeline.md` (parallel audit agents return structured final responses). The conventions above assume both are honored — this sub-file does NOT restate them.
