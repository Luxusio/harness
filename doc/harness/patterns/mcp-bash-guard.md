---
title: mcp_bash_guard — block Bash-layer mutations of gated paths
freshness: suspect
invalidated_by_paths:
  - plugin/scripts/mcp_bash_guard.py
  - plugin/scripts/prewrite_gate.py
  - plugin/scripts/_lib.py
  - plugin/hooks/hooks.json
tier: 2
freshness_updated: 2026-04-26T14:50:21Z
---

# mcp_bash_guard

PreToolUse hook on `Bash`. Closes the Bash-layer bypass where agents mutate
gated paths via shell commands rather than `Write` / `Edit` / `MultiEdit`.

Signalling contract matches `prewrite_gate` — stdout JSON decision, silent on
allow, fail-open on exception.

## Classification

Three gated categories (imported from `prewrite_gate`):

| Category | Rule id | Owner hint |
|----------|---------|-----------|
| protected-artifact | `protected-artifact` | MCP write tool for that artifact (e.g. `mcp__harness__write_handoff`) |
| workflow-control-surface | `workflow-control-surface` | `maintain-skill` |
| source | `source` | `developer` |

The gate classifies the *resolved target* of the mutation against these
categories. Paths outside all three are silent allow.

## Mutation verbs detected

The guard shlex-tokenises the command (respecting quotes + shell operators),
splits at `BOUNDARY_TOKENS` (`&&`, `||`, `|`, `;`, `&`, `\n`), then inspects
each command segment. Leading env assignments (`FOO=bar sed ...`) are skipped
before the command basename is examined (fixes a legacy bypass).

| Verb / pattern | Target extracted from |
|----------------|------------------------|
| `>`, `>>` (+ inline `N>`, `N>>`) | the token immediately following the redirect operator |
| `tee` / `tee -a` | every non-option argument |
| `sed -i` (and `sed -iBACKUP`) | last non-option argument |
| `perl -pi` (and `perl -pi.bak`) | last non-option argument |
| `cp`, `mv`, `install`, `touch`, `truncate` | last non-option argument |
| `python[3] -c "open('x','w')"` | first argument of `open()` |
| `python[3] -c "Path('x').write_text(...)"` | first argument of `Path()` |
| `python[3] -c "os.replace(src, 'x')"` | second argument of `os.replace()` |
| `python[3] -c "shutil.copy(src, 'x')"` | second argument of `shutil.copy(...)` |

`2>` stderr redirect is intentionally **not** blocked — logs are common.

## Known-safe verbs (no classification attempt)

`ls`, `cat`, `head`, `tail`, `grep` / `rg`, `find`, `wc`, `diff`, `git log`,
`git diff`, etc. The guard is silent on allow, so these produce no audit
noise.

## Known gaps (deferred, not in PR1)

The current guard does **not** descend into nested shells or substitutions:

- `bash -c "sed -i x file"` — the mutation is hidden inside the `-c` argument
  as a single shlex token; not recursed.
- `eval "sed -i x file"` — same mechanism; not evaluated.
- `$(sed -i x file)` command substitution and `` `...` `` backticks — not
  extracted.
- `python -c` with base64 / `exec(...)` obfuscation — regex patterns miss
  dynamically-constructed writes.
- Symlink target resolution — `os.path.realpath` is not applied before
  `_classify_gated_path`, so `ln -s plugin/CLAUDE.md /tmp/link && echo x > /tmp/link`
  bypasses classification.

Gaps are tracked in `doc/harness/tasks/TASK__gate-reliability-pr1/deferred-scope.md`
and will be revisited in later PRs. `HARNESS_SKIP_MCP_GUARD=1` is the current
manual override if you need to work around one.

## Performance

Hook timeout is 3 s. The guard short-circuits any command longer than 64 KiB
and precompiles its regex set at module load (one import per hook spawn).
Typical commands (≤ 8 KiB) complete well under 50 ms.

## Deny-reason structure

Identical schema to `prewrite_gate`:

```
[gate=mcp_bash_guard rule=<category> path=<repo-relpath> owner=<role> docs=<pattern-doc>] <human text>
escape: HARNESS_SKIP_MCP_GUARD=1 <retry>
```

## Escape hatches

| Env var | Effect | Audit |
|---------|--------|-------|
| `HARNESS_SKIP_MCP_GUARD=1` | one-shot silent allow | `gate-bypass` in `doc/harness/learnings.jsonl` |

Use when you know a bash mutation is legitimate — e.g. during a maintenance
rollout. Recurring activations against the same path signal that the path
should either move under the MAINTENANCE task or get a proper tool (plan skill
/ MCP write) rather than shell-level mutation.

## Fail-safe behaviour

- Top-level import failure → module `sys.exit(0)` (fail-open).
- Exception inside `main()` → `_log_gate_error` to `learnings.jsonl`; exit 0.
- Malformed / empty stdin → silent allow.
- Unclosed quotes → shlex ValueError → fall back to whitespace split; no crash.
- Non-`Bash` tool payload → silent allow.

The hook wrapper's `|| true` is a belt; the JSON-decision mechanism is the
suspenders. Both are kept in place.

## Related

- Tier 2: [`prewrite-gate.md`](./prewrite-gate.md) — same signalling contract on `Write` / `Edit` / `MultiEdit`
- Contract: C-05 (protected artifact), C-12 (hooks fail-safe)
