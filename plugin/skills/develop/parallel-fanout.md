# Parallel Fanout

This sub-file covers Phase 3.0 / Phase 4.5 / Phase 6.6 / Phase 7 / Phase 7.7 parallel-Agent fanout. Loaded when N>=2 component-independent ACs OR a fanout-enabled quality / verification / dogfood phase fires, or when an AC routed to an ac-worker declares `**Tests:**` paths (test-author pairing). Lazy-load only; do not pre-read from `SKILL.md`.

---

## Parallel Fanout Convention

For development, `ready-lanes.md` and public WBS `dispatch prepare` own the
source assignment. Independent ready outcomes use managed worktrees, with
execution ownership separate from progress WBS. Host observations and concrete
serial facts replace optional small-task fallbacks. Start each finished lead's
review/QA while siblings develop; after completion refresh host inventory and
prepare the next ready lane immediately. Existing resource leases serialize
only their conflicting execution, and one coordinator integrates serially.
This takes precedence over older within-task fanout examples below.

The orchestrator uses three Claude Code primitives. Pick the smallest primitive
type that fits the work; do not reduce worker count for independent ACs.
Parallel is the default posture. Mandatory parallel delegation is
capability/task-shape based: when `Agent(...)` is available and the work has
independent lanes, spawn one worker per lane. This is mandatory
capability/task-shape routing; the user does not need to request delegation.
"User did not ask for delegation" is an invalid skip rationale. Sequential
execution needs a declared dependency, unavailable Agent tool, or actual observed host/resource capacity, and any sequential fallback after a matched fanout
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
- Collect each AC's required writer returns before promoting that AC in
  PROGRESS.md; unrelated sibling results do not block completed-lane updates.
  Executors return status, changed paths, and blockers in their final response;
  the coordinator is the only writer to PROGRESS.md.
- A `TeamCreate` + N `Task` worker spawns: emit `TeamCreate` in turn 1; emit all N `Task` calls in turn 2 — never split worker spawns across multiple turns.

**Inline spawn template** (copyable):

```
# Issue these N Agent calls in ONE assistant message.
# Give each lane clear scope and ownership in its prompt.
Agent(subagent_type="harness:ac-worker",
      prompt="Lane <task_id>:AC-001. Implement AC-001 per PLAN.md ...")
Agent(subagent_type="harness:ac-worker",
      prompt="Lane <task_id>:AC-002. Implement AC-002 per PLAN.md ...")
Agent(subagent_type="harness:ac-worker",
      prompt="Lane <task_id>:AC-NNN. Implement AC-NNN per PLAN.md ...")

# Paired example (same message): an AC declaring both **Files:** and **Tests:**.
Agent(subagent_type="harness:ac-worker",
      prompt="Lane <task_id>:AC-001. Implement AC-001 per PLAN.md ... You own only its **Files:**; harness:test-author owns its **Tests:** for this AC.")
Agent(subagent_type="harness:test-author",
      prompt="Lane <task_id>:AC-001-tests. Write AC-001's tests per PLAN.md, only in its **Tests:** paths.")
```

## Batch cap

Read `ready-lanes.md`: the coordinator owns public dispatch prepare/refill;
bound managed leads consume the exact parent assignment. Use the executable
`parallel_dispatch.py` admission helper for bounded nested roles, sharing the
parent inventory before spawning and after each completed lane. The configured cap is
an upper bound on AC lanes; actual available host agent slots also bound every
spawn, including paired test authors and nested workers. Refill ready lanes
without an unrelated-sibling completion barrier.

Managed WBS pools resolve `develop.fanout_cap` from `doc/harness/manifest.yaml`
at initialization through `batch_state.cap`:

```yaml
develop:
  fanout_cap: 6
```

An integer from 1 to 8 is the cap. An integer above 8 caps at 8. A missing key or `develop` section defaults to 4; ordinary invalid values
(zero, negative, fractional, boolean, quoted string, null, list) fall back to 4.
Encoded integers (`+6`, `0x6`, digit separators), aliases, tags, merges, escaped
strings and unsupported document forms are refused; they do not fall back. Setup preserves
the key and never writes it.

An explicit operational `--max-leads` may lower managed capacity, never raise
it. Standalone pools use `batch.max_leads`, default 3, or an explicit operational
limit; values above 8 clamp to 8. Existing pools pin their initialized cap:
manifest changes and prepare replays do not resize them. Actual host slots
always bound spawns, including nested helpers and paired authors.

The coordinator writes `effective fanout cap: <N> (<source>)` on a line above
the lane table, using the returned source: `default`, `develop.fanout_cap`, `explicit`,
`default-invalid-manifest`, or standalone `manifest`.

The cap governs Phase 3.0 AC lanes only; the other fanout phases stay bounded
by their own lens lists. The cap counts AC lanes, not agents — an AC's
`harness:test-author` rides in its AC's lane.

Use the effective cap C for each batch; for N>C admit successive batches of
up to C without an unrelated-sibling completion barrier. Do not hardcode a
second default-four batch limit when configured capacity is larger.
Merge cost controls batch size only. It does not justify collapsing two or more
independent ACs into one executor below the cap.

Theoretical demand without a host bound is 1 ac-worker + 1 test-author + 3
sub-workers per lane = 5 x cap: 20 agents at the default cap 4, 40 agents at the
maximum cap 8. Never spawn that demand without actual available slots; nested
workers use only explicitly assigned spare capacity.

---

## Parallelization Triggers

The orchestrator MUST fanout when any row matches. PLAN.md AC dependency matrix is the single source of truth for the first four rows; `git diff --name-only` and runtime context drive the last two.

| Trigger | When | Action |
|---------|------|--------|
| Component-independent N≥2 | PLAN AC matrix has 2 or more ACs whose target file sets are pairwise disjoint | Parallel `Agent(...)` fanout, one per AC, in one assistant message |
| Test-author pairing | An AC routed to `harness:ac-worker` declares both `**Files:**` and `**Tests:**` | Spawn one `harness:test-author` in the same assistant message as that ac-worker |
| API↔frontend split | PLAN AC matrix declares both backend/API files (`*api*`, `*routes/*`, `*endpoint*`, `*graphql*`) AND frontend files (`*.tsx/.jsx/.vue/.svelte/.html/.css/.scss`) | Contract-first sequential prelude (API contract / shared types AC), then parallel-fanout the consumer ACs |
| Helper-extract-first | Consumers depend on a shared helper extraction, including a necessary helper discovered during implementation | Amend the current PLAN/WBS autonomously for necessary work within the authorized outcome; declare the real prerequisite and actual execution ownership before assigning writers or starting dependent consumers. Run extraction sequentially before parallel consumers. Do not invent helper progress nodes solely to describe file ownership. Escalate only a material unresolved decision or missing authorization; never broaden a worker's ownership implicitly |
| Multi-lens QA / dogfooder | Phase 7 has 2 or more applicable QA lenses (from manifest + diff scope) OR dogfooder is queued for the Phase 7 final-PASS cycle | All QA calls in one assistant message with `lens="<lens>"`; dogfooder batched alongside on the final-PASS pass. FAIL cycles skip dogfooder |
| Quality audit fanout (Phase 4.5) | Quality audit pipeline runs: coverage trace + visual smoke (browser-only) | One assistant message; conditional specialists (security / perf / migration / LLM-trust) added when diff scope matches, per `quality-audit-pipeline.md` § Phase 4.5. Advisory inputs, not verdicts |
| Independent review fanout (Phase 6.6) | Selected discovery or `wbs(action="context")` supplies 2 or more dependency-free calls | Initial batch: selected defect hunters plus routed `review-security`. Start dependent `review-code` only after attempted hunter finals; at LIGHT it may batch with security. Every required lens must PASS before Phase 7 QA starts. |

### Extra review breadth under a single declared lens (advisory)

Advisory, not a fanout trigger. Additional concern-area specialists return findings for the formal reviewer to verify. Await all actual findings and resolve substantive failures; completion order cannot supersede an unresolved failure. Keep exactly one full-sweep formal code reviewer per review attempt under the depth/hunter protocol.

### Re-review after remediation

Follow `quality-audit-pipeline.md`'s two-cycle ceiling and remediation matrix.
The second cycle is the last: executable behavior recomputes from the prior
depth floor, test-logic-only work reruns contract/test only, and narrative or
task-artifact wording uses deterministic checks without hunters and forfeits
the remaining discovery budget. After the ceiling, that forfeit, or when prior
cycle count is unavailable on recovery, select or retain DEEP, spawn no hunter,
and let one fresh formal reviewer verify the final diff and prior remediation
evidence, using the canonical exhausted or unknown-budget invocation reason.
Rerun security only for security-relevant changes, independently and
without hunter payload. The formal code reviewer remains dependent on any
selected hunter attempts.

### Component-independent definition

Two ACs are **component-independent** iff one of the following holds:

1. Their current execution ownership sets are disjoint: the union of `**Files:**` and
   `**Tests:**` for one AC shares no path with the union for the other (A's
   `**Tests:**` must not overlap B's `**Files:**` either).
2. Any shared file is factored into a dedicated helper-extract AC that runs first (sequential prelude → parallel consumers).

Component-independence uses the current execution ownership and WBS dependencies, not a mandatory initial file layout or the current diff. Resolve ambiguous ownership at dispatch time; declare actual prerequisites without inventing a progress-document artifact. Do not silently combine independent ready outcomes into one executor.

### Small-task edge case

Use the existing WBS dispatch prepare facts: actual predecessor IDs, conflicting
outcome scopes, observed capability and slot capacity. A small edit estimate
never overrides a ready independent pair with capacity. Reject
`reason:"small-task"` as an assignment bypass.

### Invalid skip rationales

User-request-based routing is forbidden. The fanout decision comes from runtime
capability plus task shape: available `Agent(...)`, independent ACs, applicable
QA lenses, or independent audit workers. Do not use `user-did-not-ask`, "user
did not ask for delegation", "not requested", or equivalent wording to justify
inline execution. If the trigger matches and the run goes sequential, record
the concrete blocker in the lane table: declared dependency, unavailable Agent
tool, or actual observed capacity/resource conflict.

### Lane table requirement

Before implementation, write the `effective fanout cap:` line from Batch cap
above, then emit this table and use it as the routing contract:

| AC | Files | Depends on | Lane | Route | Reason |
|----|-------|------------|------|-------|--------|

`Route` follows public dispatch prepare: managed handoffs or a direct assignment
with concrete serial facts. Two independent ready lanes with capacity trigger
parallel managed spawns. Choose ownership at execution time; progress WBS
contains no mandatory implementation file list.

### Test-author lane

An AC routed to `harness:ac-worker` that declares non-empty `**Files:**` and
non-empty `**Tests:**` gets one `harness:test-author`, spawned in the same
assistant message as that AC's ac-worker. `**Tests:** none` means no
test-author for that AC. `**Files:** none` marks a test-only AC, which runs
as one ordinary `harness:ac-worker` lane owning its `**Tests:**`, no pair.
A sequential AC the coordinator implements itself under `developer.md` has no
pair either; the coordinator writes its tests.

The paired ac-worker owns only `**Files:**`; it never writes the AC's
`**Tests:**` paths and does not run or wait on the AC's `Verify:` — that
happens during reconciliation below.

Lane table notation: the `Files` cell lists both sets and the `Lane` cell
notes "+test-author"; the Route vocabulary is unchanged (the pair shares its
AC's `Agent(...)` row).

Reconciliation starts when that AC's implementation and test author return,
without waiting for unrelated siblings. The coordinator runs its scoped
`**Tests:**`; a full-tree `Verify:` command waits for all relevant writers.
Refill independent ready lanes after each verified AC, and reconcile:

| Result | Handling |
|--------|----------|
| Green | AC complete |
| Red, test matches PLAN.md | fix in the `**Files:**` lane |
| Red, test asserts beyond or against PLAN.md | fix in the `**Tests:**` lane |
| Collection or import error | decide the interface from PLAN.md and fix that lane (an import of a new symbol PLAN.md names is expected red until the implementation lands) |
| PLAN.md cannot settle it | `needs-coordinator-review` handling (amend PLAN or escalate; never retry with the same ownership) |

No fix crosses between `**Files:**` and `**Tests:**`.
Use the evidence-driven recovery in `fix-first-pattern.md`: preserve the
failure and inspect actual state, then continue authorized repairs without an
attempt quota. Moving a failure between lanes never clears it or changes
execution ownership. If ownership must change, obtain the coordinator's
updated assignment before writing. Required failures remain unfinished;
restore affected independent review PASS before actual QA. Stop for unknown
effects, an external blocker, a material unresolved decision or missing
authorization with the concrete unblock condition.

Fallback: when `harness:test-author` is unavailable (for example an older
installed plugin), run the ac-worker unpaired, owning `**Files:**` and
`**Tests:**`, and write the reason in the lane table.

### ac-worker sub-split (Claude only)

An ac-worker may split its own AC across at most 3 sub-workers
owning pairwise-disjoint subsets of its `**Files:**`, spawned in one message,
one level deep. Every sub-worker call always passes
`subagent_type="harness:ac-worker"` — never a lens agent (reviewers,
hunters, qa-*, ux-*, dogfooder) . The parent ac-worker
stays the sole reporter: it waits for every sub-worker, runs its AC checks
over the combined result, and the coordinator sees one `AC-NNN:` block. The
full allowlist, depth bound, and fallback rules live in
`plugin/agents/ac-worker.md`.

---

## Stage Agent Routing

Harness phases map to specific agent types and model tiers. Models stay AS-DECLARED in agent frontmatter — this matrix is a routing convention, not a model override. See `SKILL.md` § Model Routing for the per-work tier choice; do not duplicate it here.

| Phase | Agent type | Model source | Notes |
|-------|------------|--------------|-------|
| 3 (per-AC implement) | `harness:ac-worker` | inherit (sonnet) | One Agent per AC for parallel batches; one inline call for sequential ACs only when dependency-bound, carrying ac-worker's own ladder copy. Otherwise the coordinator implements sequential ACs under `developer.md`. May sub-split one level deep (Claude only; see ac-worker sub-split above) |
| 3 (per-AC tests) | `harness:test-author` | inherit (sonnet) | One per AC that declares both `**Files:**` and `**Tests:**`, spawned in the same message as its ac-worker; owns only `**Tests:**`; no lens |
| 4.5 (pre-review audit inputs) | `oh-my-claudecode:executor` | haiku | Coverage trace, visual smoke, and the conditional migration/contract, LLM-trust, and performance specialists listed in `quality-audit-pipeline.md` § Phase 4.5. Advisory inputs, not verdicts |
| 6.6 (independent review) | `harness:code-reviewer` / `harness:security-reviewer` | per agent frontmatter | Routed from `wbs(action="context")`; selected hunters/security batch first, then dependent code review. At LIGHT code/security may batch. Must PASS before Phase 7 QA. |
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
