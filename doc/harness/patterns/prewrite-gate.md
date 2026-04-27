---
title: prewrite_gate — plan-first + protected artifacts + scope lock
freshness: suspect
invalidated_by_paths:
  - plugin/scripts/prewrite_gate.py
  - plugin/scripts/_lib.py
  - plugin/hooks/hooks.json
tier: 2
freshness_updated: 2026-04-26T14:50:21Z
---

# prewrite_gate

PreToolUse hook on `Write` / `Edit` / `MultiEdit`. Blocks writes that would
violate harness invariants. The hook wrapper preserves C-12 fail-safe (`|| true`)
by signalling decisions via stdout JSON rather than exit code.

## Rules (in order)

| # | Rule id | When | Deny owner (`owner=`) |
|---|---------|------|-----------------------|
| 1 | (escape) | `HARNESS_SKIP_PREWRITE=1` | — (silent allow, logs `gate-bypass`) |
| 2 | `C-05-protected-artifact` | Write to any `PROTECTED_ARTIFACTS` basename *inside* a task dir | the owning skill / CLI |
| 3 | (allow) | Other writes *inside* any task dir | — |
| 4 | (allow) | Paths under `EXEMPT_PREFIXES` | — |
| 5 | `workflow-control-surface` | Write to any `WORKFLOW_CONTROL_SURFACE` entry without a MAINTENANCE marker on the active task | `maintain-skill` |
| 6 | `no-active-task` | Source write without `.active` pointer | `plan-skill` |
| 7 | `invalid-active` | `.active` unreadable / missing / out of tree | `plan-skill` |
| 8 | `C-02-plan-first` | Source write but active task lacks `PLAN.md` (and no MAINTENANCE marker) | `plan-skill` |
| 9 | `scope-lock-forbidden` | Path matches `forbidden_paths` in the active task's `PROGRESS.md` | `developer` |

A write that hits no block rule is a silent allow (silence is the trust signal).

## Deny-reason structure

Every deny emits a structured tail followed by a human sentence and an escape hint:

```
[gate=prewrite rule=<id> path=<repo-relpath> owner=<role> docs=<pattern-doc>] <human text>
escape: HARNESS_SKIP_PREWRITE=1 <retry>
```

Agents can parse the tail to route actions without scanning prose. Humans read
the sentence to understand what to do next.

## Protected artifacts

The `PROTECTED_ARTIFACTS` dict maps basename → owner-role. Owners are space-free
so the structured tail remains grep-stable; the deny sentence names the human
tool to route through (e.g. `update_checks.py` for `CHECKS.yaml`).

## Workflow-control-surface

Files that define harness runtime behaviour. Writes require a task with a
`MAINTENANCE` marker. The set currently includes `plugin/CLAUDE.md`,
`plugin/hooks/hooks.json`, `plugin/scripts/{prewrite_gate,mcp_bash_guard,stop_gate,_lib}.py`,
`plugin/mcp/harness_server.py`, `doc/harness/manifest.yaml`.

Touch `doc/harness/tasks/<task>/MAINTENANCE` (or route through the maintain skill)
to enable writes. The prewrite gate itself does not create this marker.

## Scope lock

If `PROGRESS.md` exists in the active task's directory, the gate enforces:

- `forbidden_paths` — deny with rule `scope-lock-forbidden`
- `allowed_paths` / `test_paths` — advisory; unlisted paths log a warning but do not block (gate philosophy: block only when explicit)

One-shot bypass: `HARNESS_DISABLE_SCOPE_LOCK=1` (audit flag is dropped in
`audit/scope-lock-bypass.flag` for post-hoc review).

## Escape hatches

| Env var | Effect | Audit |
|---------|--------|-------|
| `HARNESS_SKIP_PREWRITE=1` | one-shot silent allow | `gate-bypass` in `doc/harness/learnings.jsonl` |
| `HARNESS_DISABLE_SCOPE_LOCK=1` | skip scope lock for one write | `scope-lock-bypass.flag` in task `audit/` dir |

Activations are logged. If your `learnings.jsonl` shows recurring `gate-bypass`
entries against the same path, that is a signal to either move the path into
the task's `allowed_paths` or to run the work under a maintain task.

## Fail-safe behaviour

- Top-level `_lib` import failure → module-level `sys.exit(0)` (fail-open).
- Exception inside `main()` → `_log_gate_error` writes a `gate-error` entry to
  `learnings.jsonl` and the hook exits 0.
- Malformed / empty stdin → silent allow.

The hook wrapper is `python3 ... || true`, so a process-level crash is also
allowed through. The JSON decision mechanism is what gives the gate teeth —
exit-code blocking is not used.

## Related

- Tier 2: [`mcp-bash-guard.md`](./mcp-bash-guard.md) — same signalling contract on the Bash surface
- Tier 2: [`scope-lock.md`](./scope-lock.md) — PROGRESS.md scope lock details
- Contract: C-02 (plan-first), C-05 (protected artifact), C-12 (hooks fail-safe)
