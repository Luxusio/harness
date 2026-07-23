# harness Architecture Specification

tags: [harness, spec, architecture]
status: draft
created: 2026-04-09
updated: 2026-07-23
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
HEAD ancestry must validate. Invalid baselines and failed Git snapshots block
the gate instead of degrading to an empty task scope. Dirt that existed at
task start remains excluded when the identical content is later committed.
For a newly scaffolded task in a Git repository, baseline capture is mandatory
and happens before `TASK_STATE.yaml` is created. An unborn repository or
transient capture failure blocks `task_start` with recovery guidance instead of
creating a task whose later committed payload could disappear. Identifiable
legacy tasks that already lack a baseline retain their prior compatibility.
New tasks also receive a protected `TASK_BASELINE.required` generation marker;
if their baseline is later deleted, review, install, and close fail closed
instead of reclassifying the task as legacy.

## Protected Artifacts

| Artifact | Writer |
|---|---|
| `PLAN.md`, `PLAN.meta.json`, optional `CHECKS.yaml`, optional `AUDIT_TRAIL.md` | MCP `write_plan` |
| `CHECKS.yaml` status transitions after plan | `plugin/scripts/update_checks.py` |
| `TASK_BASELINE.json`, `TASK_BASELINE.required` | task-start runtime |
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

`task_context`, `task_verify`, and `task_close` reuse one request-local snapshot
of source-derived Git paths and review fingerprints. The snapshot is isolated by
execution context, is discarded on success or exception, and never caches review
or QA receipts. Before `task_close` writes closed state, it clears that snapshot
and reruns the complete context, receipt, runtime-freshness, and CHECKS gates;
after those gates finish, it clears the cache again and requires the end-of-gate
changed-path fingerprint map and HEAD to match the initial values. It also
compares uncached raw review/QA receipt-stream fingerprints across the final
gate. Source, receipt, or HEAD changes observed between them therefore fail
closed and require a fresh verification. A failed or timed-out Git changed-path snapshot and an
unavailable initial or final HEAD are invalid evidence and block close rather
than being treated as an empty, stable repository state. Git roots confirmed
earlier in a request remain trusted across cache refreshes, so temporarily
removing `.git` or `HEAD` cannot downgrade a later command failure to synthetic
fixture compatibility. Git changed paths use
NUL-delimited output so control characters remain unambiguous; regular files
are opened without following symlinks, symlink targets are hashed directly,
and the pathname identity is rechecked after reading so rename replacement,
unreadable, or special path types invalidate the snapshot. Git path identity is
preserved end to end; separator normalization is applied only on Windows.
Every parent-index gitlink OID is fingerprinted, including uninitialized
submodules. Initialized submodules additionally include checkout HEAD and
worktree identity, so a staged gitlink update or clean checkout move is both
review-routed and freshness-gated. Gitlink worktrees and their path components
must be real directories rather than symlinks. A submodule `.git` control file
is read without following symlinks, must resolve inside the parent Git common
directory, and Git must report the validated worktree itself as its top level;
this worktree-binding query is intentionally uncached so an in-request gitdir
retarget is detected. Submodule HEAD is read with the validated gitdir and
worktree passed explicitly to Git, and the control-file binding is compared
before and after. These checks prevent traversal into external repositories,
including nested gitlinks.

Codex runtimes do not always forward collaboration tools to plugin
`PostToolUse`. SessionStart and each installed Codex root hook therefore validate
the current official `session_id` (with a matching environment fallback),
canonical repository, root rollout, and initial offset, then writes a versioned
registration under the current user's state directory. This restoration path is
strictly opt-in: without `doc/harness/manifest.yaml`, global hooks do not create
or restore Harness watcher state. Setup detection stops at the nearest Git root,
starting from the physical `realpath` of the hook cwd, so an independent nested
repository or symlinked external project cannot inherit an outer repository's
manifest. Registration retries
briefly on SessionStart when rollout creation races hook delivery. Later hooks
restore missing or invalid registration state without overwriting a valid
initial offset; late recovery begins at the current offset and covers only
future subagent starts. Root-owned workspace ancestors common in container
mounts are accepted for task binding only when group/other write bits are
absent, while symlink checks and current-user ownership of the task directory
remain enforced. An existing version-3
registration is validated from its
exact state and rollout paths before discovery, so ordinary hook events do not
recursively scan the session tree, acquire the registration lock, or rewrite
state. Discovery for a missing registration is deadline-aware and registration
locking is non-blocking. PreToolUse, UserPromptSubmit, and PostToolUse wrappers
enforce one total child-work deadline strictly below their configured outer
Codex hook timeout; individual subprocesses consume the remaining shared budget.
It does not fork. The existing Harness MCP server discovers these registrations
and hosts one passive daemon watcher thread per root tuple. MCP restart replays
from the immutable initial offset; receipt deduplication makes replay safe.
Because Codex MCP processes do not receive the root thread id as process state,
the watcher binds a root to a task only from that root rollout's successful
Harness `task_start` or `task_context` completion event, after canonical task
state validation and before the reviewed or QA child starts.

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
