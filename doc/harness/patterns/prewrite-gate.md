---
title: prewrite_gate — protected artifacts, plan-first, scope lock
freshness: current
invalidated_by_paths:
  - plugin/scripts/prewrite_gate.py
  - plugin/scripts/hook_pre_tool_use.py
  - plugin/scripts/_lib.py
  - plugin/hooks/hooks.json
tier: 2
freshness_updated: 2026-10-30T00:00:00Z
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

The source of truth is `_check_path` in `plugin/scripts/prewrite_gate.py`.
The gate uses `doc/harness/wbs/WBS__<id>` and `WBS.control.json`; only an
exact-session, current-open-run binding without a conflict fence grants write
focus. Legacy `.active` files and shared hints grant no authority.

| Order | Check | Result |
|-------|-------|--------|
| 1 | Empty/non-object payload, or `HARNESS_SKIP_PREWRITE=1` | allow; the bypass logs `gate-bypass` |
| 2 | Native Codex session metadata, including aliases and non-Harness cwd | C-05 deny (`native-session-runtime`) |
| 3 | Invalid Harness workspace; otherwise no enabled Harness root | invalid workspace denies; non-Harness allows |
| 4 | Registered same-repository worktree target | route to its own root and full gates; the cwd root's symlink escape deny takes precedence |
| 5 | Target resolves outside the selected root | deny a symlink escape; otherwise apply foreign-checkout C-05 protection, then return |
| 6 | Incomplete explicit WBS namespace migration | `wbs-migration-required` deny |
| 7 | Protected artifact, including the entire retired `doc/harness/tasks/` tree | C-05 deny (owning tool) |
| 8 | Other files inside the canonical WBS root, or `EXEMPT_PREFIXES` | allow |
| 9 | Workflow-control surface | require a bound WBS with `MAINTENANCE` and `require_source_assignment`; then return |
| 10 | Non-source extension | allow |
| 11 | No exact current WBS binding | `no-active-wbs` deny when strict compliance is enabled or an open/invalid WBS exists; otherwise allow |
| 12 | Invalid bound WBS path | `invalid-active` deny |
| 13 | Missing PLAN.md | C-02 deny unless the WBS has `MAINTENANCE` or `execution_mode: micro` |
| 14 | WBS integration custody | `require_integrated_for_wbs(..., measure=False)` failure denies (`wbs-work-pack`) |
| 15 | Observable UI/API/native/desktop behavior without a linked REQ | `C-REQ-observable-doc-required` deny |
| 16 | PROGRESS.md `forbidden_paths` match | `scope-lock-forbidden` deny |
| 17 | Current WBS execution assignment | `require_source_assignment` failure denies (`wbs-execution-assignment`) |

A write that hits no deny rule is a silent allow. These direct-write checks do
not replace independent review PASS before actual QA, current WBS checks at
`wbs(action="verify")`/`wbs(action="close")`, or verified installation.

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
| `WBS.control.json`, `WBS.json` | basename, anywhere in the root | `WBS-control-mcp` |
| `PLAN.md` | basename | `plan-skill` |
| `TASK.json` | retired control basename | `legacy-data-migration` |
| `RECEIPTS.jsonl` | preserved historical output, basename | `historical-wbs-artifacts` |
| `REVIEWS.jsonl` | only canonical `doc/harness/wbs/WBS__*/REVIEWS.jsonl` outside retired trees | `historical-wbs-artifacts` |
| `doc/harness/tasks/**`, retired batch `archive/batch/TASK__*` trees | entire retired trees | `legacy-data-migration` |
| `doc/harness/goals` and its direct `*.json` children | retired control | `legacy-data-migration` |
| `doc/harness/archive/{goal-migration,task-control-migration,goal-wbs-migration,wbs-control-migration,wbs-namespace-migration}/**` | entire migration authority trees | `legacy-data-migration` |
| `doc/harness/runtime/batches/**` | entire batch control tree | `batch-pool-runtime` |
| `doc/harness/wbs/.active`, `.active_sessions/*` | runtime markers, not ordinary authoring surfaces | `WBS-control-runtime` |
| `work/verification-targets/**` | immutable verification targets | `WBS-control-runtime` |
| native Codex session metadata | source authentication only | `native-session-runtime` |

Migration-owned trees route through explicit `wbs(action="migrate")`; current
review findings stay in actual independent agent responses. Preserved historical
receipt/review files have no collector or replacement writer.

## Cross-checkout protection

A registered same-repository worktree target receives full checks using its own
Harness root, exact session binding, plan, integration custody and execution
assignment. Registration validation includes the canonical gitfile, back-pointer
and Harness manifest. Lexical traversal preserves the owning checkout boundary.

For other out-of-root targets, `_foreign_protected_target` checks physical then
requested paths. Protected shapes resolve their own Harness root from an existing
ancestor using filesystem checks; a valid foreign root receives C-05 denial with
its owner and checkout path. Ordinary foreign source files receive no source
checks in this fallback. Resolution exceptions log `gate-error` and allow that
path; subsequent paths in the same tool call are still checked.

The cwd root's symlink escape denial takes precedence over worktree routing.
A target outside every Harness root, an invalid foreign manifest, or a foreign
symlink resolving outside every Harness root is not protected by this fallback.
A cwd outside Harness returns early, except for native session metadata.
When `.claude/worktrees` is a symlink leaving the cwd root, address the worktree
by its real path or work from its own root; the main checkout's symlink path can
trigger `symlink-outside-control` even for ordinary source. Separately,
`harness:batch` preflight accepts external targets as `outside-repo` and checks
ignore coverage at the path Git sees for internal targets, using
`batch_preflight.worktrees_ignore` and `setup_finalize._git_visible_path`.
That ignore preflight does not bypass the direct-write gate's symlink denial.
See `doc/harness/REQ__parallel-tasks-via-worktree-leads.md`.

## Workflow-control-surface

Files that define harness runtime behaviour. Writes require a bound WBS with a
`MAINTENANCE` marker. The set currently includes `plugin/CLAUDE.md`,
`plugin/hooks/hooks.json`, `plugin/scripts/{prewrite_gate,_lib}.py`,
`plugin/mcp/harness_server.py`, `doc/harness/manifest.yaml`.

Create `doc/harness/wbs/<wbs>/MAINTENANCE` to enable writes, and record the
reason in the WBS plan or final summary. The prewrite gate itself does not
create this marker. With the marker, the gate still checks current execution
assignment before returning; later plan-first, REQ and scope-lock rules do not apply to these files.

## Scope lock

If `PROGRESS.md` exists in the bound WBS directory, a source-extension write
that matches `forbidden_paths` is denied with rule `scope-lock-forbidden`.
`allowed_paths` and `test_paths` do not change the decision, and an unlisted
path is a silent allow. Details: [`scope-lock.md`](./scope-lock.md).

## Escape hatches

| Env var | Effect | Audit |
|---------|--------|-------|
| `HARNESS_SKIP_PREWRITE=1` | Skips every rule, C-05 included | a `gate-bypass` row in `doc/harness/learnings.jsonl` per bypassed call |
| `HARNESS_DISABLE_SCOPE_LOCK=1` | Skips only the scope lock | overwrites `<wbs>/audit/scope-lock-bypass.flag`; the next evaluation without the variable deletes it |

The gate reads both variables on every call and never clears them. Each one
applies to every write while set in the runtime's environment, meaning the
environment the runtime gives its hook processes. The Codex wrapper's
protected-artifact fallback honors `HARNESS_SKIP_PREWRITE` the same way.

Recurring `gate-bypass` rows against the same path are a signal to fix the
WBS scope or run the work under an authorized maintenance WBS instead.

## Timeouts and killed hooks

A hook killed before it prints emits no decision, and both runtimes then run
the tool.

| Runtime | Budget | When the budget runs out |
|---------|--------|--------------------------|
| Claude | `plugin/hooks/hooks.json` PreToolUse timeout: 10 s, the C-12 maximum | Claude Code kills the hook and runs the tool. **The write proceeds, protected artifacts included.** Nothing on the hook side can make a killed Claude hook deny. |
| Codex | Outer hook timeout 5 s (set by `install.py`; it is part of the Codex hook trust hash). `hook_pre_tool_use.py` gives the gate child `CHILD_TIMEOUT_SECONDS` = 3.0 s | When the child times out, exits nonzero with empty stdout, or cannot start, the wrapper calls `prewrite_gate.protected_artifact_decision` in-process. That runs only C-05 protection and the global bypass, and denies protected targets even under an invalid manifest. Every other write is allowed (C-12). If the fallback raises, or Codex kills the wrapper at 5 s, the write proceeds. |

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
