# harness Architecture Specification

tags: [harness, spec, architecture]
status: draft
created: 2026-04-09
updated: 2026-08-13
task_ref: TASK__harness-architecture

---

## Overview

harness is a goal-oriented control plane for repo-mutating agent work. Users
state work as a native Goal. Harness hooks and MCP tools sync that Goal into a
harness Goal, then create child tasks only as needed. Small requests may stay as
one child task; broad requests may grow additional child tasks as bugs, pages,
domains, or follow-up gaps are discovered.

The design goal is simple runtime behavior:

- one public Goal owns the user request
- child tasks hold scoped implementation work
- task-local planning is written through `write_plan`
- verification is receipt-backed through hook-observed subagent starts
- durable knowledge lands in committed docs, skills, patterns, scripts, or tests

## Goal Flow

```text
native Goal -> harness Goal -> child task(s) -> verify -> close task -> next task or finish Goal
```

The Goal is not a skill. Hooks inject the synchronization instruction and MCP
tools maintain the only ordered Goal child list. Known future children may be recorded
before their task directories exist and retain list order. The agent chooses
whether the Goal needs another thin vertical child by inspecting the current
task result, discovered gaps, pending user feedback, and durable learnings.

Closing a task also marks the matching child entry closed in the active
Goal. A Goal can become `complete` only when it has at least one child and every
child's canonical task state is both closed and receipt-verified PASS. Starting
the same terminal Goal is an explicit resume operation: it preserves its task
queue and creation history while returning the Goal to `active` and clearing
the prior terminal timestamp.
Terminal Goals reject child mutation and repeat finish calls until that explicit
`goal_start`, so blocked or complete state cannot accumulate unfinished work.

## Task Loop

Every repo-mutating child task follows this loop:

```text
plan when needed -> minimum-sufficient develop -> independent review -> runtime QA -> verify -> close
```

No verification is skipped. If verification finds a gap, the task returns to
develop or creates a follow-up child task when the gap is separable.

For each Goal child, post-close continuation is ordered as:
`task_close -> self-improvement/learning promotion/hygiene scheduling ->
goal_next_task`. Memory and automatic learning are independent of orchestration;
runbooks, staged learnings, promotion, search, doc hygiene, and hygiene follow-up
remain active after every child.

Harness lifecycle operations do not inspect Git state. `task_start` creates the
task artifacts and active marker without capturing HEAD, dirty paths,
submodules, gitlinks, worktree bindings, or a source baseline. `task_context`,
`task_verify`, and `task_close` likewise do not run `git status`, `git diff`, or
source fingerprinting. This keeps nested repositories, ignored checkouts,
submodules, and linked worktrees outside the control plane.

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
| durable docs under `doc/<area>/<TYPE>__*.md` | normal committed doc edits or `plugin/scripts/req_scaffold.py` |

Manual evidence writers are intentionally absent. User corrections must be
promoted directly into `PLAN.md` or durable project documentation.

### Control-plane artifact integrity

Task selectors accepted by MCP tools resolve to one immediate
`doc/harness/tasks/TASK__<safe-id>` child. Bare IDs are normalized for
compatibility; canonical repository-relative and absolute task paths are
accepted. Traversal, control characters, selector mismatches, outside paths,
and symlink aliases are rejected before task artifacts are created or changed.
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
stops create no authority. The hook and Stop gate derive lifecycle state from
the current task's unified receipts and never maintain a background registry
or registry lock. Bash cannot directly invoke or import lifecycle
receipt-authoring entrypoints.

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

`task_context`, `task_verify`, and `task_close` read task artifacts and receipt
streams only. They do not discover repositories, enumerate changed paths,
validate Git metadata, or invalidate PASS because files changed. A developer
who edits after QA must decide whether to rerun review or QA. Harness deliberately
accepts that risk instead of imposing a repository-integrity monitor on local
development. Missing or malformed task artifacts and receipt streams still fail
closed. Public context and verification responses expose verdicts, required
lenses, `missing_for_close`, `next_action`, and one report path; they do not
embed raw receipts, completion summaries, transcript locations, or a duplicate
review report path.

`TASK.json.run_id` is the non-Git generation identity and a canonical UUIDv7
whose embedded millisecond timestamp supplies the run-start cutoff.
`task_start` creates it and every resume rotates it while clearing prior
receipts. The session marker carries only that run ID to lifecycle watchers;
start/completion receipts must match it. A replayed rollout event whose
timestamp predates the UUIDv7 cutoff is ignored. This prevents prior-run
evidence from satisfying a resumed task.

Codex SessionStart and spawn-selective PreToolUse establish the bounded root
registration used by the MCP-hosted watcher. Registration and lifecycle
authority follow the single-direct protocol ADR; late registration never
recovers already-completed work.

PreToolUse dispatch is selective: plan-first/artifact ownership runs only for
direct write tools (`Write|Edit|MultiEdit|apply_patch`), while the shell
mutation guard runs only for `Bash|shell`.
Read-only, browser, and unrelated tools do not launch either gate. Browser
delegation has no generic PreToolUse enforcement.

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
to standard plan-review-merge, and the full verification loop is enabled. The
failure-avoidance question is also omitted; setup owns and reapplies C-100 as
`말하지 않은 범위도 멋대로 수정하는 것`. Project purpose and
undetectable verification facts remain the only interview inputs. Setup also
adds a missing `@CONTRACTS.md` runtime import with an idempotent targeted edit
and enables Health scoring from every census-detected API/frontend test and
quality command without asking.

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

harness does not require users to choose between run/autopilot modes. A Goal may
remain small or expand child tasks dynamically. Hooks provide compact context
and record subagent starts; MCP tools own explicit state transitions.
