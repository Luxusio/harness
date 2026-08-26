---
title: mcp_bash_guard — block Bash-layer mutations of gated paths
freshness: current
invalidated_by_paths:
  - plugin/scripts/mcp_bash_guard.py
  - plugin/scripts/prewrite_gate.py
  - plugin/scripts/_lib.py
  - plugin/hooks/hooks.json
tier: 2
freshness_updated: 2026-08-26T00:00:00Z
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
| protected-artifact | `protected-artifact` | artifact owner, such as `write_plan`, task-control MCP, or runtime lifecycle hook |
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
| `cp`, `install`, `touch`, `truncate` | last non-option argument |
| `ln`, `link`, `cp -l` / `cp --link` | every source/destination operand (hard-link export protection) |
| `mv`, `rm`, `unlink`, `chmod`, `chown`, `chgrp` | every non-option operand |
| `python[3] -c "open('x','w')"` | first argument of `open()` |
| `python[3] -c "Path('x').write_text(...)"` | first argument of `Path()` |
| `python[3] -c "os.replace(src, 'x')"` | second argument of `os.replace()` |
| `python[3] -c "shutil.copy(src, 'x')"` | second argument of `shutil.copy(...)` |
| static `os.link` / `os.rename` / `os.replace` / `os.remove` / `os.unlink` and `Path` mutation calls | protected source and destination arguments |
| Direct lifecycle receipt entrypoint invocation/import | synthetic `RECEIPTS.jsonl` target |

`2>` stderr redirect **is** blocked when it targets a protected artifact:
`_INLINE_REDIRECT_RE` matches any file-descriptor number, not just `1`. This is
an over-block relative to the original "logs are common" intent, kept because a
stderr redirect onto a receipt file is not a logging pattern worth preserving.

`background_hook.py`, `subagent_lifecycle.py`, `codex_lifecycle_watcher.py`,
and direct imports of `record_subagent_receipt` are runtime-owned receipt
capabilities. A model-issued Bash command may not invoke them directly; only
the configured runtime lifecycle hooks/watchers may author those events.
Detection normalizes common `env`/`command`/`uv run` wrappers, and catches
Python `-m` and import forms whose module or symbol name appears *literally* in
the command text. Computed forms no longer resolve: the AST walker that
normalized `__import__`/`import_module`/`getattr` over concatenated names was
deleted with script inspection on 2026-08-26, so
`python3 -c "n=chr(115)+'ubagent_lifecycle'; __import__(n)"` now allows. That
allow is recorded in `doc/common/REQ__process__bash-guard-script-execution.md`.
Detection is
independent of the outer wrapper executable once a protected lifecycle target
is visible. A small explicit read-only allowlist preserves pytest, git
inspection, and non-mutating text inspection of those source files; other
commands that mention protected lifecycle modules fail closed. Concatenated or
qualified references (`'subagent_'+'lifecycle'`, `_lib.record_subagent_receipt`)
are caught by the alphanumeric-flattening text match, not by AST inspection.
The guard no longer reads or parses *script files*; inline `python -c` code is
still AST-parsed for filesystem writes (see the verb table above), and that
parse is what blocks a forged `VERDICT: PASS` one-liner.
**Script execution is not gated** (see
`doc/common/REQ__process__bash-guard-script-execution.md` for the settled
decision and evidence). The guard does not read, AST-scan, or deny a script it
is asked to run; what a program does once started is left to agent discipline.

An earlier design did inspect script files for receipt-writer imports. It was
removed on 2026-08-26 after a security review demonstrated four bypasses —
heredoc/herestring (`<<`, `<<<`, `<(…)` parsed as the script path),
`PYTHONPATH` + `sitecustomize.py` (runs before the script is opened), plant-and-run
outside `repo_root`, and a trailing `-m` short-circuiting inspection — each
proven by writing a forged PASS receipt. An inspection that ordinary commands
trip over but a determined caller walks through is false assurance, and it
trained agents to reach for `HARNESS_SKIP_MCP_GUARD`.

What remains on this surface is a cheap literal-text match: a command naming a
lifecycle entrypoint or receipt-writer symbol outright still denies. Obfuscated
forms are not caught and are not claimed to be.

Existing hard-link aliases of native Goal JSON are recognized by inode even
outside the repository. Goal readers independently require owner-controlled,
single-link, stable regular files, so an unrecognized alias cannot become
Goal authority.
Claude `projects/*/<session>/subagents/agent-*.jsonl` transcripts are likewise
classified as protected receipt provenance even though they live outside the
repository; redirection, mutator verbs, and recognized inline Python writes to
those leaves are denied.

## Known-safe verbs (no classification attempt)

`ls`, `cat`, `head`, `tail`, `grep` / `rg`, `find`, `wc`, `diff`, `git log`,
`git diff`, etc. The guard is silent on allow, so these produce no audit
noise.

## Read-only inspection is not mutation

The guard flattens the whole command to alphanumerics and raises
`protected_marker` when any name in `LIFECYCLE_RECEIPT_ENTRYPOINTS` or
`PROTECTED_MUTATION_SYMBOLS` appears anywhere in it. That heuristic cannot tell
a write from a mention, so `echo "=== write_active_marker ==="` or
`grep -n "RECEIPTS_NAME" plugin/scripts/_lib.py` used to be denied. Compound
commands failed the same way: shell keywords (`for`, `if`, `[`) do not resolve
through `shutil.which()`, so a segment holding a gated path was classified as
"unrecognized executable with gated path".

Two sets in `mcp_bash_guard.py` express the relief:

- `NON_MUTATING_COMMANDS` — command words that cannot write a file on their own
  (`echo`, `printf`, `stat`, `basename`, `cut`, `jq`, …).
- `SHELL_CONTROL_WORDS` — shell keywords and non-mutating builtins.

`_is_non_mutating_command()` combines them and is consulted from both
`_safe_lifecycle_source_inspection()` and `_safe_gated_path_inspection()`.

**Why this is safe, and the invariant to preserve:** these sets suppress only
the *name-mention* heuristics. Redirections are detected independently by
`_extract_redirect_targets()`, which walks the token stream for
`REDIRECT_TOKENS` and inline redirect forms regardless of the command word, so
`echo x > RECEIPTS.jsonl` is still denied — via its redirect target, not via
the command name. The relief is also per segment: a `for` wrapper does not
launder a redirect inside its body.

`GIT_NON_MUTATING_SUBCOMMANDS` covers the same idea for git. `add` and `commit`
move content into the index and object store and cannot change what a protected
artifact contains on disk, so they are admitted — without them harness lifecycle
files could not even be staged (`git add plugin/scripts/background_hook.py` hit
the name-mention heuristic and was denied). Subcommands that rewrite the working
tree stay blocked: `checkout`, `restore`, `rm`, `clean`, `mv`, `apply`, `stash`,
`reset`, `revert`, `merge`, `rebase`, `cherry-pick`, `pull`.

**Do not add** anything that can write: `tee`, `dd`, `cp`, `mv`, `install`,
`truncate`, `touch`, `ln`, `sed -i`, `perl -pi`, `awk` (`print > "file"`), or
`env` (runs an arbitrary command). `sort` and `diff` are admitted only when no
`-o` / `--output` option is present.

This matters more than ordinary ergonomics: receipt entries carry no signature
or HMAC. But this guard is **not** the only control, and must not be described
as one — integrity rests on hook ownership of `RECEIPTS.jsonl` and on
`task_verify` ordering. The guard raises the cost of an accidental or careless
append; a determined caller has documented routes past it (see Known gaps).
Both directions are pinned by
`tests/test_mcp_bash_guard_readonly_inspection.py`; keep the negative cases
passing before touching either set.

## Known gaps

The current guard descends through direct `bash -c` / `sh -c` command strings.
For Python `-c`, command substitution and backticks fail closed: the resolved
code cannot be inspected statically, and it is the inline AST parse that catches
a one-line receipt write. That deny lived inside the script-inspection function
until 2026-08-26 and was dropped with it by accident; it was restored separately
because it belongs to the inline `-c` control, which was deliberately kept.
Other dynamic constructs remain:

- command substitution or backticks around non-Python mutators — not extracted.
- **variable indirection defeats redirect detection entirely.**
  `F=<protected path>; echo x >> $F` allows, and the write succeeds. Redirect
  targets are extracted from *unexpanded* tokens, before the shell-value
  expansion loop runs, so an expansion never reaches them. The `${F}` brace form
  and `xargs -I{} sh -c '... >> {}'` behave the same. This is pre-existing, not
  a consequence of the 2026-08-26 change, and it is the clearest illustration of
  why this gate is a guardrail rather than a control.
- `python -c` with base64 / `exec(...)` obfuscation — regex patterns miss
  dynamically-constructed writes.
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
