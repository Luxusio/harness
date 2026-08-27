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

The guard splits the command at newlines **outside quotes**, shlex-tokenises
each line (respecting quotes + shell operators), splits those at
`BOUNDARY_TOKENS` (`&&`, `||`, `|`, `;`, `&`, `(`, `)`, `|&`, `;;`, `;&`, `;;&`), then inspects each command
segment. The line split is separate and necessary:
`shlex(whitespace_split=True)` consumes newlines as whitespace and never emits
them, so a `"\n"` boundary token could never match and a multi-line command
collapsed into a single segment — dispatch is on the first command word, so
`echo start` on line 1 laundered any mutator on line 2. The quote-awareness is
equally necessary in the other direction: splitting raw text made the body of a
`git commit -m "…"` message into command segments, so a message naming a
lifecycle symbol was denied as a receipt mutation. Leading env assignments (`FOO=bar sed ...`) are skipped
before the command basename is examined (fixes a legacy bypass).

| Verb / pattern | Target extracted from |
|----------------|------------------------|
| any token that is entirely redirect punctuation — optional fd digits, optional `&`, one or two `>`, optional `\|`/`&` | the token immediately following the operator. Matched by shape, never by an enumerated list |
| `sort` / `diff` with `-o` / `--output[=]` | the option's value — these are readers until given an output file |
| `sed -i`/`--in-place`, `perl -i` with `-p`/`-n`, `touch`, `truncate` | **every** non-option operand: they rewrite all of them, so one extra filename must not walk the real target |
| `tee` / `tee -a` | every non-option argument |
| any verb, when the segment carries a redirect | redirect operators and their operands are removed before the verb's own operands are read, so a trailing `2>/dev/null` cannot become "the last operand" |
| `cp`, `mv`, `install`, `rsync` | last non-option argument, or `-t <dir>` / `--target-directory=<dir>` when present |
| `cp`/`mv`/`install`/`rsync` into a **directory** | the reconstructed `<dest>/<name>` for each source, since that path is never a token. `dir/.` and `dir/*` sources are enumerated rather than basenamed |
| glob token (`*`, `?`, `[`) anywhere a path is classified | every existing path the pattern expands to, plus the literal token. `_GLOB_EXPANSION_CAP` bounds the walk itself (`islice` over `iglob`). A match identical to the pattern — a file literally named `x*y` — is dropped, since re-classifying it is what made expansion non-terminating |
| `ln`, `link`, `cp -l` / `cp --link` | every source/destination operand (hard-link export protection) |
| `mv`, `rm`, `unlink`, `chmod`, `chown`, `chgrp` | every non-option operand |

`2>` stderr redirect **is** blocked when it targets a protected artifact. The
deny comes from the shape rule in `_extract_redirect_targets` — `2` and `>`
tokenize separately under `punctuation_chars=True`, so the operator is matched
and the following token is classified. (`_INLINE_REDIRECT_RE` also tolerates a
leading fd number, but it is only reachable through the unclosed-quote
`command.split()` fallback.) This is an over-block relative to the original
"logs are common" intent, kept because a stderr redirect onto a receipt file is
not a logging pattern worth preserving.

## What is deliberately NOT gated

**Execution is not gated, in any form.** Running a script, running inline
`python -c` / `node -e` / `perl -e` code, invoking `background_hook.py` or
`subagent_lifecycle.py` directly, importing `record_subagent_receipt` — none
of it is denied here. Nor is an unrecognized executable that merely carries a
gated path as an argument.

This is the settled decision of 2026-08-27, reached after six review rounds.
Each round found another spelling that reached the same write; the fixes grew
faster than the coverage, and two of them were worse than the gap they
closed — one denied ordinary `subprocess.run(['pytest', <path>])`, another
recursed on a self-matching glob name until the whole guard failed open. The
same branch also denied `ruff check <this file>` and `git checkout <this
file>`, which made the guard an obstacle to repairing itself.

What the gate keeps is the one judgement this layer can actually make: a
command whose **verb writes a file**, whose target resolves to a protected
path. Redirects, `tee`, `sed -i`, `perl -pi`, `cp`/`mv`/`install`/`rsync`
(including directory destinations and `-t`), `truncate`, `touch`, `ln`,
`rm`/`chmod`/`chown`, and working-tree-rewriting `git` subcommands.

Receipt integrity does not rest here and never did. It rests on hook
ownership of `RECEIPTS.jsonl` and on `task_verify` ordering. Treat this gate
as a guardrail against accident. Do not reintroduce execution inspection to
close a newly-found spelling — the next spelling always exists.

Existing hard-link aliases of native Goal JSON are recognized by inode even
outside the repository. Goal readers independently require owner-controlled,
single-link, stable regular files, so an unrecognized alias cannot become
Goal authority.
Claude `projects/*/<session>/subagents/agent-*.jsonl` transcripts are likewise
classified as protected receipt provenance even though they live outside the
repository; redirection and mutator verbs targeting those leaves are denied.
Inline Python is not inspected, so a `python -c` write to one allows.

## Known-safe verbs (no classification attempt)

`ls`, `cat`, `head`, `tail`, `grep` / `rg`, `find`, `wc`, `git log`,
`git diff`, etc. The guard is silent on allow, so these produce no audit
noise.

`sort` and `diff` are **not** in this class: they are readers until given
`-o`/`--output`, which names a file they overwrite, and that operand is
classified. `sort -o <receipt> a` and `diff --output=<receipt> a b` deny.

## Historical: read-only inspection relief

Until 2026-08-27 the guard flattened the command to alphanumerics, raised
a `protected_marker` when a lifecycle name appeared anywhere in it, and
then needed relief sets (`NON_MUTATING_COMMANDS`, `SHELL_CONTROL_WORDS`,
`GIT_NON_MUTATING_SUBCOMMANDS`) so that `grep`, `cat` and `git add` were
not denied for merely naming a file.

All of that is gone with execution gating. Nothing is denied for naming a
path, so nothing needs relief for naming one. `GIT_NON_MUTATING_SUBCOMMANDS`
survives for a different reason: it separates working-tree-rewriting git
subcommands from index-only ones.

## Known gaps

The current guard descends into a nested shell's script: `bash`, `sh`, `dash`,
`zsh`, `ksh`, `ash`, and `busybox <shell>`, with the script found as `-c`,
inside a short-option cluster ending in `c` (`-lc`, `-xc`), or as
`--command[=]`, skipping `--` and value-taking shell options (`-o`, `-O`,
`--rcfile`, `--init-file`).
Inline `python -c` code is not inspected in any form, so every spelling of an
inline write allows. That is the settled decision, not a gap to close.

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
  `vim -es`, `sponge`, `patch`): the operands of an executable with no verb
  model are not classified at all, so no candidate is produced — note `tee`,
  `cp` and `echo >` still deny on the identical bare name, because those go
  through basename classification.
- `python -m <module> <protected path>` is not inspected, like all python
  execution. A stdlib module used as a file-writing
  utility (`python3 -m json.tool in.json <protected path>`) overwrites the
  target. This sits on the seam between "script execution is not gated" (which
  covers `-m`) and the mutation-verb class the gate does enforce. Left open
  deliberately: gating non-option operands of `-m` would re-deny read-only tools
  such as `python3 -m mypy plugin/scripts/mcp_bash_guard.py`, which is the false
  deny this whole change removed.
- **a directory operand is not a classifiable artifact.** `git clean -fd <dir>`,
  `git checkout -- .`, `git restore .`, `git apply <patch>` and `git reset
  --hard` reach protected artifacts and gated source without the artifact ever
  appearing as a token. The git model classifies file operands only.
- **the guard re-implements a partial getopt, per verb.** This is the class
  that produced the last four review rounds, and it is not closed. For each
  modelled verb the guard has to know which options take values, and which
  operand is the destination. Every gap found so far was one of: an attached
  value (`-es/a/b/`, `-t<dir>`), a separated value trailing the operands
  (`cp SRC DST -S bak`), or a spelling of the same switch the model did not
  list. Assume more exist. A caller who wants past this layer has far cheaper
  routes anyway — `python3 -c` is ungated by design, and
  `HARNESS_SKIP_MCP_GUARD=1` is documented — so completeness here buys less
  than it costs. Add a spelling when it is reported; do not treat the option
  model as exhaustive.
- **analysis cost is bounded by a timeout that fails open.** The hook gets
  3 s (`plugin/hooks/hooks.json`); exceeding it emits no decision, which is
  an allow. So any super-linear path is a bypass: re-classifying the unsplit
  line once per segment made a 22 KB command of ordinary `echo "a"b;`
  padding time out, converting every deny on that line into an allow.
  Keep classification linear in command length, and treat a new nested loop
  over tokens as a security change, not a performance one. Note
  `_COMMAND_LENGTH_CAP` fails *closed* above 64 KB, so the exposed window is
  the one below it.
- **an exception is an allow.** `main()` has a catch-all that exits 0, so any
  input that makes a path call raise suppresses every deny on the line — a
  NUL in one token was enough. `_normalize_candidate_path` swallows
  ValueError/OSError for that reason; keep new path handling inside it.
- **the tokenizer is not bash's, and the divergence moves the operand.** This
  is the invariant to hold: *the token list handed to classification must be
  positionally identical to the word list bash would build.* Two divergences
  have already cost a live bypass each — dropping empty quoted words shifted
  every positional consumption one place left, so `cp payload <<"" <receipt>`
  had the receipt eaten as a heredoc delimiter; and leaving word-start comments
  in the stream put a comment word in the destination slot, so
  `cp payload <receipt> #` overwrote the artifact. Empty words are now kept and
  comments are removed in `_unquoted_lines`, which is the only stage that knows
  the quote state. Any further place where `shlex` and bash disagree on *which
  word is where* is a bypass, not a cosmetic difference.
- **quoting forms the guard does not model.** Token text is read as shell
  syntax, so the guard must know how each token was quoted. `_quoted_flags`
  establishes that by lexing twice — posix for values, non-posix for quoting —
  and the two lists must agree token-for-token.

  Agreement is established *by construction*, not merely checked: backslash
  tokens are merged and empty quoted words are filtered with posix emptiness
  semantics, because each of those desynchronised the lexes by one. That
  mattered more than it sounds. The earlier fallback for a mismatch was "treat
  every token as unquoted", which handed the caller an off switch for
  quote-awareness costing two characters (`''`) inside the very string being
  inspected — `sed -i s/a/b/ '' '<' <receipt>` walked through.

  When alignment still cannot be established the flags are `None`, and the
  segment is classified under **both** readings, keeping every target either
  produces. A deny from either interpretation denies.

  An earlier attempt gave each consumer its own "conservative" default —
  unknown-as-quoted when stripping redirects, unknown-as-unquoted when
  classifying them. That was wrong, and it was a regression: leaving a redirect
  operand in the argv is also a laundering direction, because it becomes the
  last operand, so an everyday `cp src "<dir>"/RECEIPTS.jsonl 2>/dev/null`
  stopped denying. `None` is not rare either — adjacent-quote concatenation
  (`"a"b`, `/tmp/a\ b`) produces it. There is no safe *side*; the safe answer is
  the union. A quoting construct that defeats both lexes consistently, in the
  same direction, would still be a gap.

## Escape hatch

`HARNESS_SKIP_MCP_GUARD=1` bypasses the gate for one Bash call and logs a
`gate-bypass` entry to `learnings.jsonl`. It must be set in the hook process's
environment; an inline `VAR=1 cmd` prefix does not reach it, because the hook
runs before the command.

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
