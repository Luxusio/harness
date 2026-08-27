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

The guard splits the command into lines, shlex-tokenises each (respecting quotes
+ shell operators), splits those at `BOUNDARY_TOKENS` (`&&`, `||`, `|`, `;`,
`&`), then inspects each command segment. The line split is separate and
necessary: `shlex(whitespace_split=True)` consumes newlines as whitespace and
never emits them, so a `"\n"` boundary token could never match and a multi-line
command collapsed into a single segment — dispatch is on the first command word,
so `echo start` on line 1 laundered any mutator on line 2. Leading env assignments (`FOO=bar sed ...`) are skipped
before the command basename is examined (fixes a legacy bypass).

| Verb / pattern | Target extracted from |
|----------------|------------------------|
| any token that is entirely redirect punctuation — optional fd digits, optional `&`, one or two `>`, optional `\|`/`&` | the token immediately following the operator. Matched by shape, never by an enumerated list |
| `tee` / `tee -a` | every non-option argument |
| `sed -i` (and `sed -iBACKUP`) | last non-option argument |
| `perl -pi` (and `perl -pi.bak`) | last non-option argument |
| `cp`, `mv`, `install`, `rsync`, `touch`, `truncate` | last non-option argument, or `-t <dir>` / `--target-directory=<dir>` when present |
| `cp`/`mv`/`install`/`rsync` into a **directory** | the reconstructed `<dest>/<name>` for each source, since that path is never a token. `dir/.` and `dir/*` sources are enumerated rather than basenamed |
| glob token (`*`, `?`, `[`) anywhere a path is classified | every existing path the pattern expands to, capped at `_GLOB_EXPANSION_CAP`, plus the literal token |
| `python -c` calling `os.system` / `os.popen` / `subprocess.*` | every string constant reachable from that call, re-tokenised |
| `ln`, `link`, `cp -l` / `cp --link` | every source/destination operand (hard-link export protection) |
| `mv`, `rm`, `unlink`, `chmod`, `chown`, `chgrp` | every non-option operand |
| `python[3] -c "open('x','w')"` | first argument of `open()` |
| `python[3] -c "Path('x').write_text(...)"` | first argument of `Path()` |
| `python[3] -c "os.replace(src, 'x')"` | second argument of `os.replace()` |
| `python[3] -c "shutil.copy(src, 'x')"` | second argument of `shutil.copy(...)` |
| static `os.link` / `os.rename` / `os.replace` / `os.remove` / `os.unlink` and `Path` mutation calls | protected source and destination arguments |
| Direct lifecycle receipt entrypoint invocation/import | synthetic `RECEIPTS.jsonl` target |

`2>` stderr redirect **is** blocked when it targets a protected artifact. The
deny comes from the shape rule in `_extract_redirect_targets` — `2` and `>`
tokenize separately under `punctuation_chars=True`, so the operator is matched
and the following token is classified. (`_INLINE_REDIRECT_RE` also tolerates a
leading fd number, but it is only reachable through the unclosed-quote
`command.split()` fallback.) This is an over-block relative to the original
"logs are common" intent, kept because a stderr redirect onto a receipt file is
not a logging pattern worth preserving.

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
still AST-parsed for filesystem writes and for shell-outs (see the verb table
above). That parse catches the direct one-liner forms — `open(…,'a').write(…)`,
`Path(…).write_text`, `shutil.copy`, and `os.system`/`subprocess` carrying a
path — but it is pattern-based and does not make a forged `VERDICT: PASS`
impossible; `python -c "$VAR"`, base64/`exec`, and computed names all pass. It
raises the cost of the obvious spellings, nothing stronger.
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
`_extract_redirect_targets()`, which walks the token stream for tokens matching
the redirect *shape* (`_PURE_REDIRECT_OP_RE`) and inline redirect forms
regardless of the command word, so
`echo x > RECEIPTS.jsonl` is still denied — via its redirect target, not via
the command name. The relief is also per segment: a `for` wrapper does not
launder a redirect inside its body.

Until 2026-08-26 that invariant silently did not hold. `punctuation_chars=True`
emits a whole redirect operator as one token, and `_INLINE_REDIRECT_RE` then
captured the operator's *own trailing punctuation* as the path — `>|` matched
with `group(2) == "|"`, a non-path — so the real target, the next token, was
never inspected and `echo x >| RECEIPTS.jsonl` truncated a protected artifact
through the gate.

The first fix enumerated the four spellings then known and was wrong for the
same reason: `>>|` leaked identically. Detection is now shape-based
(`_PURE_REDIRECT_OP_RE`): a token that is *entirely* redirect punctuation
carries no path, so its target is the following token. No spelling needs to be
enumerated, and adding a command word to the relief sets does not require
re-auditing an operator list.

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
For Python `-c`, command substitution and backticks fail closed in both the
quoted and unquoted forms: the resolved code cannot be inspected statically, and
it is the inline AST parse that catches a one-line receipt write. That deny
lived inside the script-inspection function until 2026-08-26 and was dropped
with it by accident; it was restored separately because it belongs to the inline
`-c` control, which was deliberately kept.

The unquoted form needs its own check. `python3 -c $(cat f.py)` tokenizes to
`[…, '-c', '$', '(', 'cat', 'f.py', ')']`, so the operand is a bare `$`;
inspecting only the operand text misses it, and the resulting empty string then
parses cleanly. The pre-2026-08-26 guard had the same hole. Note the deny also
fires on a literal backtick inside otherwise legitimate inline code — an
accepted over-block, since shell quoting is not recoverable after tokenization.

Other dynamic constructs remain:

- command substitution or backticks around non-Python mutators — not extracted.
- **hard-link aliases of task-directory artifacts are not caught.** Only native
  Goal JSON is inode-checked (`_is_goal_control_inode_alias`); `RECEIPTS.jsonl`,
  `PLAN.md` and `TASK.json` are matched by path and basename, so writing through
  a pre-existing hard link to one of them allows. Creating the alias in-band
  still denies, so this needs an alias planted by some other route.
- **a path the command never names is not classified.** `find … -exec truncate
  -s0 {} \;`, `find … -delete`, `tar -C <dir> -xf`, and `unzip -d <dir>` all
  reach a protected artifact without it appearing as a token. A bare filename
  after `cd` is the same shape under an unrecognized executable (`ed`, `ex`,
  `vim -es`, `sponge`, `patch`): `_embedded_path_candidates` requires a `/`, so
  it yields no candidate — note `tee`, `cp` and `echo >` still deny on the
  identical bare name, because those go through basename classification.
- `python -c "$VAR"` — variable indirection defeats the inline `-c` AST parse
  the same way it defeats redirect detection. `read -r CODE < forge.py;
  python3 -c "$CODE"` allows, while `python3 -c "$(< forge.py)"` denies.
- `python -m <module> <protected path>` is not inspected. The python branch
  returns after the inline `-c` check, so a stdlib module used as a file-writing
  utility (`python3 -m json.tool in.json <protected path>`) overwrites the
  target. This sits on the seam between "script execution is not gated" (which
  covers `-m`) and the mutation-verb class the gate does enforce. Left open
  deliberately: gating non-option operands of `-m` would re-deny read-only tools
  such as `python3 -m mypy plugin/scripts/mcp_bash_guard.py`, which is the false
  deny this whole change removed.
- **variable indirection defeats redirect detection entirely.**
  `F=<protected path>; echo x >> $F` allows, and the write succeeds. Redirect
  targets are extracted from *unexpanded* tokens, before the shell-value
  expansion loop runs, so an expansion never reaches them. The `${F}` brace form
  and `xargs -I{} sh -c '... >> {}'` behave the same. This is pre-existing, not
  a consequence of the 2026-08-26 change, and it is the clearest illustration of
  why this gate is a guardrail rather than a control.
- `python -c` with base64 / `exec(...)` obfuscation — regex patterns miss
  dynamically-constructed writes.
This list is the tracking surface — the former pointer to
`doc/harness/tasks/TASK__gate-reliability-pr1/deferred-scope.md` was dangling
(that task directory does not exist, and task directories are gitignored, so
they cannot hold durable knowledge). The expected-behavior matrix in
`doc/common/REQ__process__bash-guard-script-execution.md` is the normative
statement. `HARNESS_SKIP_MCP_GUARD=1` is the manual override.

The gaps above are the ones where the artifact path never becomes a classifiable
token. That is not the only way a route slips through — several rounds of review
found paths that *were* in the token stream and still went unclassified (a glob
basename, a reconstructed directory destination, a `-t` option value, a string
constant inside `os.system`). Each of those was a fixable oversight and was
fixed; do not read the list above as a complete theory of the gate's limits.

What is durable: this surface is a guardrail, not a control. Receipt integrity
rests on hook ownership of `RECEIPTS.jsonl` and on `task_verify` ordering. If a
claim here and the code disagree, the code is right and this file is a defect.

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
