# harness Architecture Specification

tags: [harness, spec, architecture]
status: draft
created: 2026-04-09
updated: 2026-08-11
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
tools maintain the harness Goal/task queue. The agent chooses whether the Goal
needs another child task by inspecting the current task result, discovered gaps,
and pending user feedback.

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

Harness lifecycle operations do not inspect Git state. `task_start` creates the
task artifacts and active marker without capturing HEAD, dirty paths,
submodules, gitlinks, worktree bindings, or a source baseline. `task_context`,
`task_verify`, and `task_close` likewise do not run `git status`, `git diff`, or
source fingerprinting. This keeps nested repositories, ignored checkouts,
submodules, and linked worktrees outside the control plane.

The plan declares the intended source scope and applicable review/QA lenses in
`PLAN.meta.json`. These declarations route work and verification; Harness does
not prove that every edited file belongs to that scope. Post-review or post-QA
edits, concurrent mutations, and scope drift are developer-owned risks.
Explicit setup, installer, release, or diagnostic commands may still inspect
Git or hash a concrete payload when that operation intrinsically requires it.
Those opt-in checks are not task lifecycle prerequisites.

The initialization commit point is a valid `TASK_STATE.yaml` plus the session
active marker. Optional environment probing remains bounded, performs no Git
probe, and cannot turn a
committed scaffold into a failed start.

## Protected Artifacts

| Artifact | Writer |
|---|---|
| `PLAN.md`, `PLAN.meta.json`, optional `CHECKS.yaml`, optional `AUDIT_TRAIL.md` | MCP `write_plan` |
| `CHECKS.yaml` status transitions after plan | `plugin/scripts/update_checks.py` |
| `SUBAGENT_RECEIPTS.jsonl` | Codex/Claude subagent lifecycle hooks, including the root-hook-registered, MCP-hosted Codex watcher |
| `REVIEW_RECEIPTS.jsonl` | Codex/Claude reviewer lifecycle hooks, including the root-hook-registered, MCP-hosted Codex watcher |
| `CONVERSATION.md` | Codex/Claude UserPromptSubmit/Subagent hooks |
| durable docs under `doc/<area>/<TYPE>__*.md` | normal committed doc edits or `plugin/scripts/req_scaffold.py` |

Manual evidence writers are intentionally absent. The MCP server does not expose
manual evidence writers, critic writers, handoff writers, or REQ writer tools.
`CONVERSATION.md` is readable task history; machine enforcement only reads
explicit `<!-- item: ... -->` markers.

### Control-plane artifact integrity

Task selectors accepted by MCP tools resolve to one immediate
`doc/harness/tasks/TASK__<safe-id>` child. Bare IDs are normalized for
compatibility; canonical repository-relative and absolute task paths are
accepted. Traversal, control characters, selector mismatches, outside paths,
and symlink aliases are rejected before task artifacts are created or changed.
Goal IDs use the same safe-name boundary, and Goal child entries persist the
canonical repository-relative task path.

`write_plan` validates the complete supplied bundle before its first write, so
an invalid optional `CHECKS.yaml` or `AUDIT_TRAIL.md` cannot leave a new PLAN or
meta file behind. `task_blocked` requires an existing `TASK_STATE.yaml` before
writing `BLOCKED.md`. Missing `CHECKS.yaml` remains compatible with task packs
that predate the ledger; a present empty, malformed, duplicate-ID, or
invalid-status ledger is invalid and cannot be reconciled or closed. AC field
updates preserve the ledger's existing item indentation and use unambiguous
regular-expression group replacement.

Per-session active markers are authoritative only while their canonical task
state is live. Marker leaves must be regular files and are read without
following symlinks; a session marker's embedded session and task identities
must match its filename and resolved task. A malformed or mismatched marker,
or one pointing at a closed/blocked task, falls back to the legacy `.active`
marker. The legacy marker remains
conservative for pre-state task packs so Stop and prewrite gates do not silently
disengage; active-task iteration filters terminal per-session records as well.

The confinement boundary validates lexical and physical paths and avoids
following pre-existing control-plane leaf symlinks. Harness runs with the
calling user's privileges and does not provide a privilege boundary against a
hostile process running concurrently as that same user; such a process can
already write every target Harness can write. A descriptor-relative filesystem
transaction layer is intentionally out of scope unless Harness later gains a
privilege transition or a distinct lower-trust concurrent repository writer.

## Verification

Verification has two responsibilities:

- operation check: the changed system works as implemented
- intent adequacy check: the result satisfies the user's request and durable
  requirements

A source-changing task requires the read-only reviewers declared by its plan.
The plan normally includes the code reviewer and adds security review when the
planned work crosses a sensitive boundary. Their hook-owned
`REVIEW_RECEIPTS.jsonl` completions must explicitly PASS for the current task.

Only QA started after the latest required review PASS is eligible for runtime
PASS. Every QA lens declared in `PLAN.meta.json` then needs a hook-owned
`SUBAGENT_RECEIPTS.jsonl` completion with explicit `VERDICT: PASS`. A start
receipt proves delegation only. FAIL, BLOCKED_ENV, missing verdicts, missing
lenses, unmatched lifecycle events, and incorrect review-before-QA ordering
prevent close. Receipts bind task, agent, lens, lifecycle identity, and event
ordering; they do not bind HEAD, a diff, touched paths, or a source fingerprint.
Self-authored PASS notes, summaries, or narrative evidence do not close the
task. Commands and inline checks may still help debugging, but close authority
comes from `task_verify` reading task state and the lifecycle streams.

Verification delegation is a workflow optimization, not a generic pre-tool
policy. Browser and heavy full-suite work should use an applicable `qa-*` lens
when isolation materially reduces context or process load. Inline browser use
remains valid when it is the lighter available path; its potentially large DOM,
screenshot, or evaluation payload is an accepted caller-owned context cost and
does not relax the receipt-backed close requirements above.

Codex collaboration APIs are capability-versioned rather than fixed to one
tool set. A structurally identified `wait_agent` result such as
`status[agent_id].completed` is a complete lifecycle signal; `list_agents` is
only a fallback when it exists and the wait response omitted identities or
final responses. When Codex does not forward collaboration calls to
PostToolUse, the registered root-rollout watcher also recognizes an
`exec`-wrapped or direct `multi_agent_v1__spawn_agent`, the returned `agent_id`,
matching child metadata and final/task-complete pair, and correlated completion
from `wait_agent.status[agent_id].completed`, `<subagent_notification>`, or
`close_agent.previous_status.completed`. A schema without `task_name` must
provide exactly one strict `task_name: <name>` first prompt line; nickname and
arbitrary prose never supply identity. The watcher requires every task, agent,
lens, and final response identity to agree, and deduplicates
equivalent completion sources before recording PASS. An unsupported observed
adapter is reported explicitly rather than presented as another QA retry.

`write_plan` owns the canonical audit header but accepts both convenient caller
forms: audit data rows only, or a complete Markdown audit table with an optional
`# Audit Trail` heading, header row, and separator. It strips duplicate framing
and stores exactly one canonical header. Inputs that cannot be normalized are
rejected before any bundled artifact write, with an accepted example and a
specific correction instead of an undocumented row-only requirement.

`task_context`, `task_verify`, and `task_close` read task artifacts and receipt
streams only. They do not discover repositories, enumerate changed paths,
validate Git metadata, or invalidate PASS because files changed. A developer
who edits after QA must decide whether to rerun review or QA. Harness deliberately
accepts that risk instead of imposing a repository-integrity monitor on local
development. Missing or malformed task artifacts and receipt streams still fail
closed.

`TASK_RUN.json` is a small non-Git generation marker. `task_start` creates it,
terminal resume rotates it, the session marker carries it to lifecycle watchers,
and start/completion receipts must match the current generation. A replayed
rollout event whose timestamp predates the generation start is ignored. This
prevents a prior-run agent from satisfying a reopened task without restoring any
source snapshot or change detector.

Codex runtimes do not always forward collaboration tools to plugin
`PostToolUse`. SessionStart and each installed Codex root hook therefore validate
the current official `session_id` (with a matching environment fallback),
canonical repository, root rollout, and initial offset, then writes a versioned
registration under the current user's state directory. This restoration path is
strictly opt-in and binds to an ancestor containing
`doc/harness/manifest.yaml`; Git repository boundaries are not consulted.
Registration stores the control root separately from the session cwd and
validates the rollout against the latter. Registration retries
briefly on SessionStart when rollout creation races hook delivery. Later hooks
restore missing or invalid registration state without overwriting a valid
initial offset; late recovery begins at the current offset and covers only
future subagent starts. Root-owned workspace ancestors common in container
mounts are accepted for task binding only when group/other write bits are
absent, while symlink checks and current-user ownership of the task directory
remain enforced. An existing version-4
registration is validated from its
exact state and rollout paths before discovery, so ordinary hook events do not
recursively scan the session tree, acquire the registration lock, or rewrite
state. Discovery for a missing registration is deadline-aware and registration
locking is non-blocking. UserPromptSubmit and PostToolUse wrappers enforce one
total child-work deadline below their configured outer Codex hook timeout.
PreToolUse selects at most one gate child and gives it a fixed bounded timeout.
PreToolUse dispatch is selective: plan-first/artifact ownership runs only for
direct write tools (`Write|Edit|MultiEdit|apply_patch`), while the shell
mutation guard runs only for `Bash|shell`.
Read-only, browser, and unrelated tools do not launch either gate. Browser
delegation has no generic PreToolUse enforcement.
Lifecycle root resolution is included in that hard budget. The single lifecycle
watcher observes ordered runtime spawn/completion events; it does not run Git or
fingerprint source.
The lifecycle watcher itself does not fork. The existing Harness MCP server discovers these registrations
and hosts one passive daemon watcher thread per root tuple. MCP restart replays
from the immutable initial offset; receipt deduplication makes replay safe.
Root-delivered child messages become completion candidates only when their
runtime envelope is `Message Type: FINAL_ANSWER`; intermediate progress
`MESSAGE` deliveries are ignored so they cannot poison the later attested
completion.
Because Codex MCP processes do not receive the root thread id as process state,
the watcher binds a root to a task only from that root rollout's successful
Harness `task_start` or `task_context` completion event, after canonical task
state validation and before the reviewed or QA child starts.
Session marker selection uses the hook payload, explicit Harness/Codex session
ids, and then `CODEX_THREAD_ID`; the thread fallback is valid even when
`HARNESS_RUNTIME` is unset.

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

Each rollout is opened with no-follow semantics and validated by descriptor
identity, owner, link count, non-group/world-writable mode, size, and an owner-controlled non-writable session
directory chain. Path replacement or inode changes fail closed. A
per-registration interprocess lease ensures that concurrent MCP servers cannot
tail and append evidence for the same root tuple at the same time.

The watcher accepts completion only when the root-delivered message matches the
correlated child rollout's final answer and task-complete record.
Historical finals, unrecognized lenses, cross-repository lineage, partial
records, symlinks, unsafe registrations, and schema ambiguity cannot create
PASS. The unresolved repository `doc/harness/tasks` directory chain is also
owner/type checked with `lstat`; symlinked task roots cannot redirect receipts
across repositories. No model-callable MCP tool can author review or QA evidence. The
MCP-hosted watcher and classic PostToolUse path share the same protected receipt
owner and ordering gates. No recovery hook converts a child that completed
before start capture into evidence.

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
