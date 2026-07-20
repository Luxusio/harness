---
name: develop
description: Implement PLAN.md. Orchestrates per-AC implementation, quality audit, verification gate, durable learning capture, and close. Uses aggressive parallel agents for implementation, quality, and verification phases. Detail lives in sub-files — this file is the orchestration layer.
argument-hint: <task-id>
user-invocable: false
allowed-tools: Read, Glob, Grep, Bash, Edit, Write, Agent, Skill, AskUserQuestion, mcp__plugin_harness_harness__task_start, mcp__plugin_harness_harness__task_context, mcp__chrome-devtools__navigate_page, mcp__chrome-devtools__take_snapshot, mcp__chrome-devtools__take_screenshot, mcp__chrome-devtools__evaluate_script, mcp__chrome-devtools__wait_for, mcp__chrome-devtools__list_pages, mcp__chrome-devtools__new_page, mcp__chrome-devtools__select_page, mcp__chrome-devtools__emulate, mcp__chrome-devtools__click, mcp__chrome-devtools__fill, mcp__chrome-devtools__press_key, mcp__chrome-devtools__type_text, mcp__chrome-devtools__hover, mcp__chrome-devtools__list_network_requests, mcp__chrome-devtools__performance_start_trace, mcp__chrome-devtools__performance_stop_trace, mcp__chrome-devtools__lighthouse_audit
---

Implement the plan for a harness task. Reads PLAN.md, implements changes, verifies completeness, captures durable learnings, and closes through MCP.

Explicit user invocation or approval of this harness repo-mutating workflow
authorizes the subagents required by the workflow's verification and review
gates. Examples include "use harness", "run/continue/close the harness task",
native `/goal`, or clear approval to proceed with a harness task. This workflow
authorization does not apply to read-only answers or ordinary non-harness work.

When the runtime exposes explicit close tools such as `close_agent`, track each
spawned agent id and close it after the result is consumed, the agent completes
or fails, it is cancelled, or it is no longer needed. Before final response,
`task_close`, or handoff, do not leave completed agents open unless the user
explicitly asked to keep a still-running agent alive. Completed agents can
continue to count toward the concurrency limit until closed.

## Voice

Develop-orchestrator voice: opinionated, concrete, builder-to-builder. The develop skill is the entry point for the implement → audit → verify → handoff loop — sub-files inherit voice rules but the parent sets the tone.

- Lead with the point. Say what the phase did, what it found, what changes downstream.
- Be concrete. Name files, functions, line numbers, AC ids, commit hashes, test names. Real numbers over qualifiers.
- Tie technical choices to outcomes — what the next phase reads, what the final response should say, what the verifier now has evidence for.
- Be direct about quality. A confident PASS without test evidence matters more than a thoroughly-explained FAIL. Stale verdicts matter. Scope creep matters.
- Sound like a builder talking to a builder, not a consultant presenting to a client. No founder cosplay, no hype.
- No em dashes. No AI vocabulary: `delve`, `crucial`, `robust`, `comprehensive`, `nuanced`, `multifaceted`, `furthermore`, `moreover`, `additionally`, `pivotal`, `landscape`, `tapestry`, `underscore`, `foster`, `showcase`, `intricate`, `vibrant`, `fundamental`, `significant`, `seamless`, `leverage`. These signal AI prose; cut them.
- Korean/English bilingual context: technical terms stay English, explanations may use Korean.
- The user has context you do not. Adversarial agreement is a recommendation, not a decision. The user decides at premise gate (Phase 2 EUREKA), at scope-expansion gate (Phase 5), and at any 3-strike escalation.

Good: "AC-003 done. PROGRESS.md:34 records 9/10 completeness. Per-AC test passed (`tests/regression/task_xx/test_ac_003__loop_detect.py`). Edge case deferred: nested phase loops, tracked as a follow-up."
Bad: "I have successfully completed the implementation of AC-003 and the changes appear to be working as expected based on my analysis."

## Anti-shortcut clause

CHECKS.yaml `passed` is evidence the gate ran, not a substitute for fresh runtime verification (C-04 IRON LAW: PASS verdict must be fresh after the last edit). PROGRESS.md is the scope-lock contract for this task. Hand-editing CHECKS.yaml or skipping `update_checks.py` produces a plausible-looking ledger that lies about `reopen_count` and `last_updated` (the May 2026 update_checks indent bug — `learnings.jsonl` 2026-05-08 — silently corrupted CHECKS.yaml across 6 tasks before detection; that incident is the precise failure mode this clause prevents). If you find yourself wanting to mark something `passed` because the previous run was green, stop — re-verify against the current state of the repo. Stale evidence is worse than no evidence.

**Highest-tier verification mandate.** If a task creates, unblocks, or documents a verification path, using that path is part of the same task. Do not ask "should I verify it?" when the required services, rebuild, seed, token, browser, API, or CLI route are locally available. Execute the highest available tier yourself, then report the tier reached and any concrete blocker. Ask only when verification would require destructive state changes, paid/external credentials, production resources, or a genuine product choice between valid approaches.

## Confusion Protocol

For high-stakes implementation ambiguity — blast radius >5 files (`verification-gate.md:166-179` has the gate that fires here), 3-strike hypothesis exhaustion (`verification-gate.md:151-164`), T2 vs T3 test-failure ambiguity (`test-failure-triage.md:23-36`), Phase 2 EUREKA flagging PLAN.md as wrong, Phase 5 scope creep mid-fix-loop — STOP. Name it in one sentence, present 2-3 options with concrete tradeoffs, and ask via AskUserQuestion. Cross-reference: parent format at `plugin/skills/plan/decision-principles.md` § AskUserQuestion Format.

Reserve this protocol for high-stakes ambiguity where the wrong choice changes scope, architecture, or verification outcome. The bar is: "if I pick wrong, the entire implementation is built on a misread of intent or scope, and the cost shows up in verify or close, not now." Running the highest available verification tier is not scope expansion and is not a reason to ask; it is the completion condition for the task.

## Context Health

Soft directive — degrade gracefully, never block.

- **`[PROGRESS]` summary at phase boundaries.** Phase 3 (per-AC implement) and Phase 4.5-4.8 (parallel quality audit) are the longest runs. When any phase exceeds ~5 minutes, surface a 1-2 sentence checkpoint: done, next, surprises. Helps the user track progress without scrolling, and helps you self-check direction.
- **Loop detection.** If the same fix-cycle pattern, the same hypothesis, or the same gate fires 3 times without converging, STOP and reassess. Options: premise re-confirm via AskUserQuestion (Phase 2 EUREKA path); spawn a fresh adversarial agent on a different model (cross-model blind-spot reset); pause for user check-in. Looping silently is worse than asking.
- Progress summaries and loop-detection notices NEVER mutate git state.

## Premise Gate / User Challenge

Two structured triggers that replace silent overrides in earlier prose:

1. **Phase 2 EUREKA premise gate** — when the search-before-building scan reveals PLAN.md's approach is suboptimal (the in-place EUREKA flag at Phase 2 below). Surface the discovery through AskUserQuestion with options `[Re-ground premise — re-run plan skill with new premise]`, `[Simplify scope — narrow this AC and proceed]`, `[Proceed as planned — capture EUREKA as a durable learning if reusable]`, or free-text `Other`. The user-confirmed direction must be visible in task state or a durable artifact, not only conversation history.
2. **Phase 5 scope-expansion challenge** — when scope drift detection finds an unrelated file change that turns out to be necessary for the AC. Surface the scope change through AskUserQuestion with options `[Revert — change belongs in a separate task]`, `[Add to scope — update task state/plan rationale]`, `[Defer to new task — open follow-up]`, or free-text `Other`.

Both triggers cross-reference the AskUserQuestion format from `plugin/skills/plan/decision-principles.md` § AskUserQuestion Format. The plan-orchestrator series proved structured AskUserQuestion at premise-shift / scope-expansion points produces measurably better outcomes than prose directives.

## Error Philosophy

MCP does not tolerate mid-task stops. **Never halt with a bare BLOCKED.** Use `AskUserQuestion` with options; user decides. Errors are consumed by the running agent, not humans.

**No mid-task scope cuts.** Do NOT fire AskUserQuestion to ask the user whether to drop ACs, split the task, or defer items mid-Phase. The plan was approved at Phase 5 of plan-skill; develop executes it. If a genuine blocker prevents completion of an AC, escalate via the existing BLOCKED → AskUserQuestion path with the concrete blocker, not a meta scope question.

## Model Routing

Route work to the cheapest sufficient model. Inline below; full rationale in sub-files.

| Work | Model |
|------|-------|
| Implementation (Phase 3) | inherit |
| Balanced code review (6.6) | independent highest-available reviewer |
| Conditional security review (6.6) | independent security reviewer |
| Everything else mechanical | haiku (test-coverage tracing, completion audit, runtime smoke, visual smoke) |

## Flow

Phases run in strict order; each phase must complete before the next. Sub-files are lazy-loaded — do NOT pre-read them, load each only in the phase that needs it. Every phase is idempotent on re-run; check PROGRESS.md and TASK_STATE.yaml to resume instead of restarting from Phase 0.

**Graceful degradation:** missing tool or phase prerequisite → skip cleanly, log reason, do NOT install missing tools. Skipped-phase table:

| Missing | Phases skipped |
|---------|----------------|
| Linter / build / test framework / coverage tool | 3.7 / 3.8 / 4.5 / 4.9 |
| `browser_qa_supported: false` or Chrome MCP missing | 3 visual, 3.9, 4 Agent D, 7 browser debug |
| Dev server unreachable | 3.9 |
| No QA_KNOWLEDGE.yaml / learnings.jsonl | 0 / 1 (first run creates them) |

### Phase 0: Pre-flight

Verify `doc/harness/manifest.yaml` and `TASK_STATE.yaml` parse and `status` is one of: created, planning, implementing, verifying, closed. No other task holds write focus. On failure, `AskUserQuestion` with setup-skill / task-id / continue-anyway options.

**Context Recovery:** inspect TASK_STATE.yaml/PROGRESS.md for the current task
and list the 3 newest task directories. If an in-progress task matches the
current `task_id`, state "resuming from prior session" in the conversation.

**Health baseline snapshot:** capture composite health score for Phase 8 delta. Best-effort — skip cleanly.

Run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/health.py --dry-run || true`.

Reads `health_components` from manifest (falls back to `test_command`). Output includes per-component PASS/FAIL + composite 0–10 score. `--dry-run` prevents appending to project-level history at this stage.

### Phase 1: Load plan

Read `doc/harness/tasks/<task_id>/`:
1. `PLAN.md`, `REQUEST.md` (if present), `TASK_STATE.yaml`.
2. Extract: objective, scope (in/out), target files, acceptance criteria (AC-001+), verification commands.
3. **Resume check:** `PROGRESS.md` → skip ACs listed in `completed_acs`. For each completed AC, compare target-file mtimes against `PROGRESS.md` mtime; files modified post-PROGRESS → mark "needs re-verification", do not blindly skip.
4. **Learnings bootstrap:** `head -20 doc/harness/learnings.jsonl` and `ls doc/harness/patterns/*.md`. If PLAN.md absent, `AskUserQuestion` (run plan skill / check task_id / abort).

**Durable Docs Preflight:** before source implementation, read PLAN.md `Durable Docs Decision` as a documentation-impact judgment, not a rote REQ checklist. Confirm whether the task is `REQ needed`, `Pattern/skill doc enough`, or `No durable doc needed`, then run the REQ detector mentally or via `plugin/scripts/req_detector.py` when request, feedback, target files, or planned surfaces imply observable UI/API/mobile/native/desktop behavior. If a REQ path is selected or detector output is high-confidence, create or update that `doc/<area>/REQ__*.md` before editing source files, using `direct REQ doc edit` or `plugin/scripts/req_scaffold.py` when no existing REQ fits. If the task touches observable UI/API/backoffice/admin screens, routes, controllers, native navigation/back-stack behavior, or endpoints and PLAN says `REQ: n/a`, stop source implementation and amend/create the REQ first; do not wait for close or durable docs to discover the missing REQ. For harness process, agent instruction, testing guidance, or implementation-pattern changes, prefer `GUIDE` or skill/pattern docs and keep `REQ: n/a` with a specific reason.

**User Feedback Event Review:** before each dependent build/test/review action
at phase boundaries, read `<task_dir>/USER_FEEDBACK.jsonl` when present. The
file is an automatic context-rich event log, not a durable source of truth by
itself. For every new event, decide whether it changes the current task,
verification criteria, product/design/domain direction, or future harness
behavior. Reflect it before the next action that depends on it: update code,
tests, PLAN/task state/durable docs, or the relevant REQ/GUIDE/ADR/POLICY; defer it to
a follow-up; reject it with a reason; or ask the user if the decision is still
ambiguous. Do not wait until close to act on feedback that changes what should
be built, tested, or judged. Close-time disposition is only the safety net.

### Phase 2: Read + Search Before Building

Read target files and dependencies from PLAN.md. For each AC, before implementing:
1. Grep for existing solutions — function names, utilities, patterns.
2. Check framework/stdlib built-ins.
3. Follow existing codebase conventions, not invented ones.
4. Only build new when nothing fits — extend over duplicate.

**Eureka check:** if search reveals PLAN.md's approach is suboptimal (reinventing, wrong assumption), flag `EUREKA: AC-NNN — <discovery>` in the conversation and ask for the user-confirmed direction before changing course. Persist reusable discoveries as `type:"eureka"` in `learnings.jsonl`, then promote durable ones to a committed skill, pattern, test, or typed doc before close.

**Baseline screenshot (browser projects):** see `browser-verification.md` → "Phase 2: Baseline Screenshot".

### Phase 3.0: AC Dependency Analysis

Classify ACs as SEQUENTIAL (shared files or data dependency) or PARALLEL (component-independent). Build a dependency matrix from each AC's `**Files:**` declaration in PLAN.md. **PLAN.md AC dependency matrix is the single source of truth** — `git diff --name-only` is a secondary signal, never a sole trigger.

Before Phase 3 implementation starts, write a visible lane table to the conversation.
This is the routing contract the user can audit:

| AC | Files | Depends on | Lane | Route | Reason |
|----|-------|------------|------|-------|--------|
| AC-001 | `<paths>` | none | A | `Agent(...)` | independent files |
| AC-002 | `<paths>` | AC-001 | A | sequential | declared dependency |

`Route` must be one of: `Agent(...)`, `sequential-prelude`, `sequential-dependent`,
or `sequential-small-task`. Fill the table before editing files. If the table has
two or more independent `Agent(...)` rows, spawn those executors in one assistant
message before doing local implementation work.

**Default posture: parallel-first.** Mandatory parallel delegation is
capability/task-shape based: if the runtime has `Agent(...)` and the lane table
shows independent work, spawn one worker per lane. This is mandatory
capability/task-shape routing; a user request is not a prerequisite for
delegation. Do not skip parallel fanout because "user did not ask for
delegation"; that is an invalid skip rationale. Do not wait for the user to
request delegation. User request is not a condition for parallel routing. Assume
every AC, QA lens, quality audit, and verification command can run in a subagent
unless the dependency matrix proves otherwise. The coordinator's job is to split
work, spawn siblings together, keep shared ledgers serialized, and merge. Inline
work is reserved for sequential preludes, dependency-bound followups, and tiny
evidenced exceptions.

**Component-independent (definition for this phase):** two ACs are component-independent iff (a) their PLAN target file sets are disjoint, OR (b) shared files are factored into a dedicated helper-extract AC that runs first (sequential prelude → parallel consumers).

**Enforcement (N>=2 component-independent ACs):** when the matrix yields two or more ACs that are pairwise component-independent, the orchestrator MUST issue N parallel Agent calls in a single assistant message. Treat this as the normal path, not an optimization. Explicit additional triggers (always fanout, even at N=2):

- **API↔frontend split** — PLAN AC matrix declares both backend/API files (`*api*`, `*routes/*`, `*endpoint*`, `*graphql*`) and frontend files (`*.tsx/.jsx/.vue/.svelte/.html/.css/.scss`). Contract-first → parallel consumers.
- **Helper-extract-first** — PLAN explicitly contains a helper-extraction AC. Run that extract AC sequentially first, then parallel-fanout the consumer ACs.

**Helper-extract-first guard.** The extract trigger fires ONLY when the extract is already a declared AC in PLAN.md. Mid-task "extract while I'm here" is scope creep blocked by Phase 5. If a fanout decision *would* require extracting a helper from shared files but no such AC exists, the trigger does not fire — run sequentially and surface the missing AC in final response or a follow-up plan cycle.

**Sequential fallback.** If a parallel trigger matches but the route is
sequential, state the concrete reason in the visible lane table before editing:
`reason`, `ac_count`, affected AC ids, and the blocking fact. Valid reasons are
`small-task`, `declared-dependency`, and `agent-tool-unavailable`.
`user-did-not-ask`, "user did not ask for delegation", or any equivalent
user-request rationale is invalid.

**Small-task edge case.** N=2 ACs where total edit volume is genuinely trivial
(target estimate <10 changed lines combined and <15 seconds of editing) may use
`sequential-small-task`. Record the concrete estimate in the lane table. Default
is parallel; sequential is the narrow exception. User requests for speed or
aggressive parallelism disable this edge case for the current task.

Inline spawn template (copyable; one block per AC, ALL in one assistant turn):

```
Agent(name="<task_id>:AC-NNN", subagent_type="harness:ac-worker",
      prompt="Implement AC-NNN per PLAN.md target files <list>. Return changed paths, test results, and blockers in your final response. Do not edit PROGRESS.md or CHECKS.yaml.")
Agent(name="<task_id>:AC-001", subagent_type="harness:ac-worker",
      prompt="Implement AC-001 per PLAN.md target files <list>. Return changed paths, test results, and blockers in your final response. Do not edit PROGRESS.md or CHECKS.yaml.")
Agent(name="<task_id>:AC-002", subagent_type="harness:ac-worker",
      prompt="Implement AC-002 per PLAN.md target files <list>. Return changed paths, test results, and blockers in your final response. Do not edit PROGRESS.md or CHECKS.yaml.")
```

Use one Agent per independent AC. Do not assign multiple independent ACs to one
executor. The coordinator reads subagent final responses and merges PROGRESS.md
and CHECKS.yaml after all siblings return.

See `plugin/skills/develop/parallel-fanout.md` for the full Parallelization Triggers table, Spawn-all-in-one-message rule, Stage Agent Routing matrix, and the Audit hook (the rule's 6-month value is re-verified via `learnings.jsonl type:parallel-trigger` log).

**Rollback protocol** — on ANY sibling Agent failure during a parallel batch:

```bash
for _AC in <list of siblings that had already promoted>; do
  python3 ${CLAUDE_PLUGIN_ROOT}/scripts/update_checks.py \
    --task-dir <task_dir> --ac "$_AC" --status open --note parallel-fallback
done
# Then state the rollback reason in the conversation and sequential-retry the
# failed batch.
```

After rollback, retry the failed batch sequentially. Do NOT re-fanout the same batch — that masks the underlying failure mode.

### Phase 3.1: Scope Lock

Declare allowed / test / forbidden paths in PROGRESS.md. Before each file edit:
- allowed → proceed. test → proceed. forbidden → BLOCK + escalate. unlisted → WARN, auto-add to allowed with note.

### Phase 3: Implement

1. For sequential batches, work **one AC at a time**, in order. For parallel
   batches, wait for all sibling executor result files, then merge progress once.
   Skip ACs in `completed_acs`.
2. **Follow existing patterns.** Smallest coherent diff. No speculative features.
   Every sequential implementation and `harness:ac-worker` follows the
   minimum-sufficient ladder from `plugin/agents/developer.md`: trace first,
   then no change → reuse → stdlib → platform/framework → installed dependency
   → smallest clear local expression → minimum new code. Never trade away
   current validation, auth, transactions, concurrency safety, cleanup,
   security, accessibility, tests, or requested behavior for fewer lines.

**PROGRESS.md after each AC:**

```yaml
task_id: <task_id>
phase: 3
completed_acs:
  - id: AC-001
    status: done
    tests: passed
    completeness: 9            # 0-10 — see rubric below
    deferred_edges: []         # edge cases consciously skipped
current_ac: <next or "done">
partial_ac: null               # or { id: AC-003, note: "edits done, regression test pending" }
decisions:
  - { choice: "...", reason: "...", ac: AC-001 }
attempts:
  - { ac: AC-002, tried: "...", failed_because: "...", resolved_with: "..." }
notes:
  - "<file:line> — <observation>"
updated: <ISO timestamp>
```

**AC Completeness rubric (0-10):** covers how much of the AC's surface area was addressed (NOT confidence that it works — that's Phase 4.6).

| Score | Meaning |
|-------|---------|
| 10 | Happy path + all edge cases + negative paths + regression tests |
| 8-9 | Happy path + common edges + regression test. Rare edges documented |
| 6-7 | Happy path + main branches. Some edges deferred with justification |
| 4-5 | Happy path only. Significant surface skipped |
| ≤3  | Partial — AC should not be marked done |

Any AC scoring ≤7 MUST list `deferred_edges`. ≤5 requires explicit justification in PROGRESS.md or the final response (MVP scope, user-deferred, etc.).

**Acceptance Ledger update (after each AC):** once the AC's code is in and per-AC tests pass, mark it `implemented_candidate` in CHECKS.yaml. Only Phase 7 promotes to `passed`. Never hand-edit CHECKS.yaml.

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/update_checks.py \
  --task-dir doc/harness/tasks/<task_id>/ \
  --ac AC-00X --status implemented_candidate \
  --evidence "<file:line | test name>"
```

**Per-AC test run:** each executor runs its own targeted tests for the AC it owns. `git diff --name-only HEAD~1` → for each changed source, find test files that import/reference it (mirror path or import search) → run only those. If no tests exist for changed module, write one (Phase 3.5 rule). If PLAN.md specifies per-AC verify commands, prefer those. For parallel AC batches, targeted tests run inside the sibling executor contexts before the coordinator touches PROGRESS.md or CHECKS.yaml.

**Delegation rule (C-18 / Verification delegation).** Browser MCP tools (`mcp__chrome-devtools__*`) MUST be delegated to `harness:qa-browser` — the gate blocks main-session calls. Bash test runners (`pytest`, `npm test`, `pnpm test`, `vitest`, `cargo test`, `go test`, …) are allowed inline only for small targeted per-AC runs; full-suite verification MUST be delegated to qa-* agents. Spawn every applicable lens (`qa-cli`, `qa-api`, `qa-browser`, `qa-desktop`) in one assistant message, let each lens run its commands in its own context, and await every lens. Claude/Codex hooks record lifecycle events in `SUBAGENT_RECEIPTS.jsonl`; `task_verify` requires fresh completed explicit PASS verdicts. See `plugin/CLAUDE.md` § 8c.

Per-AC test failures → fix immediately. These are free; only Phase 7 full-suite failures count toward the 3-cycle limit.

**Per-AC visual verification** (browser projects only): see `browser-verification.md` → "Per-AC Visual Verification" and "Per-AC Interaction Testing".

### Phase 3.3: Auto-checkpoint (post all ACs)

After all ACs reach `implemented_candidate`, snapshot task state for session resume:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/write_checkpoint.py \
  --task-dir doc/harness/tasks/<task_id>/ \
  --note "Phase 3 complete — all ACs at implemented_candidate"
```

### Phase 3.4–3.6: Per-AC Quality Gate

Runs continuously during Phase 3.

- **3.4 Test framework bootstrap** — if project has no framework and no `doc/harness/.no-test-bootstrap` opt-out marker, offer minimal setup (JS/TS: vitest or bun:test; Python: pytest; Go/Rust: built-in). Log bootstrap to `learnings.jsonl` type `test-bootstrap`. If user declines, create opt-out marker.
- **3.5 Regression rule + Test-Evidence Gate** — two related rules.

  *Regression rule:* if the diff modifies existing behavior and no test covers the changed path, write a regression test immediately. Commit separately: `test: regression test for <what>`.

  *Test-Evidence Gate (since v2.3):* `update_checks.py` rejects promotion of `kind in {feature, functional}` ACs to `implemented_candidate` / `passed` unless `--test-evidence <path>` resolves to a real file inside the repo (no symlinks, no traversal). Use the bypass with a documented reason for ACs that genuinely have no test surface (configs, narration, migrations):
  ```bash
  # Promote with evidence:
  python3 ${CLAUDE_PLUGIN_ROOT}/scripts/update_checks.py \
    --task-dir doc/harness/tasks/<task_id>/ --ac AC-001 \
    --status implemented_candidate \
    --test-evidence tests/regression/task_xx/test_ac_001__behavior.py

  # Bypass with reason (logged to learnings.jsonl):
  python3 ${CLAUDE_PLUGIN_ROOT}/scripts/update_checks.py \
    --task-dir doc/harness/tasks/<task_id>/ --ac AC-007 \
    --status implemented_candidate \
    --no-test-required "narration-only AC, no behavior to test"
  ```
  Allowlist (no evidence required): `kind in {bugfix, doc, verification}`. Bugfix is gated separately by Iron Law (`--root-cause`). Missing `kind:` field defaults to `unknown` and skips the gate (backward-compat).

  QA agents may include `codifiable:` YAML blocks in their final response for
  future regression-test extraction. Do not write critic artifacts for this.
	- **3.6 Fix-first pattern** — see `fix-first-pattern.md`. Classify AUTO-FIX (dead code, magic numbers, stale comments, missing guards) and ASK (API design, architecture, security, DRY extractions). Auto-fix immediately; surface ASK items through the current user-input mechanism or final response. The **3-attempt escalation rule** also lives in this sub-file and applies to every fix loop (per-AC, Phase 7, browser debug).
	- **3.6.1 Durable docs (REQ/GUIDE/ADR/POLICY)** — read PLAN.md `Durable Docs Decision` before implementation. Treat it as a documentation-impact judgment: `REQ needed`, `Pattern/skill doc enough`, or `No durable doc needed`. Create or update each selected `doc/<area>/<TYPE>__<name>.md` file; selected REQ docs must be written before source implementation, not after code is done. Use `direct REQ doc edit` / `req_scaffold.py` as the happy path when observable behavior is detected and no existing REQ fits. Use DDD-style areas or bounded contexts such as `ui`, `api`, `auth`, `billing`, `catalog`, `runtime`, `verification`, or `common`. Use `REQ` for user-visible behavior, externally consumed API contracts, constraints, and observable bugfixes; write intended observable behavior plus verification cues. Existing-screen state changes count: filters, search, sorting, loading, empty/error states, visibility, labels, native navigation/back-stack behavior, and click/input behavior. New pages, admin/backoffice screens, routes, controllers, and endpoints require a REQ even when additive. PLAN.md acceptance criteria are task-local artifacts and never substitute for a durable `REQ`. Recheck the actual diff after implementation: if you added observable UI/API behavior that PLAN marked `REQ: n/a`, create the missing REQ, link it from PLAN.md or the changed durable doc. If the diff instead changed harness process, agent instructions, testing guidance, or implementation patterns, update the relevant `GUIDE`, skill, pattern doc, or tests rather than inventing a REQ. Use `GUIDE` for reusable coding, design, testing, or implementation guidance. Use `ADR` for significant technical choices with alternatives, reasons, consequences, and tradeoffs. Use `POLICY` only for external security, legal, data-handling, approval, licensing, or organizational constraints that harness cannot fully enforce by itself; keep harness-internal execution rules in skills, agents, scripts, and tests. Keep each updated durable doc directly in the repo and link selected REQ paths from PLAN.md when the close gate needs them. For internal-only refactors, one-off tests, or non-observable maintenance, keep `REQ: n/a` in PLAN.md with a specific non-observable reason; the reason must say which durable knowledge surfaces remain unchanged.

### Phase 3.7–3.9: Post-implementation health

After all ACs done. Each runs only if prerequisite exists.

- **3.7 Lint & Format** — run linter and formatter on `git diff --name-only` only. `--fix` where safe. Re-run per-AC tests after. Skip if none configured.
- **3.8 Build check** — compile / typecheck the diff (or full project). Build failures are always T1 (our code). Fix immediately.
- **3.9 Runtime smoke** — see `runtime-smoke.md`. Project-type-specific (browser / API / CLI). Smoke commands run inside the qa-* agent (Verification delegation, C-18). Main only spawns + reads results.

### Phase 4: Plan Completion Audit (haiku)

Haiku agent cross-references every AC against `git diff --stat` and classifies each as DONE / PARTIAL / NOT DONE / CHANGED + category (CODE / TEST / MIGRATION / CONFIG / DOCS). Be conservative with DONE (file touched ≠ AC done); be generous with CHANGED (goal met by different means).

For PARTIAL / NOT DONE, classify cause: scope-cut / context-exhaustion / misunderstood / blocked / forgotten / evolved. Fix forgotten and misunderstood immediately; log scope-cut + blocked in PROGRESS.md; mark evolved as CHANGED with the new approach.

### Phase 4.5–4.8: Quality Audit Pipeline

Read `quality-audit-pipeline.md` in full. Phase 4.5 gathers coverage, visual,
migration/contract, LLM-trust, and proportional performance inputs before the
final checkpoint. The old generic adversarial, line-count Red Team, and quality
synthesis agents are replaced by the mandatory balanced review gate in Phase
6.6. Canonical code/security routing comes from `task_context`
`required_review_lenses`, which checks paths and diff content.

**Phase 4.85 Coverage Synthesis** — use the coverage diagram from the audit
agent final response to update tests directly. Do not create a separate test
extra plan file.

**Phase 4.9 Coverage Gate** — if manifest declares `coverage_minimum` / `coverage_target`, enforce. Below minimum = BLOCK (write tests); below target = WARN in final response. 3 fix cycles max; on exhaustion `AskUserQuestion` (continue / lower threshold / defer).

### Phase 5: Scope Drift Detection

`git diff --name-only` — each file is:
- In scope → proceed.
- Related but unlisted → acceptable, note in PROGRESS.md or final response.
- Unrelated → revert. Belongs in a separate task.
- Missing from plan but necessary → note as "unplanned-but-necessary".

**SCOPE CREEP signals:** unrelated changes, "while I was in there" edits, new features not in PLAN, reformatted distant modules.
**MISSING REQUIREMENTS:** PLAN.md requirements not addressed by any change; partial implementations (model exists but controller missing); test coverage gaps.
**Documentation staleness:** if changed file has a corresponding doc (README section, API doc, inline docblock), flag stale under "Documentation Debt" — writer skill fixes, not here.

### Phase 6: Bisectable Commits

Split into coherent commits in this order:

| Order | Layer | Examples |
|-------|-------|----------|
| 1 | Infrastructure | Config, build, deps, CI |
| 2 | Models / Services / Data | Schema, types, data layer, business logic |
| 3 | Controllers / Views / API | Routes, UI components, endpoints |
| 4 | Tests | Test additions (separate from impl) |
| 5 | Docs / Metadata | VERSION, CHANGELOG, README, durable docs |

Each commit must leave the codebase working. Bisect stops at infra layer, not mid-feature.

### Phase 6.5: Verification Gate

- **6.5 IRON LAW** — PASS = PASS. No stale PASS. No unverified claim. Runtime verdict must be fresh after last file change.
- Use the current quality score to decide whether to continue fixing or proceed.
  Do not write a project stats series for per-task scores.

### Phase 6.6: Independent Code Review Gate

Read `quality-audit-pipeline.md` § Phase 6.6. Call `task_context`, spawn every
required read-only review lens in parallel, await explicit verdicts, and require
fresh `REVIEW_RECEIPTS.jsonl` PASS for the current HEAD and worktree
fingerprint. Send only `FIX_NOW` findings to the original minimum-sufficient
implementer. Any edit loops through focused tests/checkpoint and all required
reviewers again. Do not start Phase 7 QA until review PASS.

### Phase 7: Verification Gate

Read `verification-gate.md` in full. Delegates full-suite test commands from PLAN.md to all applicable qa-* agents in parallel, classifies failures (GATE/PERIODIC × OWN/PRE-EXISTING), triages with hypothesis-driven debugging, enforces the 3-cycle limit with investigate-skill escalation on cycle 3.

**Main session MUST spawn the appropriate qa-* lens for full-suite verification (Verification delegation, C-18).** The gate hard-enforces browser MCP calls and the develop contract hard-enforces full-suite delegation. Bash test runners remain allowed inline only for targeted per-AC runs and debug reruns. Heavy full-suite execution and background process state belong in qa-* isolated contexts. Let the qa-* lens execute, then run `task_verify`; the hook-recorded `SUBAGENT_RECEIPTS.jsonl` entry is the verification signal.

QA must be spawned after Phase 6.6 PASS; a QA run started before the latest
review PASS is stale evidence and cannot close the task.

For user-facing changes, also spawn the applicable ux-* lens (`ux-cli`,
`ux-api`, `ux-browser`, or `ux-desktop`) when manifest support and touched-path
rules require it. UX agents return findings in their final response; no
critic artifact is written.

**No verification opt-in prompt.** If Phase 7 reveals a now-available live/API/browser/CLI verification route, run it through the appropriate qa-* lens. Do not ask the user whether to perform the verification. A rebuild, local service restart, local seed, or dev-only token generation is normal verification work unless it crosses the destructive/external boundary above.

When durable docs changed under `doc/<area>/<TYPE>__*.md`, pass those paths to the QA lens as intent evidence. QA uses `REQ` as behavior/contract verification criteria, `GUIDE` as implementation quality and consistency criteria, `ADR` as architecture intent and tradeoff criteria, and `POLICY` as external constraint criteria.

**Multi-lens QA spawns follow `parallel-fanout.md` Parallelization Triggers — when two or more QA lenses apply (e.g., `qa-browser` + `qa-api` for a fullstack diff), issue ALL agent calls in a single assistant message so the hooks record each subagent start.

**Also implements:**
- **Transience filter** — a failure must reproduce on 2 consecutive runs to count as `failed`. Single-run failures are logged as `transient` in `learnings.jsonl` and not counted toward the 3-cycle limit.
- **Severity × confidence close gate** — after synthesis, block close on:
  - `critical` AND confidence ≥ 7
  - `high` AND confidence ≥ 8
  Lower severities may be deferred in final response or follow-up tasks — do not block close.
- **Acceptance Ledger promotion** — on gate pass, `update_checks.py --status passed`. On gate fail, `--status failed` (auto-increments `reopen_count`), loop back to fix cycle. Close gate requires every AC to be `passed` or `deferred`.

### Phase 7.5: Auto-checkpoint (post verify gate)

After Phase 7 completes (pass or fail), snapshot for mid-task resume:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/write_checkpoint.py \
  --task-dir doc/harness/tasks/<task_id>/ \
  --note "Phase 7 done — runtime_verdict=$(grep runtime_verdict <task_dir>/TASK_STATE.yaml | awk '{print $2}')"
```

### Phase 7.6: Health score capture

Run health score and compare it to the Phase 0 baseline if still available in
context:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/health.py --dry-run || true
```

### Phase 7.7: Dogfood (post-QA, pre-close)

After the verification gate passes, spawn the dogfooder agent to use the product
as a power user. The dogfooder finds friction, gaps, and missing workflows — things
QA doesn't catch because they aren't bugs.

**Dogfooder spawn batches with Phase 7's final-PASS-cycle QA spawn — issue both in a single assistant message when Phase 7 is on its terminal PASS pass. FAIL cycles skip dogfooder (its work would be discarded). See `parallel-fanout.md` Parallelization Triggers row "Multi-lens QA / dogfooder".

```
Agent({
  subagent_type: "harness:dogfooder",
  prompt: "Dogfood task <task_id>. PLAN.md: <task_dir>/PLAN.md. Use the product like the target user. Return high-impact findings and suggested follow-ups in your final response. Do not write task-local narrative files.",
  mode: "auto"
})
```

The dogfooder does NOT gate task completion. Its output is:
- subagent final response — structured suggestion list with prioritized backlog.
- If high-impact findings exist, it recommends re-planning or creates a follow-up
  task when the finding blocks the agreed done criteria.

Skip conditions:
- `runtime_verdict` is not PASS (QA must pass first).
- Task is maintenance-only (no user-facing change).
- `git diff --name-only` against the **user-facing globs** below produces an empty intersection (pure infra/refactor).

**User-facing globs** (used by the diff intersection check):

```
**/*.{tsx,jsx,vue,svelte,html,css,scss}
plugin/agents/**
plugin/skills/**
**/routes/**
**/api/**
bin/**
cli/**
README.md
doc/changes/**
```

Exact shell predicate. **Source of truth: `TASK_STATE.yaml touched_paths`** (refreshed every `task_verify` call). The previous `git diff --name-only HEAD~1 HEAD` reading was unreliable — it returned the last single commit only, missing uncommitted task changes and earlier commits in multi-commit batches, so Phase 7.7 routinely read the previous task's files.

```bash
_USER_FACING=$(python3 - <<'PY' 2>/dev/null || echo ""
import yaml, sys, re, pathlib
state_path = pathlib.Path("<task_dir>/TASK_STATE.yaml")
if not state_path.exists():
    sys.exit(0)
state = yaml.safe_load(state_path.read_text()) or {}
paths = state.get("touched_paths") or []
pat = re.compile(r"(\.tsx|\.jsx|\.vue|\.svelte|\.html|\.css|\.scss)$|^(plugin/agents/|plugin/skills/|.*/routes/|.*/api/|bin/|cli/|README\.md|doc/changes/)")
for p in paths:
    if pat.search(p):
        print(p)
        break
PY
)
[ -z "$_USER_FACING" ] && echo "SKIP_DOGFOOD" || echo "RUN_DOGFOOD"
```

`SKIP_DOGFOOD` short-circuits the spawn; `RUN_DOGFOOD` proceeds to the Agent call above. The predicate intentionally errs toward running the dogfooder when the intersection is non-empty even by one file — a false positive is cheaper than a missed user-facing regression. On TASK_STATE.yaml missing or parse error, the predicate emits empty → SKIP_DOGFOOD (safe default; a dogfooder skip is recoverable, a wrong-files dogfood run is noise).

### Phase 7.8: Harness source auto-install (post-QA, pre-close)

When the repository being changed is the harness plugin source itself (root
`install.py` plus `plugin/` and `plugin-codex/` are present), a terminal fresh
review+QA PASS MUST immediately run:

```bash
python3 plugin/scripts/install_verified.py \
  --task-dir doc/harness/tasks/<task_id>
```

This is part of completion, not a suggestion. Run it after the last source
edit and verification, before `task_close`, so stale installed hooks cannot
prevent the task from reaching the close gate. Capture the installer exit code
and runtime summaries. The trusted helper verifies canonical harness identity,
fresh review+QA receipts, HEAD/worktree freshness, and a fingerprint-scoped
success marker before it invokes `python3 install.py --force`. A failed install blocks completion; never claim the
source is deployed. Do not rerun installation for docs-only edits after this
step. The current process may retain already-loaded MCP/hooks, so report when a
new session is required without forging receipts. The same successful
fingerprint is skipped under a task-local lock. Skip only when the user
explicitly opts out of installation.

### Phase 8: Close and final response

**Concreteness standard:** every user-facing claim must locate without searching — name file, function, line, test, command, or subagent lens. "Fixed auth bug" is not acceptable; `auth.ts:47 — added null check on session.token` is.

Before `task_close`, verify these are true:

1. Every AC is `passed` or explicitly `deferred` in CHECKS.yaml.
2. `task_verify` reports a fresh PASS after the last edit.
3. Required QA/UX subagents were spawned when the runtime exposed them; hook-owned `SUBAGENT_RECEIPTS.jsonl` proves the start.
4. User feedback events have terminal disposition in task state: `promoted`, `handled-local`, `deferred`, or `rejected`.
5. `CONVERSATION.md` has no open `<!-- item: ... status=open -->` markers; captured items name a durable ref, rejected items name a reason, deferred items name a follow-up task/goal.
6. Durable docs are updated when the task changed user-visible behavior, externally consumed API contracts, reusable guidance, significant decisions, external constraints, user-stated durable rules, or reusable implementation knowledge.
7. Reusable EUREKA discoveries, user corrections, dogfood findings, setup recipes, and repeated friction are either promoted to committed artifacts or explicitly rejected/deferred with a concrete reason.

Call `task_close`, then provide a concise final response with:

1. Summary (one sentence per AC or task slice)
2. Files changed (important files only, with one-line description)
3. Verification results and subagent lenses used
4. Durable docs or learning artifacts updated, or a specific no-doc rationale
5. Remaining risks, deferred items, or follow-up tasks

**Quality Score:**
```
score = (ac_completion × 0.40) + (test_coverage × 0.30)
      + (adversarial_clean × 0.20) + (scope_discipline × 0.10)
```
- `ac_completion` = (done / total) × 10. Deferred = 0.5.
- `test_coverage` = (tested paths / total changed paths) × 10. No framework → 5.
- `adversarial_clean` = max(0, 10 - (crit × 3 + high × 1.5 + med × 0.5)). Fixed at 0.25 weight.
- `scope_discipline` = 10 / 7 / 4 / 0 (none / auto-added / justified / unjustified).

**Cleanup:** PROGRESS.md persists beyond Phase 8 as the scope-lock contract for any post-close edits. Keep PROGRESS.md in place; do not create a separate narrative handoff artifact.

### Phase 8.5: Reflect and Log (capture-when-fresh, no quota)

When you discover something genuinely useful during develop — a real bug whose fix is non-obvious, a build/test/tool gotcha that wasted a cycle, a workaround that's worth knowing next time — log it the moment you find it, **while it's fresh**. Log only concrete, reusable facts at discovery time; leave the log untouched when there is no durable learning. A signal-free entry is worse than no entry.

A good entry names a concrete fact + a concrete fix, both groundable in files / commands / test output. Examples that pass the bar:

- `pytest tests/regression/<task>/` requires `test_` prefix on filenames; the harness QA codifier auto-prefixes for this reason (file:`plugin/scripts/qa_codifier.py`).
- `git diff --name-only HEAD~1` returns paths only — to filter by AC-targeted source, mirror-grep the import graph rather than relying on filename prefixes.
- The `task_close` MCP no longer auto-writes gate-warn entries to learnings.jsonl (changed 2026-05-08); previous logs are noise, not learnings.

Examples that do NOT pass the bar:
- "What took longer than expected" / "What surprised me" — open-ended self-prompts produce vague entries. If a real friction point exists, it'll surface concretely; if not, leave it.
- Confidence-calibration tables when AC count is small — statistical noise, not durable knowledge.

Schema:

```bash
echo '{"ts":"'"$(date -u +%Y-%m-%dT%H:%M:%SZ)"'","type":"operational|pitfall|eureka|feedback","source":"develop","key":"SHORT_KEY","insight":"FACT + FIX","files":["<path>"],"task":"'"<task_id>"'"}' >> doc/harness/learnings.jsonl 2>/dev/null || true
```

`type=operational` for tooling/syntax/path facts. `type=pitfall` for traps to avoid. `type=eureka` for first-principles discoveries. `type=feedback` for user-stated preferences. `files` enables staleness detection if a referenced path is later deleted. Silent-fail on write error. Never blocks.

### Phase 8.5.1: Feedback-Derived Rules (judgment required, capture optional)

Review user corrective feedback from the task. Convert corrective feedback into a reusable conditional behavior rule only when it can be reduced to a readable "When X, do Y. Verify by Z." instruction.

Classify the task as exactly one:
- `none` — no user feedback implies a future behavior rule.
- `captured` — feedback produced a reusable conditional rule and it was recorded in `learnings.jsonl` for promotion, or directly in a committed durable artifact.
- `rejected` — feedback looked like a preference or complaint but should not become a rule. Record the reason in task state or final response.

Capture only rules that have all three parts:
- Trigger: the situation where the rule applies.
- Action: what the agent should do.
- Verification: how tests, review, task state, or final response can prove the rule was followed.

Reject entries that are blame narratives, task-local preferences, vague style opinions, or one-off urgency requests. Write behavior rules for Tier 2 docs; convert incident-shaped lessons into behavior or reject them.

When captured, the durable artifact or learning text must be readable prose, for example:

```markdown
## Feedback-Derived Rules

Status: captured

When changing runtime-specific harness plugin behavior, review both the canonical `plugin/` tree and the runtime-specific tree such as `plugin-codex/`.

Verify by explaining in the final response which side changed and why any other side was left unchanged.
```

If the rule should enter Tier 2, log a structured learning so the promotion script can render readable Markdown:

```bash
echo '{"ts":"'"$(date -u +%Y-%m-%dT%H:%M:%SZ)"'","type":"feedback-rule","source":"develop","key":"SHORT_RULE_NAME","trigger":"<situation>","action":"<behavior>","verification":"<how to prove it>","reason":"<why this prevents recurrence>","task":"'"<task_id>"'"}' >> doc/harness/learnings.jsonl 2>/dev/null || true
```

### Phase 8.5.2: Commit-backed Learnings (mandatory classification)

Classify whether this task produced knowledge that must be shared through git.
`doc/harness/learnings.jsonl` is local, gitignored staging; it does not satisfy
the shared-memory bar by itself. Future contributors only inherit what lands in
committed artifacts.

Classify candidates before close:

```markdown
## Commit-backed Learnings

Status: none | captured | rejected

- captured: <committed path> — <rule/fact now shared>
- rejected: <candidate> — <why it is task-local, noisy, or not reusable>
```

Use `captured` when a reusable discovery, dogfood finding, user correction,
setup command, or repeated friction changed a committed skill, script, test, or
durable doc in this task. Use `rejected` when you considered a candidate but it
should remain local. Use `none` only when there was no reusable learning.

### Phase 8.5.3: Self-Healing Candidates (mandatory classification)

Classify whether this task revealed a recurring failure mode that the harness or
project can prevent next time. This includes development friction, QA-discovered
verification gaps, tool/schema drift, CI command drift, brittle setup commands,
and repeated manual recovery steps. QA lenses should surface candidates in their
final response; Phase 8 owns the final classification.

For harness-improvement candidates, treat dogfood feedback and agent retros as
hypotheses until checked against the repo. Before marking a candidate `applied`
or proposing follow-up work, inspect the owning code path and relevant tests.
Classify the claim as `confirmed`, `partially-confirmed`, `already-handled`,
`duplicate`, `not-found`, or `needs-runtime-check`. If it is
`partially-confirmed`, rewrite it to the smallest accurate failing case. If the
raw proposal would weaken an existing QA/runtime/close gate, preserve the gate's
safety intent by proposing an explicit alternative evidence tier rather than
removing the gate. Record the corrected scope and evidence path in task state or final response.

If develop or QA discovered a working repo-local setup/test/dev-server command
after one or more failed attempts, record it before close as a pending runbook
candidate:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/runbook_memory.py capture \
  --id "<short-id>" \
  --description "<what this starts/tests/verifies>" \
  --failed-command "<representative failed command>" \
  --command "<final successful command or wrapper script>" \
  --failure-class "<missing-env|wrong-host|crlf-env|missing-tool|dev-server-bootstrap|db-bootstrap>" \
  --source-phase "<develop|qa-browser|qa-api|qa-cli>" \
  --source-task "<task_id>" \
  --gotcha "<why the first attempt failed>"
```

The candidate is not shared memory yet. Before close, either approve
it into a committed artifact (`doc/harness/runbooks.yaml`, manifest, script, or
durable doc), ask before deferring it, or reject/skip it as one-off/noisy.

Use this structure in task state or the final response:

```markdown
## Self-Healing Candidates

Status: none | applied | deferred | rejected

- applied: <failure mode> — <changed committed path> now prevents recurrence
- deferred: <failure mode>
  user_decision: <separate task | not now | other user wording>
  reason: <why not in this task>
  proposed_artifact: <path> | proposed_task: <task>
- rejected: <candidate> — <why one-off, noisy, or not worth automating>
```

Use `applied` only when this task changed a committed artifact named on the
bullet. If the improvement is real but large or risky, do not silently defer:
ask the user before close. On Claude, use `AskUserQuestion`. On Codex, use
`request_user_input` when available; otherwise ask in conversation and wait for
the user's reply. Use `deferred` only after recording that user decision plus
the reason and proposed artifact/task. Use `rejected` for one-off environment
noise or non-reproducible complaints. Use `none` only when develop, QA, dogfood,
and close produced no self-healing signal.

### Phase 8.6: durable docs

Mechanical. Read `TASK_STATE.yaml` touched paths + `doc/CLAUDE.md` registered roots. For each file, map to doc root. Call `task_verify`.

When the task changes `doc/<area>/REQ__*.md`, `GUIDE__*.md`, `ADR__*.md`, or
`POLICY__*.md` OR the task's `<task_dir>/USER_FEEDBACK.jsonl` is non-empty
(per C-101 in `CONTRACTS.local.md`), spawn the documentation-review subagent after
durable docs. It verifies both durable docs consistency and durable doc quality, and
runs the Retrospective REQ pass over USER_FEEDBACK.jsonl to catch user-stated
requirements that closed without becoming durable REQ docs. The task cannot
close with unresolved durable-doc gaps; a changed REQ with vague or missing
observable behavior is a FAIL, not a warning. Candidate REQs written by the
Retrospective pass land with `status: candidate` frontmatter and do not block
close on their own. Do not write legacy critic markdown artifacts.

### Phase 8.7: Distilled Change Doc

One-paragraph summary of the task's user-visible behavior change. Lives at `doc/changes/<date>-<slug>.md`. Optional if no user-visible change. Writer skill consumes this for release notes.
