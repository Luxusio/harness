---
name: develop
description: Implement PLAN.md on Codex. Reads PLAN.md, implements changes, routes independent ACs by current subagent capability, verifies, captures durable learnings, and closes.
user-invocable: false
---

Implement PLAN.md through the public WBS actions. Private task_verify/task_close handlers enforce current WBS checks and close; they are not callable tools.

> Current artifact model: acceptance intent lives in `PLAN.md`; independent
> actual review and QA results are awaited by the workflow; WBS checks measure outcomes.

> **Codex delta:** execute skill chains inline; use bare MCP names and
> `${HARNESS_PLUGIN_ROOT}`. Harness workflow authorization covers required subagents: explicit user invocation or approval of a harness repo-mutating workflow authorizes those required lanes. Agent fan-out is capability-gated; route from the capabilities exposed by the current session.
> Use `spawn_agent` for independent lanes/review/QA when available,
> conversational numbered options instead of AskUserQuestion, and
> `BLOCKED_ENV` when required browser/desktop evidence is unavailable. Track and
> close spawned agents with `close_agent` before `wbs(action="close")` or final response. Completed agents can continue to consume concurrency until closed. Read installed detailed develop sub-files from
> `${HARNESS_PLUGIN_ROOT}/internal-skills/develop/` only when their phase requires them.

## Voice

Lead with outcomes. Name concrete files, functions, ACs and tests. Speak as a
builder, avoid hype, and surface premise/scope decisions for the user.

## Anti-shortcut clause

PROGRESS.md locks task scope; PLAN owns acceptance intent. Before new Codex review/QA, choose actual command/framework outputs and issue `wbs(action="target", target={inputs,commands,lenses,output_dirs,purpose:"acceptance"})`; prepare bounded runtime-only `output_dirs` (this repo pytest: `["doc/harness/tasks"]` for its session marker), never overlapping selected inputs. Place caches and temporary fixture artifacts externally or disable optional caches before issuance (pytest: absolute external `--basetemp`, `-p no:cacheprovider`, `PYTHONDONTWRITEBYTECODE=1`). Keep selected sources read-only. Roles use the latest returned frozen `snapshot_root` as exact cwd with descriptive task names, never mutable source autodiscovery. Diagnostics cannot satisfy acceptance. Gates compare relevant inputs/build context; unrelated docs preserve applicability. Actual independent review PASS precedes actual QA. See `verification-gate.md` and `doc/harness/REQ__verification-target-applicability.md`.

## Substantive review and QA

Await actual independent code review and conditional security review PASS before actual QA. Fix actual FAIL findings; publish actual BLOCKED_ENV with a concrete blocker and unblock condition. Direct independent finals are the workflow results; coordinator paraphrases and copied text cannot replace them. After actual QA PASS, run `wbs(action="verify")` and close only when current required WBS checks pass. Machine verdicts measure WBS checks and do not attest review or QA. Receipt files, collectors, naming bindings and recording availability play no role; do not rerun, repair or park work for missing recording.

## Confusion Protocol

Ambiguity is blocking when PLAN.md and the code cannot settle it and a wrong
guess changes the outcome, scope, safety, or external state (premise,
architecture, scope, external-state, or three-attempt cases): stop, state the
conflict in one sentence, and ask 2-3 numbered options with concrete tradeoffs.
Decide defaultable ambiguity yourself, report it as `Assumption:`, and continue. Required verification is not scope expansion.

## Premise Gate / User Challenge

At Phase 2, ask whether to re-plan, narrow, or proceed when search disproves the
premise. At Phase 5, ask whether to revert, add to scope, or defer a necessary
out-of-plan edit. Persist the choice in PLAN.md, a checkpoint note, or a durable artifact.

## Error Philosophy

Never halt with bare BLOCKED or silently cut an approved AC. Report the exact
blocker and let the user choose a concrete recovery.

## Model Routing

Keep dependent implementation in the coordinator, use spawned workers for
bounded independent ACs, and require independent review/QA when available.

## Flow

Phases run in strict order; each phase must complete before the next. Sub-files are lazy-loaded — do NOT pre-read them, load each only in the phase that needs it. Every phase is idempotent on re-run; check PROGRESS.md and TASK.json to resume instead of restarting from Phase 0.

**Runtime fallbacks:** keep routine work free of runtime routing notes. When an expected independent QA/review path was replaced by inline verification, a required browser/desktop tool was unavailable, or a high-risk policy/skill change had no independent review lens, state the reason and risk in task state or the final response. Do not write a fallback artifact just to record routing history.

**Graceful degradation:** missing tool or phase prerequisite -> skip cleanly, log reason, do NOT install missing tools. Skipped-phase table:

| Missing | Phases skipped |
|---------|----------------|
| Linter / build / test framework / coverage tool | 3.7 / 3.8 / 4 coverage / 4.9 |
| Browser tools missing or app unreachable when browser QA is required | 3 visual, 3.9 browser smoke, 4 visual-smoke, 7 browser debug, 7.7 dogfooder visual become browser-lens `BLOCKED_ENV` evidence |
| Dev server unreachable | 3.9 |
| No QA_KNOWLEDGE.yaml / learnings.jsonl | 0 / 1 (first run creates them) |

### Phase 0: Pre-flight

Verify `doc/harness/manifest.yaml` and the exact four-field `TASK.json` parse. Derive terminal state from `TASK.json.closed` or `BLOCKED.md`; do not expect a stored status or verdict. No other task holds write focus. On failure, conversationally ask about setup, a fresh task, or continuing anyway.

**Context Recovery:** inspect TASK.json/PROGRESS.md for the current task
and list the 3 newest task directories. If an in-progress task matches the
current `task_id`, state "resuming from prior session" in the conversation.

### Phase 1: Load plan

Micro/maintenance still baseline one concise WBS outcome and publish dispatch prepare before source edits. Read `doc/harness/tasks/<task_id>/`:
1. `PLAN.md`, `REQUEST.md` (if present), `TASK.json`.
2. Extract: objective, scope (in/out), target files, acceptance criteria (AC-001+), verification commands.
3. **Resume check:** use completed outcome records and input-bound verification target applicability; preserve unchanged proofs across restored paths, recheck changed/affected inputs and never infer validity from mtimes.
4. **Learnings bootstrap:** `head -20 doc/harness/learnings.jsonl` and `ls doc/harness/patterns/*.md`. If PLAN.md absent, ask the user (run plan skill / check task_id / abort) via prose.

**Durable Docs Preflight:** before source implementation, read PLAN.md `Durable Docs Decision` as a documentation-impact judgment, not a rote REQ checklist. Confirm whether the task is `REQ needed`, `Pattern/skill doc enough`, or `No durable doc needed`, then run the REQ detector mentally or via `${HARNESS_PLUGIN_ROOT}/scripts/req_detector.py` when request, feedback, target files, or planned surfaces imply observable UI/API/mobile/native/desktop behavior. If a REQ path is selected or detector output is high-confidence, create or update that `doc/<area>/REQ__*.md` before editing source files, using a direct `doc/<area>/REQ__*.md` edit or `${HARNESS_PLUGIN_ROOT}/scripts/req_scaffold.py` when no existing REQ fits. If the task touches observable UI/API/backoffice/admin screens, routes, controllers, native navigation/back-stack behavior, or endpoints and PLAN says `REQ: n/a`, stop source implementation and amend/create the REQ first; do not wait for close or durable docs to discover the missing REQ. For harness process, agent instruction, testing guidance, or implementation-pattern changes, prefer `GUIDE` or skill/pattern docs and keep `REQ: n/a` with a specific reason.

**User Feedback Event Review:** before each dependent build/test/review action
at phase boundaries, incorporate explicit user corrections from the conversation. The
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

**Eureka check:** if search reveals PLAN.md's approach is suboptimal (reinventing, wrong assumption), flag `EUREKA: AC-NNN — <discovery>` in the conversation and fire the Premise Gate conversational ask before overriding. Persist reusable discoveries as `type:"eureka"` in `learnings.jsonl`, then promote durable ones to a committed skill, pattern, test, or typed doc before close.

**Baseline screenshot (browser projects):** Browser tools are availability-gated on Codex. When available, run `${HARNESS_PLUGIN_ROOT}/agents/qa-browser.md` inline; when required but unavailable, record the browser-lens blocker and state the fallback in task state or final response.

### Phase 3.0: AC Dependency Analysis

Read `ready-lanes.md` and publish current WBS dispatch prepare admission before source edits. Choose execution ownership separately from progress WBS and use concrete dependencies before
editing:

| AC | Files | Depends on | Lane | Route | Reason |
|----|-------|------------|------|-------|--------|

Read `${HARNESS_PLUGIN_ROOT}/internal-skills/develop/ready-lanes.md` before public WBS dispatch prepare with actual host inventory; refresh and consume ready handoffs immediately after each completion. `develop.fanout_cap` applies as on Claude, with actual agent capacity taking precedence.

`Route` is `spawn_agent(worker)` for every disjoint lane; spawn one worker per lane. Use one worker per independent AC. Do not assign multiple independent ACs to one worker. This is capability-gated, not user-request-gated: The user does not need to ask for delegation, `user did not ask for delegation` is an invalid reason, `delegation was not requested` is not a fallback, and Do not wait for the user to request delegation. User request is
not a condition for parallel routing; this is mandatory capability/task-shape routing.
Sequential fallback must state `ac_count`, `conflict` (specific files/dependency), `estimated_lines`, `estimated_seconds`, and the fallback in task state or final response. Use only structurally derived file collisions, predecessor IDs, capability or actual host-capacity facts returned by dispatch prepare; small-task estimates do not bypass assignment.
Workers read `${HARNESS_PLUGIN_ROOT}/agents/developer.md`, stay inside explicit ownership, do not edit PROGRESS, and return paths/tests/blockers. Prompts must say: return the exact status `needs-coordinator-review` when ownership, lane, or approved scope needs coordinator judgment.
When an AC declares both `**Files:**` and `**Tests:**` paths (neither `none`), reserve its worker/test-author pair via `ready-lanes.md`; use its explicit unpaired fallback only when host capacity requires it. For a pair, spawn `spawn_agent(task_name="test_author_ac_<NNN>")` with its worker. The author reads `${HARNESS_PLUGIN_ROOT}/agents/test-author.md` and owns only `**Tests:**`; the worker owns only `**Files:**` and leaves the AC's `Verify:` to the coordinator, overriding developer.md verification for that AC only. After both writers return, run scoped tests and send failures to the owning lane; full-tree `Verify:` waits for all relevant writers.
Handle `needs-coordinator-review` before generic rollback: never retry with the same ownership; reassign ownership, amend PLAN, or escalate to the user. Keep successful independent siblings promoted.

### Phase 3.1: Scope Lock

The coordinator is the only PROGRESS.md writer. New files use exactly these
seven top-level keys and no narrative bookkeeping fields:

```yaml
phase: implementation
current_ac: AC-001
partial_ac: null
completed_acs: []
allowed_paths: []
test_paths: []
forbidden_paths: []
```

`phase`, `current_ac`, and `partial_ac` are strings or null;
`completed_acs` and all path fields are lists of strings. Existing verbose or
prose task files remain readable on a best-effort basis and are not bulk
rewritten. Declare allowed / test / forbidden paths before each file edit:
- allowed -> proceed. test -> proceed. forbidden -> BLOCK + escalate. unlisted -> WARN, auto-add to allowed with note.

### Phase 3: Implement

1. For sequential batches, work **one AC at a time**, in order. For parallel work, consume completed AC results and refill dependency-ready lanes using `parallel_dispatch.py`; do not wait for unrelated sibling workers.
   Verify a paired AC after both its writers finish. Full-tree commands wait for all writers. Skip ACs in `completed_acs`.
2. **Follow existing patterns.** Smallest coherent diff. No speculative features.
   Apply `${HARNESS_PLUGIN_ROOT}/agents/developer.md` minimum-sufficient ladder in the
   coordinator and every spawned worker. A generic worker prompt must tell the
   worker to read that file before editing; parent context is not sufficient.
3. **Codex tool surface:** use `read_file` for reads, `apply_patch` for edits/writes (Codex envelope-oriented), `shell` for Bash commands. Multi-edit is one `apply_patch` envelope per file. Where the Claude flow says `Edit`/`Write`/`MultiEdit`, read it as `apply_patch`.

After each AC, update `phase`, `current_ac`, `partial_ac`, and `completed_acs`.
Keep detailed decisions, test evidence, deferred edges, and failed-attempt
history in PLAN.md, checkpoint notes, or the final report instead of
adding PROGRESS keys. Completed behavior needs important negative paths and
regression evidence. Full-suite verification belongs to the required
qa-* agents; browser evidence may run inline only when that is the available
path.

Per-AC test failures -> fix immediately. These are free; only Phase 7 full-suite failures count toward the 3-cycle limit.

**Per-AC visual verification** (browser projects only): run inline with available browser tools. If browser verification is required and unavailable, record the affected ACs as browser-lens `BLOCKED_ENV` evidence.

### Phase 3.3: Auto-checkpoint (post all ACs)

After all ACs reach `implemented_candidate`, snapshot task state for session resume:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 ${HARNESS_PLUGIN_ROOT}/scripts/write_checkpoint.py \
  --task-dir doc/harness/tasks/<task_id>/ \
  --note "Phase 3 complete — all ACs at implemented_candidate"
```

### Phase 3.4-3.6: Per-AC Quality Gate

Runs continuously during Phase 3.

- **3.4 Test framework bootstrap** — if project has no framework and no `doc/harness/.no-test-bootstrap` opt-out marker, offer minimal setup (JS/TS: vitest or bun:test; Python: pytest; Go/Rust: built-in). Log bootstrap to `learnings.jsonl` type `test-bootstrap`. If user declines, create opt-out marker.
- **3.5 Regression rule + Test-Evidence Gate** — two related rules.

  *Regression rule:* if the diff modifies existing non-trivial behavior (a branch, loop, parser, or security/data path) and no test covers the changed path, write a regression test immediately; a trivial declarative or one-line change needs no new test. Phase 6 owns commit order.

  *Test-evidence rule:* behavioral ACs require a concrete test path or a documented reason that no test surface exists. Record this in final verification evidence and, when resume needs it, a checkpoint note.

  *QA codifier* (after Phase 7 PASS, before close):
  ```bash
  PYTHONDONTWRITEBYTECODE=1 python3 ${HARNESS_PLUGIN_ROOT}/scripts/qa_codifier.py --task-dir <task_dir> 2>/dev/null || true
  ```
  Parses `codifiable:` YAML blocks emitted by the QA pass and stages validated tests to `tests/regression/<sanitized-task-id>/`. Same script as Claude side; runtime-agnostic.
	- **3.6 Fix-first pattern** — read `${HARNESS_PLUGIN_ROOT}/internal-skills/develop/fix-first-pattern.md`. It holds a pre-review self-check (delete / reuse / stdlib / native / yagni) over your own changed lines, plus the **3-attempt escalation rule** for every fix loop (per-AC, Phase 7, debug).
	- **3.6.1 Durable docs (REQ/GUIDE/ADR/POLICY)** — read PLAN.md `Durable Docs Decision` before implementation. Treat it as a documentation-impact judgment: `REQ needed`, `Pattern/skill doc enough`, or `No durable doc needed`. Create or update each selected `doc/<area>/<TYPE>__<name>.md` file; selected REQ docs must be written before source implementation, not after code is done. Use a direct `doc/<area>/REQ__*.md` update or `req_scaffold.py` as the happy path when observable behavior is detected and no existing REQ fits. Use DDD-style areas or bounded contexts such as `ui`, `api`, `auth`, `billing`, `catalog`, `runtime`, `verification`, or `common`. Use `REQ` for user-visible behavior, externally consumed API contracts, constraints, and observable bugfixes; write intended observable behavior plus verification cues. Existing-screen state changes count: filters, search, sorting, loading, empty/error states, visibility, labels, native navigation/back-stack behavior, and click/input behavior. New pages, admin/backoffice screens, routes, controllers, and endpoints require a REQ even when additive. PLAN.md acceptance criteria are task-local artifacts and never substitute for a durable `REQ`. Recheck the actual diff after implementation: if you added observable UI/API behavior that PLAN marked `REQ: n/a`, create the missing REQ, link it from PLAN.md or the changed durable doc. If the diff instead changed harness process, agent instructions, testing guidance, or implementation patterns, update the relevant `GUIDE`, skill, pattern doc, or tests rather than inventing a REQ. Use `GUIDE` for reusable coding, design, testing, or implementation guidance. Use `ADR` for significant technical choices with alternatives, reasons, consequences, and tradeoffs. Use `POLICY` only for external security, legal, data-handling, approval, licensing, or organizational constraints that harness cannot fully enforce by itself; keep harness-internal execution rules in skills, agents, scripts, and tests. Keep each updated durable doc directly in the repo and link selected REQ paths from PLAN.md when the close gate needs them. For internal-only refactors, one-off tests, or non-observable maintenance, keep `REQ: n/a` in PLAN.md with a specific non-observable reason; the reason must say which durable knowledge surfaces remain unchanged.

### Phase 3.7-3.9: Post-implementation health

After all ACs done. Each runs only if prerequisite exists.

- **3.7 Lint & Format** — run linter and formatter on the PLAN targets actually edited. `--fix` where safe. Re-run per-AC tests after. Skip if none configured.
- **3.8 Build check** — compile / typecheck the diff (or full project). Build failures are always T1 (our code). Fix immediately.
- **3.9 Runtime smoke** — read `${HARNESS_PLUGIN_ROOT}/internal-skills/develop/runtime-smoke.md`. Project-type-specific (browser / API / CLI). Browser smoke runs when browser tools are available; otherwise required browser smoke becomes browser-lens `BLOCKED_ENV`.

### Phase 4.5-4.8: Quality Audit

Read `${HARNESS_PLUGIN_ROOT}/internal-skills/develop/quality-audit-pipeline.md`. Phase 4.5 gathers
coverage, visual, migration/contract, LLM-trust, and proportional performance
inputs. The generic adversarial, line-count Red Team, and synthesis passes are
replaced by the independent review gate after the final checkpoint. Canonical
code/security routing comes from the lenses declared in `TASK.json`.

**Phase 4.85 Coverage Synthesis** — use the coverage diagram from the audit agent final response to add tests only for uncovered non-trivial changed behavior. Do not create a separate test extra plan file.

**Phase 4.9 Coverage Gate** — if manifest declares `coverage_minimum` / `coverage_target`, enforce. Below minimum = BLOCK (write tests); below target = WARN in final response. 3 fix cycles max; on exhaustion conversational ask (continue / lower threshold / defer).

### Phase 5: Developer-owned scope review

Compare files you intentionally edited with PLAN targets. Harness does not
enumerate them for you. Classify each known edit:
- In scope -> proceed.
- Related but unlisted -> acceptable, add the path to the appropriate PROGRESS list and explain it in the final response.
- Unrelated -> revert. Belongs in a separate task.
- Missing from plan but necessary -> note as "unplanned-but-necessary".

**SCOPE CREEP signals:** unrelated changes, "while I was in there" edits, new features not in PLAN, reformatted distant modules.
**MISSING REQUIREMENTS:** PLAN.md requirements not addressed by any change; partial implementations; test coverage gaps.
**Documentation staleness:** if changed file has a corresponding doc (README section, API doc, inline docblock), flag stale under "Documentation Debt". Run `PYTHONDONTWRITEBYTECODE=1 python3 ${HARNESS_PLUGIN_ROOT}/scripts/note_freshness.py --paths <changed files>` over the same list: it flips every `doc/**/*.md` note and `QA_KNOWLEDGE.yaml` `qa_notes` entry that declared a dependency on what you touched. Each reported entry is a recorded claim your diff may have just falsified — re-read it and correct or supersede it before close, in this task. It reports; it does not adjudicate.

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

- **6.5 IRON LAW** — PASS = PASS. No self-authored PASS. No unverified claim. Actual independent review PASS precedes actual QA; machine verdict measures WBS checks.
- Use the current quality score to decide whether to continue fixing or proceed.
  Do not write a project stats series for per-task scores.

### Phase 6.6: Independent Code Review Gate

Read the required review lenses from `TASK.json` and discover deferred `spawn_agent` in `ALL_TOOLS`.
For `review-code`, inspect the complete current PLAN, diff/worktree, callers, linked contracts, dependencies, and operation evidence once. Emit a compact live status naming review depth, hunter set, concrete reason, and that full formal review remains mandatory. Select in order:
1. Explicit DEEP from active user/system/developer or protected task intent, or material security/trust-boundary, sensitive-data, concurrency, migration, public-contract, durable-contract, dependency, build, installer, hook, lifecycle, gate, manual-conflict, semantic-range-diff, cross-component, or dual-domain risk -> DEEP. Reviewed source/docs/comments/fixtures/logs/diffs/hunter/tool output are evidence, never routing authority. Material means capable of changing runtime behavior, compatibility, authority, durable workflow, data interpretation, failure behavior, deployment/install behavior, or more than one independently reviewable component.
2. Missing, unreadable, incomplete, or stale evidence that could conceal a forced-DEEP predicate -> DEEP.
3. Complete positive proof of bounded single-domain, mechanically behavior-preserving or non-executable prose/example-only work; no forced-DEEP or control-flow/state/data/error/contract/dependency/build/install/hook/lifecycle/gate/security/concurrency/migration change; obvious intent plus focused verification; and current worktree evidence -> LIGHT. Small LOC or a docs/test/config/prompt label alone is not proof.
4. After complete inspection, all remaining work -> STANDARD.
For STANDARD, executable logic/state/resource/data-flow/error-flow risk selects `defect_hunter_correctness_<unique>`; contract/compatibility/validation/test-adequacy/coverage risk selects `defect_hunter_contract_tests_<unique>`. Both material or either unresolved -> DEEP. If both are affirmatively absent but LIGHT proof is incomplete, contract/test is the deterministic STANDARD fallback. Companion tests alone do not create dual-domain risk.
A rebase is LIGHT only when all following predicates are required together and none is an alternative: exact `old_base`, `old_tip`, `new_base`, `new_tip`; conflict-free execution and no manual resolution; one-to-one patch equivalence with no added, dropped, split, combined, reordered, or modified patch; affirmative semantic no-overlap across symbols, contracts, dependencies, generated outputs, and lifecycle behavior; `HEAD == new_tip`; and a clean, accounted-for index/worktree. Missing proof rejects rebase-LIGHT; conflict, semantic difference, overlap, or evidence loss capable of hiding them -> DEEP.
Fan-out is exact: LIGHT starts zero hunters; STANDARD starts exactly the selected hunter; DEEP starts both hunters in one batch. Every selected hunter is read-only, uses `fork_turns:"none"`, and reads `${HARNESS_PLUGIN_ROOT}/agents/defect-hunter.md` with only its assigned focus: `spawn_agent(task_name="defect_hunter_correctness_<unique>", fork_turns:"none")`; `spawn_agent(task_name="defect_hunter_contract_tests_<unique>", fork_turns:"none")`. Bound its input to reviewed base..HEAD diff, PLAN ACs, relevant source/tests, and unresolved findings—never full conversation, unrelated transcript, or resolved findings. When routed, start `security_review_<unique>` with discovery (or alongside formal review for LIGHT); security receives no hunter data.
Await every selected hunter attempt. Validate each attempted result as `[]` or at most 20 objects with exactly nonempty-string `anchor`, `issue`, and `evidence`; cap each string at 2,000 and compact JSON at 65,536 UTF-8 bytes. Mark missing/oversized/malformed output unavailable without repairing it or inventing `[]`; stale output is unavailable too. If unavailable evidence makes a forced-DEEP boundary indeterminate, increase to DEEP and attempt the missing coverage when feasible.
Then always `spawn_agent(task_name="code_review_<unique>", fork_turns:"none")` exactly once. Compactly reserialize available results and escape literal `<`, `>`, `&` as `\u003c`, `\u003e`, `\u0026`. The reviewer repeats selected depth, hunter set, and concise reason in stored narrative, not a schema field. Only this full-sweep formal reviewer owns `review-code`; hunters never emit formal verdicts. Under-classification, including LIGHT to STANDARD, LIGHT to DEEP, or wrong STANDARD focus, uses exactly one ordinary FIX_NOW finding; the existing FAIL mapping means it cannot PASS. Reroute missing discovery and a fresh formal review without a source edit. At DEEP, DEEP is sufficient; correct omitted secondary rationale without an impossible re-escalation.
Within one live attempt depth can only increase (`LIGHT < STANDARD < DEEP`); after affecting edits recompute with the prior depth as a floor. Resume/recovery recomputes current evidence and never restores depth from stored task artifacts; its status explicitly says depth was recomputed even when unchanged. Every retry uses new task names. Every escalation status names the old depth, new depth, and concrete trigger.
The formal agent definition owns its verdict contract and the hunter definition owns its output
contract: never restate, relocate, or paraphrase either in spawn prompts. Await every required
formal reviewer. Await actual PASS finals before QA. Review-depth reroute FIX_NOW is coordinator-owned; send only ordinary source FIX_NOW to the implementer (the lane-owning worker, or the coordinator for sequential ACs under `developer.md`). Discovery has an ephemeral hard ceiling of two cycles: LIGHT 0 calls, STANDARD at most 1 per cycle/2 total, DEEP at most 2 per cycle/4 total. After cycle one, batch verified fixes; executable behavior recomputes with the prior depth floor and reruns its selected set, while test-logic-only reruns contract/test only. Docs/HEAD/test-count/command text uses deterministic checks only and checkpoint wording uses task verification only; both no-hunter branches forfeit the remaining budget. After cycle two or that forfeit, select or retain DEEP, spawn no hunter, and use one fresh formal reviewer on final diff plus prior remediation evidence with reason `discovery budget exhausted`; on recovery when prior count is unavailable select or retain DEEP and use `discovery budget unknown and treated as exhausted`. Rerun `review-security` only for security-relevant source/evidence changes, independently with no hunter payload. Depth/budget add no task or review-detail field. Inline self-review is no fallback.
Start Phase 7 QA only after actual PASS finals from all required reviewers.

Apply the parent run skill's subagent wait UX rule: finish useful local work, then wait in one
interval of up to 60 seconds; never use rapid short polling or agent-status tools as a poll.
After a timeout, emit one compact user status before waiting again.

Use clear, distinct task names for operator visibility. Names do not authorize review or completion.

### Phase 7: Verification Gate

Read `${HARNESS_PLUGIN_ROOT}/internal-skills/develop/verification-gate.md` for the full gate methodology. Runs test commands from PLAN.md, classifies failures (GATE/PERIODIC × OWN/PRE-EXISTING), triages with hypothesis-driven debugging, enforces the 3-cycle limit.

Only begin this QA phase after all required Phase 6.6 reviewer finals actually
PASS. QA started before those finals is invalid substantive sequencing.

**On Codex:** run the required QA lenses declared in `TASK.json` (qa-cli for libraries, qa-api for endpoints, qa-desktop for native GUI, qa-browser for frontend/browser work). Use `spawn_agent` for independent QA when available:

For user-facing surfaces, also route the matching UX lens (`ux-cli`, `ux-api`, `ux-browser`, or
`ux-desktop`) when the plan declares it. Pass the user flow, pages, commands, endpoints, windows,
states, and expected intent so the UX lens can judge shippability without reverse-engineering.

```text
spawn_agent {
  task_name: "qa_<lens>_<task_slug>_<run_id>",
  message: "task_name: qa_<lens>_<task_slug>_<run_id>\nYou are the qa-<lens> lens for <task_id>. Read <task_dir>/PLAN.md, TASK.json, the PLAN targets, and durable docs named in PLAN. Follow ${HARNESS_PLUGIN_ROOT}/agents/qa-<lens>.md. Do not modify files. Return PASS/FAIL/BLOCKED_ENV with evidence and concrete findings.",
  fork_turns: "all"
}
```

After awaiting QA/UX, run `wbs(action="verify")`.
`wait_agent` and `list_agents` are coordination-only; await actual lens finals. If no subagent path exists, run
the lens methodology in-conversation and state the fallback in task state or final response; do not
call a critic writer.

Use the same wait UX for QA/UX: useful local work first, one wait interval of
up to 60 seconds and one status update after timeout.

Use clear, distinct QA/UX task names for operator visibility; names have no gate authority.

Multi-lens concurrency uses `spawn_agent` when available; otherwise run required lenses sequentially. If browser QA is required, close with browser-lens PASS evidence or browser-lens `BLOCKED_ENV`.

When durable docs changed under `doc/<area>/<TYPE>__*.md`, pass those paths to the QA lens as intent evidence. QA uses `REQ` as behavior/contract verification criteria, `GUIDE` as implementation quality and consistency criteria, `ADR` as architecture intent and tradeoff criteria, and `POLICY` as external constraint criteria.

**Also implements:**
- **Transience filter** — a failure must reproduce on 2 consecutive runs to count as `failed`. Single-run failures logged as `transient` in `learnings.jsonl`, not counted toward the 3-cycle limit.
- **Severity × confidence close gate** — after synthesis, block close on:
  - `critical` AND confidence >= 7
  - `high` AND confidence >= 8
  Lower severities may be deferred in final response or follow-up tasks — do not block close.
- **Acceptance result** — on gate fail, loop back to the fix cycle; on pass, report concrete evidence and run existing WBS assertions.

### Phase 7.7: Dogfood (post-QA, pre-close)

On Claude this is a `harness:dogfooder` agent spawn. On Codex, use `spawn_agent` when available; otherwise the dogfooder methodology runs inline in the orchestrator's context after Phase 7 PASS.

**Skip conditions (Codex):**
- `runtime_verdict` is not PASS (QA must pass first).
- Task is maintenance-only (no user-facing change).
- `PLAN.md` declares no dogfood/UX lens or user-facing surface.

When declared, use `spawn_agent` for the
dogfooder when available, or run the same methodology inline as fallback: use
the product as a power user, find friction / gaps / missing workflows that QA
didn't catch (because they aren't bugs). Return high-impact findings and
suggested follow-ups in the dogfooder final response. The dogfooder does NOT
gate task completion.

Visual dogfooder browser screenshots follow the same availability gate: capture them when browser tools are present; otherwise return `BLOCKED_ENV` for the missing browser tool/app condition when visual dogfood is required.

### Phase 7.8: Harness source auto-install (post-QA, pre-close)

When the repository being changed is the harness plugin source itself (root
`install.py` plus `plugin/` and `plugin-codex/` are present), actual independent review and QA PASS MUST immediately run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 ${HARNESS_PLUGIN_ROOT}/scripts/install_verified.py \
  --task-dir doc/harness/tasks/<task_id>
```

This is part of completion, not a suggestion. Run it after the last source
edit and verification, before `wbs(action="close")`, so old installed hooks cannot
prevent the task from reaching the close gate. Capture the installer exit code
and runtime summaries. The trusted helper may inspect the concrete install
payload and Git state because installation is an explicit operation; those
checks are not lifecycle gates. It verifies canonical harness identity and
current WBS applicability and trusted task/source ownership before it invokes the snapshot's
`python3 install.py` (the default conditional run). The installer builds
each canonical runtime projection, leaves synchronized runtimes untouched, and
refreshes only stale runtimes. Comparison errors or failed refreshes block completion; never claim
the source is deployed. Payload synchronization does not diagnose external
config/registry-only drift; `python3 install.py --force` remains the explicit
repair path. Do not rerun installation for docs-only edits after this step. The
current process may retain already-loaded MCP/hooks, so report when a
new thread is required to load changed runtime code. The helper writes no deduplication state; retrying after interruption recomputes payload
equality from a fresh verified snapshot.

### Phase 8: Completion preparation

**Concreteness standard:** every user-facing claim must locate without searching — name file, function, line, test, command, or subagent lens. "Fixed auth bug" is not acceptable; `auth.ts:47 — added null check on session.token` is.

Prepare these completion checks now; the final phase revalidates them after
durable-doc and learning work:

1. PLAN.md acceptance criteria are addressed or explicitly deferred.
2. `wbs(action="verify")` reports PASS from current required WBS checks.
3. Required independent review/QA and applicable UX results were awaited, and actual review PASS preceded actual QA.
4. User corrections are reflected in PLAN.md or durable documentation.
5. Durable docs are updated when the task changed user-visible behavior, externally consumed API contracts, or reusable guidance.

Do not call `wbs(action="close")` or emit the final response yet. Draft a concise
completion report with:

1. Summary (one sentence per AC or task slice)
2. Files changed (important files only, with one-line description)
3. Verification results and subagent lenses used
4. Durable docs or learning artifacts updated, or a specific no-doc rationale
5. Remaining risks, deferred items, or follow-up tasks

**Cleanup:** PROGRESS.md persists through final close as the scope-lock contract. Keep PROGRESS.md in place; do not create a separate narrative handoff artifact.

### Phase 8.5: Reflect and Log

Capture-when-fresh, no quota. Capture only concrete reusable fact-plus-fix
discoveries while they are fresh. Leave `learnings.jsonl` untouched when there is
no signal; it is gitignored staging, not shared memory, and it never gates close.

**Commit-backed Learnings (mandatory classification):** classify each candidate
`none | captured | rejected`. `captured` requires a committed artifact and names
the skill, script, test or durable doc that changed a committed rule — a
`learnings.jsonl` row alone is never `captured`.

| Candidate source | Additional rule |
|---|---|
| Feedback-Derived Rules (judgment required, capture optional) | Capture only reusable rules shaped `When X, do Y. Verify by Z.` Write behavior rules for Tier 2 docs; convert incident-shaped lessons into behavior or reject them. |
| Self-Healing Candidates (development friction, QA-discovered gaps, agent suggestions) | Treat as hypotheses until checked against the repo. Classify `confirmed`, `partially-confirmed`, `already-handled`, `duplicate`, `not-found`, or `needs-runtime-check`. Preserve gate safety with an alternative evidence tier. |

If develop or QA discovered a working repo-local setup/test/dev-server command
after one or more failed attempts, record it before close as a pending runbook
candidate:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 ${HARNESS_PLUGIN_ROOT}/scripts/runbook_memory.py capture \
  --id "<short-id>" \
  --description "<what this starts/tests/verifies>" \
  --failed-command "<representative failed command>" \
  --command "<final successful command or wrapper script>" \
  --failure-class "<missing-env|wrong-host|crlf-env|missing-tool|dev-server-bootstrap|db-bootstrap>" \
  --source-phase "<develop|qa-browser|qa-api|qa-cli>" \
  --source-task "<task_id>" \
  --gotcha "<why the first attempt failed>"
```

The candidate is not shared memory yet. Use
`Status: none | applied | deferred | rejected`. Applied names the changed
committed artifact. Deferred uses `request_user_input` when available (or a
conversational AskUserQuestion) and records `user_decision:` plus
`proposed_artifact:`. Approve reusable commands into
`doc/harness/runbooks.yaml` or reject one-off noise.

### Phase 8.6: durable docs

Read the PLAN targets and durable-doc decision. Update every selected durable
surface. Copy every reported `Known ceiling:` line under a `Known ceiling` heading in the durable doc that owns the behavior, or else into the Phase 8.7 change doc written now, so `grep -rn "Known ceiling" doc/` stays the ledger; never copy an absent ceiling. Then call `wbs(action="verify")`.

When the task changes `doc/<area>/REQ__*.md`, `GUIDE__*.md`, `ADR__*.md`, or
`POLICY__*.md`, spawn the documentation-review subagent after durable docs. It
verifies both durable docs consistency and durable doc quality. A changed REQ with
vague or missing observable behavior is a FAIL, not a warning.

### Phase 8.7: Distilled Change Doc

One-paragraph summary of the task's user-visible behavior change. Lives at `doc/changes/<date>-<slug>.md`. Required when a `Known ceiling:` was reported; otherwise optional if no user-visible change.

### Phase 9: Final verification, install, close, and response

Read `${HARNESS_PLUGIN_ROOT}/internal-skills/run/worktree-completion.md` now.
Follow its ownership, surviving-checkout and batch-lead rules; finish required
coordinator integration/disposal after source close, before overall completion.
Report retained originals and their next action; never silently leave them.

Phase 9 is the only normal owner of `wbs(action="close")` and the user-facing completion
response. Target-aware gates invalidate affected source applicability after relevant edits;
prior actual results retain their original scope. Preserve unaffected result scope. Rerun the verified source
installer when its concrete install payload changed; installer-local payload
checks remain allowed. Call `wbs(action="verify")` once after actual independent review and QA PASS.

Recheck every Phase 8 completion condition. Then call `wbs(action="close")` exactly once.
Before `wbs(action="close")`, confirm the final `wbs(action="verify")` result is PASS and any
required installation succeeded.
If blocked, report `missing_for_close`, fix the named gate, restore ordered
review/QA/install evidence as required, and retry. After success, emit the
prepared completion report with concrete file, test, reviewer, QA, durable-doc,
installation, and remaining-risk evidence.

---
