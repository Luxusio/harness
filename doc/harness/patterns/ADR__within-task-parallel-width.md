# ADR: Within-task parallel width

Status: accepted (2026-09-28)

## Context

A harness develop task already fans independent ACs out to one
`harness:ac-worker` each. Four limits kept that width narrow:

- PLAN.md never declared which files an AC owns. The develop fanout rules
  computed component independence from `**Files:**` declarations, but the plan
  skill never asked the planner to write them, so lane cutting depended on the
  coordinator guessing.
- The batch cap was a hard-coded 4 that a project could not change.
- One ac-worker wrote an AC's source and its tests, one after the other.
- An AC whose files did not depend on each other still ran as one editor.

The user approved four changes on 2026-09-28 (proposal items 1, 2, 3 and 5)
and rejected a fifth (item 4). This record states what was built, its bounds,
where Codex differs, and what was rejected.

## Decision

### 1. ACs declare their ownership (plan skill)

`plugin/skills/plan/write-artifacts.md` § 6.6 defines the per-AC shape:

```text
**AC-00N: <outcome>.**
**Files:** `<source path>`, ... | none
**Tests:** `<test path>`, ... | none
**Depends on:** none | AC-00M, ...
- <criteria>
- Verify: `<command>`
```

No path appears in both `**Files:**` and `**Tests:**`. The planner cuts ACs
along disjoint file seams, declares a change several ACs need as its own
helper-extract AC that the consumers list in `**Depends on:**`, and puts tests
that pin implementation-produced text in a test-only AC (`**Files:** none`)
that depends on the implementing AC. The plan reviewer brief
(`plugin/skills/plan/review-phases.md`) gains one width check: it flags ACs
whose paths overlap without a shared helper-extract AC, and single ACs that
bundle independent file seams.

Both files are copied into the Codex payload, so they stay runtime-neutral:
the new text names no Claude agent type, no `Agent(...)` call, and no
backticked Claude-only path.

### 2. Configurable batch cap

`plugin/skills/develop/parallel-fanout.md` § Batch cap reads the optional
manifest key `develop.fanout_cap` once at Phase 3.0:

| `develop.fanout_cap` | Effective cap |
|---|---|
| key or `develop` section absent | 4 |
| integer 1 to 8 | that integer |
| integer above 8 | 8 |
| any other value (zero, negative, fractional, boolean, string, null, list) | 4 |

The coordinator writes `effective fanout cap: <N> (<source>)` above the lane
table, so a clamp or an ignored value is visible. The cap governs Phase 3.0 AC
lanes only; the review, QA and audit fanouts stay bounded by their own lens
lists. The cap counts AC lanes, not agents, because the cost it limits is the
per-AC PROGRESS.md merge. The default wording "For N>4, spawn batches of up to
4" is unchanged.

Harness writes the key nowhere. This repository's `doc/harness/manifest.yaml`
does not set it, and setup keeps it when a project adds it:
`setup_finalize.migrate_manifest_text` copies unknown keys through for both
current and legacy manifests.

### 3. Test-author lane

A new agent, `plugin/agents/test-author.md` (sonnet; Read, Write, Bash, Glob,
Grep, LS; no `Agent`), writes one AC's tests from PLAN.md intent and existing
public interfaces, never from the implementation being written beside it. An
AC routed to `harness:ac-worker` that declares both `**Files:**` and
`**Tests:**` gets one `harness:test-author` in the same assistant message.

- The paired ac-worker owns only `**Files:**` and leaves the AC's `Verify:` to
  the coordinator.
- Once that AC's two writers return, the coordinator runs its scoped tests;
  full-tree `Verify:` commands wait for all relevant writers. Independent
  ready lanes refill without waiting for the slowest sibling. Green completes the AC. A red test that matches PLAN.md
  goes back to the `**Files:**` lane; a test that asserts beyond or against
  PLAN.md goes back to the `**Tests:**` lane; when PLAN.md cannot settle it,
  normal `needs-coordinator-review` handling applies.
- No fix crosses between `**Files:**` and `**Tests:**`, and the 3-Attempt
  Escalation Rule bounds the loop. Attempts count per failing test across both
  lanes, so moving a red test to the other lane does not reset its count.
- `**Tests:** none` means no test author. A test-only AC runs as one ordinary
  ac-worker lane, and a sequential AC the coordinator implements itself has no
  pair.
- When the agent is unavailable (an older installed plugin), the ac-worker
  runs unpaired and owns both sets.

The Route vocabulary is unchanged; the pair shares its AC's `Agent(...)` row.
Names such as `test_author_ac_001` identify work for operators and grant no
verification authority.

### 5. ac-worker sub-split (Claude only)

`plugin/agents/ac-worker.md` gains the `Agent` tool. An ac-worker may hand
pairwise-disjoint subsets of its `**Files:**` to at most 3 unnamed sub-workers
spawned in one message, keeping any remainder.

- Always `subagent_type="harness:ac-worker"`. Never an omitted type, because a
  general-purpose agent inherits every tool, including harness MCP writers.
  Never `harness:developer`, `harness:task-lead`, or `harness:test-author`.
  Never a lens agent: `code-reviewer`, `security-reviewer`, `defect-hunter`,
  any `qa-*`, any `ux-*`, `dogfooder`. Independent review and QA belong to the coordinator, preserving actual
  review-before-QA order.
- Splits go one level deep only. Each sub-worker prompt says
  `Sub-worker of AC-NNN: do not split further`, and the Sub-split section tells
  an ac-worker whose prompt carries that marker that it never spawns.
- A sub-worker runs scoped tests for its own paths only, because sibling
  sub-workers are still writing files the AC's `Verify:` runs. That command
  runs over the combined result once every sub-worker returns: by the parent,
  or by the coordinator when the AC is also paired with a test author.
- The parent is the sole reporter and returns one `AC-NNN:` block. A
  sub-worker's `blocked` or `needs-coordinator-review` becomes the parent's.
- Without the `Agent` tool (for example at the nesting depth limit under a
  batch task-lead), the ac-worker implements its AC without splitting.

## Codex scope

| Change | Codex |
|---|---|
| AC shape and width check | yes, through the payload copy of both plan sub-files |
| `develop.fanout_cap` | yes: shared ready-lane dispatcher, constrained by actual host slots |
| test-author lane | yes: `plugin-codex/agents/test-author.md` plus one Phase 3.0 line |
| ac-worker sub-split | no: Codex has no ac-worker agent |

The Codex line spawns the test author with
`spawn_agent(task_name="test_author_ac_<NNN>")`, adding `_<n>` digits on a
retry for operator readability. Names have no lens-binding authority.

## Rejected alternatives

- **Item 4, worktree-isolated ac-workers.** Each ac-worker would have run in
  its own git worktree. The user rejected it on 2026-09-28. Lanes keep sharing
  the task's checkout, and disjoint ownership remains the only isolation
  between them.
- **A separate `harness:ac-subworker` without `Agent`.** Its frontmatter would
  enforce the one-level limit, but it duplicates the ac-worker's ladder copy
  and replaces the approved sub-worker type.
- **Counting agents against the cap.** Pairing would halve AC width at every
  cap.
- **Treating a cap above 8 as invalid.** Clamping is more predictable, and the
  effective-cap line shows it.

## Consequences

- Unbounded theoretical demand is 5 x cap concurrent agents (one ac-worker, one
  test author and three sub-workers per lane): 20 at the default cap and 40 at
  8. Since 2026-09-29 the shared ready-lane dispatcher additionally bounds
  actual admission by host slots and requires an atomic paired reservation.
- The dispatcher rejects invalid graphs and oversubscribed proposals, but
  the host call and reservation remain coordinator-owned. Sub-split bounds
  and the spawn allowlist remain workflow rules. No hook or MCP tool enforces them. Frontmatter grants `Agent` as a
  whole, so an ac-worker that ignored its allowlist could still start another
  agent type. A sub-worker reads the same agent file and keeps the same
  `Agent` tool, so only the prompt marker stops it from splitting again, so an extra level is not mechanically prevented. These residual risks are
  accepted.
- `tests/test_within_task_parallel_width.py` pins the text of every rule
  above, the readable task-name convention, and setup's preservation of the
  key.
