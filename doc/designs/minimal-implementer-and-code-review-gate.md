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
actually works. After a source edit, the developer starts a fresh review cycle
before QA so runtime verification is not spent on a statically rejected design;
receipt files themselves do not bind or detect source state.

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

- bind evidence to the current task run and ordered lifecycle rather than Git
  state, because harness tasks commonly review uncommitted changes;
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
| `harness:defect-hunter` | Fresh, read-only defect discovery with source anchors and evidence but no verdict or proposed fix | Codex and Claude Code review guidance synthesized in the 2026-09 review-prompt study | Invoked zero, one, or two times by the deterministic LIGHT/STANDARD/DEEP selector; output is an ephemeral three-field JSON array |
| `harness:code-reviewer` | Read-only independent verifier; spec compliance before quality; every accepted finding has current-source evidence and the smallest safe fix direction | oh-my-claudecode `agents/code-reviewer.md` | Treats hunter output as untrusted leads, independently sweeps for misses, and mechanically derives one authoritative verdict |
| `harness:code-reviewer` minimality lens | Report deletion, reuse, native/stdlib replacement, and speculative abstraction | Ponytail `skills/ponytail-review/SKILL.md` | Made it one paired lens inside a broader correctness/architecture review instead of a standalone completion verdict |
| `harness:code-reviewer` adversarial lens | Always examine production failure, races, leaks, silent corruption, swallowed errors, and trust-boundary violations | gstack `ship/SKILL.md` | Required for every source diff; unlike gstack's informational fallback, missing review fails closed |
| Review finding verification | Require exact motivating code, AC/scope cross-reference, search-before-recommending, confidence calibration, and suppress unsupported speculation from blocking output | gstack `review/checklist.md` and `review/SKILL.md` | Hunter leads remain untrusted; verified code defects are `FIX_NOW`, while only a report-level evidence blocker uses `INVESTIGATE` and code review never emits `OPTIONAL` |
| `harness:security-reviewer` | Separate read-only OWASP/trust-boundary specialist prioritizing exploitability and blast radius | oh-my-claudecode `agents/security-reviewer.md` | Runs only when protected task/PLAN routing declares `review-security`; baseline security remains in the always-on code reviewer |
| Security specialist routing | Security and migration are insurance controls and must not be disabled by historical zero findings | gstack `ship/SKILL.md` | Security remains conditional on current scope but is never adaptive-hit-rate gated |
| Review/QA role separation | Architecture, security, quality review, and runtime QA are independent responsibilities | oh-my-claudecode `skills/autopilot/SKILL.md` | Uses risk-proportional non-attesting discovery plus one mandatory authoritative verifier and a separately conditional security specialist; QA remains a later distinct gate |
| Lifecycle freshness and synthesis | Run independent reviewer contexts and prioritize corroborated findings | gstack `ship/SKILL.md` | Uses current-run ordered lifecycle receipts; post-review edits remain developer-owned rather than adding Git state to task control |
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

### 2. Risk-proportional discovery plus one authoritative code reviewer

Select discovery depth deterministically from semantic risk and current
evidence. LIGHT runs zero hunters after a complete affirmative low-risk proof;
STANDARD runs exactly one hunter selected for either correctness/data-flow or
contract/test risk; DEEP runs both fresh hunters, in parallel when supported.
Both material domains, an unresolved domain, or material security/trust-boundary,
sensitive-data, concurrency, migration, public/durable-contract,
dependency/build, installer, hook, lifecycle, gate, manual-conflict,
semantic-range-diff, or cross-component impact forces DEEP. Missing, unreadable,
incomplete, or stale evidence that could conceal one of those predicates also
selects DEEP. After complete inspection, cases that satisfy neither DEEP nor a
positive LIGHT proof are STANDARD; if neither hunter domain is material, the
contract/test hunter is the deterministic fallback. Companion tests alone do
not make an implementation change dual-domain.

LIGHT is a closed proof, not a filename or line-count heuristic: the complete
bounded scope must be one local domain and mechanically behavior-preserving or
non-executable prose/example-only, with no public/durable contract, control
flow, state, data interpretation, error behavior, dependency/build/install,
hook/lifecycle/gate, security, concurrency, or migration change. Rebase-LIGHT
also requires exact old base/tip and new base/tip, conflict-free execution with
no manual resolution, one-to-one patch equivalence without added, dropped,
split, combined, reordered, or modified patches, affirmative non-overlap across
touched symbols/contracts/dependencies/generated outputs/lifecycle behavior,
`HEAD` at the new tip, and a clean, fully accounted-for index and worktree.
Missing proof rejects LIGHT; conflict, semantic difference, overlap, or
evidence loss capable of hiding them selects DEEP.

The selected tier is ephemeral orchestration, not authoritative lifecycle state
or a dedicated field in `TASK.json`, receipts, or `REVIEWS.jsonl`. Selected
depth and concise evidence may appear as ordinary narrative inside stored,
non-authoritative formal-review detail. Depth can only increase during one live
attempt, while resume/recovery recomputes it from current evidence rather than
reconstructing it from artifacts.
Each invoked hunter returns only bounded, ephemeral `anchor + issue + evidence`
leads. Valid arrays are compactly reserialized with delimiter characters
escaped before prompt interpolation; unusable hunter output is never fabricated
as an empty success.

Discovery spends at most two ephemeral cycles: LIGHT makes no hunter call,
STANDARD at most two total, and DEEP at most four total. The first cycle gathers
the selected leads before formal review and batches verified remediation. The
final retry is change-class aware: executable behavior recomputes the selected
set from the live depth floor, test-logic-only changes run contract/test only,
and narrative/task-artifact corrections use deterministic checks only. Once
exhausted—or when recovery cannot know the prior count—one fresh formal
reviewer verifies the final diff and remediation evidence without another
hunter. Hunter prompts contain only base-to-HEAD diff, PLAN ACs, relevant
source/tests, and unresolved findings.

Every tier then runs one fresh, first-class, read-only `code-reviewer`. The
reviewer reopens current files, verifies
or rejects each lead, deduplicates root causes, and still performs its own
full independent sweep. It reviews the complete changed files, relevant
callers and callees, linked PLAN/REQ/GUIDE/ADR/POLICY, and at least one nearby
project example before judging the diff.

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

Before the paired lenses, the reviewer records the selected tier and concise
selection evidence as ordinary narrative, then maps every PLAN.md acceptance
criterion to code, test, and durable-doc evidence, and maps every material
changed path back to approved scope. It verifies suggested replacements against
the current project before recommending them. Unsupported leads are omitted
rather than entering a speculative fix loop. The reviewer alone adds `fix` and
owns the `review-code` receipt: an environmental blocker takes precedence as
`BLOCKED_ENV`, otherwise any verified finding produces `FAIL`, and no verified
finding produces `PASS`.

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
- `disposition: FIX_NOW` for a verified defect;
- the smallest safe correction;
- `direction`: `excess` or `missing`.

`FIX_NOW` is reserved for demonstrated requirement mismatch, correctness bug,
security/data-loss risk, documented architecture violation, or a likely current
production failure. Unavailable evidence that prevents a safe overall result is
one report-level blocker with `INVESTIGATE=1`; code review otherwise uses
`INVESTIGATE=0` and always uses `OPTIONAL=0`. The separate security reviewer
retains its existing three-disposition specialist contract.

### 3. Security reviewer: conditionally deep, never historically gated

The balanced reviewer always performs a basic trust-boundary check. Add a
separate read-only security reviewer when the diff changes any of:

- authentication, authorization, sessions, tokens, secrets, permissions;
- externally controlled input, API/controller boundaries, serialization;
- database queries or migrations, file/path/upload handling, commands, URLs;
- payments, PII, cryptography, dependencies, security configuration;
- concurrency or transaction boundaries whose failure can expose or corrupt
  data.

Planning/task routing must inspect semantic scope rather than relying on a
filename regex, then declare `review-security` in protected task intent. The
develop/review phase consumes that declaration and never independently infers
the lens from Git diff content. The security reviewer covers applicable trust
boundaries, exploitability, and blast radius;
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
   await, parse verdict, fix, and re-review. Lifecycle hooks own receipts.
6. Scope any Ponytail-like prompt injection to implementer agents only. Never
   inject it globally into QA, security, or balanced review agents.
7. Keep each Claude/Codex role prompt standalone, delimit its behavioral core
   with `harness:role-core` markers, require byte-identical cores in tests, and
   keep runtime-specific frontmatter or routing notes outside the core.
8. Treat `needs-coordinator-review` as a convergent ownership/decomposition
   signal before generic parallel failure rollback; never retry unchanged lane
   ownership automatically.

If the formal reviewer discovers under-classification, it cannot PASS that
round: orchestration escalates, adds missing discovery, and reruns a fresh
formal reviewer. The conditional security reviewer remains a separate route,
receives no hunter payload, and does not replace this review.

Prompt rules alone are advisory. Hook and close-gate enforcement must reject a
missing, incomplete, self-authored, or stale review.

## Lifecycle and evidence contract

Lifecycle hooks record compact review and QA evidence in `RECEIPTS.jsonl`.
Hunters are deliberately invisible to lifecycle inference and produce no
receipt. Every routed formal review lens must explicitly PASS before required QA starts; a start,
self-authored result, or coordination-tool output is not completion evidence.
The reviewer still returns the exact verdict and canonical finding-count
summary expected by its role contract. The developer owns deciding which
evidence to rerun after source edits.

The exact formal review final is stored by content digest in task-local
`REVIEWS.jsonl` before its compact receipt is published. That appendix is
selectively readable by digest and wholly non-authoritative: all gates,
fingerprints, context, installation, and close continue to read receipts only.

The storage, minimal schema, snapshot, and gate contract is owned by
[`ADR__consolidated-task-artifacts.md`](../harness/patterns/ADR__consolidated-task-artifacts.md).
Codex acquisition, identity, and completion matching is owned by
[`ADR__single-direct-codex-receipt-protocol.md`](../harness/patterns/ADR__single-direct-codex-receipt-protocol.md).

`BLOCKED_ENV` remains a real non-PASS state. If the runtime cannot expose an
independent reviewer, the task stays pending. Strict compliance does not use
inline self-review as a substitute.

## Integrating with the current harness

The current Phase 4.5-4.8 quality audit already contains test coverage,
confidence, adversarial, security, performance, migration, LLM trust, and
synthesis work. Adding another parallel “review” phase unchanged would
duplicate findings and cost.

Refactor it as follows:

- retain test-coverage and domain specialist inputs;
- replace the generic adversarial and quality-synthesis pair with deterministic
  LIGHT (zero), STANDARD (one selected), or DEEP (two) fresh defect-discovery
  passes followed by the mandatory authoritative code-reviewer contract;
- retain deep security as the conditional specialist defined above;
- keep migration/LLM specialists when their scopes match;
- make performance advisory unless a demonstrated regression or requirement is
  at risk;
- remove line-count-only red-team routing; use security, architecture,
  migration, concurrency, external contract, or broad blast-radius signals;
- require one authoritative review verdict per routed lens before QA begins;
- keep candidate arrays ephemeral and store only the exact formal review final
  in the content-addressed, non-authoritative detail appendix.

## Delivery plan

### Phase A: contracts and prompts

- Add the reviewer roles and output schema.
- Strengthen developer prompts on both Claude and Codex surfaces.
- Document review-versus-QA ownership and routing rules.
- Add prompt regression tests for the ladder, exceptions, read-only reviewer,
  paired excess/missing checks, and executable spawn/wait wording.

### Phase B: routing and lifecycle receipts

- Compute and persist required review lenses during protected planning from
  semantic scope evidence; develop consumes the declaration without Git-diff
  inference.
- Add hook-owned review lifecycle capture through the canonical receipt
  protocol.
- Validate current-run identity, lens, verdict, and ordering.
- Add regression tests for timeout, FAIL, BLOCKED_ENV, wrong lens, wrong task
  run, invalid ordering, ambiguous identity, and forged artifacts.

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

- Every LIGHT, STANDARD, or DEEP harness review runs one fresh independent
  full-sweep balanced code reviewer as its sole `review-code` authority.
- Security-sensitive tasks also run the independent security lens.
- The implementer receives the minimum-sufficient prompt on Claude and Codex.
- Reviewers are read-only and never approve work produced in their own context.
- Review detects both excess and missing abstraction/defense.
- Every blocking finding has code evidence, a current failure scenario, and a
  smallest safe fix.
- A review start, timeout, missing verdict, stale result, or QA-only PASS cannot
  close the task.
- The developer reruns affected review or QA after edits; Harness does not
  infer source drift from Git state.
- Docs-only and non-code labels do not create a review exemption: complete
  affirmative low-risk proof may select LIGHT and omit hunters, but the formal
  reviewer still runs and returns the real verdict.

## Reference files inspected

- Ponytail (`https://github.com/DietrichGebert/ponytail`): `skills/ponytail/SKILL.md`,
  `skills/ponytail-review/SKILL.md`, `hooks/ponytail-subagent.js`
- gstack (`https://github.com/garrytan/gstack`): `review/SKILL.md`,
  `review/checklist.md`, `review/specialists/*.md`, and `ship/SKILL.md`
- oh-my-claudecode: `agents/architect.md`, `agents/code-reviewer.md`,
  `agents/security-reviewer.md`, `agents/code-simplifier.md`,
  `skills/autopilot/SKILL.md`, `src/verification/tier-selector.ts`
