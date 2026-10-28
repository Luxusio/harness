# harness Architecture Specification

tags: [harness, spec, architecture]
status: draft
created: 2026-04-09
updated: 2026-10-28
task_ref: TASK__harness-architecture

---

## Overview

Harness uses one WBS for any repo-mutating request. A native Goal supplies
only its objective and continuation. PLAN owns rolling outcomes, explicit
checks report verified weight, and internal task/run identities own source ownership and completion. `wbs` is the sole callable MCP tool.

## WBS Flow

```text
request / native objective -> WBS open -> rolling plan -> develop/check
-> isolated work packs when independent -> integrated review/QA -> close
```

Refine useful unfinished outcomes when progress stalls for two hours. New
user requirements and necessary discoveries update the same tree without
inventing user requirements or freezing implementation paths. Dispatch carries
current worktree ownership separately from progress measurement. Retired Goal
records and old pack bindings require explicit migration preview/apply; no
compatibility queue remains. See [measured progress](REQ__measurable-wbs-progress.md)
and [migration](patterns/native-goals.md).

## Task Loop

Every repo-mutating child task exposes this public loop:

```text
task start -> plan -> minimum-sufficient develop -> runtime QA -> close
```

Independent review and `task_verify` are internal close gates, not additional
public stages. No verification is skipped. If an internal gate finds a gap, the
task returns to develop or creates a follow-up child task when the gap is
separable.

Planning depth does not determine how many times the user must approve. Intake
may ask before review only when the target or outcome is too unclear for useful
inspection. Full and compact planning both reuse explicit requests,
clarifications, and delegated authority; they collect only unresolved material
outcome, scope, risk, irreversible-action, or external-state decisions into one
post-review interaction. The answer authorizes the resulting plan. A separate
final approval runs only when the user explicitly requested pre-code approval.
A later unauthorized material delta returns to the specific decision interaction
instead of being hidden behind generic plan approval. This authority is retained
in the existing PLAN intent summary and decision trail, not a new task field or
approval ledger.

`task_start` is both the creation and resume entry point; there is no separate
`task_resume` operation. For an existing valid open or blocked task, the default
call preserves the same `run_id`, `TASK.json`, and measured WBS checks and only
rebinds the current session. A blocked resume publishes
that unchanged-run binding before removing `BLOCKED.md`. Generation replacement
is destructive and therefore explicit: only `fresh_run: true` rotates the run
for an intentionally new generation. A closed task refuses the default call and names
that explicit fresh-run choice; invalid or unsafe control artifacts refuse in
both modes.

Within the open WBS, next selects ready outcomes for development/checks.
After all required work and independent evidence pass, close the execution and
then run self-improvement/learning promotion before separately authorized follow-ups. Memory
and automatic learning are independent of orchestration; runbooks, staged
learnings, promotion, and search remain active after every child.

Machine completion authority is current required measured WBS checks, blocked
state and declared source applicability. Await actual independent code review
and conditional security review PASS before actual QA as workflow requirements.
Machine verdicts do not attest review or QA. Work packs retain destination
branch, ancestry and archived integration proof. Workspace selection validates
registered linked-worktree files. These checks preserve progress, isolation and
integration without a receipt system or replacement ledger.

The plan declares the intended source scope in `PLAN.md`; `TASK.json` stores
the applicable review/QA lenses. These declarations route work and verification; Harness does
not prove that every edited file belongs to that scope. Post-review or post-QA
edits, concurrent mutations, and scope drift are developer-owned risks.
Explicit setup, installer, release, or diagnostic commands may still inspect
Git or hash a concrete payload when that operation intrinsically requires it.
Those opt-in checks are not task lifecycle prerequisites.

The initialization commit point is a valid `TASK.json` plus the session
active marker. `task_start` does not persist or return an environment snapshot;
environment facts are recomputed by the operation that needs them.

## Protected Artifacts

| Artifact | Writer |
|---|---|
| `PLAN.md` | WBS plan action |
| `TASK.json` | WBS open/plan/close lifecycle owners |
| durable docs under `doc/<area>/<TYPE>__*.md` | normal committed doc edits or `plugin/scripts/req_scaffold.py` |

Manual evidence writers are intentionally absent. User corrections must be
promoted directly into `PLAN.md` or durable project documentation.

### Control-plane artifact integrity

Public WBS selectors normalize one root to its internal canonical
`doc/harness/tasks/TASK__<safe-id>` execution directory. Traversal, selector
mismatches, outside paths and symlink aliases refuse before mutation. Private
execution helpers retain canonical task selectors, not public tool aliases.
Goal IDs and child records are interpreted only as explicit migration input.

`write_plan` validates the complete supplied PLAN and required-lens bundle
before its first write, so invalid input cannot leave a new PLAN or lens update
behind. `task_blocked` requires an existing valid `TASK.json` before writing
`BLOCKED.md`. Prior supported task controls require explicit migration preview/apply; ordinary
calls do not provide compatibility readers. Unsupported historical controls are
retained and reported; active or pool-referenced controls cannot be skipped.

Per-session active markers are authoritative only while their canonical task
state is live. Marker leaves must be regular files and are read without
following symlinks; a session marker's embedded session and task identities
must match its filename and resolved task. A malformed or mismatched marker,
or one pointing at a closed/blocked task, falls back to the global `.active`
marker. That marker remains a conservative Claude/session compatibility bridge
so Stop and prewrite gates do not silently disengage; active-task iteration
filters terminal per-session records as well.

The confinement boundary validates lexical and physical paths and avoids
following pre-existing control-plane leaf symlinks. Plan, block, and
task-authority publication holds an exclusive lock on the validated task
directory descriptor and uses descriptor-relative atomic writes and rollback.
Directory replacement or an unsafe leaf fails closed without publishing into
the replacement path.

## Verification

Verification has two responsibilities:

- operation check: the changed system works as implemented
- intent adequacy check: the result satisfies the user's request and durable
  requirements

A source-changing task requires actual independent read-only code review,
conditional security review, then every applicable QA lens. Await direct finals;
a start, coordinator summary or copied text cannot replace an independent result.
Fix actual FAILs and publish actual environmental blockers. Missing recording
never warrants repair, reruns or parking. The active contract is
[receipt-free execution and completion](REQ__receipt-free-execution-and-completion.md).

PLAN.md owns acceptance intent; existing WBS assertions own measured outcome
checks. `TASK.json` has `run_id`, `execution_mode`, `required_lenses`, and
`closed: boolean`. Only the public owner publishes closure. Failed, unrun or
stale required checks block close. No machine verdict claims review/QA attestation.

Browser and heavy full-suite verification use the applicable independent QA
role when available. The roles report concrete evidence; static inspection
cannot replace required runtime execution. Scope changes after review/QA require
substantive freshness assessment by the developer.

Context/verify/close read current task control and WBS state, not historical
receipt files, transcripts or review appendices. Explicit migration preserves
historical bytes and task/run/WBS identities. Default resume retains the same
run and measured checks. No watcher startup, registration, spawn-name binding,
recording capability check or missing-attestation parking remains.

PreToolUse dispatch is selective: plan-first/artifact ownership runs only for
direct write tools (`Write|Edit|MultiEdit|apply_patch`). Bash/shell mutation is
not intercepted by Harness. Read-only, browser, shell, and unrelated tools do
not launch the direct-write gate. Browser delegation has no generic PreToolUse
enforcement.

Nested repositories and submodules need no source registration for lifecycle
verification. Crossing a nested Git boundary requires the ancestor Harness
root's session-specific active-task marker; the repository-wide legacy marker
cannot authorize it. Hooks bind the session to that control root and active
task. The plan may
name nested source paths for human routing, but the lifecycle neither scans nor
authorizes them through Git.
Runtime project-document edits reject symlink components and use a bounded,
atomic helper for routing and contract-import changes.

Setup applies the recommended routing and operating profile without asking the
user to design Harness policy: proactive routing is enabled, the runtime
routing block is injected, audience defaults to SaaS/public, execution defaults
to standard plan-review-merge, and the full verification loop is enabled.
Project purpose and undetectable verification facts remain the only interview
inputs; scope protection comes from the managed Harness contract and plan scope
lock rather than a generated project-local contract. Setup also
adds a missing `@CONTRACTS.md` runtime import with an idempotent targeted edit
and enables Health scoring from every census-detected API/frontend test and
quality command without asking.

Fresh setup creates no `CONTRACTS.local.md` overlay. On Repair/Upgrade, setup
removes only the canonical legacy import immediately after the managed
`CONTRACTS.md` block; any existing project-owned local file is left untouched
but is no longer loaded. Project-specific instructions belong in `AGENTS.md`,
`CLAUDE.md`, or the matching durable `doc/` surface, while executable setup
configuration remains in the manifest.

No model-callable MCP tool can author an independent review or QA PASS. Task
ownership, source applicability and stable installation snapshots remain required.

## Durable Knowledge

Task-local files are not shared memory. User-stated durable requirements,
observable behavior contracts, significant decisions, reusable implementation
guidance, and surprising reusable discoveries must be promoted to committed
surfaces:

- `REQ__*.md` for observable behavior and contracts
- `GUIDE__*.md` for reusable guidance
- `ADR__*.md` for significant technical choices
- `POLICY__*.md` for external constraints
- skills, patterns, scripts, or tests when those are the right executable
  knowledge surface

`doc/harness/learnings.jsonl` is staging only. It helps identify promotion
candidates but does not satisfy the durable-memory requirement by itself.

## Runtime Boundaries

Harness uses one rolling WBS for ordinary requests and native objectives. Hooks provide compact context
and protect source/task ownership; MCP tools own explicit state transitions.
