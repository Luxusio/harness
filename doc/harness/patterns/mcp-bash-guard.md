---
title: mcp_bash_guard — block Bash-layer mutations of gated paths
freshness: current
invalidated_by_paths:
  - plugin/scripts/mcp_bash_guard.py
  - plugin/scripts/prewrite_gate.py
  - plugin/scripts/_lib.py
  - plugin/hooks/hooks.json
tier: 2
freshness_updated: 2026-08-27T00:00:00Z
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
leading fd number. It is reachable on the ordinary path, not only through the
unclosed-quote `command.split()` fallback as this note once claimed: a glued
quote such as `echo '>'<path>` yields the single token `>path`, which the regex
matches. That shape allows today — the leading-character rule in
`_quoted_operator_words` marks it a literal — but the regex reaching it at all
is what made it a deny for several revisions.)
This is an over-block relative to the original
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
- **a quoted operator sharing its spelling with a real separator on the same
  line is not reinterpreted.** `cp /tmp/f ';' <artifact>` denies on its own,
  but `echo "a"b ; cp /tmp/f ';' <artifact>` allows: two `;` boundaries, one
  quoted `;`, and no way to tell which is which without the positional
  alignment this branch by definition lacks. Preferring the merge is worse — it
  denied `rm -rf "$PWD"/build ; find . -name '*.py' -exec grep -l foo {} ';' ;
  wc -l <plan>`, reporting an `install.py` that appears nowhere on the line,
  because the merge glued the `rm` to the `find` and glob-expanded `'*.py'`. A
  file literally named `;` is obfuscation; `find -exec … ';'` is an everyday
  idiom. The count rule takes the idiom.

  The `"$PWD"` in that reproduction is load-bearing and was missing from the
  first three versions of this note. Without it `_quoted_flags` aligns,
  `quotable` is empty, and neither gate is consulted — so the plain spelling
  allows under every variant, including the buggy one, and proves nothing. A
  reproduction that does not reproduce is worse than no reproduction: it was
  cited in four places as the justification for this machinery.
- **a quoted operator is evidence only where the guard looks for it.** Two
  places consult the raw text for a whole quote-delimited word spelling an
  operator: segment boundaries (`_quotable_operators`) and redirect operators
  (`_quoted_operator_words`). Both were added after the *absence* of the check
  produced a false deny — `echo "ok"; rm -f /tmp/x; wc -l "$PWD"/<plan>` for
  the first, `grep -n ">" "$PWD"/<source>` for the second, each blocking a pure
  reader and naming a path the line never writes. If a third consumer of token
  text is added, assume it has the same hole until shown otherwise. Note the
  redirect path has no both-readings union behind it, so a wrong guess there is
  final.

  **The answer has to be per occurrence, and two weaker rules shipped first.**
  Keyed on presence, a single quoted `>` suppressed *every* real `>` on the
  line, so `grep -n ">" "$PWD"/<file> ; echo x > <receipt>` wrote the artifact.
  Keyed on counts — "as many quoted spellings as occurrences" — it then refused
  to skip whenever a line held both, so `grep -c ">" <file> > /tmp/out`, a
  reader with an ordinary redirect, denied again. Each rule was wrong in the
  opposite direction, and each was written as the fix for the other.

  The question was never *how many*; it is *which one* — but "the k-th raw word
  reducing to a spelling is the k-th token of that spelling" was a third wrong
  answer, and the most convincing of them. The two lexes disagree about **word
  boundaries**, not just about quoting: `'>'q` is two raw words and one token
  `>q`, so the quoted word had no token behind it and donated its flag to the
  next real `>`. Twelve shapes wrote protected artifacts that way.

  A fourth answer reconciled two shlex passes word-for-word, and it failed the
  same way once more: the lexers disagree about boundaries in ways no pairing
  survives. `punctuation_chars=False` does not split `2>/dev/null` into three
  tokens; `punctuation_chars=True` does not raise on `-F'|'`, it silently
  mangles the boundaries, so a retry keyed on the exception never fires.

  **So there is no second lexer.** The raw command is scanned once, tracking
  quote and escape state, recording for every character whether it came from
  inside a quote — the only fact this ever needed. Tokens then consume
  *characters*, so one scanned word can satisfy a run of tokens. The flag is
  decided by the token's **leading** character: a real operator can never have
  a quoted first one, because if it is quoted it is a literal. Requiring the
  whole span to be quoted instead reinstated three over-blocks bash never
  writes, silently reverting the commit that had just removed them.

  The scan gives up — `[]`, skip nothing, classify everything — when it ends
  mid-quote or a token cannot be matched against the text. The older two-lexer
  degrade rules are gone with the second lexer.

  **That degrade is only safe because the tokens are aligned by construction.**
  "Classify everything" is not a neutral fallback: it reads a quoted `'>'`
  literal as a real operator and denies the next word, so it *generates* false
  denies. It was reached routinely, not exceptionally — every line carrying an
  unquoted `$( … )` or backtick reached it, because the flags were recomputed
  against the token stream `_collapse_substitutions` had already rewritten and a
  rewritten stream can never match the raw text. Ordinary readers such as
  `cd $(git rev-parse --show-toplevel) && grep -n '>' "$PWD"/<plan>` denied,
  naming a mutation the line never performs. The flags are now computed once
  against the tokens as the lexer produced them and carried through both
  rewriting stages, with synthesized tokens taking `False`. **A token stream
  produced by a rewriting stage must never be handed to this function.**

  **Within `_quoted_operator_words`, no glue spelling can make a real redirect
  operator be skipped.** That scoping matters — an earlier version of this
  paragraph claimed "the fail-open direction is shut" without it, and that is
  false for the redirect path as a whole, because `_quoted_flags` can hand out
  a wrong flag before this helper is consulted. See the fail-open residue.

  Known residue, each verified to reproduce:

  - **fail-open.** An escape adjacent to an empty quote run desynchronises
    `_quoted_flags` by one. Two spellings, both verified to write and allow:
    `echo pwned ''\>> <artifact>` and `echo pwned ""\ > <artifact>` (also
    `''\ >` and `;""\ >`). `_quoted_flags` (not this helper) declares alignment
    on equal word counts, and here the counts match while the correspondence is
    shifted by one, so the real operator inherits a quoted flag. Equal counts
    are not alignment; reconstruction would be. Closing it means changing
    `_quoted_flags`, a wider blast radius than this task accepted.
  - **fail-open.** `echo x ;> <path>` writes and allows. `_split_control_cluster`
    only decomposes tokens made entirely of `()&|;`, and `>` is outside that
    set, so `;>` is never split and the segment never ends. `echo x ; > <path>`
    — one space apart — denies. Pre-existing and out of scope here, but it is a
    plausible compact spelling rather than obfuscation. Glue opens no hole on
    the *boundary* path, which is what makes this specific to `;>`: a glued
    operator such as `p'|'` produces the token `p|`, which is not in
    `BOUNDARY_TOKENS`, and `_split_control_cluster` rejects any token not made
    wholly of `()&|;`.
  - **deny-leaning, six shapes.** An operator token whose first character is
    unquoted but which carries a quote later — `2'>'<path>`, `2">"<path>`,
    `2\><path>`, `1'>'<path>`, `1\><path>`, `2'>' <path>` — denies while bash
    writes nothing: to bash these are literal words, not redirects. This is the
    cost of deciding on the leading character alone. Measured against real bash,
    `any(q for _, q in span)` allows all six with **zero** fail-opens across the
    286 shapes tried, so it is a live candidate — but the leading rule is the
    one validated over ~1300 cases by review, and swapping the security-critical
    predicate on a 16-shape corpus is a change that needs its own adversarial
    pass. Deliberately left as residue rather than pinned by a test: asserting
    these denies would enshrine six behaviours bash contradicts.

  Four earlier entries were removed rather than kept: `x''` never reproduced at
  all; `echo q'>'` stopped once the lexer retry landed; `echo '>'<path>` is
  allowed again under the leading-character rule; and `ls /tmp/a\ b''` was
  listed as deny-leaning while it simply allows — `/tmp/a b` is not a gated
  path, so nothing there could ever deny. A residue list is a claim like any
  other, and this one has now carried three that did not hold.

- **an operator spelled across quote runs is not recognised as quotable.**
  `touch '&''&' <artifact>` builds the token `&&` while the raw text contains
  no `&&` substring, so the merged reading is not offered and the write
  allows. The same holds for a backslash-escaped operator (`tee \; <artifact>`)
  once anything else on the line defeats quote alignment. Both are deliberate
  obfuscation rather than phrasing anyone reaches by accident, and the
  alternative — inferring quotability without knowing the quoting — is what
  produced fabricated denies on ordinary readers. Recorded rather than closed.

  Worth knowing if you touch this: `'&''&'` *used* to deny, but only as a
  side effect of the borrowed-quote-mark bug. A deny that exists by accident
  is not coverage, and trading it for correct behaviour on ordinary readers
  is the right direction under this gate's stated bar.
- **a substitution in the basename slot is not resolved.** `echo z >
  <task>/$(echo RECEIPTS.jsonl)` allows, as do the `tee`, `sed -i`, `perl -pi`,
  `truncate`, `dd of=`, `cp`, `mv` and `install` spellings of the same
  destination. The collapse keeps the directory prefix — dropping it was a
  token deletion — but the basename is only known after the shell runs it, and
  denying every unknown basename inside a protected directory is a policy
  change with its own over-block risk. Unlike the gaps above this one is
  deliberate obfuscation rather than phrasing anyone reaches by accident, which
  is why it is recorded here instead of closed.
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
  Classification is by exact basename, so a *directory* operand is never a gated
  token: `rm -rf <taskdir>`, `mv <taskdir> /tmp/x`, `chmod -R 000 <taskdir>`,
  `git clean -fd doc/harness` and `rm -rf doc/harness/tasks` all allow, each
  destroying artifacts the file-operand spellings deny. Unlike most rows here
  this is an *accident* shape, not a determined-caller one — a stray `rm -rf`
  on a task directory is a plausible slip. Closing it means classifying a
  directory operand that contains a gated artifact.
- **the shell's cwd is never inferred; a relative operand always resolves
  against the hook's cwd.** This costs a real over-block: `cd /tmp/work && echo
  hi > out.py` denies as a repo *source* mutation and names
  `<repo-root>/out.py`, a file the command never touches, so writing a scratch
  script in a temp directory is refused. Tracking `cd` was implemented to remove
  that, and reverted after measurement: **a linear segment walk cannot know
  whether a `cd` executed, or in which shell.** Fifteen shapes run a `cd` that
  does not move the caller, so bash writes the artifact in the repo while any
  tracker honouring the `cd` resolves the write outside it and allows —
  `( cd /tmp ) ; …` with its glued and nested spellings, `( cd /tmp ) | tee
  <artifact>`, `cd /tmp | cat ; …`, `while false; do cd /tmp; done ; …`,
  `if false; then cd /tmp; fi ; …`, `f() { cd /tmp; } ; …`, `cd /tmp & …`,
  `false && cd /tmp ; …`, `true || cd /tmp ; …`.

  Two intermediate designs each looked complete and were not. Counting paren
  depth closed six of the fifteen. A whitelist — refuse to track on any
  grouping, pipeline, background or control-flow token, and refuse a `cd`
  reached across `&&`/`||` — closed all fifteen and then lost to
  `cd /tmp ; cd $OLDPWD ; echo PWNED > install.py`, because **declining to
  track is not the safe direction.** Once a `cd` has been honoured, a later
  unresolvable one can move the shell back *toward* the repo, and the stale
  outside-repo cwd converts a real write into an allow. A sound design must
  reset to the original cwd on every construct it cannot model, which is close
  to not tracking at all.

  The trade is deliberate: an annoying deny beats a silent write to a protected
  artifact, in a gate this doc already calls an accident guardrail. Pinned by
  `test_cwd_is_not_inferred_from_cd`, which asserts all fifteen deny. Do not
  re-add cwd tracking without a design that answers every row there.

  Residual asymmetry worth knowing: `cd /tmp && echo x > RECEIPTS.jsonl` denies
  (the operand is rejoined to the repo root) while `echo x > /tmp/RECEIPTS.jsonl`
  and `cp /tmp/a /tmp/RECEIPTS.jsonl` allow. Two spellings of one intent
  disagree. That is a consequence of not modelling cwd, not a basename policy —
  `_normalize_candidate_path` returns `""` for anything outside the repo root,
  so the basename rule never runs on the absolute spellings.
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

  Cost is not only about command *length*. Glob expansion walks the
  filesystem, and `glob` cost is multiplicative in the number of
  wildcard-bearing path components, so `cp /*/*/*/*/*/*/*/*/* <dir>/` took
  66 s from a 30-character operand — enough that prefixing any line with one
  cheap-looking `cp` converted its deny into an allow. Note that `islice` over
  `iglob` is *necessary but not sufficient*, and the difference is easy to
  miss: a deep pattern matching many files short-circuits after the cap and
  looks fast, while the same depth matching few or none still walks everything
  (48 s and beyond). Test a cost bound with a pattern that matches nothing.

  `_GLOB_COMPONENT_CAP` bounds this, but only for patterns reaching *outside*
  the repository, and that distinction was learned the hard way. Cost comes
  from the tree a pattern is anchored to, not from how it is spelled:
  `<repo>/*/*/*/*/RECEIPT?.jsonl` is 0.06 s and names live artifacts, while the
  absolute `/*/*/*/*/*/*/*/*/*zzzznomatch` is 50 s. A plain component count
  refused both, which re-opened the glob-in-basename route — a cost bound that
  silently stops classifying is a bypass wearing a performance fix's clothes.

  Containment is decided on the normalized literal prefix before the first
  wildcard, not by a `startswith` on the joined pattern. The textual version
  looked equivalent and was not: `os.path.join(base, "../../*/*/…")` starts
  with `base`, so `..` traversal was exempted from the cap and one operand ran
  49 s. A lexical prefix is not a containment proof.

  The wider lesson, after three rounds of it: **per-item caps get defeated by
  repeating the item.** Capping one glob's depth left 250 shallow globs on one
  line at 4 s; bounding the substitution scan by "closers remaining" left an
  opener that can never close (`$((` consumes no `)`) rescanning to end of line
  at 4.4 s. `_ANALYSIS_BUDGET_SECONDS` bounds the invocation instead — but only
  through the loops that consult it, and "so a new repetition trick cannot
  reopen the class" was an overstatement this doc carried while one was open.

  **The recursion is itself a repetition.** `eval`/`bash -c` descent re-enters
  `_extract_mutation_targets`, and each level pays both readings, so cost is
  roughly 4^depth: eight wrappers around a plain `cp <src> <receipt>` — no
  padding at all — took 6.3 s against the 3 s hook timeout, and the write
  allowed. The strided checks deeper in could not substitute, because each
  nested segment is too small to reach its stride. The budget is consulted at
  the recursion entry now, which takes that shape to 1.0 s and a deny.

  Exhausting it **denies**, with `method="uninspectable command (analysis
  budget exhausted)"`. It does not degrade to "not extracted": on this path
  "classified nothing" *is* the allow, so refusing to decide is the only
  fail-closed option, and it joins the oversize-payload and oversize-command
  denies as a case where the reason legitimately cannot name a cause. That
  only holds for loops that actually consult the budget — operand
  classification, the segment walk, redirect extraction, glob expansion and
  the substitution scan all do. A new cost loop that does not will overrun
  without ever reaching the deny, so adding one is a security change.
- **an exception is an allow.** `main()` has a catch-all that exits 0, so any
  input that makes a path call raise suppresses every deny on the line — a
  NUL in one token was enough. `_normalize_candidate_path` swallows
  ValueError/OSError for that reason; keep new path handling inside it.
  RecursionError is the same class from the other direction: unbounded descent
  into `eval`/`bash -c` nesting raised it, and the cost was super-linear, so
  `eval eval … cp <src> <receipt>` hit both fail-open paths at once.
  `_NESTED_DESCENT_CAP` bounds it. Past the cap the guard stops descending
  and the deep case allows — the same allow as any unextracted nested content,
  chosen over an exception that suppresses the *whole line*.
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

  The clustering that complicates collapse also broke *segmentation*, and for
  longer: `BOUNDARY_TOKENS` matched operators as exact strings, but a plain
  subshell emits `');'`, `')&&'`, `')|'`, `')&'` as a single token. None of
  those is in the set, so the segment never ended and `_process_segment_once`
  dispatched the entire rest of the line on the subshell's first command word.
  `( echo hi ); cp <payload> <receipt>` allowed while the same line with a
  space before the `;` denied — one character between a deny and an allow, on
  phrasing nobody would think of as evasion. `_split_control_cluster`
  decomposes a token made only of control punctuation by longest match, so
  `&&` never becomes two `&`. Splitting only ever adds boundaries, so it
  cannot turn a deny into an allow — *provided the quoting is known*. It is
  not always: one adjacent-quote word anywhere on the line makes `_quoted_flags`
  return `None`, and every token then reads as unquoted.

  Both single-reading answers were tried and both were wrong. Expanding under
  unknown alignment decomposed a quoted operator literal (`tee ');' <artifact>`,
  where `');'` is a filename) into real boundaries that truncated the segment
  before the artifact. Declining to expand reopened the clustered-closer bypass
  for any line containing one adjacent-quote word — and `"$PWD"/doc` is enough,
  so `(ls "$PWD"/doc); cp <payload> <receipt>` allowed. Under unknown alignment
  both readings are classified and their targets unioned, the same policy the
  quoting reconciliation already uses.

  An alternate reading needs *evidence*, though, not merely unknown alignment.
  The merged-pair and unsplit-line readings exist for one shape — a quoted
  operator that is really a filename (`touch '|' <artifact>`) — but they were
  taken whenever `_quoted_flags` returned `None`, which is almost always, since
  `"$PWD"/x`, `"$(pwd)"/x` and `/tmp/a\ b` all defeat alignment. So
  `ls "$PWD"/doc ; rm /tmp/x ; wc -l <task>/PLAN.md` was read as a single
  command and denied: a reader blocked, with the deny naming a PLAN.md that the
  line's `rm` operand never was. A quoted operator leaves a mark in the source —
  a quote character touching it — and `_quotable_operators` looks for exactly
  that. Without the mark there is no reading in which the segments join, so
  there is nothing to union.

  The whole-line union cannot compensate for a wrong split, which is worth
  stating because it looks like it should: that reading dispatches on the
  line's *first* command word, so `echo "a"b ; touch '|' <artifact>` was still
  dispatched on `echo`. What covers it is classifying each pair of adjacent
  segments *merged* across the ambiguous boundary — linear, since every token
  joins at most two pairs.

  Positional identity is necessary but not sufficient — a stage may also
  *delete* words, and deletion is invisible to a shift check. Substitution
  collapse did: `punctuation_chars=True` clusters trailing punctuation, so the
  closer of `$(date)` arrives as `);`, `)&&`, `)|` or `))` and never equals
  `")"`. The scan ran off the end and discarded every remaining token, so
  `echo $(date);cp /tmp/payload <receipt>` had its second command deleted
  before classification. Collapse now matches a closer inside a cluster and
  re-emits the remainder. The mirror case is the opener: a substitution glued
  to a preceding word (`--target-directory=$(pwd)/x`, `-t$(pwd)/x`) must keep
  that prefix, or the option name is dropped and its value stops being read as
  a destination. Treat "which words survive" as part of the same invariant as
  "which word is where".

  The first repair of that deletion introduced two more, both worth naming
  because they are the shapes a fix naturally takes:

  - *Not deleting cost time.* "Keep the token and move on" was written as a
    plain `index += 1`, so n unclosed openers each rescanned to end of line.
    40 KB of `'$('` padding took 3.2 s, the hook budget is 3 s, and a timeout
    emits no decision — the padding disabled every deny on the line.

    The first repair latched the failure — "this scan found nothing, so no
    later one will either" — which is false, and was itself a worse fail-open.
    A scan also fails when no closer brings *this* opener's depth to zero, and
    `$((` leaves depth 2 forever; a later opener with smaller depth would still
    have found one. So a single `$((` anywhere ahead suppressed collapse for
    the rest of the line, and `echo $(( ; cp /tmp/x $(pwd)/<artifact>` allowed
    — one token instead of 40 KB. The skip is now conditioned on the thing
    that is actually provable: a running count of closer characters still
    ahead. Zero left means no scan can succeed; anything else, scan.
  - *Gluing is also deletion.* `$(pwd)/doc` is one word to bash and two to
    shlex, so the two get merged. Deciding that with `")/" in command` asked
    about the whole line, so a stray `)/` anywhere — `cd $(dirname .)/.` is an
    ordinary idiom — re-enabled the merge for an unrelated span and fused two
    real operands, so `sed -i $(echo s/O/X/) <artifact>` swallowed the artifact
    as sed's script expression. Adjacency is now read from the character after
    *that span's own* closer, located by counting closer characters, which
    survive tokenization one-for-one and in order.

    That counting has to be exhaustive to be meaningful. Two omissions have
    already desynchronised it: quoted words (`"a)/b"` contributes a real `)` to
    the source) and the tokens a *backtick* span swallows, whose parens are
    consumed just the same. Either omission makes every later span read a
    too-low ordinal and inherit some other paren's adjacency — losing a real
    merge in one direction and fabricating one in the other, which rebases an
    out-of-repo operand into the repo and over-blocks. Count closers wherever
    they occur, not only where they are syntactically interesting.
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
