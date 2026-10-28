---
name: develop
description: Implement PLAN.md through scoped development, independent review, QA, verified install when applicable, and task close.
argument-hint: <task-id>
user-invocable: false
allowed-tools: Read, Glob, Grep, Bash, Edit, Write, Agent, Skill, AskUserQuestion, mcp__plugin_harness_harness__wbs, mcp__chrome-devtools__navigate_page, mcp__chrome-devtools__take_snapshot, mcp__chrome-devtools__take_screenshot, mcp__chrome-devtools__evaluate_script, mcp__chrome-devtools__wait_for, mcp__chrome-devtools__list_pages, mcp__chrome-devtools__new_page, mcp__chrome-devtools__select_page, mcp__chrome-devtools__emulate, mcp__chrome-devtools__click, mcp__chrome-devtools__fill, mcp__chrome-devtools__press_key, mcp__chrome-devtools__type_text, mcp__chrome-devtools__hover, mcp__chrome-devtools__list_network_requests, mcp__chrome-devtools__performance_start_trace, mcp__chrome-devtools__performance_stop_trace, mcp__chrome-devtools__lighthouse_audit
---

Implement PLAN.md through the public WBS actions. Private task_verify/task_close handlers enforce current WBS checks and close; they are not callable tools.

> Current artifact model: acceptance intent lives in `PLAN.md`; independent
> actual review and QA results are awaited by the workflow; WBS checks measure outcomes.

Explicit user invocation or approval of this harness repo-mutating workflow
authorizes the subagents required by the workflow's verification and review
gates. Examples include "use harness", "run/continue/close the harness task",
native `/goal`, or clear approval to proceed with a harness task. This workflow
authorization does not apply to read-only answers or ordinary non-harness work.

When the runtime exposes explicit close tools such as `close_agent`, track each
spawned agent id and close it after the result is consumed, the agent completes
or fails, it is cancelled, or it is no longer needed. Before final response,
`wbs(action="close")`, or handoff, do not leave completed agents open unless the user
explicitly asked to keep a still-running agent alive. Completed agents can
continue to count toward the concurrency limit until closed.

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
conflict in one sentence, and ask 2-3 options with concrete tradeoffs via
`AskUserQuestion`. Decide defaultable ambiguity yourself, report it as
`Assumption:`, and continue. Running required verification is not scope expansion.

## Premise Gate / User Challenge

At Phase 2, ask whether to re-plan, narrow, or proceed when search disproves the
premise. At Phase 5, ask whether to revert, add to scope, or defer a necessary
out-of-plan edit. Persist the choice in PLAN.md, a checkpoint note, or a durable artifact.

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

Verify `doc/harness/manifest.yaml` and the exact four-field `TASK.json` parse. Derive terminal state from `TASK.json.closed` or `BLOCKED.md`; do not expect a stored status or verdict. No other task holds write focus. On failure, `AskUserQuestion` with setup-skill / fresh-task / continue-anyway options.
A `harness:batch` lead (`plugin/agents/task-lead.md`) runs this same flow inside its own worktree and never calls `AskUserQuestion`; it returns undelegated decisions to the coordinator instead.

**Context Recovery:** inspect TASK.json/PROGRESS.md for the current task
and list the 3 newest task directories. If an in-progress task matches the
current `task_id`, state "resuming from prior session" in the conversation.

### Phase 1: Load plan

Micro/maintenance still baseline one concise WBS outcome. Coordinators publish dispatch prepare before source edits; bound managed leads consume their parent assignment. Read `doc/harness/tasks/<task_id>/`:
1. `PLAN.md`, `REQUEST.md` (if present), `TASK.json`.
2. Extract: objective, scope (in/out), target files, acceptance criteria (AC-001+), verification commands.
3. **Resume check:** use completed outcome records and input-bound verification target applicability; preserve unchanged proofs across restored paths, recheck changed/affected inputs and never infer validity from mtimes.
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

Read `ready-lanes.md`: coordinators publish current WBS dispatch prepare; a
bound managed lead consumes its exact parent assignment without preparing or
refilling the parent pool. Choose execution ownership separately from progress
WBS and use concrete dependencies before editing:

| AC | Files | Depends on | Lane | Route | Reason |
|----|-------|------------|------|-------|--------|
| AC-001 | `<paths>` | none | A | Agent | disjoint files |

`Route` must be one of: `Agent(...)`, `sequential-prelude`, `sequential-dependent`.
Fill the table before editing files; two or more independent `Agent(...)` rows
run in one batch. For example,
`Agent(subagent_type="harness:ac-worker", prompt="Lane <task_id>:AC-001. ...")`
and the same for AC-002.
Use one Agent per independent AC. Do not assign multiple independent ACs to one
executor. Disjoint ACs use one worker per lane when Agent is available.
Sequential routes require concrete dependency, file collision, shared resource
or observed host capacity/capability facts. AC workers carry their own ladder copy in `plugin/agents/ac-worker.md` and stay inside explicit ownership. Do not edit PROGRESS.md; workers return changed paths, tests and blockers; their `AC-NNN:` status and a developer `Status:` line use the same tokens.
Handle `needs-coordinator-review` before generic rollback: never retry with the
same ownership; reassign ownership, amend PLAN, or escalate to the user. Keep
successful independent siblings promoted. Record a failed parallel lane and
retry only that dependency path sequentially. Load
`parallel-fanout.md` before any `Agent(...)` batch: it owns the batch cap,
`harness:test-author` pairing, and ac-worker sub-splits.

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
- allowed → proceed. test → proceed. forbidden → BLOCK + escalate. unlisted → WARN, auto-add to allowed with note.

### Phase 3: Implement

1. For sequential batches, work **one AC at a time**, in order. For parallel
   work, coordinators consume completed AC results and refill dependency-ready lanes using
   `parallel_dispatch.py`; do not wait for unrelated sibling executors.
   Verify a paired AC after both its writers finish. Full-tree commands wait for all writers. Skip ACs in `completed_acs`.
2. **Follow existing patterns.** Smallest coherent diff. No speculative features.
   Before the first sequential edit, the coordinator Reads the role core of
   `${CLAUDE_PLUGIN_ROOT}/agents/developer.md` and implements under it; parent context is not
   propagation. Every sequential implementation and `harness:ac-worker` follows
   the minimum-sufficient ladder: trace first,
   then no change → reuse → stdlib → platform/framework → installed dependency
   → smallest clear local expression → minimum new code. Never trade away
   current validation, auth, transactions, concurrency safety, cleanup,
   security, accessibility, tests, or requested behavior for fewer lines.

After each AC, update `phase`, `current_ac`, `partial_ac`, and `completed_acs`.
Keep detailed decisions, test evidence, deferred edges, and failed-attempt
history in PLAN.md, checkpoint notes, or the final report instead of
adding PROGRESS keys. A completed AC needs its happy path, important
negative/edge paths and regression evidence; partial work remains explicit.
Each worker runs its own targeted tests. Full-suite verification
belongs to the required qa-* agents. Await actual independent QA finals;
`wbs(action="verify")` measures current required WBS checks.

Per-AC test failures → fix immediately. These are free; only Phase 7 full-suite failures count toward the 3-cycle limit.

**Per-AC visual verification** (browser projects only): see `browser-verification.md` → "Per-AC Visual Verification" and "Per-AC Interaction Testing".
Prefer delegating Browser MCP tools (`mcp__chrome-devtools__*`) to
`harness:qa-browser`; inline use is allowed for the lightest targeted check.

### Phase 3.3: Auto-checkpoint (post all ACs)

After all ACs reach `implemented_candidate`, snapshot task state for session resume:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 ${CLAUDE_PLUGIN_ROOT}/scripts/write_checkpoint.py \
  --task-dir doc/harness/tasks/<task_id>/ \
  --note "Phase 3 complete — all ACs at implemented_candidate"
```

### Phase 3.4–3.6: Per-AC Quality Gate

Runs continuously during Phase 3.

- **3.4 Test framework bootstrap** — if project has no framework and no `doc/harness/.no-test-bootstrap` opt-out marker, offer minimal setup (JS/TS: vitest or bun:test; Python: pytest; Go/Rust: built-in). Log bootstrap to `learnings.jsonl` type `test-bootstrap`. If user declines, create opt-out marker.
- **3.5 Regression rule + Test-Evidence Gate** — two related rules.

  *Regression rule:* if the diff modifies existing non-trivial behavior (a branch, loop, parser, or security/data path) and no test covers the changed path, write a regression test immediately; a trivial declarative or one-line change needs no new test. Phase 6 owns commit order.

  *Test-evidence rule:* behavioral ACs require a concrete test path or a documented reason that no test surface exists. Record this in the final verification evidence and, when resume needs it, a checkpoint note.

  QA agents may include `codifiable:` YAML blocks in their final response for
  future regression-test extraction. Do not write critic artifacts for this.
	- **3.6 Fix-first pattern** — see `fix-first-pattern.md`: a pre-review self-check (delete / reuse / stdlib / native / yagni) over your own changed lines, plus the **3-attempt escalation rule** for every fix loop (per-AC, Phase 7, browser debug).
	- **3.6.1 Durable docs (REQ/GUIDE/ADR/POLICY)** — read PLAN.md `Durable Docs Decision` before implementation. Treat it as a documentation-impact judgment: `REQ needed`, `Pattern/skill doc enough`, or `No durable doc needed`. Create or update each selected `doc/<area>/<TYPE>__<name>.md` file; selected REQ docs must be written before source implementation, not after code is done. Use `direct REQ doc edit` / `req_scaffold.py` as the happy path when observable behavior is detected and no existing REQ fits. Use DDD-style areas or bounded contexts such as `ui`, `api`, `auth`, `billing`, `catalog`, `runtime`, `verification`, or `common`. Use `REQ` for user-visible behavior, externally consumed API contracts, constraints, and observable bugfixes; write intended observable behavior plus verification cues. Existing-screen state changes count: filters, search, sorting, loading, empty/error states, visibility, labels, native navigation/back-stack behavior, and click/input behavior. New pages, admin/backoffice screens, routes, controllers, and endpoints require a REQ even when additive. PLAN.md acceptance criteria are task-local artifacts and never substitute for a durable `REQ`. Recheck the actual diff after implementation: if you added observable UI/API behavior that PLAN marked `REQ: n/a`, create the missing REQ, link it from PLAN.md or the changed durable doc. If the diff instead changed harness process, agent instructions, testing guidance, or implementation patterns, update the relevant `GUIDE`, skill, pattern doc, or tests rather than inventing a REQ. Use `GUIDE` for reusable coding, design, testing, or implementation guidance. Use `ADR` for significant technical choices with alternatives, reasons, consequences, and tradeoffs. Use `POLICY` only for external security, legal, data-handling, approval, licensing, or organizational constraints that harness cannot fully enforce by itself; keep harness-internal execution rules in skills, agents, scripts, and tests. Keep each updated durable doc directly in the repo and link selected REQ paths from PLAN.md when the close gate needs them. For internal-only refactors, one-off tests, or non-observable maintenance, keep `REQ: n/a` in PLAN.md with a specific non-observable reason; the reason must say which durable knowledge surfaces remain unchanged.

### Phase 3.7–3.9: Post-implementation health

After all ACs done. Each runs only if prerequisite exists.

- **3.7 Lint & Format** — run linter and formatter on `git diff --name-only` only. `--fix` where safe. Re-run per-AC tests after. Skip if none configured.
- **3.8 Build check** — compile / typecheck the diff (or full project). Build failures are always T1 (our code). Fix immediately.
- **3.9 Runtime smoke** — see `runtime-smoke.md`. Project-type-specific (browser / API / CLI). Smoke commands run inside the qa-* agent (Verification delegation, C-18). Main only spawns + reads results.

### Phase 4.5–4.8: Quality Audit Pipeline

Read `quality-audit-pipeline.md` in full. Phase 4.5 gathers coverage, visual,
migration/contract, LLM-trust, and proportional performance inputs before the
final checkpoint. The old generic adversarial, line-count Red Team, and quality
synthesis agents are replaced by the mandatory balanced review gate in Phase
6.6. Canonical code/security routing comes from `wbs(action="context")`
`required_review_lenses`, derived from the canonical `TASK.json.required_lenses`
set without inspecting paths or diff content.

**Phase 4.85 Coverage Synthesis** — use the coverage diagram from the audit agent final response to add tests only for uncovered non-trivial changed behavior. Do not create a separate test extra plan file.

**Phase 4.9 Coverage Gate** — if manifest declares `coverage_minimum` / `coverage_target`, enforce. Below minimum = BLOCK (write tests); below target = WARN in final response. 3 fix cycles max; on exhaustion `AskUserQuestion` (continue / lower threshold / defer).

### Phase 5: Scope Drift Detection

`git diff --name-only` — each file is:
- In scope → proceed.
- Related but unlisted → acceptable, add the path to the appropriate PROGRESS list and explain it in the final response.
- Unrelated → revert. Belongs in a separate task.
- Missing from plan but necessary → note as "unplanned-but-necessary".

**SCOPE CREEP signals:** unrelated changes, "while I was in there" edits, new features not in PLAN, reformatted distant modules.
**MISSING REQUIREMENTS:** PLAN.md requirements not addressed by any change; partial implementations (model exists but controller missing); test coverage gaps.
**Documentation staleness:** if changed file has a corresponding doc (README section, API doc, inline docblock), flag stale under "Documentation Debt" — writer skill fixes, not here. Run `PYTHONDONTWRITEBYTECODE=1 python3 ${CLAUDE_PLUGIN_ROOT}/scripts/note_freshness.py --paths <changed files>` over the same list: it flips every `doc/**/*.md` note and `QA_KNOWLEDGE.yaml` `qa_notes` entry that declared a dependency on what you touched. Each reported entry is a recorded claim your diff may have just falsified — re-read it and correct or supersede it before close, in this task. It reports; it does not adjudicate.

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

- **6.5 IRON LAW** — PASS = PASS. No unverified claim. Machine runtime verdict measures current WBS checks; actual independent review and QA remain mandatory.
- Decide whether to continue fixing or proceed from the concrete open review and
  QA findings — unresolved `FIX_NOW` items, failing GATE tests, uncovered
  behavioral ACs. Do not compute a summary score and do not write a project
  stats series for per-task scores.

### Phase 6.6: Independent Code Review Gate
Read `quality-audit-pipeline.md` § Phase 6.6 and call `wbs(action="context")`. For
`review-code`, classify the complete current evidence with the canonical
LIGHT/STANDARD/DEEP decision table there. LIGHT spawns zero hunters, STANDARD
spawns exactly the selected correctness or contract/test hunter, and DEEP
spawns both together. Validate attempted minimal candidate arrays, then pass
only available arrays as delimited untrusted leads to one newly spawned,
full-sweep formal code reviewer at every depth. A routed security reviewer may
run with discovery and remains separate and unchanged; security receives no
hunter data. Only formal reviewers
own lens verdicts. Depth is attempt-local and may only increase;
resume/recovery recomputes it rather than reading stored task state. Await explicit PASS finals from every required reviewer. **The agent
definition owns each formal verdict contract and hunter output contract — never
restate, relocate, or paraphrase it in a spawn prompt.** Describe scope, focus and candidate data without re-specifying output syntax.

Send only `FIX_NOW` findings to the original minimum-sufficient implementer (the lane-owning worker, or the coordinator for sequential ACs under `developer.md`), except coordinator-owned review-depth reroutes. Discovery has at
most two live cycles (LIGHT 0 calls, STANDARD at most 2, DEEP at most 4): after
the first round follow the canonical change-class retry matrix; its no-hunter
branches forfeit the remaining budget. Then use a
fresh DEEP formal-only pass with the canonical exhausted/unknown invocation
reason once the budget is exhausted. Any affecting edit loops
through focused tests/checkpoint and affected formal reviewers again. Start Phase 7 QA only after actual PASS finals from all required reviewers.

### Phase 7: Verification Gate
Read `verification-gate.md` in full. Delegates full-suite test commands from PLAN.md to all applicable qa-* agents in parallel, classifies failures (GATE/PERIODIC × OWN/PRE-EXISTING), triages with hypothesis-driven debugging, enforces the 3-cycle limit with `hypothesis-driven-debugging.md` root-cause escalation on cycle 3.

**Main session MUST spawn the appropriate qa-* lens; full-suite verification MUST be delegated to qa-* agents (Verification delegation, C-18).** Spawn every applicable lens. Browser delegation is workflow guidance rather than a PreTool denial; full-suite delegation remains required by the develop contract. Bash test runners remain allowed inline only for targeted per-AC runs and debug reruns. Heavy full-suite execution and background process state belong in qa-* isolated contexts. Await actual applicable QA/UX PASS, then run `wbs(action="verify")`; the machine verdict measures current WBS checks; actual QA results come from the independent final.

QA must be spawned after actual Phase 6.6 reviewer PASS finals. A QA run started
before those finals is invalid substantive sequencing.

For user-facing changes, also spawn the applicable ux-* lens (`ux-cli`,
`ux-api`, `ux-browser`, or `ux-desktop`) when manifest support and touched-path
rules require it. UX agents return findings in their final response; no
critic artifact is written.

**No verification opt-in prompt.** If Phase 7 reveals a now-available live/API/browser/CLI verification route, run it through the appropriate qa-* lens. Do not ask the user whether to perform the verification. A rebuild, local service restart, local seed, or dev-only token generation is normal verification work unless it crosses the destructive/external boundary above.

When durable docs changed under `doc/<area>/<TYPE>__*.md`, pass those paths to the QA lens as intent evidence. QA uses `REQ` as behavior/contract verification criteria, `GUIDE` as implementation quality and consistency criteria, `ADR` as architecture intent and tradeoff criteria, and `POLICY` as external constraint criteria.

**Multi-lens QA spawns follow `parallel-fanout.md` Parallelization Triggers — when two or more QA lenses apply (e.g., `qa-browser` + `qa-api` for a fullstack diff), issue ALL agent calls in a single assistant message so independent QA lanes run concurrently.

**Also implements:**
- **Transience filter** — a failure must reproduce on 2 consecutive runs to count as `failed`. Single-run failures are logged as `transient` in `learnings.jsonl` and not counted toward the 3-cycle limit.
- **Severity × confidence close gate** — from actual independent reviewer/QA findings, block close on:
  - `critical` AND confidence ≥ 7
  - `high` AND confidence ≥ 8
  Lower severities may be deferred in final response or follow-up tasks — do not block close.
- **Acceptance result** — on gate fail, loop back to the fix cycle; on pass, report concrete evidence and run the existing WBS assertions.

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
- Current actual QA/UX results have not all PASSed (machine PASS is insufficient).
- Task is maintenance-only (no user-facing change).
- `PLAN.md` declares no user-facing surface and
  `dogfood_required` is not explicitly true.

Dogfood routing is plan-owned. Use declared surfaces such as `frontend`, `api`,
`cli`, or `desktop`; do not infer them from Git diff output.
When the PLAN declaration is ambiguous, run the dogfooder or record an explicit plan
decision. Lifecycle commands never scan the repository to make this choice.

### Phase 7.8: Harness source auto-install (post-QA, pre-close)

When the repository being changed is the harness plugin source itself (root
`install.py` plus `plugin/` and `plugin-codex/` are present), actual independent review and QA PASS MUST immediately run:

```bash
python3 plugin/scripts/install_verified.py \
  --task-dir doc/harness/tasks/<task_id>
```

This is part of completion, not a suggestion. Run it after the last source
edit and verification, before `wbs(action="close")`, so stale installed hooks cannot
prevent the task from reaching the close gate. A `harness:batch` lead (`plugin/agents/task-lead.md`) skips this step; the coordinator's integration task installs once after all leads merge. Capture the installer exit code
and runtime summaries. The trusted helper verifies canonical harness identity,
current WBS applicability, trusted task/source ownership and a byte-stable install-payload snapshot
before it invokes the snapshot's
`python3 install.py` (the default conditional run). The installer builds each
canonical runtime projection, leaves synchronized runtimes untouched, and
refreshes only stale runtimes. Comparison errors or failed refreshes block
completion; never claim the source is deployed. Payload synchronization does not
diagnose external config/registry-only drift; `python3 install.py --force` remains
the explicit repair path. Do not rerun installation for docs-only edits after
this step. The current process may retain already-loaded MCP/hooks, so report when a
new session is required to load changed runtime code. The helper writes no deduplication state; retrying after interruption recomputes payload
equality from a fresh verified snapshot.

### Phase 8: Close and final response

**Concreteness standard:** every user-facing claim must locate without searching — name file, function, line, test, command, or subagent lens. "Fixed auth bug" is not acceptable; `auth.ts:47 — added null check on session.token` is.

Before `wbs(action="close")`, verify these are true:

1. PLAN.md acceptance criteria are addressed or explicitly deferred.
2. `wbs(action="verify")` reports PASS from current required WBS checks.
3. Required independent review/QA and applicable UX results were awaited, and actual review PASS preceded actual QA.
4. User corrections are reflected in PLAN.md or durable documentation.
5. Durable docs are updated when the task changed user-visible behavior, external contracts, or reusable guidance.

Read `${CLAUDE_PLUGIN_ROOT}/skills/run/worktree-completion.md`; after `wbs(action="close")`, complete its coordinator disposal before claiming overall completion. Batch leads return to the coordinator without self-removal. If already closed, finish outstanding disposal without repeating close or review solely for cleanup.

Then provide a concise final response with:

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
PYTHONDONTWRITEBYTECODE=1 python3 ${CLAUDE_PLUGIN_ROOT}/scripts/runbook_memory.py capture \
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
Mechanical. Read the task's changed paths and `doc/CLAUDE.md` registered roots. For each file, map to doc root. Copy every reported `Known ceiling:` line under a `Known ceiling` heading in the durable doc that owns the behavior, or else into the Phase 8.7 change doc written now, so `grep -rn "Known ceiling" doc/` stays the ledger; never copy an absent ceiling. Call `wbs(action="verify")`.

When the task changes `doc/<area>/REQ__*.md`, `GUIDE__*.md`, `ADR__*.md`, or
`POLICY__*.md` OR the current task contains explicit durable user corrections,
spawn the documentation-review subagent after
durable docs. It verifies both durable docs consistency and durable doc quality, and
runs the Retrospective REQ pass over the conversation to catch user-stated
requirements that closed without becoming durable REQ docs. The task cannot
close with unresolved durable-doc gaps; a changed REQ with vague or missing
observable behavior is a FAIL, not a warning. Candidate REQs written by the
Retrospective pass land with `status: candidate` frontmatter and do not block
close on their own.

### Phase 8.7: Distilled Change Doc
One-paragraph summary of the task's user-visible behavior change. Lives at `doc/changes/<date>-<slug>.md`. Required when a `Known ceiling:` was reported; otherwise optional if no user-visible change. Writer skill consumes this for release notes.
