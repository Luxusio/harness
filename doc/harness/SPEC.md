# harness Architecture Specification

tags: [harness, spec, architecture]
status: draft
created: 2026-04-09
updated: 2026-09-27
task_ref: TASK__harness-architecture

---

## Overview

Harness uses one WBS for any repo-mutating request. A native Goal supplies
only its objective and continuation. PLAN owns rolling outcomes, explicit
checks report verified weight, and internal task/run identities own ordered
review/QA receipts and completion. `wbs` is the sole callable MCP tool.

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
call preserves the same `run_id`, `TASK.json`, and `RECEIPTS.jsonl` and only
rebinds the current session and lifecycle watcher. A blocked resume publishes
that unchanged-run binding before removing `BLOCKED.md`. Generation replacement
is destructive and therefore explicit: only `fresh_run: true` rotates the run
and resets review/QA receipts. A closed task refuses the default call and names
that explicit fresh-run choice; invalid or unsafe control artifacts refuse in
both modes.

For each completed execution, post-close continuation is ordered as:
`wbs(close) -> self-improvement/learning promotion -> wbs(next)`. Memory
and automatic learning are independent of orchestration; runbooks, staged
learnings, promotion, and search remain active after every child.

Harness lifecycle operations do not inspect Git state. `task_start` creates the
task artifacts and active marker without capturing HEAD, dirty paths,
submodules, gitlinks, worktree bindings, or a source baseline. `task_context`,
`task_verify`, and `task_close` likewise do not run `git status`, `git diff`, or
source fingerprinting. This keeps nested repositories, ignored checkouts,
submodules, and linked worktrees outside the control plane. The one exception
is the optional task-tool `workspace` argument (`harness:batch` leads): the
server reads that linked worktree's gitfile and back-pointer files, without
running Git, to validate it and use it as the task root
(`doc/harness/REQ__parallel-tasks-via-worktree-leads.md`).

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
| `PLAN.md` | MCP `write_plan` |
| `TASK.json` | task lifecycle MCP tools |
| `RECEIPTS.jsonl` | Codex/Claude review and QA lifecycle hooks, including the root-hook-registered, MCP-hosted Codex watcher |
| `REVIEWS.jsonl` | shared review-detail writer reached only by formal review completion lifecycle hooks or `review-log` |
| durable docs under `doc/<area>/<TYPE>__*.md` | normal committed doc edits or `plugin/scripts/req_scaffold.py` |

Manual evidence writers are intentionally absent. User corrections must be
promoted directly into `PLAN.md` or durable project documentation.

### Control-plane artifact integrity

Task selectors accepted by MCP tools resolve to one immediate
`doc/harness/tasks/TASK__<safe-id>` child. Bare IDs are normalized for
compatibility through `task_id` and `slug`; canonical repository-relative and
absolute task paths are accepted only through `task_dir`. A tool such as
`task_blocked` that exposes only `task_id` therefore does not accept a path.
Traversal, control characters, selector mismatches, outside paths, and symlink
aliases are rejected before task artifacts are created or changed.
Goal IDs use the same safe-name boundary, and Goal child entries persist the
canonical repository-relative task path.

`write_plan` validates the complete supplied PLAN and required-lens bundle
before its first write, so invalid input cannot leave a new PLAN or lens update
behind. `task_blocked` requires an existing valid `TASK.json` before writing
`BLOCKED.md`. Removed task-control artifacts have no compatibility readers or
migration path; starting a fresh run is the recovery action.

Per-session active markers are authoritative only while their canonical task
state is live. Marker leaves must be regular files and are read without
following symlinks; a session marker's embedded session and task identities
must match its filename and resolved task. A malformed or mismatched marker,
or one pointing at a closed/blocked task, falls back to the global `.active`
marker. That marker remains a conservative Claude/session compatibility bridge
so Stop and prewrite gates do not silently disengage; active-task iteration
filters terminal per-session records as well.

The confinement boundary validates lexical and physical paths and avoids
following pre-existing control-plane leaf symlinks. Receipt, plan, block, and
task-authority publication holds an exclusive lock on the validated task
directory descriptor and uses descriptor-relative atomic writes and rollback.
Directory replacement or an unsafe leaf fails closed without publishing into
the replacement path.

## Verification

Verification has two responsibilities:

- operation check: the changed system works as implemented
- intent adequacy check: the result satisfies the user's request and durable
  requirements

A source-changing task requires the plan-declared read-only reviewers, followed
by every declared QA lens. Only ordered, lifecycle-owned, explicit PASS evidence
for the current task run can close the task; a start or self-authored result
cannot. Acceptance intent remains in `PLAN.md`, and later source drift is
developer-owned.

On Claude builds that deliver `SubagentStop` but no `SubagentStart`, the stop
hook requires exact top-level official agent/session identity, the matching
session marker and run, and a stable matching current-run Claude transcript
whose recorded start attachment supplies the agent type before emitting the
single-use inferred-start/completed pair in one task
transaction. Missing, foreign, stale, replayed, aliased, untrusted, or unbound
stops create no authority. The hook (and, until its removal on 2026-09-23, the
Claude Stop gate) derives lifecycle state from
the current task's unified receipts and never maintains a background registry
or registry lock. The official adapter does not expose a generic receipt-append
tool, but a hostile shell can invoke shipped lifecycle scripts directly; that
accepted exposure is outside Harness's integrity boundary.

Receipt storage, minimal schema, immutable snapshot, and gate semantics are
normatively defined by
[ADR__consolidated-task-artifacts.md](patterns/ADR__consolidated-task-artifacts.md).
Codex acquisition, identity, and completion are normatively defined by
[ADR__single-direct-codex-receipt-protocol.md](patterns/ADR__single-direct-codex-receipt-protocol.md).

Verification delegation is a workflow optimization, not a generic pre-tool
policy. Browser and heavy full-suite work should use an applicable `qa-*` lens
when isolation materially reduces context or process load. Inline browser use
remains valid when it is the lighter available path; its potentially large DOM,
screenshot, or evaluation payload is an accepted caller-owned context cost and
does not relax the receipt-backed close requirements above.

`write_plan` accepts only the complete non-empty `PLAN.md` body and an optional
`required_lenses` set. It canonicalizes supported lenses, requires
`review-code` plus at least one `qa-*` lens, and atomically publishes only
`PLAN.md` and `TASK.json`. Planning rationale remains in `PLAN.md`; there is no
audit argument or audit artifact.

`task_context`, `task_verify`, and `task_close` read task control artifacts and
the receipt stream only. They never read non-authoritative `REVIEWS.jsonl`.
They do not discover repositories, enumerate changed paths,
validate Git metadata, or invalidate PASS because files changed. A developer
who edits after QA must decide whether to rerun review or QA. Harness deliberately
accepts that risk instead of imposing a repository-integrity monitor on local
development. Missing or malformed task artifacts and receipt streams still fail
closed. Public context and verification responses expose verdicts, required
lenses, `missing_for_close`, `next_action`, and one report path; they do not
embed raw receipts, completion summaries, transcript locations, or a duplicate
authoritative review report path. Exact formal review detail is an optional
content-addressed appendix selected explicitly by receipt digest; it is never a
context, verdict, fingerprint, verification, installation, or close input.

`TASK.json.run_id` is the non-Git generation identity and a canonical UUIDv7
whose embedded millisecond timestamp supplies the run-start cutoff.
`task_start` creates it for a new task. Default resume of an open or blocked task
retains it and its receipt stream; only explicit `fresh_run: true` rotates it
while clearing prior receipts. The session marker carries only that run ID to
lifecycle watchers; start/completion receipts must match it. A replayed rollout
event whose timestamp predates the UUIDv7 cutoff is ignored. This prevents
prior-generation evidence from satisfying an explicitly fresh run without
discarding same-generation evidence during ordinary resume.

Codex SessionStart and spawn-selective PreToolUse establish the bounded root
registration used by the MCP-hosted watcher. Registration and lifecycle
authority follow the single-direct protocol ADR; late registration never
recovers already-completed work.

PreToolUse dispatch is selective: plan-first/artifact ownership runs only for
direct write tools (`Write|Edit|MultiEdit|apply_patch`). Bash/shell mutation is
not intercepted by Harness. Read-only, browser, shell, and unrelated tools do
not launch the direct-write gate. Browser delegation has no generic PreToolUse
enforcement.

Nested repositories and submodules need no source registration for lifecycle
verification. Crossing a nested Git boundary requires the ancestor Harness
root's session-specific active-task marker; the repository-wide legacy marker
cannot authorize it. Hooks then bind receipts to that control root and active
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

No model-callable MCP tool can author review or QA evidence. Runtime and stream
integrity rules fail closed as specified by the two receipt ADRs above.

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

harness does not require users to choose between Goal and direct-task modes. A
Goal may remain small or expand child tasks dynamically; a focused plain request
may remain one direct task. Hooks provide compact context
and record subagent starts; MCP tools own explicit state transitions.
