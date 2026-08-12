---
name: develop
description: Implement PLAN.md on Codex. Reads PLAN.md, implements changes, routes independent ACs by current subagent capability, verifies, captures durable learnings, and closes.
user-invocable: false
---

Implement the plan for a harness task. Reads PLAN.md, implements changes, verifies completeness, captures durable learnings, and closes through MCP.

> Current artifact model: use `PLAN.md` plus unified `RECEIPTS.jsonl`. Do not
> create, read, or update `CHECKS.yaml` or `USER_FEEDBACK.jsonl`, and do not run
> `update_checks.py`; later legacy ledger wording is non-operative.

> **Codex delta:** execute skill chains inline; use bare MCP names and
> `${HARNESS_PLUGIN_ROOT}`. Harness workflow authorization covers required subagents: explicit user invocation or approval of a harness repo-mutating workflow authorizes those required lanes. Agent fan-out is capability-gated; route from the capabilities exposed by the current session.
> session. Use `spawn_agent` for independent lanes/review/QA when available,
> conversational numbered options instead of AskUserQuestion, and
> `BLOCKED_ENV` when required browser/desktop evidence is unavailable. Track and
> close spawned agents with `close_agent` before `task_close` or final response. Completed agents can continue to consume concurrency until closed. Read existing detailed develop sub-files from
> `plugin/skills/develop/` only when their phase requires them.

## Voice

Lead with outcomes. Name concrete files, functions, ACs and tests. Speak as a
builder, avoid hype, and surface premise/scope decisions for the user.

## Anti-shortcut clause

PROGRESS.md is the scope-lock contract for this task. PLAN.md owns acceptance intent, and ordered review/QA entries in the current task run provide close evidence. Harness does not detect edits after QA; the developer decides when a change warrants rerunning review or QA.

## Confusion Protocol

For premise, architecture, scope, external-state, or three-attempt ambiguity,
stop, state the conflict in one sentence, and ask 2-3 numbered options with
concrete tradeoffs. Required verification is not scope expansion.

## Premise Gate / User Challenge

At Phase 2, ask whether to re-plan, narrow, or proceed when search disproves the
premise. At Phase 5, ask whether to revert, add to scope, or defer a necessary
out-of-plan edit. Persist the choice in PLAN/PROGRESS or a durable artifact.

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

Verify `doc/harness/manifest.yaml` and the exact four-field `TASK.json` parse. Derive terminal state from `TASK.json.close_receipt_fingerprint` or `BLOCKED.md`; do not expect a stored status or verdict. No other task holds write focus. On failure, conversationally ask about setup, a fresh task, or continuing anyway.

**Context Recovery:** inspect TASK.json/PROGRESS.md for the current task
and list the 3 newest task directories. If an in-progress task matches the
current `task_id`, state "resuming from prior session" in the conversation.

**Health score snapshot:** capture a composite health score for Phase 8 comparison. Best-effort — skip cleanly.

Run `python3 ${HARNESS_PLUGIN_ROOT}/scripts/health.py --dry-run || true`.

Reads `health_components` from manifest (falls back to `test_command`). Output includes per-component PASS/FAIL + composite 0-10 score. `--dry-run` prevents appending to project-level history at this stage.

### Phase 1: Load plan

Read `doc/harness/tasks/<task_id>/`:
1. `PLAN.md`, `REQUEST.md` (if present), `TASK.json`.
2. Extract: objective, scope (in/out), target files, acceptance criteria (AC-001+), verification commands.
3. **Resume check:** `PROGRESS.md` -> skip ACs listed in `completed_acs`. For each completed AC, compare target-file mtimes against `PROGRESS.md` mtime; files modified post-PROGRESS -> mark "needs re-verification", do not blindly skip.
4. **Learnings bootstrap:** `head -20 doc/harness/learnings.jsonl` and `ls doc/harness/patterns/*.md`. If PLAN.md absent, ask the user (run plan skill / check task_id / abort) via prose.

**Durable Docs Preflight:** before source implementation, read PLAN.md `Durable Docs Decision` as a documentation-impact judgment, not a rote REQ checklist. Confirm whether the task is `REQ needed`, `Pattern/skill doc enough`, or `No durable doc needed`, then run the REQ detector mentally or via `plugin/scripts/req_detector.py` when request, feedback, target files, or planned surfaces imply observable UI/API/mobile/native/desktop behavior. If a REQ path is selected or detector output is high-confidence, create or update that `doc/<area>/REQ__*.md` before editing source files, using a direct `doc/<area>/REQ__*.md` edit or `plugin/scripts/req_scaffold.py` when no existing REQ fits. If the task touches observable UI/API/backoffice/admin screens, routes, controllers, native navigation/back-stack behavior, or endpoints and PLAN says `REQ: n/a`, stop source implementation and amend/create the REQ first; do not wait for close or durable docs to discover the missing REQ. For harness process, agent instruction, testing guidance, or implementation-pattern changes, prefer `GUIDE` or skill/pattern docs and keep `REQ: n/a` with a specific reason.

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

**Baseline screenshot (browser projects):** Browser tools are availability-gated on Codex. When available, run `plugin-codex/agents/qa-browser.md` inline; when required but unavailable, record the browser-lens blocker and state the fallback in task state or final response.

### Phase 3.0: AC Dependency Analysis

Build the routing contract from PLAN file ownership and dependencies before
editing:

| AC | Files | Depends on | Lane | Route | Reason |
|----|-------|------------|------|-------|--------|

`Route` is `spawn_agent(worker)` for every disjoint lane; spawn one worker per lane. Use one worker per independent AC. Do not assign multiple independent ACs to one worker. This is capability-gated, not user-request-gated: The user does not need to ask for delegation, `user did not ask for delegation` is an invalid reason, `delegation was not requested` is not a fallback, and Do not wait for the user to request delegation. User request is
not a condition for parallel routing; this is mandatory capability/task-shape routing.
Sequential fallback must state `ac_count`, `conflict` (specific files/dependency), `estimated_lines`, `estimated_seconds`, and the fallback in task state or final response. Valid reasons are only `spawn_agent-unavailable`, `dependency-conflict`, or `small-task`.
Workers read `plugin-codex/agents/developer.md`, stay inside explicit ownership, do not edit PROGRESS, and return paths/tests/blockers. Prompts must say: return the exact status `needs-coordinator-review` when ownership, lane, or approved scope needs coordinator judgment.
Handle `needs-coordinator-review` before generic rollback: never retry with the same ownership; reassign ownership, amend PLAN, or escalate to the user. Keep successful independent siblings promoted.

### Phase 3.1: Scope Lock

Declare allowed / test / forbidden paths in PROGRESS.md. Before each file edit:
- allowed -> proceed. test -> proceed. forbidden -> BLOCK + escalate. unlisted -> WARN, auto-add to allowed with note.

### Phase 3: Implement

1. For sequential batches, work **one AC at a time**, in order. For parallel
   batches, wait for all sibling worker results, then merge progress once. Skip
   ACs in `completed_acs`.
2. **Follow existing patterns.** Smallest coherent diff. No speculative features.
   Apply `plugin-codex/agents/developer.md` minimum-sufficient ladder in the
   coordinator and every spawned worker. A generic worker prompt must tell the
   worker to read that file before editing; parent context is not sufficient.
3. **Codex tool surface:** use `read_file` for reads, `apply_patch` for edits/writes (Codex envelope-oriented), `shell` for Bash commands. Multi-edit is one `apply_patch` envelope per file. Where the Claude flow says `Edit`/`Write`/`MultiEdit`, read it as `apply_patch`.

After each AC, record status, targeted tests, completeness, deferred edges,
decisions and attempts in PROGRESS. Completed behavior needs important negative
paths and regression evidence. Full-suite verification belongs to the required
qa-* agents; browser evidence may run inline only when that is the available
path.

Per-AC test failures -> fix immediately. These are free; only Phase 7 full-suite failures count toward the 3-cycle limit.

**Per-AC visual verification** (browser projects only): run inline with available browser tools. If browser verification is required and unavailable, record the affected ACs as browser-lens `BLOCKED_ENV` evidence.

### Phase 3.3: Auto-checkpoint (post all ACs)

After all ACs reach `implemented_candidate`, snapshot task state for session resume:

```bash
python3 ${HARNESS_PLUGIN_ROOT}/scripts/write_checkpoint.py \
  --task-dir doc/harness/tasks/<task_id>/ \
  --note "Phase 3 complete — all ACs at implemented_candidate"
```

### Phase 3.4-3.6: Per-AC Quality Gate

Runs continuously during Phase 3.

- **3.4 Test framework bootstrap** — if project has no framework and no `doc/harness/.no-test-bootstrap` opt-out marker, offer minimal setup (JS/TS: vitest or bun:test; Python: pytest; Go/Rust: built-in). Log bootstrap to `learnings.jsonl` type `test-bootstrap`. If user declines, create opt-out marker.
- **3.5 Regression rule + Test-Evidence Gate** — two related rules.

  *Regression rule:* if the diff modifies existing behavior and no test covers the changed path, write a regression test immediately. Commit separately: `test: regression test for <what>`.

  *Test-evidence rule:* behavioral ACs require a concrete test path or a documented reason that no test surface exists. Record this in PROGRESS.md and final verification evidence.

  *QA codifier* (after Phase 7 PASS, before close):
  ```bash
  python3 ${HARNESS_PLUGIN_ROOT}/scripts/qa_codifier.py --task-dir <task_dir> 2>/dev/null || true
  ```
  Parses `codifiable:` YAML blocks emitted by the QA pass and stages validated tests to `tests/regression/<sanitized-task-id>/`. Same script as Claude side; runtime-agnostic.
	- **3.6 Fix-first pattern** — read `plugin/skills/develop/fix-first-pattern.md` (Claude tree fallback). Classify AUTO-FIX (dead code, magic numbers, stale comments, missing guards) and ASK (API design, architecture, security, DRY extractions). Auto-fix immediately; surface ASK items through the current user-input mechanism or final response. The **3-attempt escalation rule** in that sub-file applies to every fix loop (per-AC, Phase 7, debug).
	- **3.6.1 Durable docs (REQ/GUIDE/ADR/POLICY)** — read PLAN.md `Durable Docs Decision` before implementation. Treat it as a documentation-impact judgment: `REQ needed`, `Pattern/skill doc enough`, or `No durable doc needed`. Create or update each selected `doc/<area>/<TYPE>__<name>.md` file; selected REQ docs must be written before source implementation, not after code is done. Use a direct `doc/<area>/REQ__*.md` update or `req_scaffold.py` as the happy path when observable behavior is detected and no existing REQ fits. Use DDD-style areas or bounded contexts such as `ui`, `api`, `auth`, `billing`, `catalog`, `runtime`, `verification`, or `common`. Use `REQ` for user-visible behavior, externally consumed API contracts, constraints, and observable bugfixes; write intended observable behavior plus verification cues. Existing-screen state changes count: filters, search, sorting, loading, empty/error states, visibility, labels, native navigation/back-stack behavior, and click/input behavior. New pages, admin/backoffice screens, routes, controllers, and endpoints require a REQ even when additive. PLAN.md acceptance criteria are task-local artifacts and never substitute for a durable `REQ`. Recheck the actual diff after implementation: if you added observable UI/API behavior that PLAN marked `REQ: n/a`, create the missing REQ, link it from PLAN.md or the changed durable doc. If the diff instead changed harness process, agent instructions, testing guidance, or implementation patterns, update the relevant `GUIDE`, skill, pattern doc, or tests rather than inventing a REQ. Use `GUIDE` for reusable coding, design, testing, or implementation guidance. Use `ADR` for significant technical choices with alternatives, reasons, consequences, and tradeoffs. Use `POLICY` only for external security, legal, data-handling, approval, licensing, or organizational constraints that harness cannot fully enforce by itself; keep harness-internal execution rules in skills, agents, scripts, and tests. Keep each updated durable doc directly in the repo and link selected REQ paths from PLAN.md when the close gate needs them. For internal-only refactors, one-off tests, or non-observable maintenance, keep `REQ: n/a` in PLAN.md with a specific non-observable reason; the reason must say which durable knowledge surfaces remain unchanged.

### Phase 3.7-3.9: Post-implementation health

After all ACs done. Each runs only if prerequisite exists.

- **3.7 Lint & Format** — run linter and formatter on the PLAN targets actually edited. `--fix` where safe. Re-run per-AC tests after. Skip if none configured.
- **3.8 Build check** — compile / typecheck the diff (or full project). Build failures are always T1 (our code). Fix immediately.
- **3.9 Runtime smoke** — read `plugin/skills/develop/runtime-smoke.md` (Claude tree fallback). Project-type-specific (browser / API / CLI). Browser smoke runs when browser tools are available; otherwise required browser smoke becomes browser-lens `BLOCKED_ENV`.

### Phase 4: Plan Completion Audit

On Claude this is a haiku sub-agent. On Codex, use `spawn_agent` when available for an independent completion audit; otherwise run the same pass inline as fallback. Cross-reference every AC against PLAN targets, implementation notes, and test evidence, then classify each as DONE / PARTIAL / NOT DONE / CHANGED + category (CODE / TEST / MIGRATION / CONFIG / DOCS). Be conservative with DONE; be generous with CHANGED (goal met by different means).

For PARTIAL / NOT DONE, classify cause: scope-cut / context-exhaustion / misunderstood / blocked / forgotten / evolved. Fix forgotten and misunderstood immediately; log scope-cut + blocked in PROGRESS.md; mark evolved as CHANGED with the new approach.

### Phase 4.5-4.8: Quality Audit

Read `plugin/skills/develop/quality-audit-pipeline.md`. Phase 4.5 gathers
coverage, visual, migration/contract, LLM-trust, and proportional performance
inputs. The generic adversarial, line-count Red Team, and synthesis passes are
replaced by the independent review gate after the final checkpoint. Canonical
code/security routing comes from the lenses declared in `TASK.json`.

**Phase 4.85 Coverage Synthesis** — use the coverage diagram from the audit
agent final response to update tests directly. Do not create a separate test
extra plan file.

**Phase 4.9 Coverage Gate** — if manifest declares `coverage_minimum` / `coverage_target`, enforce. Below minimum = BLOCK (write tests); below target = WARN in final response. 3 fix cycles max; on exhaustion conversational ask (continue / lower threshold / defer).

### Phase 5: Developer-owned scope review

Compare files you intentionally edited with PLAN targets. Harness does not
enumerate them for you. Classify each known edit:
- In scope -> proceed.
- Related but unlisted -> acceptable, note in PROGRESS.md or final response.
- Unrelated -> revert. Belongs in a separate task.
- Missing from plan but necessary -> note as "unplanned-but-necessary".

**SCOPE CREEP signals:** unrelated changes, "while I was in there" edits, new features not in PLAN, reformatted distant modules.
**MISSING REQUIREMENTS:** PLAN.md requirements not addressed by any change; partial implementations; test coverage gaps.
**Documentation staleness:** if changed file has a corresponding doc (README section, API doc, inline docblock), flag stale under "Documentation Debt".

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

- **6.5 IRON LAW** — PASS = PASS. No self-authored PASS. No unverified claim. Runtime verdict requires ordered independent receipts.
- Use the current quality score to decide whether to continue fixing or proceed.
  Do not write a project stats series for per-task scores.

### Phase 6.6: Independent Code Review Gate

Read the required review lenses from `TASK.json`. Discover deferred
`spawn_agent` in `ALL_TOOLS`. Spawn each review lens declared by TASK.json
`security_review` when declared, in one message; each reads its matching
`plugin-codex/agents/*-reviewer.md`, stays read-only, and returns exact VERDICT.
Await all reviewers. Use `wait_agent` only to coordinate completion; its output
and `list_agents` do not author receipts. Watcher-owned
review entries in `RECEIPTS.jsonl` must show PASS for the
current task receipt run and declared lens. Send only FIX_NOW to the implementer; after an
edit that affects a finding, rerun focused tests and the applicable review.
Harness does not infer this need from Git. Inline
self-review is not a strict-compliance fallback.

Apply the parent run skill's subagent wait UX rule: finish useful local work,
then wait in one interval of up to 60 seconds; never use rapid short polling or
agent-status tools as a progress poll. After a timeout, emit one compact user
status before waiting again.

Use a valid structured `task_name` containing `code_review` or
`security_review`; a matching first message line is readable context only. Do
not use a generic worker name for a required reviewer. The MCP-hosted watcher
solely owns lifecycle evidence. Follow
`doc/harness/patterns/ADR__single-direct-codex-receipt-protocol.md` for Codex
acquisition/identity/completion and
`doc/harness/patterns/ADR__consolidated-task-artifacts.md` for stream/gate
semantics. Late observation cannot recover a completed reviewer.

### Phase 7: Verification Gate

Read `plugin/skills/develop/verification-gate.md` (Claude tree fallback) for the full gate methodology. Runs test commands from PLAN.md, classifies failures (GATE/PERIODIC × OWN/PRE-EXISTING), triages with hypothesis-driven debugging, enforces the 3-cycle limit.

Only begin this QA phase after all required Phase 6.6 review lenses PASS. QA
started before those PASS events is out of order and must be rerun.

**On Codex:** run the required QA lenses declared in `TASK.json` (qa-cli for libraries, qa-api for endpoints, qa-desktop for native GUI, qa-browser for frontend/browser work). Use `spawn_agent` for independent QA when available:

For user-facing surfaces, also route the matching UX lens (`ux-cli`,
`ux-api`, `ux-browser`, or `ux-desktop`) when the plan declares it. Pass
the user flow, pages, commands, endpoints,
windows, states, and expected intent in the subagent prompt so the UX lens can
judge shippability without reverse-engineering the change.

```text
spawn_agent {
  task_name: "qa_<lens>_<task_slug>_<run_id>",
  message: "task_name: qa_<lens>_<task_slug>_<run_id>\nYou are the qa-<lens> lens for <task_id>. Read <task_dir>/PLAN.md, TASK.json, the PLAN targets, and durable docs named in PLAN. Follow plugin-codex/agents/qa-<lens>.md. Do not modify files. Return PASS/FAIL/BLOCKED_ENV with evidence and concrete findings.",
  fork_turns: "all"
}
```

After awaiting QA/UX, run `task_verify`.
`wait_agent` and `list_agents` are coordination-only; the Codex watcher records
direct lifecycle events automatically. If no subagent path exists, run
the lens methodology in-conversation and state the fallback in task state or final response; do not
call a critic writer.

Use the same wait UX for QA/UX: useful local work first, one wait interval of
up to 60 seconds and one status update after timeout.

Use structured QA/UX `task_name` values. These names are the runtime-visible
lens binding when delegated prompt bodies are encrypted in Codex rollouts.
Use a fresh task name for every QA run. Its prefix must be exactly `qa_cli_`,
`qa_api_`, `qa_browser_`, or `qa_desktop_`, followed by a short sanitized task
slug and run id. This preserves lens inference while avoiding name collisions
across sequential tasks. Run at most one agent per required lens in one cycle.

Multi-lens concurrency uses `spawn_agent` when available; otherwise run required lenses sequentially. If browser QA is required, close with browser-lens PASS evidence or browser-lens `BLOCKED_ENV`.

When durable docs changed under `doc/<area>/<TYPE>__*.md`, pass those paths to the QA lens as intent evidence. QA uses `REQ` as behavior/contract verification criteria, `GUIDE` as implementation quality and consistency criteria, `ADR` as architecture intent and tradeoff criteria, and `POLICY` as external constraint criteria.

**Also implements:**
- **Transience filter** — a failure must reproduce on 2 consecutive runs to count as `failed`. Single-run failures logged as `transient` in `learnings.jsonl`, not counted toward the 3-cycle limit.
- **Severity × confidence close gate** — after synthesis, block close on:
  - `critical` AND confidence >= 7
  - `high` AND confidence >= 8
  Lower severities may be deferred in final response or follow-up tasks — do not block close.
- **Acceptance result** — on gate fail, loop back to the fix cycle; on pass, retain evidence in the review/QA receipt stream.

### Phase 7.5: Auto-checkpoint (post verify gate)

```bash
python3 ${HARNESS_PLUGIN_ROOT}/scripts/write_checkpoint.py \
  --task-dir doc/harness/tasks/<task_id>/ \
  --note "Phase 7 done — task_verify completed; see RECEIPTS.jsonl"
```

### Phase 7.6: Health score capture

```bash
python3 ${HARNESS_PLUGIN_ROOT}/scripts/health.py --dry-run || true
```

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
`install.py` plus `plugin/` and `plugin-codex/` are present), terminal ordered
review+QA PASS receipts MUST immediately run:

```bash
python3 plugin/scripts/install_verified.py \
  --task-dir doc/harness/tasks/<task_id>
```

This is part of completion, not a suggestion. Run it after the last source
edit and verification, before `task_close`, so old installed hooks cannot
prevent the task from reaching the close gate. Capture the installer exit code
and runtime summaries. The trusted helper may inspect the concrete install
payload and Git state because installation is an explicit operation; those
checks are not lifecycle gates. It verifies canonical harness identity and
ordered review+QA receipts before it invokes `python3 install.py --force`. A failed install blocks completion; never claim the
source is deployed. Do not rerun installation for docs-only edits after this
step. The current process may retain already-loaded MCP/hooks, so report when a
new thread is required without forging receipts. The helper writes no install
receipt or deduplication state; retrying after interruption reinstalls from a
fresh verified snapshot. Skip only when the user explicitly opts out of installation.

### Phase 8: Completion preparation

**Concreteness standard:** every user-facing claim must locate without searching — name file, function, line, test, command, or subagent lens. "Fixed auth bug" is not acceptable; `auth.ts:47 — added null check on session.token` is.

Prepare these completion checks now; the final phase revalidates them after
durable-doc and learning work:

1. PLAN.md acceptance criteria are addressed or explicitly deferred.
2. `task_verify` reports PASS from the required ordered receipt sequence.
3. Required QA/UX subagents were spawned when the runtime exposed them; watcher-owned receipts prove start and explicit completion.
4. `CONVERSATION.md` has no open `<!-- item: ... status=open -->` markers.
5. Durable docs are updated when the task changed user-visible behavior, externally consumed API contracts, or reusable guidance.

Do not call `task_close` or emit the final response yet. Draft a concise
completion report with:

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
- `test_coverage` = (tested paths / total changed paths) × 10. No framework -> 5.
- `adversarial_clean` = max(0, 10 - (crit × 3 + high × 1.5 + med × 0.5)).
- `scope_discipline` = 10 / 7 / 4 / 0 (none / auto-added / justified / unjustified).

**Cleanup:** PROGRESS.md persists through final close as the scope-lock contract. Keep PROGRESS.md in place; do not create a separate narrative handoff artifact.

### Phase 8.5: Reflect and Log (capture-when-fresh, no quota)

Capture only concrete reusable fact-plus-fix discoveries while fresh. Leave
`learnings.jsonl` untouched when there is no signal; it never gates close.

### Phase 8.5.1: Feedback-Derived Rules (judgment required, capture optional)

Classify `none | captured | rejected`. Capture only reusable rules of the form
"When X, do Y. Verify by Z." Write behavior rules for Tier 2 docs; convert
incident-shaped lessons into behavior or reject them.

### Phase 8.5.2: Commit-backed Learnings (mandatory classification)

Classify `none | captured | rejected`. `learnings.jsonl` is gitignored staging,
not shared memory. `captured` requires a committed artifact and names the skill,
script, test or durable doc that changed a committed rule.

### Phase 8.5.3: Self-Healing Candidates (mandatory classification)

Treat development friction, QA-discovered gaps and agent suggestions as
hypotheses until checked against the repo. Classify each as `confirmed`,
`partially-confirmed`, `already-handled`, `duplicate`, `not-found`, or
`needs-runtime-check`. Preserve gate safety with an alternative evidence tier.

If develop or QA discovered a working repo-local setup/test/dev-server command
after one or more failed attempts, record it before close as a pending runbook
candidate:

```bash
python3 ${HARNESS_PLUGIN_ROOT}/scripts/runbook_memory.py capture \
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
surface, then call `task_verify`.

When the task changes `doc/<area>/REQ__*.md`, `GUIDE__*.md`, `ADR__*.md`, or
`POLICY__*.md`, spawn the documentation-review subagent after durable docs. It
verifies both durable docs consistency and durable doc quality. A changed REQ with
vague or missing observable behavior is a FAIL, not a warning. Do not write
legacy critic markdown artifacts.

### Phase 8.7: Distilled Change Doc

One-paragraph summary of the task's user-visible behavior change. Lives at `doc/changes/<date>-<slug>.md`. Optional if no user-visible change.

### Phase 9: Final verification, install, close, and response

Phase 9 is the only normal owner of `task_close` and the user-facing completion
response. The developer owns deciding whether work after review or QA affects
the evidence and therefore warrants rerunning the applicable lenses. Harness
does not inspect Git or automatically stale receipts. Rerun the verified source
installer when its concrete install payload changed; installer-local payload
checks remain allowed. Call `task_verify` once after the final required receipt
sequence is complete.

Recheck every Phase 8 completion condition. Then call `task_close` exactly once.
Before `task_close`, confirm the final `task_verify` result is PASS and any
required installation succeeded.
If blocked, report `missing_for_close`, fix the named gate, restore ordered
review/QA/install evidence as required, and retry. After success, emit the
prepared completion report with concrete file, test, reviewer, QA, durable-doc,
installation, and remaining-risk evidence.

---
