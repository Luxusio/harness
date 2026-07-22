---
date: 2026-07-20
status: accepted
scope: harness develop workflow
references:
  ponytail: 16f29800fd2681bdf24f3eb4ccffe38be3baec6b
  gstack: a7593d70ef1b6500d1f6457c58cf7c9896cf6062
  oh-my-claudecode: 21a6e488ce12d79b9a22d37e1093ac8e79f21029
---

# Minimal implementer and independent code review gate

## Decision

Split the post-plan workflow into three independent responsibilities:

1. a minimal implementer writes the smallest sufficient change;
2. read-only reviewers judge the final static diff and its surrounding code;
3. QA verifies the resulting behavior in the real runtime.

The target flow is:

```text
PLAN
  -> minimal implementation + focused tests
  -> independent code review
  -> fix required findings
  -> fresh re-review
  -> independent runtime QA
  -> close gate
```

Code review is not another name for QA. Review checks whether the solution is
the right shape, safe, and proportionate. QA checks whether the final solution
actually works. Any source edit invalidates both receipts; review runs before
QA so runtime verification is not spent on a statically rejected design.

## What the reference implementations establish

### Ponytail

Ponytail's useful constraint is **minimum sufficient code**, not minimum line
count. Its implementation ladder is: do nothing if unnecessary, reuse the
codebase, use the standard library, use the platform, use an installed
dependency, use the smallest local expression, and only then add new code.
It requires reading and tracing the real flow before choosing the smallest
solution. It explicitly refuses to simplify away trust-boundary validation,
data-loss prevention, security, accessibility, or requested behavior.
The stronger operational rules are equally important: inspect direct and
sibling callers before a bug fix, place one correction at the shared root
cause, prefer deletion and boring existing primitives only after comprehension,
choose edge-case correctness over a flimsier short form, and leave one focused
runnable check for non-trivial behavior.

Its `ponytail-review` is deliberately narrow: it reports only deletion and
simplification opportunities and leaves correctness and security to separate
review passes. Its subagent hook also shows that a persona intended for one
role needs explicit subagent propagation or scoping; parent-session context is
not enough.

Adopt:

- the implementation ladder;
- search and trace before editing;
- shared-root-cause fixes after inspecting affected callers;
- deletion, reuse, and boring clear primitives when they preserve the complete
  requested behavior;
- no speculative abstraction, dependency, configuration, or defense;
- explicit exceptions for current trust boundaries and failure risks;
- one proportionate runnable regression check for non-trivial behavior;
- a deletion/simplification lens in review.

Do not adopt:

- raw line count as the implementation objective;
- forced one-liners, max-three-line handoffs, intensity/session modes,
  framework-free tests, or source-code `ponytail:` comments;
- shipping a reduced interpretation and asking later when PLAN/user intent
  already requires the complete behavior;
- Ponytail injection into QA or reviewers;
- a simplification-only review as the completion gate.

### gstack

gstack's strongest ideas are workflow mechanics. It uses independent,
read-only adversarial review, records the reviewed commit, calibrates findings
with severity and confidence, requires concrete source evidence, dispatches
specialists from diff scope, and synthesizes duplicate findings. Its
adversarial pass is always-on because changed line count is a poor proxy for
risk. Security and migration specialists are treated as insurance and are not
disabled by a history of finding nothing.

Adopt:

- an always-on independent adversarial posture;
- file-and-line evidence plus a concrete failure scenario;
- scope-triggered specialists and parallel dispatch;
- freshness metadata and cross-review synthesis;
- separate `FIX_NOW` and `INVESTIGATE` outcomes.
- AC-to-diff/test/doc scope auditing, claim verification before findings, and
  confidence calibration that suppresses unsupported blockers.

Adapt:

- record a worktree diff fingerprint as well as `HEAD`, because harness tasks
  commonly review uncommitted changes;
- make unavailable or incomplete required review fail closed instead of
  treating it as a non-blocking enhancement;
- route by risk signals, not a 50/200-line threshold alone.

### oh-my-claudecode

oh-my-claudecode provides useful role boundaries: architect, code reviewer,
security reviewer, simplifier, and QA are separate agents; reviewers are
read-only; architecture and security changes select a stronger verification
tier; final validation runs architecture, security, and quality perspectives.

Adopt:

- read-only reviewers that never approve their own implementation context;
- spec compliance before style;
- architecture, security, and general quality as distinct lenses;
- deeper review for security or architectural changes.

Do not adopt directly:

- generic SOLID enforcement, fixed function-length limits, or mandatory
  cyclomatic thresholds. Those rules can manufacture abstractions that do not
  fit the project;
- a full OWASP and dependency audit on every typo or docs-only diff;
- many always-on agents whose findings overlap and create fix churn;
- a mutating simplifier after implementation. Minimality should be a read-only
  review lens, with fixes returned to the original implementer.

## Recommended roles

### Agent behavior provenance

| Harness role / mechanism | Behavior adopted | Primary reference | Harness adaptation |
|--------------------------|------------------|-------------------|--------------------|
| `harness:developer` and `harness:ac-worker` | Understand the real flow first; then stop at no change, reuse, stdlib, platform, installed dependency, smallest local expression, minimum new code | Ponytail `skills/ponytail/SKILL.md` | Named **minimum sufficient**, not minimum LOC; applied only to mutating implementers |
| `harness:developer` and `harness:ac-worker` | Inspect direct/sibling callers, fix a bug once at the shared root cause, prefer deletion and boring clear primitives, and leave one runnable check for non-trivial logic | Ponytail `skills/ponytail/SKILL.md` | PLAN/user intent and worker ownership remain authoritative; an AC worker may read outside its lane but returns an ownership blocker instead of editing another lane |
| `harness:developer` and `harness:ac-worker` | Do not simplify away trust-boundary validation, data-loss prevention, security, accessibility, or requested behavior | Ponytail `skills/ponytail/SKILL.md` | Expanded to current authorization, transaction, concurrency, cleanup, and error-propagation invariants |
| `harness:code-reviewer` | Read-only independent reviewer; spec compliance before quality; every finding has source evidence, severity, confidence, and a clear verdict | oh-my-claudecode `agents/code-reviewer.md` | Removed generic SOLID/function-length thresholds and positive-commentary requirements; added project-scale proportionality |
| `harness:code-reviewer` minimality lens | Report deletion, reuse, native/stdlib replacement, and speculative abstraction | Ponytail `skills/ponytail-review/SKILL.md` | Made it one paired lens inside a broader correctness/architecture review instead of a standalone completion verdict |
| `harness:code-reviewer` adversarial lens | Always examine production failure, races, leaks, silent corruption, swallowed errors, and trust-boundary violations | gstack `ship/SKILL.md` | Required for every source diff; unlike gstack's informational fallback, missing review fails closed |
| Review finding verification | Require exact motivating code, AC/scope cross-reference, search-before-recommending, confidence calibration, and suppress unsupported speculation from blocking output | gstack `review/checklist.md` and `review/SKILL.md` | Added `direction: excess|missing` and `disposition: FIX_NOW|INVESTIGATE|OPTIONAL`; only strongly evidenced `FIX_NOW` enters the implementation loop |
| `harness:security-reviewer` | Separate read-only OWASP/trust-boundary specialist prioritizing exploitability and blast radius | oh-my-claudecode `agents/security-reviewer.md` | Runs conditionally from path **or diff-content** signals; baseline security remains in the always-on code reviewer |
| Security specialist routing | Security and migration are insurance controls and must not be disabled by historical zero findings | gstack `ship/SKILL.md` | Security remains conditional on current scope but is never adaptive-hit-rate gated |
| Review/QA role separation | Architecture, security, quality review, and runtime QA are independent responsibilities | oh-my-claudecode `skills/autopilot/SKILL.md` | Uses one balanced reviewer plus conditional security specialist to reduce duplicate findings; QA remains a later distinct gate |
| Lifecycle freshness and synthesis | Persist reviewed revision, run independent reviewer contexts, and prioritize corroborated findings | gstack `ship/SKILL.md` | Stores hook-owned `HEAD` plus uncommitted worktree fingerprint; any edit invalidates review and requires later QA |
| Reviewer persona propagation | Parent context does not reliably reach subagents; explicitly inject or scope the role | Ponytail `hooks/ponytail-subagent.js` | Reviewer and implementer prompts are named role files on Claude and explicit methodology references in Codex spawn prompts |

These are behavioral references, not vendored dependencies. Harness owns the
final prompts, routing, receipts, and close semantics.

### 1. Minimal implementer: always used for source changes

Keep the existing `developer` role but replace its loose “simplicity first”
paragraph with a testable contract based on Ponytail.

The implementer must:

1. trace the requested path, its callers, data flow, and adjacent project
   pattern before editing;
2. stop at the first sufficient rung: no change, reuse, stdlib, platform,
   installed dependency, local expression, then minimum new code;
3. avoid single-consumer interfaces, factories, extension points, flags, and
   dependencies unless a current requirement or project boundary needs them;
4. fix a root cause at the shared boundary instead of adding guards to each
   symptom path;
5. preserve necessary validation, authorization, transactionality,
   concurrency control, cleanup, error propagation, accessibility, and other
   current invariants;
6. leave the smallest meaningful regression check for non-trivial behavior;
7. report only the skipped complexity and the concrete condition that would
   justify adding it later.
8. inspect direct and sibling callers for a bug and fix the shared root cause;
9. prefer deletion and boring clear existing primitives after comprehension,
   while choosing correctness over a shorter but flimsier expression;
10. use the project's existing test conventions for one focused runnable check
    of non-trivial branches, parsers, concurrency, security, or data-loss paths.

The named AC worker applies the same operational rules within its assigned
lane: it may inspect direct and relevant sibling callers outside owned files,
but it never edits them. If the shared-root correction belongs to another lane,
it returns exact status `needs-coordinator-review`. The coordinator handles that
status before generic rollback: reassign ownership inside approved targets,
amend the lane/AC through the protected plan flow, or escalate. It never retries
the same ownership unchanged. The worker distinguishes a missing upstream lane
or prerequisite from a package dependency, and may add a package only when its
manifest and lockfile are assigned to the lane. It reproduces bugs when feasible,
prefers deletion and boring clear primitives after comprehension, preserves
accessibility and data-loss safeguards, records deliberate ceilings with their
expansion trigger, and admits the package only for a current boundary where it
is materially clearer or safer than the smallest local implementation.

The Codex generic-worker spawn template must load the full developer role and
must name the same exact `needs-coordinator-review` producer contract whenever
convergence requires an ownership, lane, or approved-scope change. A consumer
branch alone is insufficient because ordinary blocker prose would bypass it.

The prompt must say “minimum sufficient”, never “fewest lines”. Dense code,
removed error handling, and missing tests are not accepted as minimalism.

### 2. Balanced code reviewer: always used for source changes

Add one first-class, read-only `code-reviewer` agent. It reviews the complete
changed files, relevant callers and callees, linked PLAN/REQ/GUIDE/ADR/POLICY,
and at least one nearby project example before judging the diff.

It owns five paired lenses:

| Lens | Excess to detect | Missing work to detect |
|------|------------------|------------------------|
| Architecture | new layer or dependency direction with no present need | violation of documented boundaries, ownership, or contracts |
| Abstraction | one-use interface/helper/factory, speculative flexibility | duplicated policy or invariant that can already diverge |
| Defensive logic | duplicate validation, impossible-state guards, swallowed errors, speculative retries | trust-boundary validation, authorization, cleanup, timeout, idempotency, transaction or concurrency protection |
| Correctness | generalized machinery beyond the requested behavior | wrong branch, edge case, error propagation, compatibility, migration, or test gap |
| Maintainability | comments/config/types that add indirection without information | names or structure that obscure a current domain rule |

The review must not recommend an abstraction merely because a design principle
can be named. It may require one only when current code has multiple consumers,
duplicated policy, a documented boundary, or a volatile external interface.

Before the paired lenses, the reviewer maps every CHECKS acceptance criterion
to code, test, and durable-doc evidence, and maps every material changed path
back to approved scope. It verifies suggested replacements against the current
project before recommending them. Confidence below the blocking threshold
becomes a named investigation, non-blocking optional note, or is omitted rather
than entering a speculative fix loop.

Test evidence is evaluated through the complete proof chain: setup and fixtures,
the production path and branch actually executed, and the outcome assertion.
A test file, name, or green execution is not behavior coverage by itself. Smoke
assertions such as “renders”, “does not throw”, or “is defined” prove only that
named property; mocks or stubs must not bypass the boundary being claimed.
Opposite, error, and partial-failure branches are checked when the AC or current
risk depends on them, without demanding exhaustive suites for trivial
declarative changes.

The reviewer never edits. For every proposed addition or deletion it must give:

- exact `file:line` evidence;
- the present-day failure or maintenance scenario;
- `severity` and `confidence`;
- `disposition`: `FIX_NOW`, `INVESTIGATE`, or `OPTIONAL`;
- the smallest safe correction;
- `direction`: `excess` or `missing`.

`FIX_NOW` is reserved for demonstrated requirement mismatch, correctness bug,
security/data-loss risk, documented architecture violation, or a likely current
production failure. `INVESTIGATE` needs unavailable runtime/domain evidence.
`OPTIONAL` is non-blocking and must not trigger automatic code growth.

### 3. Security reviewer: conditionally deep, never historically gated

The balanced reviewer always performs a basic trust-boundary check. Add a
separate read-only security reviewer when the diff changes any of:

- authentication, authorization, sessions, tokens, secrets, permissions;
- externally controlled input, API/controller boundaries, serialization;
- database queries or migrations, file/path/upload handling, commands, URLs;
- payments, PII, cryptography, dependencies, security configuration;
- concurrency or transaction boundaries whose failure can expose or corrupt
  data.

Routing must inspect changed paths **and diff content**. A filename regex alone
misses a five-line authorization removal in a generic controller. The security
reviewer covers applicable trust boundaries, exploitability, and blast radius;
it does not report style or general refactoring advice. Security review is an
insurance control and is never disabled due to a low historical hit rate.

For local tools, hooks, plugins, installers, and repository code, the applicable
security surface includes physical versus lexical paths, symlink components,
gitfile/worktree/submodule/nested-repository boundaries, metadata confinement,
TOCTOU identity and type revalidation, ownership and writable modes, subprocess
argv/shell/environment/cwd handling, and hook/model/tool output provenance and
freshness. These are conditional lenses: a finding still needs a concrete
attack, concurrent-writer, corruption, privilege, or present failure path.

### 4. QA agents: unchanged responsibility, later in the flow

`qa-api`, `qa-browser`, `qa-cli`, and `qa-desktop` continue to own runtime and
intent verification. They do not inherit the minimal-implementation persona and
do not satisfy the code-review receipt. Review PASS cannot satisfy QA, and QA
PASS cannot satisfy review.

## Prompt changes are required

Mechanical gates make the workflow unavoidable, but prompts define what each
agent actually does. Both are required.

Update these prompt surfaces:

1. `plugin/agents/developer.md`: add the minimum-sufficient ladder, exceptions,
   and concise implementation handoff.
2. `plugin/skills/develop/SKILL.md`: make Phase 3 spawn instructions pass the
   same implementer contract and insert the review gate before Phase 7 QA.
3. `plugin-codex/internal-skills/develop/SKILL.md`: carry the same role contract
   and explicit deferred-tool discovery/spawn/wait sequence for Codex.
4. Add `plugin/agents/code-reviewer.md` and
   `plugin/agents/security-reviewer.md`, plus Codex equivalents or inline role
   templates where Codex cannot register named agents.
5. Update session/resume/final prompt injection so it says which review lenses
   are required and gives the executable sequence: discover tool, spawn,
   await, parse verdict, record receipt, fix, and re-review.
6. Scope any Ponytail-like prompt injection to implementer agents only. Never
   inject it globally into QA, security, or balanced review agents.
7. Keep each Claude/Codex role prompt standalone, delimit its behavioral core
   with `harness:role-core` markers, require byte-identical cores in tests, and
   keep runtime-specific frontmatter or routing notes outside the core.
8. Treat `needs-coordinator-review` as a convergent ownership/decomposition
   signal before generic parallel failure rollback; never retry unchanged lane
   ownership automatically.

Prompt rules alone are advisory. Hook and close-gate enforcement must reject a
missing, incomplete, self-authored, or stale review.

## Lifecycle and evidence contract

Create a separate `REVIEW_RECEIPTS.jsonl`; do not overload QA's
`SUBAGENT_RECEIPTS.jsonl` semantics.

Each required reviewer produces a hook-owned completion record containing:

```json
{
  "event": "review_completed",
  "task_id": "TASK__...",
  "agent_id": "...",
  "lens": "review-code|review-security",
  "verdict": "PASS|FAIL|BLOCKED_ENV",
  "base_sha": "...",
  "head_sha": "...",
  "diff_fingerprint": "sha256:...",
  "finished_at": "...",
  "finding_counts": {"fix_now": 0, "investigate": 0, "optional": 0}
}
```

The hook records starts and lifecycle completions. A start receipt is never a
PASS. The close gate requires:

- Codex completion collection uses `wait_agent` followed by `list_agents`,
  because the wait result itself has neither a target identity nor transcript;
- reviewer line 1 is the exact verdict and line 2 is the single canonical
  `FINDING_COUNTS` record; missing or contradictory counts remain pending;

- all routed review lenses completed with explicit PASS;
- the completion belongs to the current task and spawned agent;
- `head_sha` and the canonical worktree diff fingerprint still match;
- no source edit occurred after the latest review;
- all required QA lenses subsequently completed with fresh PASS.

`BLOCKED_ENV` remains a real non-PASS state. If the runtime cannot expose an
independent reviewer, the task stays pending unless repository policy explicitly
allows an inline fallback. Strict compliance should not silently self-review.

## Integrating with the current harness

The current Phase 4.5-4.8 quality audit already contains test coverage,
confidence, adversarial, security, performance, migration, LLM trust, and
synthesis work. Adding another parallel “review” phase unchanged would
duplicate findings and cost.

Refactor it as follows:

- retain test-coverage and domain specialist inputs;
- replace the generic adversarial and quality-synthesis pair with the balanced
  code-reviewer contract;
- retain deep security as the conditional specialist defined above;
- keep migration/LLM specialists when their scopes match;
- make performance advisory unless a demonstrated regression or requirement is
  at risk;
- remove line-count-only red-team routing; use security, architecture,
  migration, concurrency, external contract, or broad blast-radius signals;
- persist one deduplicated review verdict and receipt before QA begins.

## Delivery plan

### Phase A: contracts and prompts

- Add the reviewer roles and output schema.
- Strengthen developer prompts on both Claude and Codex surfaces.
- Document review-versus-QA ownership and routing rules.
- Add prompt regression tests for the ladder, exceptions, read-only reviewer,
  paired excess/missing checks, and executable spawn/wait wording.

### Phase B: routing and lifecycle receipts

- Compute required review lenses from changed paths and diff-content signals.
- Add hook-owned review start/completion capture.
- Add canonical worktree diff fingerprinting.
- Validate agent identity, lens, verdict, task id, and freshness.
- Add regression tests for missing wait, timeout, FAIL, BLOCKED_ENV, wrong lens,
  wrong task, stale SHA, dirty diff after review, and forged artifacts.

### Phase C: develop-flow and close gate

- Place review after implementation quality checks and before full runtime QA.
- Feed only `FIX_NOW` findings back to the minimal implementer.
- Re-run affected focused tests, then all required review lenses.
- Start QA only after review PASS.
- Require fresh review PASS and subsequent QA PASS in `task_verify` and
  `task_close`.

### Phase D: rollout and calibration

- Run one release in shadow mode: review is mandatory to execute, but only
  security/data-loss/requirement findings block close.
- Measure false positives, repeated findings, review latency, post-review code
  growth, and defects first found by QA.
- Promote well-calibrated correctness/architecture `FIX_NOW` findings to the
  blocking gate.
- Keep `OPTIONAL` findings out of automatic fix loops.

## Acceptance criteria for implementation

- Every source-changing harness task runs an independent balanced code review.
- Security-sensitive tasks also run the independent security lens.
- The implementer receives the minimum-sufficient prompt on Claude and Codex.
- Reviewers are read-only and never approve work produced in their own context.
- Review detects both excess and missing abstraction/defense.
- Every blocking finding has code evidence, a current failure scenario, and a
  smallest safe fix.
- A review start, timeout, missing verdict, stale result, or QA-only PASS cannot
  close the task.
- Any post-review edit invalidates review; any post-QA edit invalidates both.
- Docs-only and non-code tasks use an explicit routing exemption rather than a
  fabricated PASS.

## Reference files inspected

- Ponytail (`https://github.com/DietrichGebert/ponytail`): `skills/ponytail/SKILL.md`,
  `skills/ponytail-review/SKILL.md`, `hooks/ponytail-subagent.js`
- gstack (`https://github.com/garrytan/gstack`): `review/SKILL.md`,
  `review/checklist.md`, `review/specialists/*.md`, and `ship/SKILL.md`
- oh-my-claudecode: `agents/architect.md`, `agents/code-reviewer.md`,
  `agents/security-reviewer.md`, `agents/code-simplifier.md`,
  `skills/autopilot/SKILL.md`, `src/verification/tier-selector.ts`
