---
title: prewrite_gate — protected artifacts, plan-first, scope lock
freshness: current
invalidated_by_paths:
  - plugin/scripts/prewrite_gate.py
  - plugin/scripts/hook_pre_tool_use.py
  - plugin/scripts/_lib.py
  - plugin/hooks/hooks.json
tier: 2
freshness_updated: 2026-10-19T06:06:14Z
---

# prewrite_gate

`plugin/scripts/prewrite_gate.py` gates direct writes. Claude runs it as the
PreToolUse hook for `Write|Edit|MultiEdit` (`plugin/hooks/hooks.json`). Codex
runs it as a child of `plugin/scripts/hook_pre_tool_use.py` for
`Write|Edit|MultiEdit|apply_patch`. Bash and shell writes are not gated.

A deny is a JSON object on stdout and the gate exits 0:

```json
{"hookSpecificOutput": {"hookEventName": "PreToolUse",
  "permissionDecision": "deny", "permissionDecisionReason": "..."}}
```

An allow prints nothing. The hook command ends with `|| true` (C-12); that
cannot mask a deny because the decision rides on stdout, not on the exit code.

## Payload

The gate reads the whole stdin payload and hands it to
`_lib.read_hook_input` with an exact `max_chars`
(`prewrite_gate._read_whole_hook_input`). The shared 64 KiB default of
`read_hook_input` stays in place for other hooks. With that default, a
Write/Edit/MultiEdit/apply_patch payload over 64 KiB was cut mid-JSON, parsed
to `{}`, and allowed, protected artifacts included.

The gate does not pass a fixed large cap instead: `sys.stdin.read(n)` on a pipe
can allocate `n` bytes for a read, so a very large `n` can raise `MemoryError`,
and `read_hook_input` would turn that into the same silent allow. A payload
too large to read or parse in memory is still allowed.

The payload bytes decode as UTF-8 with `surrogateescape` whatever the locale
(`_parse_hook_payload`), so an invalid byte cannot empty the payload. The Codex
wrapper decodes the tool name the same way before it dispatches to the gate,
and its fallback below passes the same bytes to the same parser.

Write targets come from `tool_input.file_path` (or `.path`). For `apply_patch`
the gate collects every `*** Add/Update/Delete File:` target, then every
`*** Move to:` target, without duplicates. Relative paths resolve against the
payload `cwd`. Paths are checked in that order and the first deny ends the
call.

## Rules (in order)

| # | Rule id | When | Result (`owner=`) |
|---|---------|------|-------------------|
| 0 | — | Empty payload, or not a JSON object | silent allow |
| 1 | (escape) | `HARNESS_SKIP_PREWRITE=1` | silent allow, logs `gate-bypass` |
| 2 | `C-05-protected-artifact` | Target is native Codex session metadata (`<codex home>/sessions/**/rollout-*.jsonl`) used for source authentication, including aliases and non-Harness cwd | deny (`native-session-runtime`) |
| 3 | `invalid-harness-workspace` | The payload `cwd`'s Harness root cannot be resolved (manifest or a path component is a symlink, not a regular file, unreadable) | deny (`harness:setup`) |
| 4 | — | No regular `doc/harness/manifest.yaml` at the root | silent allow |
| 5 | `symlink-outside-control` | The requested path is inside the root but its realpath is outside | deny (`developer`) |
| 6 | `C-05-protected-artifact` | Target outside the root that is a protected artifact of *its own* valid Harness root (see [Cross-checkout protection](#cross-checkout-protection)); any other out-of-root target is a silent allow | deny (same owner as rule 7) |
| 7 | `C-05-protected-artifact` | Target is a protected artifact (see [Protected artifacts](#protected-artifacts)) | deny (owning tool) |
| 8 | — | Any other path under `doc/harness/tasks/` | silent allow |
| 9 | — | Path under `EXEMPT_PREFIXES` | silent allow |
| 10 | `workflow-control-surface` | `WORKFLOW_CONTROL_SURFACE` file and the active task has no `MAINTENANCE` marker; with the marker it is an immediate allow | deny (`maintain-skill`) |
| 11 | — | Extension not in `SOURCE_EXTENSIONS` | silent allow |
| 12 | `no-active-task` | No active task, and the manifest sets top-level `strict_compliance_requires_delegation: true` or some task is open or invalid; otherwise silent allow | deny (`plan-skill`) |
| 13 | `invalid-active` | The active task path is missing, not a directory, or outside `doc/harness/tasks/` | deny (`plan-skill`) |
| 14 | `C-02-plan-first` | Active task has no PLAN.md, no `MAINTENANCE` marker, and is not `execution_mode: micro` | deny (`plan-skill`) |
| 15 | `C-REQ-observable-doc-required` | The path looks like observable UI/API/native/desktop behavior and the task links no `doc/<area>/REQ__*.md` | deny (`developer`) |
| 16 | `scope-lock-forbidden` | Path matches `forbidden_paths` in the active task's `PROGRESS.md` | deny (`developer`) |

A write that hits no deny rule is a silent allow (silence is the trust signal).

## Deny-reason structure

Every deny emits a structured tail followed by a human sentence and an escape hint:

```
[gate=prewrite rule=<id> path=<repo-relpath> owner=<role> docs=<pattern-doc>] <human text>
escape: HARNESS_SKIP_PREWRITE=1 <retry>
```

`emit_permission_decision` then appends `↳ next action`, `↳ owner`, and
`↳ docs` lines when the rule has them, and cuts the whole reason at 2000
characters. Agents can parse the tail to route actions without scanning prose.

## Protected artifacts

Historical RECEIPTS/REVIEWS retain denial labels solely to prevent direct
mutation of preserved artifacts. No active collector, detail writer or
recording-based close authority exists.

The `PROTECTED_ARTIFACTS` dict maps basename → owner-role. Owners are space-free
so the structured tail remains grep-stable; the deny sentence names the human
tool to route through (for example `wbs(action="plan")` for `PLAN.md`).

| Target | Match | `owner=` |
|--------|-------|----------|
| `TASK.json` | basename, anywhere in the root | `task-control-mcp` |
| `PLAN.md` | basename | `plan-skill` |
| `RECEIPTS.jsonl` | preserved historical output, basename | `historical-task-artifacts` |
| `REVIEWS.jsonl` | preserved historical output, only `doc/harness/tasks/TASK__*/REVIEWS.jsonl` | `historical-task-artifacts` |
| `doc/harness/goals/*.json` | retired direct children | `legacy-data-migration` |
| `doc/harness/runtime/batches/**` | pool metadata, immutable WBS owner and lock | `batch-pool-runtime` |
| `doc/harness/archive/goal-migration/**` | entire archive, originals and manifest | `legacy-data-migration` |
| `doc/harness/archive/task-control-migration/**` | entire migration authority tree | `legacy-data-migration` |
| `doc/harness/tasks/.active`, `doc/harness/tasks/.active_sessions/*` | path | `task-control-runtime` |
| native Codex session metadata | rule 2, source authentication only | `native-session-runtime` |

## Cross-checkout protection

A Claude `harness:batch` lead runs with its cwd in a linked worktree
(`<main>/.claude/worktrees/<name>`). The gate's control root is then the
worktree, so the main checkout and sibling worktrees lie outside it. Without
this rule, writes to those checkouts' protected artifacts were allowed. The same
applies to a main-checkout session writing into an out-of-tree worktree.

Before ordinary target rules, the gate walks the target's lexical parents and
validates a same-repository registered worktree using its canonical gitfile,
back-pointer and Harness manifest. For a validated target it uses that
checkout's root for C-05, plan-first, workflow-control, REQ and scope-lock
checks, including when Codex's native cwd remains main. This also protects
in-tree worktree task-local REVIEWS.jsonl. The original cwd root's symlink
escape denial takes precedence; lexical traversal retains the owning worktree
boundary when a symlink inside it points elsewhere.

For targets not routed to a validated same-repository worktree, the gate
handles a target outside the cwd's Harness root as follows:

1. It skips targets whose name cannot be a protected artifact: the basename is
   not a `PROTECTED_ARTIFACTS` key, the path is not goal JSON, and it is not
   `.active` or under `.active_sessions/`.
2. For the remaining targets it resolves the target's own root with
   `harness_root_resolution(dirname(target))`. That call does file checks only
   and runs no Git subprocess. It tries the realpath first, then the requested
   path.
3. If that root is valid (a regular manifest and no resolution error) and the
   target is one of its protected artifacts (`TASK.json`, `PLAN.md`,
   `RECEIPTS.jsonl`, task-local `REVIEWS.jsonl`, `doc/harness/goals/*.json`,
   `doc/harness/tasks/.active`, `doc/harness/tasks/.active_sessions/*`), the
   gate denies the write:
   - rule id: `C-05-protected-artifact`;
   - owner and sentence: the same as an in-root deny;
   - extra sentence: `The target belongs to another Harness checkout: <root>.`;
   - tail `path=`: relative to that other root.

Only C-05 applies in this foreign-checkout fallback. Ordinary source files
outside the selected root are allowed by that fallback. Validated registered
worktree targets instead receive the full rules above using their own task.

Behavior that does not change:
- A cwd outside any Harness root keeps its early return.
- A target outside every Harness root is still a silent allow.
- An exception in this branch is logged to the cwd's root through
  `_log_gate_error`, and the write is allowed (C-12). It never ends the loop
  over the remaining paths of the same tool call.

Known limits:
- A directory symlink inside a foreign root that points out of every Harness
  root is allowed.
- A foreign root with an invalid manifest is not treated as a valid root, so
  writes into it are allowed.
- When a checkout's own `.claude` (or `.claude/worktrees`) is a symlink that
  leaves the repository — a layout manifest v7's ignore migration accepts — a
  write addressed *through the main checkout's path* into a linked worktree
  under it (`<main>/.claude/worktrees/<name>/...`) resolves outside the main
  control root and is denied by `symlink-outside-control`, even for an
  ordinary source file, before the cross-checkout branch runs. A session
  working in such a worktree writes under its own root and is unaffected;
  otherwise use the worktree's real path. For `harness:batch`, preflight
  accepts external targets as `outside-repo`; an internal target is checked
  for ignore coverage at the path Git sees. This does not bypass the direct
  write gate above. See `doc/harness/REQ__parallel-tasks-via-worktree-leads.md`.

## Workflow-control-surface

Files that define harness runtime behaviour. Writes require a task with a
`MAINTENANCE` marker. The set currently includes `plugin/CLAUDE.md`,
`plugin/hooks/hooks.json`, `plugin/scripts/{prewrite_gate,_lib}.py`,
`plugin/mcp/harness_server.py`, `doc/harness/manifest.yaml`.

Touch `doc/harness/tasks/<task>/MAINTENANCE` to enable writes, and record the
reason in close-time Self-Healing Candidates. The prewrite gate itself does not
create this marker. With the marker the gate allows the write at once, so the
later plan-first, REQ and scope-lock rules do not apply to these files.

## Scope lock

If `PROGRESS.md` exists in the active task's directory, a source-extension write
that matches `forbidden_paths` is denied with rule `scope-lock-forbidden`.
`allowed_paths` and `test_paths` do not change the decision, and an unlisted
path is a silent allow. Details: [`scope-lock.md`](./scope-lock.md).

## Escape hatches

| Env var | Effect | Audit |
|---------|--------|-------|
| `HARNESS_SKIP_PREWRITE=1` | Skips every rule, C-05 included | a `gate-bypass` row in `doc/harness/learnings.jsonl` per bypassed call |
| `HARNESS_DISABLE_SCOPE_LOCK=1` | Skips only the scope lock | overwrites `<task>/audit/scope-lock-bypass.flag`; the next evaluation without the variable deletes it |

The gate reads both variables on every call and never clears them. Each one
applies to every write while set in the runtime's environment, meaning the
environment the runtime gives its hook processes. The Codex wrapper's
protected-artifact fallback honors `HARNESS_SKIP_PREWRITE` the same way.

Recurring `gate-bypass` rows against the same path are a signal to fix the
task's scope or run the work under a maintenance task instead.

## Timeouts and killed hooks

A hook killed before it prints emits no decision, and both runtimes then run
the tool.

| Runtime | Budget | When the budget runs out |
|---------|--------|--------------------------|
| Claude | `plugin/hooks/hooks.json` PreToolUse timeout: 10 s, the C-12 maximum | Claude Code kills the hook and runs the tool. **The write proceeds, protected artifacts included.** Nothing on the hook side can make a killed Claude hook deny. |
| Codex | Outer hook timeout 5 s (set by `install.py`; it is part of the Codex hook trust hash). `hook_pre_tool_use.py` gives the gate child `CHILD_TIMEOUT_SECONDS` = 3.0 s | When the child times out, exits nonzero with empty stdout, or cannot start, the wrapper calls `prewrite_gate.protected_artifact_decision` in-process. That runs only the C-05 rules (4, 6 and 7 above) and the rule-1 escape, and denies protected targets, also under a manifest that fails rule 2. Every other write is allowed (C-12). If the fallback raises, or Codex kills the wrapper at 5 s, the write proceeds. |

The 2.0 s between the Codex child budget and the outer timeout is reserved for
wrapper startup, child termination and fallback; it does not guarantee they
finish before the outer timeout. Child output that arrives in time is passed
through unchanged, even with a nonzero exit.

## Fail-safe behaviour

- Top-level `_lib` import failure → module-level `sys.exit(0)` (allow).
- Unexpected exception in `main()` → `log_gate_crash` appends a `gate-crash`
  row (`script`, `tool_name`, `payload_keys`, `error`) to `learnings.jsonl`
  and the gate exits 0 (allow).
- Cross-checkout resolution error → `gate-error` row, allow for that path.
- Scope-lock parse or enforcement error → `gate-parse-fail` row, allow.
- Empty or non-object payload → silent allow.

The gate never blocks through its exit code; the JSON decision is what gives
it teeth.

## Related

- REQ: `doc/harness/REQ__protected-artifact-denial-survives-payload-size-and-hook-timeout.md`
- Tier 2: [`scope-lock.md`](./scope-lock.md) — PROGRESS.md scope lock details
- Contract: C-02 (plan-first), C-05 (protected artifact), C-12 (hooks fail-safe)
