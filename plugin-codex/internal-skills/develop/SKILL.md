---
name: develop
description: Implement PLAN.md on Codex. Reads PLAN.md, implements changes, routes independent ACs by current subagent capability, verifies, captures durable learnings, and closes.
user-invocable: false
---

Implement the plan for a harness task. Reads PLAN.md, implements changes, verifies completeness, captures durable learnings, and closes through MCP.

> **Codex runtime notes** (delta from Claude develop skill — read these first):
> - **No `Skill()` chain.** Where Claude invokes `Skill("harness:plan", task_id)` etc., Codex orchestrator reads the relevant SKILL.md inline and executes its phases as part of the same conversation. The plan / verify / close transitions still happen — they're just prose flow, not tool calls.
> - **Agent fan-out is capability-gated, not user-request-gated.** Where Claude spawns `oh-my-claudecode:executor` (per-AC parallel implementation), `harness:qa-*` (verification), `harness:dogfooder` (post-PASS dogfooding), or haiku sub-agents (test-coverage trace, adversarial review, edge-case scan), Codex should use `spawn_agent` when the current session exposes it. The user does not need to ask for delegation. `user did not ask for delegation`, `delegation was not requested`, and equivalent rationale are invalid skip reasons. QA/review must not be self-authored when `spawn_agent` is available: Codex hooks record each subagent start in `SUBAGENT_RECEIPTS.jsonl`; do not write receipt or critic artifacts yourself. If `spawn_agent` is unavailable, run the equivalent role methodology inline in the orchestrator's own context and state the fallback in task state or final response only when it affects verification. Multi-AC implementation can remain sequential only when `spawn_agent` is unavailable, ACs are dependent or file-conflicting, or the documented `sequential-small-task` threshold applies; preserve independent QA/review by routing from current session capability.
> - **No `AskUserQuestion` structured tool.** Where Claude emits an AskUserQuestion with labeled options, Codex emits the question + options as plain prose and reads the user's reply on the next turn. Options stay numbered/lettered so the user can pick them by short response (e.g. "A", "B", "1", "2").
> - **Browser tools are availability-gated on Codex.** The Claude `qa_delegation_gate.py` blocks main-session `mcp__chrome-devtools__*` calls and redirects them to `harness:qa-browser`; Codex routes by available tools. Use `spawn_agent` for the qa-browser lens when available, or run `plugin-codex/agents/qa-browser.md` inline when browser tools are present but no subagent path exists. If browser verification is required and tools or a reachable app are missing, return `BLOCKED_ENV` with the exact blocker instead of fabricating a PASS.
> - **MCP tool names are bare** on Codex (`task_start`, `task_verify`, `task_close`, `task_context`, `update_checks`). The Claude long-form (Claude-prefixed) does not apply.
> - **Env var is `HARNESS_PLUGIN_ROOT`**, not `CLAUDE_PLUGIN_ROOT`. The Codex plugin install sets it; Bash blocks below use this variant.
> - **Sub-file fallback.** This SKILL.md does NOT ship Codex-native sub-files in v1.5 (browser-verification.md, fix-first-pattern.md, parallel-fanout.md, quality-audit-pipeline.md, runtime-smoke.md, test-failure-triage.md, verification-gate.md). Where the Claude flow loads a sub-file, the Codex flow reads the same sub-file at `plugin/skills/develop/<name>.md` (Claude tree) and applies the sub-file's methodology with the runtime-substitution rules above. Codex-native sub-files are a v2 ergonomics improvement; v1.5 ships methodology parity by reference.

## Voice

Develop-orchestrator voice: opinionated, concrete, builder-to-builder. The develop skill is the entry point for the implement -> audit -> verify -> handoff loop — sub-files inherit voice rules but the parent sets the tone.

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

CHECKS.yaml `passed` is evidence the gate ran, not a substitute for fresh runtime verification (C-04 IRON LAW: PASS verdict must be fresh after the last edit). PROGRESS.md is the scope-lock contract for this task. Hand-editing CHECKS.yaml or skipping `update_checks.py` produces a plausible-looking ledger that lies about `reopen_count` and `last_updated`. If you find yourself wanting to mark something `passed` because the previous run was green, stop — re-verify against the current state of the repo. Stale evidence is worse than no evidence.

## Confusion Protocol

For high-stakes implementation ambiguity — blast radius >5 files, 3-strike hypothesis exhaustion, T2 vs T3 test-failure ambiguity, Phase 2 EUREKA flagging PLAN.md as wrong, Phase 5 scope creep mid-fix-loop — STOP. Name it in one sentence, present 2-3 options with concrete tradeoffs, and ask the user via conversational prose with numbered options. Read the reply on the next turn and proceed from the user-confirmed direction.

Reserve this protocol for high-stakes ambiguity where the wrong choice changes scope, architecture, or verification outcome. The bar is: "if I pick wrong, the entire implementation is built on a misread of intent or scope, and the cost shows up in verify or close, not now."

## Context Health

Soft directive — degrade gracefully, never block.

- **`[PROGRESS]` summary at phase boundaries.** Phase 3 (per-AC implement) and Phase 4 (quality audit) are the longest runs. Use `spawn_agent` for independent work when available; when phases still exceed ~5 minutes, surface a 1-2 sentence checkpoint: done, next, surprises.
- **Loop detection.** If the same fix-cycle pattern, the same hypothesis, or the same gate fires 3 times without converging, STOP and reassess. Options: premise re-confirm (Phase 2 EUREKA path) via conversational ask; switch the implementation approach; pause for user check-in. Looping silently is worse than asking.
- Progress summaries and loop-detection notices NEVER mutate git state.

## Premise Gate / User Challenge

Two structured triggers that replace silent overrides in earlier prose. On Codex both render as conversational asks:

1. **Phase 2 EUREKA premise gate** — when the search-before-building scan reveals PLAN.md's approach is suboptimal. Surface the discovery through the EUREKA path and ask the user (prose, with numbered options):
   ```
   EUREKA at AC-NNN — PLAN.md's approach looks wrong because <reason>.
   A) Re-ground premise — re-run plan skill with the new premise.
   B) Simplify scope — narrow this AC and proceed.
   C) Proceed as planned — capture EUREKA as a durable learning if reusable.
   Reply A / B / C, or describe a different direction.
   ```
   Wait for the user's next turn. The user-confirmed direction must be visible in task state or a durable artifact, not only conversation history.

2. **Phase 5 scope-expansion challenge** — when scope drift detection finds an unrelated file change that turns out to be necessary for the AC. Same shape:
   ```
   Scope expansion at <file> — touched outside PLAN target list because <reason>.
   A) Revert — change belongs in a separate task.
   B) Add to scope — update task state/plan rationale.
   C) Defer to new task — open follow-up.
   Reply A / B / C, or describe a different direction.
   ```

## Error Philosophy

The harness MCP does not tolerate mid-task stops. **Never halt with a bare BLOCKED.** Emit a conversational ask with concrete options; user decides. Errors are consumed by the running agent, not by humans.

**Scope continuity.** Execute the approved PLAN through develop. If a genuine blocker prevents completion of an AC, escalate with the concrete blocker via the BLOCKED -> conversational-ask path rather than a mid-phase meta scope question.

## Model Routing

On Claude, develop routes mechanical work to haiku and adversarial review cross-model. On Codex 0.130.0 the orchestrator runs the entire flow in a single conversation context — no model swap mid-skill. The mechanical-vs-strategic distinction still exists in *what* the orchestrator does (lighter prose for completeness audit, heavier reasoning for adversarial review) but not in *which* model does it. v2 will revisit if Codex multi_agent ergonomics make swap-mid-skill cheap.

## Flow

Phases run in strict order; each phase must complete before the next. Sub-files are lazy-loaded — do NOT pre-read them, load each only in the phase that needs it. Every phase is idempotent on re-run; check PROGRESS.md and TASK_STATE.yaml to resume instead of restarting from Phase 0.

**Runtime fallbacks:** keep routine work free of runtime routing notes. When an expected independent QA/review path was replaced by inline verification, a required browser/desktop tool was unavailable, or a high-risk policy/skill change had no independent review lens, state the reason and risk in task state or the final response. Do not write a fallback artifact just to record routing history.

**Graceful degradation:** missing tool or phase prerequisite -> skip cleanly, log reason, do NOT install missing tools. Skipped-phase table:

| Missing | Phases skipped |
|---------|----------------|
| Linter / build / test framework / coverage tool | 3.7 / 3.8 / 4 coverage / 4.9 |
| Browser tools missing or app unreachable when browser QA is required | 3 visual, 3.9 browser smoke, 4 visual-smoke, 7 browser debug, 7.7 dogfooder visual become browser-lens `BLOCKED_ENV` evidence |
| Dev server unreachable | 3.9 |
| No QA_KNOWLEDGE.yaml / learnings.jsonl | 0 / 1 (first run creates them) |

### Phase 0: Pre-flight

Verify `doc/harness/manifest.yaml` and `TASK_STATE.yaml` parse and `status` is one of: created, planning, implementing, verifying, closed. No other task holds write focus. On failure, conversational ask with setup-skill / task-id / continue-anyway options.

**Context Recovery:** inspect TASK_STATE.yaml/PROGRESS.md for the current task
and list the 3 newest task directories. If an in-progress task matches the
current `task_id`, state "resuming from prior session" in the conversation.

**Health baseline snapshot:** capture composite health score for Phase 8 delta. Best-effort — skip cleanly.

Run `python3 ${HARNESS_PLUGIN_ROOT}/scripts/health.py --dry-run || true`.

Reads `health_components` from manifest (falls back to `test_command`). Output includes per-component PASS/FAIL + composite 0-10 score. `--dry-run` prevents appending to project-level history at this stage.

### Phase 1: Load plan

Read `doc/harness/tasks/<task_id>/`:
1. `PLAN.md`, `REQUEST.md` (if present), `TASK_STATE.yaml`.
2. Extract: objective, scope (in/out), target files, acceptance criteria (AC-001+), verification commands.
3. **Resume check:** `PROGRESS.md` -> skip ACs listed in `completed_acs`. For each completed AC, compare target-file mtimes against `PROGRESS.md` mtime; files modified post-PROGRESS -> mark "needs re-verification", do not blindly skip.
4. **Learnings bootstrap:** `head -20 doc/harness/learnings.jsonl` and `ls doc/harness/patterns/*.md`. If PLAN.md absent, ask the user (run plan skill / check task_id / abort) via prose.

**Durable Docs Preflight:** before source implementation, read PLAN.md `Durable Docs Decision` as a documentation-impact judgment, not a rote REQ checklist. Confirm whether the task is `REQ needed`, `Pattern/skill doc enough`, or `No durable doc needed`, then run the REQ detector mentally or via `plugin/scripts/req_detector.py` when request, feedback, target files, or planned surfaces imply observable UI/API/mobile/native/desktop behavior. If a REQ path is selected or detector output is high-confidence, create or update that `doc/<area>/REQ__*.md` before editing source files, using a direct `doc/<area>/REQ__*.md` edit or `plugin/scripts/req_scaffold.py` when no existing REQ fits. If the task touches observable UI/API/backoffice/admin screens, routes, controllers, native navigation/back-stack behavior, or endpoints and PLAN says `REQ: n/a`, stop source implementation and amend/create the REQ first; do not wait for close or durable docs to discover the missing REQ. For harness process, agent instruction, testing guidance, or implementation-pattern changes, prefer `GUIDE` or skill/pattern docs and keep `REQ: n/a` with a specific reason.

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

**Eureka check:** if search reveals PLAN.md's approach is suboptimal (reinventing, wrong assumption), flag `EUREKA: AC-NNN — <discovery>` in the conversation and fire the Premise Gate conversational ask before overriding. Persist reusable discoveries as `type:"eureka"` in `learnings.jsonl`, then promote durable ones to a committed skill, pattern, test, or typed doc before close.

**Baseline screenshot (browser projects):** if browser tools are available in the current Codex session, capture it inline using the qa-browser methodology. If browser QA is required but unavailable, record the blocker for the browser lens.

### Phase 3.0: AC Dependency Analysis

Codex AC implementation is capability-gated, not user-request-gated. Build the
same lane table as the Claude develop skill before editing files. The user does
not need to ask for delegation; `user did not ask for delegation` is an invalid
skip rationale. Do not wait for the user to request delegation. User request is
not a condition for parallel routing.

| AC | Files | Depends on | Lane | Route | Reason |
|----|-------|------------|------|-------|--------|

`Route` is `spawn_agent(worker)`, `sequential-prelude`,
`sequential-dependent`, or `sequential-small-task`. When the table has two or
more independent AC rows and `spawn_agent` is available, you must spawn one worker per independent AC in one assistant message. This is mandatory capability/task-shape routing, not an optional optimization. Use explicit file
ownership:

```text
spawn_agent {
  agent_type: "worker",
  message: "Implement AC-00X only. Ownership: <paths>. You are not alone in the codebase; do not revert edits made by others. Edit files directly and list changed paths in your final answer.",
  fork_context: true
}
```

Use one worker per independent AC. Do not assign multiple independent ACs to one
worker. Workers return status, changed paths, and blockers in their final
response; the orchestrator merges PROGRESS.md and CHECKS.yaml after all
siblings return.

Use `sequential-small-task` only for genuinely trivial N=2 work (estimated <20
changed lines combined and <30 seconds of editing). Sequential fallback must
state `ac_count`, AC ids, `conflict` (specific dependency/file conflict, or
`none`), `estimated_lines`, `estimated_seconds`, and `reason` in the lane table.
Valid reasons are only `spawn_agent-unavailable`,
`dependency-conflict`, or `small-task`; `user did not ask for delegation` is
invalid. When `spawn_agent` is unavailable for otherwise independent ACs, record
the fallback in task state or final response.

Do not create a separate chronological artifact just because routing was sequential.

If PLAN.md declares a helper-extract AC, run it first, then route the consumer
ACs from the lane table. The dependency matrix from PLAN.md is still the single
source of truth.

### Phase 3.1: Scope Lock

Declare allowed / test / forbidden paths in PROGRESS.md. Before each file edit:
- allowed -> proceed. test -> proceed. forbidden -> BLOCK + escalate. unlisted -> WARN, auto-add to allowed with note.

### Phase 3: Implement

1. For sequential batches, work **one AC at a time**, in order. For parallel
   batches, wait for all sibling worker results, then merge progress once. Skip
   ACs in `completed_acs`.
2. **Follow existing patterns.** Smallest coherent diff. No speculative features.
3. **Codex tool surface:** use `read_file` for reads, `apply_patch` for edits/writes (Codex envelope-oriented), `shell` for Bash commands. Multi-edit is one `apply_patch` envelope per file. Where the Claude flow says `Edit`/`Write`/`MultiEdit`, read it as `apply_patch`.

**PROGRESS.md after each AC:**

```yaml
task_id: <task_id>
phase: 3
completed_acs:
  - id: AC-001
    status: done
    tests: passed
    completeness: 9
    deferred_edges: []
current_ac: <next or "done">
partial_ac: null
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
| <=3 | Partial — AC should not be marked done |

Any AC scoring <=7 MUST list `deferred_edges`. <=5 requires explicit justification in PROGRESS.md or the final response (MVP scope, user-deferred, etc.).

**Acceptance Ledger update (after each AC):** once the AC's code is in and per-AC tests pass, mark it `implemented_candidate` in CHECKS.yaml. Only Phase 7 promotes to `passed`. Update CHECKS.yaml only through `update_checks.py`.

```bash
python3 ${HARNESS_PLUGIN_ROOT}/scripts/update_checks.py \
  --task-dir doc/harness/tasks/<task_id>/ \
  --ac AC-00X --status implemented_candidate \
  --evidence "<file:line | test name>"
```

**Per-AC test run:** `git diff --name-only HEAD~1` -> for each changed source, find test files that import/reference it -> run only those. If no tests exist for changed module, write one (Phase 3.5 rule). If PLAN.md specifies per-AC verify commands, prefer those.

**Delegation rule (C-18 / Verification delegation).** On Claude, browser MCP tools (`mcp__chrome-devtools__*`) MUST be delegated to `harness:qa-browser`; the pre-tool gate enforces that. On Codex, use actual tool availability: spawn the qa-browser lens when `spawn_agent` is available, run the qa-browser methodology inline only when no subagent path exists, and write browser-lens `BLOCKED_ENV` when required browser verification cannot run. Bash test runners (`pytest`, `npm test`, `pnpm test`, `vitest`, `cargo test`, `go test`, ...) are allowed inline as on Claude. Heavy full-suite execution is still better handled as a downstream verification step than mixed into implementation work.

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

  *Test-Evidence Gate:* `update_checks.py` rejects promotion of `kind in {feature, functional}` ACs to `implemented_candidate` / `passed` unless `--test-evidence <path>` resolves to a real file inside the repo (no symlinks, no traversal). Use the bypass with a documented reason for ACs that genuinely have no test surface (configs, narration, migrations):
  ```bash
  # Promote with evidence:
  python3 ${HARNESS_PLUGIN_ROOT}/scripts/update_checks.py \
    --task-dir doc/harness/tasks/<task_id>/ --ac AC-001 \
    --status implemented_candidate \
    --test-evidence tests/regression/task_xx/test_ac_001__behavior.py

  # Bypass with reason (logged to learnings.jsonl):
  python3 ${HARNESS_PLUGIN_ROOT}/scripts/update_checks.py \
    --task-dir doc/harness/tasks/<task_id>/ --ac AC-007 \
    --status implemented_candidate \
    --no-test-required "narration-only AC, no behavior to test"
  ```
  Allowlist (no evidence required): `kind in {bugfix, doc, verification}`. Bugfix is gated separately by Iron Law (`--root-cause`). Missing `kind:` field defaults to `unknown` and skips the gate.

  *QA codifier* (after Phase 7 PASS, before close):
  ```bash
  python3 ${HARNESS_PLUGIN_ROOT}/scripts/qa_codifier.py --task-dir <task_dir> 2>/dev/null || true
  ```
  Parses `codifiable:` YAML blocks emitted by the QA pass and stages validated tests to `tests/regression/<sanitized-task-id>/`. Same script as Claude side; runtime-agnostic.
	- **3.6 Fix-first pattern** — read `plugin/skills/develop/fix-first-pattern.md` (Claude tree fallback). Classify AUTO-FIX (dead code, magic numbers, stale comments, missing guards) and ASK (API design, architecture, security, DRY extractions). Auto-fix immediately; surface ASK items through the current user-input mechanism or final response. The **3-attempt escalation rule** in that sub-file applies to every fix loop (per-AC, Phase 7, debug).
	- **3.6.1 Durable docs (REQ/GUIDE/ADR/POLICY)** — read PLAN.md `Durable Docs Decision` before implementation. Treat it as a documentation-impact judgment: `REQ needed`, `Pattern/skill doc enough`, or `No durable doc needed`. Create or update each selected `doc/<area>/<TYPE>__<name>.md` file; selected REQ docs must be written before source implementation, not after code is done. Use a direct `doc/<area>/REQ__*.md` update or `req_scaffold.py` as the happy path when observable behavior is detected and no existing REQ fits. Use DDD-style areas or bounded contexts such as `ui`, `api`, `auth`, `billing`, `catalog`, `runtime`, `verification`, or `common`. Use `REQ` for user-visible behavior, externally consumed API contracts, constraints, and observable bugfixes; write intended observable behavior plus verification cues. Existing-screen state changes count: filters, search, sorting, loading, empty/error states, visibility, labels, native navigation/back-stack behavior, and click/input behavior. New pages, admin/backoffice screens, routes, controllers, and endpoints require a REQ even when additive. PLAN.md acceptance criteria are task-local artifacts and never substitute for a durable `REQ`. Recheck the actual diff after implementation: if you added observable UI/API behavior that PLAN marked `REQ: n/a`, create the missing REQ, link it from PLAN.md or the changed durable doc. If the diff instead changed harness process, agent instructions, testing guidance, or implementation patterns, update the relevant `GUIDE`, skill, pattern doc, or tests rather than inventing a REQ. Use `GUIDE` for reusable coding, design, testing, or implementation guidance. Use `ADR` for significant technical choices with alternatives, reasons, consequences, and tradeoffs. Use `POLICY` only for external security, legal, data-handling, approval, licensing, or organizational constraints that harness cannot fully enforce by itself; keep harness-internal execution rules in skills, agents, scripts, and tests. Keep each updated durable doc directly in the repo and link selected REQ paths from PLAN.md when the close gate needs them. For internal-only refactors, one-off tests, or non-observable maintenance, keep `REQ: n/a` in PLAN.md with a specific non-observable reason; the reason must say which durable knowledge surfaces remain unchanged.

### Phase 3.7-3.9: Post-implementation health

After all ACs done. Each runs only if prerequisite exists.

- **3.7 Lint & Format** — run linter and formatter on `git diff --name-only` only. `--fix` where safe. Re-run per-AC tests after. Skip if none configured.
- **3.8 Build check** — compile / typecheck the diff (or full project). Build failures are always T1 (our code). Fix immediately.
- **3.9 Runtime smoke** — read `plugin/skills/develop/runtime-smoke.md` (Claude tree fallback). Project-type-specific (browser / API / CLI). Browser smoke runs when browser tools are available; otherwise required browser smoke becomes browser-lens `BLOCKED_ENV`.

### Phase 4: Plan Completion Audit

On Claude this is a haiku sub-agent. On Codex, use `spawn_agent` when available for an independent completion audit; otherwise run the same pass inline as fallback. Cross-reference every AC against `git diff --stat` and classify each as DONE / PARTIAL / NOT DONE / CHANGED + category (CODE / TEST / MIGRATION / CONFIG / DOCS). Be conservative with DONE (file touched != AC done); be generous with CHANGED (goal met by different means).

For PARTIAL / NOT DONE, classify cause: scope-cut / context-exhaustion / misunderstood / blocked / forgotten / evolved. Fix forgotten and misunderstood immediately; log scope-cut + blocked in PROGRESS.md; mark evolved as CHANGED with the new approach.

### Phase 4.5-4.8: Quality Audit

Read `plugin/skills/develop/quality-audit-pipeline.md` (Claude tree fallback) for the full methodology. On Codex, dispatch independent audit lenses with `spawn_agent` when available; otherwise run the same checks sequentially in the orchestrator context and state the fallback in task state or final response if expected independence was lost:

1. **Test-coverage trace** — for each changed source, identify which test path exercises it; flag uncovered branches.
2. **Confidence ratings** — per-AC confidence 0-10 (different axis than completeness — covers "does it work" not "did we cover the surface"). Highlight any AC <=6.
3. **Adversarial review** — read the diff with a fresh adversarial framing: what would break this? What edge case did we miss? What contract did we silently change? Prefer a subagent for independence when available; inline adversarial re-read is fallback.
4. **Visual-smoke** — browser-only; run with available browser tools or record browser-lens `BLOCKED_ENV` when required and unavailable.

**Diff scope detection** (routes specialists):
```bash
git diff --name-only | while read f; do
  case "$f" in
    *.tsx|*.jsx|*.css|*.scss|*.html|*.vue|*.svelte) echo "SCOPE_FRONTEND=true" ;;
    *auth*|*session*|*token*|*password*|*permission*|*guard*) echo "SCOPE_AUTH=true" ;;
    *migration*|*schema*|*db/*|*migrate*) echo "SCOPE_MIGRATIONS=true" ;;
    *api*|*endpoint*|*graphql*|*rest*|*openapi*) echo "SCOPE_API=true" ;;
  esac
done | sort -u
```

**Specialists** (use `spawn_agent` when available; otherwise run in sequence):
- Security review for SCOPE_AUTH or SCOPE_API. Never gated.
- Migration safety for SCOPE_MIGRATIONS. Never gated.
- Performance for SCOPE_API or large diffs. Gated (skip after 3+ dispatches with 0 findings).
- LLM-trust review for SKILL.md or agent.md changes.

**Red Team (conditional):** when diff >= 200 lines OR any specialist reported critical. Job: find what the first pass MISSED. Prefer `spawn_agent` for an independent adversarial pass when available; inline re-frame is fallback.

**Phase 4.85 Coverage Synthesis** — use the coverage diagram from the audit
agent final response to update tests directly. Do not create a separate test
extra plan file.

**Phase 4.9 Coverage Gate** — if manifest declares `coverage_minimum` / `coverage_target`, enforce. Below minimum = BLOCK (write tests); below target = WARN in final response. 3 fix cycles max; on exhaustion conversational ask (continue / lower threshold / defer).

### Phase 5: Scope Drift Detection

`git diff --name-only` — each file is:
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

- **6.5 IRON LAW** — PASS = PASS. No stale PASS. No unverified claim. Runtime verdict must be fresh after last file change.
- Use the current quality score to decide whether to continue fixing or proceed.
  Do not write a project stats series for per-task scores.

### Phase 7: Verification Gate

Read `plugin/skills/develop/verification-gate.md` (Claude tree fallback) for the full gate methodology. Runs test commands from PLAN.md, classifies failures (GATE/PERIODIC × OWN/PRE-EXISTING), triages with hypothesis-driven debugging, enforces the 3-cycle limit.

**On Codex:** pick the required lens for the diff (qa-cli for libraries, qa-api for endpoints, qa-desktop for native GUI, qa-browser for frontend/browser work when `browser_qa_supported: true`). Use `spawn_agent` for independent QA when available:

For user-facing surfaces, also route the matching UX lens (`ux-cli`,
`ux-api`, `ux-browser`, or `ux-desktop`) when manifest support and touched-path
rules make it applicable. Pass the user flow, pages, commands, endpoints,
windows, states, and expected intent in the subagent prompt so the UX lens can
judge shippability without reverse-engineering the change.

```text
spawn_agent {
  agent_type: "harness:qa-<lens>",
  message: "You are the qa-<lens> lens for <task_id>. Read <task_dir>/PLAN.md, CHECKS.yaml, TASK_STATE.yaml, changed files, durable docs named in PLAN or touched paths, and plugin-codex/agents/qa-<lens>.md. Follow all four roles. Do not modify files. Return PASS/FAIL/BLOCKED_ENV with evidence and concrete findings.",
  fork_context: true
}
```

After spawning QA/UX, run `task_verify` with `reconcile_acs: true`. The Codex
hook records the subagent start automatically. If no subagent path exists, run
the lens methodology in-conversation and state the fallback in task state or final response; do not
call a critic writer.

Multi-lens concurrency uses `spawn_agent` when available; otherwise run required lenses sequentially. If browser QA is required, close with browser-lens PASS evidence or browser-lens `BLOCKED_ENV`.

When durable docs changed under `doc/<area>/<TYPE>__*.md`, pass those paths to the QA lens as intent evidence. QA uses `REQ` as behavior/contract verification criteria, `GUIDE` as implementation quality and consistency criteria, `ADR` as architecture intent and tradeoff criteria, and `POLICY` as external constraint criteria.

**Also implements:**
- **Transience filter** — a failure must reproduce on 2 consecutive runs to count as `failed`. Single-run failures logged as `transient` in `learnings.jsonl`, not counted toward the 3-cycle limit.
- **Severity × confidence close gate** — after synthesis, block close on:
  - `critical` AND confidence >= 7
  - `high` AND confidence >= 8
  Lower severities may be deferred in final response or follow-up tasks — do not block close.
- **Acceptance Ledger promotion** — on gate pass, `update_checks.py --status passed`. On gate fail, `--status failed` (auto-increments `reopen_count`), loop back to fix cycle. Close gate requires every AC to be `passed` or `deferred`.

### Phase 7.5: Auto-checkpoint (post verify gate)

```bash
python3 ${HARNESS_PLUGIN_ROOT}/scripts/write_checkpoint.py \
  --task-dir doc/harness/tasks/<task_id>/ \
  --note "Phase 7 done — runtime_verdict=$(grep runtime_verdict <task_dir>/TASK_STATE.yaml | awk '{print $2}')"
```

### Phase 7.6: Health score capture

```bash
python3 ${HARNESS_PLUGIN_ROOT}/scripts/health.py || true
```

### Phase 7.7: Dogfood (post-QA, pre-close)

On Claude this is a `harness:dogfooder` agent spawn. On Codex, use `spawn_agent` when available; otherwise the dogfooder methodology runs inline in the orchestrator's context after Phase 7 PASS.

**Skip conditions (Codex):**
- `runtime_verdict` is not PASS (QA must pass first).
- Task is maintenance-only (no user-facing change).
- Empty intersection between `TASK_STATE.yaml touched_paths` and the user-facing globs below.

**User-facing globs:**
```
**/*.{tsx,jsx,vue,svelte,html,css,scss}
plugin/agents/**
plugin/skills/**
plugin-codex/skills/**
**/routes/**
**/api/**
bin/**
cli/**
README.md
doc/changes/**
```

**Predicate** (TASK_STATE.yaml `touched_paths` is the source of truth — refreshed by every `task_verify`):

```bash
_USER_FACING=$(python3 - <<'PY' 2>/dev/null || echo ""
import yaml, sys, re, pathlib
state_path = pathlib.Path("<task_dir>/TASK_STATE.yaml")
if not state_path.exists():
    sys.exit(0)
state = yaml.safe_load(state_path.read_text()) or {}
paths = state.get("touched_paths") or []
pat = re.compile(r"(\.tsx|\.jsx|\.vue|\.svelte|\.html|\.css|\.scss)$|^(plugin/agents/|plugin/skills/|plugin-codex/skills/|.*/routes/|.*/api/|bin/|cli/|README\.md|doc/changes/)")
for p in paths:
    if pat.search(p):
        print(p)
        break
PY
)
[ -z "$_USER_FACING" ] && echo "SKIP_DOGFOOD" || echo "RUN_DOGFOOD"
```

`SKIP_DOGFOOD` short-circuits; `RUN_DOGFOOD` uses `spawn_agent` for the
dogfooder when available, or runs the same methodology inline as fallback: use
the product as a power user, find friction / gaps / missing workflows that QA
didn't catch (because they aren't bugs). Return high-impact findings and
suggested follow-ups in the dogfooder final response. The dogfooder does NOT
gate task completion.

Visual dogfooder browser screenshots follow the same availability gate: capture them when browser tools are present; otherwise return `BLOCKED_ENV` for the missing browser tool/app condition when visual dogfood is required.

### Phase 8: Close and final response

**Concreteness standard:** every user-facing claim must locate without searching — name file, function, line, test, command, or subagent lens. "Fixed auth bug" is not acceptable; `auth.ts:47 — added null check on session.token` is.

Before `task_close`, verify these are true:

1. Every AC is `passed` or explicitly `deferred` in CHECKS.yaml.
2. `task_verify` reports a fresh PASS after the last edit.
3. Required QA/UX subagents were spawned when the runtime exposed them; hook-owned `SUBAGENT_RECEIPTS.jsonl` proves the start.
4. User feedback events have terminal disposition in task state: `promoted`, `handled-local`, `deferred`, or `rejected`.
5. Durable docs are updated when the task changed user-visible behavior, externally consumed API contracts, reusable guidance, significant decisions, external constraints, user-stated durable rules, or reusable implementation knowledge.
6. Reusable EUREKA discoveries, user corrections, dogfood findings, setup recipes, and repeated friction are either promoted to committed artifacts or explicitly rejected/deferred with a concrete reason.

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
- `test_coverage` = (tested paths / total changed paths) × 10. No framework -> 5.
- `adversarial_clean` = max(0, 10 - (crit × 3 + high × 1.5 + med × 0.5)).
- `scope_discipline` = 10 / 7 / 4 / 0 (none / auto-added / justified / unjustified).

**Cleanup:** PROGRESS.md persists beyond Phase 8 as the scope-lock contract for any post-close edits. Keep PROGRESS.md in place; do not create a separate narrative handoff artifact.

### Phase 8.5: Reflect and Log (capture-when-fresh, no quota)

When you discover something genuinely useful during develop — a real bug whose fix is non-obvious, a build/test/tool gotcha, a workaround worth knowing next time — log it the moment you find it. Log only concrete, reusable facts at discovery time; leave the log untouched when there is no durable learning.

```bash
echo '{"ts":"'"$(date -u +%Y-%m-%dT%H:%M:%SZ)"'","type":"operational|pitfall|eureka|feedback","source":"develop","key":"SHORT_KEY","insight":"FACT + FIX","files":["<path>"],"task":"'"<task_id>"'"}' >> doc/harness/learnings.jsonl 2>/dev/null || true
```

### Phase 8.5.1: Feedback-Derived Rules (judgment required, capture optional)

Review user corrective feedback from the task. Convert corrective feedback into a reusable conditional behavior rule only when it can be reduced to a readable "When X, do Y. Verify by Z." instruction.

Classify the task as exactly one:
- `none` — no user feedback implies a future behavior rule.
- `captured` — feedback produced a reusable conditional rule and it was recorded in `learnings.jsonl` for promotion, or directly in a committed durable artifact.
- `rejected` — feedback looked like a preference or complaint but should not become a rule. Record the reason in task state or final response.

Capture only rules with all three parts: trigger, action, and verification. Reject blame narratives, task-local preferences, vague style opinions, and one-off urgency requests. Write behavior rules for Tier 2 docs; convert incident-shaped lessons into behavior or reject them.

When captured, the durable artifact or learning text must be readable prose:

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
`POLICY__*.md`, spawn the documentation-review subagent after durable docs. It
verifies both durable docs consistency and durable doc quality. A changed REQ with
vague or missing observable behavior is a FAIL, not a warning. Do not write
legacy critic markdown artifacts.

### Phase 8.7: Distilled Change Doc

One-paragraph summary of the task's user-visible behavior change. Lives at `doc/changes/<date>-<slug>.md`. Optional if no user-visible change.

---
