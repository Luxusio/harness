---
name: develop
description: Implement PLAN.md through scoped development, independent review, QA, verified install when applicable, and task close.
argument-hint: <task-id>
user-invocable: false
allowed-tools: Read, Glob, Grep, Bash, Edit, Write, Agent, Skill, AskUserQuestion, mcp__plugin_harness_harness__task_start, mcp__plugin_harness_harness__task_context, mcp__chrome-devtools__navigate_page, mcp__chrome-devtools__take_snapshot, mcp__chrome-devtools__take_screenshot, mcp__chrome-devtools__evaluate_script, mcp__chrome-devtools__wait_for, mcp__chrome-devtools__list_pages, mcp__chrome-devtools__new_page, mcp__chrome-devtools__select_page, mcp__chrome-devtools__emulate, mcp__chrome-devtools__click, mcp__chrome-devtools__fill, mcp__chrome-devtools__press_key, mcp__chrome-devtools__type_text, mcp__chrome-devtools__hover, mcp__chrome-devtools__list_network_requests, mcp__chrome-devtools__performance_start_trace, mcp__chrome-devtools__performance_stop_trace, mcp__chrome-devtools__lighthouse_audit
---

Implement the plan for a harness task. Reads PLAN.md, implements changes, verifies completeness, captures durable learnings, and closes through MCP.

> Current artifact model: acceptance intent lives in `PLAN.md`; independent
> review and QA evidence lives in the unified `RECEIPTS.jsonl`.

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

Lead with outcomes. Name concrete files, functions, ACs and tests. Speak as a
builder, avoid hype, and surface premise/scope decisions for the user.

## Anti-shortcut clause

PROGRESS.md is the scope-lock contract for this task. PLAN.md owns acceptance intent, while ordered review and QA entries in the current TASK.json generation provide close evidence. Harness does not inspect source state after a receipt. If code changes after review or QA, the developer owns the decision to rerun the affected evidence.

**Highest-tier verification mandate.** If a task creates, unblocks, or documents a verification path, using that path is part of the same task. Do not ask "should I verify it?" when the required services, rebuild, seed, token, browser, API, or CLI route are locally available. Execute the highest available tier yourself, then report the tier reached and any concrete blocker. Running the highest available verification tier is not scope expansion. Ask only when verification would require destructive state changes, paid/external credentials, production resources, or a genuine product choice between valid approaches.

## Confusion Protocol

For premise, architecture, scope, external-state, or three-attempt ambiguity,
stop, state the conflict in one sentence, and ask 2-3 options with concrete
tradeoffs via `AskUserQuestion`. Running required verification is not scope
expansion.

## Premise Gate / User Challenge

At Phase 2, ask whether to re-plan, narrow, or proceed when search disproves the
premise. At Phase 5, ask whether to revert, add to scope, or defer a necessary
out-of-plan edit. Persist the choice in PLAN/PROGRESS or a durable artifact.

## Error Philosophy

Never halt with bare BLOCKED or silently cut an approved AC. Report the exact
blocker and let the user choose a concrete recovery.

## Flow

Phases run in strict order; each phase must complete before the next. Sub-files are lazy-loaded — do NOT pre-read them, load each only in the phase that needs it. Every phase is idempotent on re-run; check PROGRESS.md and TASK.json to resume instead of restarting from Phase 0.

**Graceful degradation:** missing tool or phase prerequisite → skip cleanly, log reason, do NOT install missing tools. Skipped-phase table:

| Missing | Phases skipped |
|---------|----------------|
| Linter / build / test framework / coverage tool | 3.7 / 3.8 / 4.5 / 4.9 |
| `browser_qa_supported: false` or Chrome MCP missing | 3 visual, 3.9, 4 Agent D, 7 browser debug |
| Dev server unreachable | 3.9 |
| No QA_KNOWLEDGE.yaml / learnings.jsonl | 0 / 1 (first run creates them) |

### Phase 0: Pre-flight

Verify `doc/harness/manifest.yaml` and the exact four-field `TASK.json` parse. Derive terminal state from `TASK.json.close_receipt_fingerprint` or `BLOCKED.md`; do not expect a stored status or verdict. No other task holds write focus. On failure, `AskUserQuestion` with setup-skill / fresh-task / continue-anyway options.

**Context Recovery:** inspect TASK.json/PROGRESS.md for the current task
and list the 3 newest task directories. If an in-progress task matches the
current `task_id`, state "resuming from prior session" in the conversation.

### Phase 1: Load plan

Read `doc/harness/tasks/<task_id>/`:
1. `PLAN.md`, `REQUEST.md` (if present), `TASK.json`.
2. Extract: objective, scope (in/out), target files, acceptance criteria (AC-001+), verification commands.
3. **Resume check:** `PROGRESS.md` → skip ACs listed in `completed_acs`. For each completed AC, compare target-file mtimes against `PROGRESS.md` mtime; files modified post-PROGRESS → mark "needs re-verification", do not blindly skip.
4. **Learnings bootstrap:** `head -20 doc/harness/learnings.jsonl` and `ls doc/harness/patterns/*.md`. If PLAN.md absent, `AskUserQuestion` (run plan skill / check task_id / abort).

**Durable Docs Preflight:** read PLAN.md `Durable Docs Decision` as a
documentation-impact judgment, not a rote REQ checklist, and confirm whether the task
is `REQ needed`, `Pattern/skill doc enough`, or `No durable doc needed`. Phase
3.6.1 owns the type taxonomy, area vocabulary, and observable-behavior examples —
do not restate them here.

What this phase uniquely owns is **ordering**: a selected REQ doc is written
before any source edit, not after the code is done. When the decision is
ambiguous, run `plugin/scripts/req_detector.py` (or reason it through) against the
request, feedback, target files, and planned surfaces.
If the task touches observable UI/API/backoffice/admin screens, routes, controllers, native navigation/back-stack behavior, or endpoints and PLAN says `REQ: n/a`, stop source implementation and amend or create the REQ now — do not defer that discovery to Phase 8.6.

**User Feedback Event Review:** before each dependent build/test/review action
at phase boundaries, incorporate explicit user corrections from the conversation. The
conversation is not a durable source of truth by itself. For every correction, decide whether it changes the current task,
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

Build the routing contract from PLAN file ownership and dependencies before
editing:

| AC | Files | Depends on | Lane | Route | Reason |
|----|-------|------------|------|-------|--------|
| AC-001 | `<paths>` | none | A | Agent | disjoint files |

`Route` must be one of: `Agent(...)`, `sequential-prelude`, `sequential-dependent`.
Fill the table before editing files; two or more independent `Agent(...)` rows
run in one batch. For example, use `Agent(name="<task_id>:AC-001"` and
`Agent(name="<task_id>:AC-002"`, each with `subagent_type="harness:ac-worker"`.
Use one Agent per independent AC. Do not assign multiple independent ACs to one
executor. Disjoint ACs use one worker per lane when Agent is available.
Sequential routes require a declared dependency, unavailable Agent, or a tiny
evidenced batch. Workers read `plugin/agents/developer.md`, stay inside explicit
ownership, Do not edit PROGRESS.md, and return changed paths, tests and blockers.
Handle `needs-coordinator-review` before generic rollback: never retry with the
same ownership; reassign ownership, amend PLAN, or escalate to the user. Keep
successful independent siblings promoted. Record a failed parallel lane and
retry only that dependency path sequentially. Load
`parallel-fanout.md` only for uncommon routing cases.

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

After each AC, record status, targeted tests, completeness, deferred edges,
decisions and failed attempts in PROGRESS. A completed AC needs its happy path,
important negative/edge paths and regression evidence; partial work remains
explicit. Each worker runs its own targeted tests. Full-suite verification
belongs to the required qa-* agents; hooks record their lifecycle and
`task_verify` requires completed explicit PASS verdicts.

Per-AC test failures → fix immediately. These are free; only Phase 7 full-suite failures count toward the 3-cycle limit.

**Per-AC visual verification** (browser projects only): see `browser-verification.md` → "Per-AC Visual Verification" and "Per-AC Interaction Testing".
Prefer delegating Browser MCP tools (`mcp__chrome-devtools__*`) to
`harness:qa-browser`; inline use is allowed for the lightest targeted check.

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

  *Test-evidence rule:* behavioral ACs require a concrete test path or a documented reason that no test surface exists. Record this in PROGRESS.md and the final verification evidence.

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
`required_review_lenses`, derived from the canonical `TASK.json.required_lenses`
set without inspecting paths or diff content.

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

- **6.5 IRON LAW** — PASS = PASS. No unverified claim. Runtime verdict must come from the current task receipt run.
- Decide whether to continue fixing or proceed from the concrete open review and
  QA findings — unresolved `FIX_NOW` items, failing GATE tests, uncovered
  behavioral ACs. Do not compute a summary score and do not write a project
  stats series for per-task scores.

### Phase 6.6: Independent Code Review Gate

Read `quality-audit-pipeline.md` § Phase 6.6. Call `task_context`, spawn every
required read-only review lens in parallel, await explicit verdicts, and require
review PASS entries in `RECEIPTS.jsonl` correlated to starts in the current task
run. Send only `FIX_NOW` findings to the original minimum-sufficient
implementer. Any edit loops through focused tests/checkpoint and all required
reviewers again. Do not start Phase 7 QA until review PASS.

### Phase 7: Verification Gate

Read `verification-gate.md` in full. Delegates full-suite test commands from PLAN.md to all applicable qa-* agents in parallel, classifies failures (GATE/PERIODIC × OWN/PRE-EXISTING), triages with hypothesis-driven debugging, enforces the 3-cycle limit with investigate-skill escalation on cycle 3.

**Main session MUST spawn the appropriate qa-* lens; full-suite verification MUST be delegated to qa-* agents (Verification delegation, C-18).** Spawn every applicable lens. Browser delegation is workflow guidance rather than a PreTool denial; full-suite delegation remains required by the develop contract. Bash test runners remain allowed inline only for targeted per-AC runs and debug reruns. Heavy full-suite execution and background process state belong in qa-* isolated contexts. Let the qa-* lens execute, then run `task_verify`; the hook-recorded `RECEIPTS.jsonl` entry is the verification signal.

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
- **Acceptance result** — on gate fail, loop back to the fix cycle; on pass, retain the evidence in the review/QA receipt stream.

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
- `PLAN.md` declares no user-facing surface and
  `dogfood_required` is not explicitly true.

Dogfood routing is plan-owned. Use declared surfaces such as `frontend`, `api`,
`cli`, or `desktop`; do not infer them from Git diff output.
When the PLAN declaration is ambiguous, run the dogfooder or record an explicit plan
decision. Lifecycle commands never scan the repository to make this choice.

### Phase 7.8: Harness source auto-install (post-QA, pre-close)

When the repository being changed is the harness plugin source itself (root
`install.py` plus `plugin/` and `plugin-codex/` are present), terminal ordered
current-run review+QA PASS receipts MUST immediately run:

```bash
python3 plugin/scripts/install_verified.py \
  --task-dir doc/harness/tasks/<task_id>
```

This is part of completion, not a suggestion. Run it after the last source
edit and verification, before `task_close`, so stale installed hooks cannot
prevent the task from reaching the close gate. Capture the installer exit code
and runtime summaries. The trusted helper verifies canonical harness identity,
current-run review+QA receipts, and a byte-stable install-payload snapshot
before it invokes the snapshot's `python3 install.py --if-stale`. The installer
builds each canonical runtime projection, leaves synchronized runtimes untouched,
and refreshes only stale runtimes. Comparison errors or failed refreshes block
completion; never claim the source is deployed. Payload synchronization does not
diagnose external config/registry-only drift; `python3 install.py --force` remains
the explicit repair path. Do not rerun installation for docs-only edits after
this step. The current process may retain already-loaded MCP/hooks, so report when a
new session is required without forging receipts. The helper writes no install
receipt or deduplication state; retrying after interruption recomputes payload
equality from a fresh verified snapshot.

### Phase 8: Close and final response

**Concreteness standard:** every user-facing claim must locate without searching — name file, function, line, test, command, or subagent lens. "Fixed auth bug" is not acceptable; `auth.ts:47 — added null check on session.token` is.

Before `task_close`, verify these are true:

1. PLAN.md acceptance criteria are addressed or explicitly deferred.
2. `task_verify` reports PASS from ordered receipts in the current TASK.json generation.
3. Required QA/UX subagents were spawned when available; hook-owned `RECEIPTS.jsonl` proves their lifecycle.
4. User corrections are reflected in PLAN.md or durable documentation.
5. Durable docs are updated when the task changed user-visible behavior, external contracts, or reusable guidance.

Call `task_close`, then provide a concise final response with:

1. Summary (one sentence per AC or task slice)
2. Files changed (important files only, with one-line description)
3. Verification results and subagent lenses used
4. Durable docs or learning artifacts updated, or a specific no-doc rationale
5. Remaining risks, deferred items, or follow-up tasks

**Cleanup:** PROGRESS.md persists beyond Phase 8 as the scope-lock contract for any post-close edits. Keep PROGRESS.md in place; do not create a separate narrative handoff artifact.

### Phase 8.5: Reflect and Log

Capture-when-fresh, no quota. Capture only concrete, reusable fact-plus-fix
discoveries while they are fresh. Leave `learnings.jsonl` untouched when there is
no signal; it is gitignored staging, not shared memory, and it never gates close.

**Commit-backed Learnings (mandatory classification):** classify each candidate
`none | captured | rejected`. `captured` requires a committed artifact and names
the skill, script, test or durable doc that changed a committed rule — a
`learnings.jsonl` row alone is never `captured`.

| Candidate source | Additional rule |
|---|---|
| Feedback-Derived Rules (judgment required, capture optional) | Capture only reusable rules shaped `When X, do Y. Verify by Z.` Write behavior rules for Tier 2 docs; convert incident-shaped lessons into behavior or reject them. Blame, urgency and task-local preference are not rules. |
| Self-Healing Candidates (development friction, QA-discovered gaps, agent suggestions) | Treat as hypotheses until checked against the repo. Classify `confirmed`, `partially-confirmed`, `already-handled`, `duplicate`, `not-found`, or `needs-runtime-check`. Preserve a gate's safety with an alternative evidence tier rather than weakening it. |

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

The candidate is not shared memory yet. Use
`Status: none | applied | deferred | rejected`. Applied names the changed
committed artifact. Deferred requires an `AskUserQuestion` decision and records
`user_decision:` plus `proposed_artifact:`. Approve reusable commands into
`doc/harness/runbooks.yaml` or another committed artifact; reject one-off noise.

### Phase 8.6: durable docs

Mechanical. Read the task's changed paths and `doc/CLAUDE.md` registered roots. For each file, map to doc root. Call `task_verify`.

When the task changes `doc/<area>/REQ__*.md`, `GUIDE__*.md`, `ADR__*.md`, or
`POLICY__*.md` OR the current task contains explicit durable user corrections
(per C-101 in `CONTRACTS.local.md`), spawn the documentation-review subagent after
durable docs. It verifies both durable docs consistency and durable doc quality, and
runs the Retrospective REQ pass over the conversation to catch user-stated
requirements that closed without becoming durable REQ docs. The task cannot
close with unresolved durable-doc gaps; a changed REQ with vague or missing
observable behavior is a FAIL, not a warning. Candidate REQs written by the
Retrospective pass land with `status: candidate` frontmatter and do not block
close on their own.

### Phase 8.7: Distilled Change Doc

One-paragraph summary of the task's user-visible behavior change. Lives at `doc/changes/<date>-<slug>.md`. Optional if no user-visible change. Writer skill consumes this for release notes.
