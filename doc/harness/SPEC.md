# harness Architecture Specification

tags: [harness, spec, architecture]
status: draft
created: 2026-04-09
updated: 2026-08-03
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

The task-start Git baseline owns the task's source scope through review, QA,
verified installation, and close. That scope is the union of current worktree
changes and paths committed between the baseline HEAD and current HEAD, so a
clean commit cannot erase the task's reviewed install payload.
Present baselines are bounded regular files read without following symlinks;
their version, repository binding, path/fingerprint map, commit identity, and
HEAD ancestry must validate. Required Git HEAD, commit, ancestry, and parent
gitlink failures block the gate. Registered local source roots are explicitly
trusted workspace configuration; Harness does not independently validate or
pin their linked-worktree metadata authority. Working-tree
dirty-path enumeration is intentionally weaker: each scan is bounded to three
seconds and a timeout or command failure records
`GIT_DIRTY_SNAPSHOT_SKIPPED`, contributes no inferred dirty paths for that
root, and lets the lifecycle continue. Dirt that existed at task start remains
excluded when captured, but a skipped root has no such guarantee.
For a newly scaffolded task in a Git repository, baseline capture is mandatory
and the written artifact must pass the same bounded reader contract before
`TASK_STATE.yaml` is created. A generated artifact that exceeds path or size
limits is removed and capture fails. An unborn repository or transient capture
failure likewise blocks `task_start` with recovery guidance instead of creating
a task whose later committed payload could disappear. Every Git-backed task
requires the baseline thereafter: missing baselines fail closed regardless of
how they were removed. Older baseline-less tasks must be restarted as a new
task, which is the explicit migration path and avoids ambiguous legacy inference.

`task_start` captures source HEADs once, derives the baseline identity from
that captured map, and computes compact context inside one
request-local Git snapshot. Git subprocesses share a 40-second cumulative
deadline, and repeated changed-path, committed-path, and gitlink queries reuse
the request snapshot. Mandatory Git evidence may consume at most 15 seconds per
enumeration command under that deadline; optional dirty enumeration uses the
smaller three-second bound. Timeout and nonzero-exit diagnostics name the
failed operation and repository. Filesystem hashing and artifact rendering
remain outside that subprocess budget. Other handlers retain their prior
two- or five-second mandatory-command limits and the same three-second optional
dirty bound.

The initialization commit point is a validated baseline, a matching
`TASK_STATE.yaml`, and the session active marker. Failures before that point
remain hard errors and do not claim that a task exists. If optional compact
context fails after that point, `task_start` returns
`start_status: ready_with_warnings`, a conservative dict-shaped context, and an
explicit instruction to call `task_context` rather than retrying
`task_start`. Resuming an existing task revalidates its baseline before writing
the active marker, returns `resumed: true` with `task_created: false`, and the
read-only start path never removes Git index locks.
Optional environment probing has one four-second subprocess budget, reads the
manifest once through a bounded no-follow regular-file check, atomically
replaces its own output leaf without following symlinks or opening special
files, and cannot turn a committed scaffold into a failed start.

## Protected Artifacts

| Artifact | Writer |
|---|---|
| `PLAN.md`, `PLAN.meta.json`, optional `CHECKS.yaml`, optional `AUDIT_TRAIL.md` | MCP `write_plan` |
| `CHECKS.yaml` status transitions after plan | `plugin/scripts/update_checks.py` |
| `TASK_BASELINE.json` | task-start runtime |
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

A source-changing task first requires the always-on read-only code reviewer and,
when path or diff-content signals identify a security-sensitive boundary, the
read-only security reviewer. Their hook-owned `REVIEW_RECEIPTS.jsonl`
completions must explicitly PASS the current HEAD and uncommitted worktree
fingerprint. Docs-only/non-code tasks receive an explicit routing exemption.

Only QA started after the latest review PASS is eligible for runtime PASS. Every
applicable QA lens then needs a hook-owned `SUBAGENT_RECEIPTS.jsonl` completion
with explicit `VERDICT: PASS`. A start receipt proves delegation only. FAIL,
BLOCKED_ENV, missing verdicts, missing lenses, unmatched lifecycle events, and
source edits after review or QA prevent close. Self-authored PASS notes,
summaries, or narrative evidence do not close the task. Commands and inline
checks may still help debugging, but close authority comes from `task_verify`
reading task state and the two lifecycle streams.

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
arbitrary prose never supply identity. The watcher captures source freshness at
start, requires every identity and final response to agree, and deduplicates
equivalent completion sources before recording PASS. An unsupported observed
adapter is reported explicitly rather than presented as another QA retry.

`write_plan` owns the canonical audit header but accepts both convenient caller
forms: audit data rows only, or a complete Markdown audit table with an optional
`# Audit Trail` heading, header row, and separator. It strips duplicate framing
and stores exactly one canonical header. Inputs that cannot be normalized are
rejected before any bundled artifact write, with an accepted example and a
specific correction instead of an undocumented row-only requirement.

`task_context`, `task_verify`, and `task_close` reuse one request-local snapshot
of source-derived Git paths and review fingerprints. The snapshot is isolated by
execution context, is discarded on success or exception, and never caches review
or QA receipts. `task_close` performs one best-effort source sync, evaluates the
context, runtime-freshness, and CHECKS gates once, then reads HEAD and the receipt
stream once for its close attestation. It does not rescan the worktree or rerun
the gates to detect an external mutation that races with that same close call.
Developers own that short concurrency window; a later lifecycle call observes
the resulting workspace state. Evidence that is missing, stale, or invalid when
the single close evaluation begins still blocks close. An unavailable HEAD or
receipt stream remains invalid evidence. A failed or timed-out working-tree
dirty scan is instead represented as empty optional evidence plus a
`GIT_DIRTY_SNAPSHOT_SKIPPED` warning. Consequently close can miss an
uncommitted change, scope drift, or stale review evidence in that root. Each
Git operation resolves the configured checkout as it exists at that time. If
its metadata changes, the developer owns that workspace transition and the
next Git command is the source of truth. Git changed paths use
NUL-delimited output so control characters remain unambiguous; regular files
are opened without following symlinks, symlink targets are hashed directly,
and the pathname identity is rechecked after reading so rename replacement,
unreadable, or special path types invalidate the snapshot. Git path identity is
preserved end to end; separator normalization is applied only on Windows.
Every parent-index gitlink OID is fingerprinted, including uninitialized
submodules. Initialized submodules additionally include checkout HEAD, so a
staged gitlink update or clean checkout move is review-routed and
freshness-gated when Git reports it. A configured `source_git_roots` path is
an explicit trust decision: Harness reads its regular `.git` control file,
passes the resolved Git directory and worktree to Git, and does not run
`--git-common-dir`, `--absolute-git-dir`, or `--show-toplevel` authority
preflights. It likewise does not compare worktree metadata inode identity or
reciprocal admin files before and after an operation. Harness never infers or
automatically registers a source root from a discovered repository or gitlink.

This availability-first model matches ordinary local Git use: checkout repair,
retargeting, and concurrent worktree administration are developer-owned. A
malformed or missing `.git` pointer still produces an actionable snapshot
failure, and required HEAD, commit, and ancestry operations still fail when Git
cannot read the selected checkout. A valid pointer that is intentionally
retargeted is accepted on the next operation. The same policy applies to
initialized direct and nested submodules discovered from the parent index:
their index OID and checkout HEAD remain evidence, but external Gitfile targets,
reciprocal admin metadata, and worktree inode identity are not independently
policed by Harness.

Codex runtimes do not always forward collaboration tools to plugin
`PostToolUse`. SessionStart and each installed Codex root hook therefore validate
the current official `session_id` (with a matching environment fallback),
canonical repository, root rollout, and initial offset, then writes a versioned
registration under the current user's state directory. This restoration path is
strictly opt-in. A normal repository requires `doc/harness/manifest.yaml` at
its Git root. A non-Git control workspace may declare exact, setup-validated
`source_git_roots`; a Git-backed control may declare only the exact direct
gitlinks described above. Hooks accept the control manifest only when the
physical hook cwd has an exact registered Git root. Missing, moved, symlinked,
duplicate, nested, or unregistered roots fail closed, so an independent nested
repository cannot inherit the outer workspace's task.
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
locking is non-blocking. PreToolUse, UserPromptSubmit, and PostToolUse wrappers
enforce one total child-work deadline strictly below their configured outer
Codex hook timeout; individual subprocesses consume the remaining shared budget.
Lifecycle root resolution is included in that hard budget. PostToolUse performs
no review/QA changed-path or fingerprint scan; the single lifecycle watcher
exclusively observes ordered runtime spawn/completion events and owns that
heavier receipt work outside the outer hook deadline.
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

`source_git_roots` has two control-root modes. For a non-Git control workspace,
the configured roots are exhaustive: each registered repository contributes
its HEAD and, when enumeration succeeds, workspace-prefixed dirty paths, while the non-Git parent
contributes only the bounded behavioral surface described below. For a
Git-backed control repository, configured roots are additive: the parent
remains the empty-prefix source binding, and every configured root adds a
service binding only after exact initialized-direct-gitlink authorization. The
baseline therefore retains the parent HEAD and direct gitlink OID as well as
each registered service's HEAD and any dirty paths returned within the optional
scan bound. The authorized parent edge is
treated as a leaf to avoid scanning the service twice, and touched paths are
routed to the longest matching registered prefix before the empty parent
prefix.

Receipt HEAD identity is a deterministic 40-hex composite over the sorted
root/HEAD tuples, and diff fingerprints resolve each touched path against its
bound root. A change in any bound repository, or in the parent's registered
gitlink OID, therefore invalidates an older review or QA PASS. The normalized
source-binding set in `TASK_BASELINE.json` is immutable for the life of the
task. Adding, removing, or changing a binding for an active task requires a new
Harness task ID; the runtime must not edit or reinterpret the existing
baseline. Setup finalization validates the configured roots and does not run
parent-level Git ignore checks when the control root itself is not a Git
repository.
Claude write, Bash, Stop, and subagent lifecycle hooks resolve a registered
child Git cwd back to this control root. Multi-Git baselines also fingerprint a
bounded parent behavioral surface (`AGENTS.md`, `CLAUDE.md`, contracts, and the
Harness manifest); normal synchronization discovers changes to those files,
including changes already present when `task_close` begins. Close does not
independently rescan that surface during its developer-owned same-call
concurrency window.
Runtime project-document edits reject symlink components and use a bounded,
atomic helper for routing and contract-import changes. Registered source-root
names are restricted to `[A-Za-z0-9._/-]+` before any control-root-relative
test command is generated, and task resume preserves configuration errors when
a previously registered child repository has moved or disappeared.

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

The watcher captures HEAD and the task diff fingerprint only while a correlated
child is still running, then accepts completion only when the root-delivered
message matches the child rollout's final answer and task-complete record.
Historical finals, unrecognized lenses, cross-repository lineage, partial
records, symlinks, unsafe registrations, and schema ambiguity cannot create
PASS. The unresolved repository `doc/harness/tasks` directory chain is also
owner/type checked with `lstat`; symlinked task roots cannot redirect receipts
across repositories. No model-callable MCP tool can author review or QA evidence. The
MCP-hosted watcher and classic PostToolUse path share the same protected receipt
owner and freshness gates. No recovery hook converts a child that completed
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
