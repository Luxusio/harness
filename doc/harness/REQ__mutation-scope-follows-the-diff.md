---
tags: [harness, testing, mutation, verification, review]
summary: 뮤테이션은 태스크가 바꾼 줄에서만 뽑는다 — 전체 스위트 비용이 그것 말고는 아무것도 허용하지 않기 때문이다. 살아남은 뮤테이션은 전체 스위트로 한 번 더 확인한 뒤에만 보고하고, 보고일 뿐 차단하지 않는다.
updated: 2026-09-10
freshness: current
invalidated_by_paths:
  - plugin/scripts/mutation_probe.py
  - tests/test_mutation_probe.py
---

# REQ — mutation scope follows the diff

Completes the item `REQ__guards-are-verified-where-they-run.md` deferred under
"Mutation testing is not built here". That document owns the general
proposition — a guard that has not run where it fails is not evidence, and a
test that does not reach the branch it names is not a test of that branch. This
one owns the machine that checks the second half against a diff, and the shape
that machine is allowed to have.

## Expected behavior

### 1. Sites come from the changed lines, and from nothing else

`plugin/scripts/mutation_probe.py` selects mutation sites from the working-tree
diff (or an explicit `--rev` range), restricted to Python files, restricted to
AST nodes that *begin* on a changed line. A deleted file contributes nothing, a
staged new file contributes all of its executable lines, and a hunk that touches
only comments, docstrings or removed lines contributes no sites at all.

This is not an optimisation. Measured on this repository, 2026-09-10:

| | |
|---|---|
| full suite, xdist, idle host | 16.3s |
| full suite, same command, loaded host | 124.1s |
| one targeted test file, `-n 0`, idle host | ~0.5s |
| `plugin/scripts/_lib.py` | 4274 lines |
| sites the real `6689dd7` diff produces, probed from HEAD | 39, plus 3 files refused |

The last row is
`python3 plugin/scripts/mutation_probe.py --rev 6689dd7~1..6689dd7 --sites-only`,
run from the repository root at HEAD `8432d22`: 39 sites across the 2 files
that are byte-identical between `6689dd7` and HEAD
(`plugin/scripts/background_hook.py` 24, `plugin/mcp/harness_server.py` 15),
and a refusal note for the 3 that are not (`install.py`, changed by `7c41aac`;
`plugin/scripts/_lib.py`, changed by `8432d22`; `tests/conftest.py`, also
changed by `7c41aac` — a mutation subject here rather than an excluded test
file, since its name matches neither `test_*.py` nor `*_test.py`).

**Three earlier drafts of this row were wrong, in different ways, and the
sequence is worth keeping.** The first said 56 and named no
invocation. The second said 60 and named the invocation — the command
reproduced the number, but the number was not what the row claimed: 21 of
those 60 came from applying the range's line numbers to working-tree content,
so they described live code the audited commit never touched. A survivor
there would have been noise wearing the label of a real finding. The third
said "39, plus 2 files refused" and named the invocation and both refused
files correctly, but undercounted the refusals by one: re-running the same
command found a third refused file, `tests/conftest.py`, that the second
correction's author did not name. A reader who reproduces this row and counts
three refusals cannot, from the row alone, tell whether three is now right or
is itself due for a fourth correction — reproduce it again rather than trust
any specific count written here, including this one.

To probe the refused files, check that revision out. The line numbers and the
source have to come from the same tree — that is the whole content of the
correction.

A survivor costs one full-suite run. At 30 mutations of whole-module scope the
run is measured in tens of minutes even on a good host, and the two-order-of-
magnitude spread between an idle and a loaded host means the bad case is the
one to design for. Changed-lines scoping is the only shape that fits inside a
task loop, so it is the design rather than a setting.

**The diff's own file headers are parsed against a boundary content cannot
forge, and independent of the operator's gitconfig.** `changed_python_lines`
anchors a `+++ ` target line on `diff --git ` at column 0, not on a preceding
`--- ` line: under `-U0`, a one-line replacement whose removed line reads
`-- old marker` and whose added line reads `++ b/elsewhere.py` renders as
`--- old marker` / `+++ b/elsewhere.py` — a real-looking header pair forged
entirely from content, because every content line in a `-U0` diff carries a
`+`/`-`/space prefix and only a true section boundary can start a raw line
with `diff --git `. The header parse also passes `-c core.quotePath=false`
and explicit `--src-prefix=a/ --dst-prefix=b/`: without the first, the
default `core.quotePath=true` quotes and octal-escapes a non-ASCII path
(`+++ "b/caf\303\251.py"`), which a blind `target[2:]` slice reads as
garbage rather than a path; without the second, an operator's
`diff.noprefix=true` strips the `a/`/`b/` prefix `target[2:]` assumes, and
every path in the run loses its first two characters. A target that still
does not carry the expected `b/` prefix (a gitconfig combination this call
did not anticipate) is named in a note rather than sliced blindly.

Test files are not mutation subjects. Breaking an assertion measures nothing
about whether the tests discriminate the product code. Which files those are
is decided by set membership against `discover_tests(repo_root, tests_dir)` —
the same ranked test set `collect_sites` already computes — not by a
directory-prefix string. An earlier version compared each changed path
against `tests_dir.rstrip("/") + os.sep`: `--tests-dir ./tests` produced
discovered paths `tests/...` (via `os.path.relpath`) against a prefix
`./tests/`, so the changed-test set came out empty and the diff's own test
files became mutation subjects — the exclusion above and the promotion in §2
lost together, by a leading dot. `os.path.normpath` on `--tests-dir` closed
that one case but not the more general one: `--tests-dir .` still normalises
to `"."`, whose prefix `"./"` no discovered path (`test_top.py`, no leading
`./`) can ever start with, and an empty prefix could not be used instead
because every path "starts with" `""` and the exclusion would then swallow
every source file, not just tests. Comparing against the discovered test set
directly needs no prefix at all and closes the class regardless of where
`tests_dir` sits.

**A site's span is read in `ast`'s own coordinates.** Two unit systems meet in
`_line_index`/`_char_offset` and both of them used to disagree with `ast`:

* `col_offset` and `end_col_offset` are UTF-8 **byte** offsets, while every
  offset downstream indexes a `str`. A line with a multibyte literal before the
  node had every span on it shifted by the delta. `if msg == "blocked" and
  count > 0:` offers 6 sites; the same line reading `"차단됨"` offered **0** —
  each mutant failed the `compile()` guard and was dropped, so the line read as
  covered while nothing on it had been attempted. Where the shift still
  compiled it was worse: the mutation landed on a *different* occurrence, or on
  an empty span past the end of the line, so `Site.apply` appended the mutant
  text to the module and reported a verdict under a label whose line was never
  touched.
* `str.splitlines` breaks on form feed, `\x0b`, `\x85` and `\u2028`; the
  tokenizer breaks on `\n` alone. One `\f` between two statements shifted every
  line after it, with the same two outcomes. (`ast` carries `_splitlines_no_ff`
  for exactly this.)

Measured live on this repository at HEAD `8432d22`, across the four files
carrying *shifted* candidates: 13 candidates, of which **7 were dropped in
silence and 6 were mutated at the wrong span** — on lines `install.py:478` and
`:1644`, `tests/test_codex_run_subagent_routing.py:106-107`,
`tests/test_lightweight_workflow_contract.py:106,126`,
`tests/test_setup_no_project_mcp_json.py:64`. Every unit in that sentence is a
candidate, not a node or a line: eight files at that HEAD have non-ASCII lines
carrying candidates, and one node at `install.py:477` contributes three whose
spans coincided with the correct ones because its start and end columns both
sit on ASCII lines while its body crosses line 478.
The conversion happens per line at the single boundary where `ast`'s numbers
enter, rather than by carrying bytes through `Site`, so one coordinate system
reaches `Site.apply`, the `compile()` guard and the file the runner writes;
this is what `ast.get_source_segment` does for the same reason.

### 2. A survivor is escalated before it is reported

Each mutation runs first against a **targeted** selection — the test files that
name the enclosing symbol, then the ones that name the module, with a file the
diff itself touched promoted above its peers. A mutation that selection kills is
definitively killed. A mutation that *survives* it is re-run against the full
suite, and only a mutation the full suite also fails to notice is reported as a
survivor.

Cheap-and-wrong towards "killed" is acceptable — the cost is a missed signal.
Cheap-and-wrong towards "survived" is not: a false survivor is noise, noise gets
the tool ignored, and a tool people believe is watching while it is ignored is
worth less than no tool. Everything that could produce a false survivor is
therefore treated as an escalation trigger rather than a result:

- no targeted test file matched at all;
- the targeted selection is red *without* any mutation (every mutation would
  otherwise read as killed);
- the full suite is red without any mutation — reported as `unknown`, never as
  a survivor, with the reason named in the report.

That last case is why the clean full-suite baseline is measured at all. It is
measured lazily, once, on the first escalation, because a run whose every
mutation dies in the targeted stage never needs it.

The escalation is also the whole cost model, so the targeted selection is worth
getting right. Measured on the `6689dd7` reconstruction below — the
`plugin/mcp/harness_server.py` slice of that diff, 15 of the 39 sites above,
not the whole thing — same sites, same verdicts, one loaded host:

| targeted selection | escalations | wall clock |
|---|---|---|
| with the test file the diff wrote for the change | 4 (the survivors) | 120s |
| without it — a `--paths` filter had hidden it | 14 | 675s |

**This row is not independently reproducible from a single pasted command.**
Producing the "without" condition means running the probe with the targeted
selection deliberately degraded — the test file the diff wrote excluded from
what the ranker can see — which is not a state a documented flag puts the
probe into by itself; it was an instrumented variant of the run, not a
`--paths`/`--rev` invocation someone else can re-type. Per this document's own
standard (§1), a measured row stands only behind an invocation or is marked as
not independently reproducible; this one is the latter. It does survive one
cross-check that does not depend on reproducing it: `14 - 4 = 10` matches the
independent "10 of 15" figure in the comment above the `changed_tests`
assignment inside `collect_sites`.

Correctness did not move between those two runs; only time did. That is the
property §2 buys: a bad guess costs minutes, never a wrong answer.

For the same reason a symbol that names a large share of the suite is dropped
from the symbol rank rather than used — `main` appears in 33 of this
repository's 75 test files and `run` in 52 (re-measured 2026-09-10 with
`discover_tests`/`read_test_bodies`; the row this replaces said 29 of 68 and
48, the third stale measured row in this document), so ranking on one picks six
arbitrary files, pays six serial pytest runs, and escalates anyway. The module
stem is the cheaper and better guess there. The cutoff never falls below the
default selection width, because a symbol matching no more files than a
selection would run costs nothing to keep.

### 3. A bounded run says what it did not attempt

`--max-sites` (default 40) and `--budget-secs` (default 300) bound the run.
When either truncates the site list the report states how many sites were not
attempted and which bound stopped it. A bounded run that reads as complete
coverage is the same defect class this tool exists to detect, one level up.

`--budget-secs` is checked only *between* sites, never during one, and the
first site always runs regardless of budget (`attempted and clock() - started
>= budget_secs` — the `attempted and` forces at least one iteration through
before the clock is consulted at all). A run therefore costs at most the
budget plus the time of one more site, and always attempts at least one site
even when the budget is set below what a single site costs. Both directions
are visible in the report rather than hidden — `seconds` and `stop_reason`
report what actually happened — but "bound" reads as a ceiling on wall clock,
and an operator who sets a small budget expecting the run to stop near it will
wait longer. This is verified by reading `probe()`'s loop directly rather than
by a measured figure here — a wall-clock number depends on host load and on
which sites happen to be attempted, in a way a code reading does not.
Reproduce with
`python3 plugin/scripts/mutation_probe.py --budget-secs 1` against the
delivered file for a current figure.

An input the probe *cannot read* is refused rather than reported as an input
with nothing in it. `git diff not-a-rev..also-not` exits 128; discarding that
status left an empty diff, so a typo'd revision printed `mutation probe: no
mutable changed lines` and exited 0 — indistinguishable from an audited range
that contains nothing mutable. A `--repo-root` that is not a directory or not a
repository, and a `--python` that is not an executable, are refused the same way
instead of raising. A relative `--python` is resolved against the operator's
working directory before the child gets it, because the child runs with
`cwd=<scratch>`; `--python .venv/bin/python` is what this repository's own suite
uses. A `--tests-dir` that matches no test file is not an error — every site
simply escalates — but the report says so, because minutes of escalation are
otherwise the only visible symptom.

A source the probe cannot read at all is refused the same way, and named
rather than silently offering zero sites. Three routes reach this:

* **not valid UTF-8.** `source_at` opens every source with `encoding="utf-8"`;
  a file that fails that decode raises `UnicodeDecodeError`, which is a
  `ValueError`, not an `OSError`. `source_at`'s other two failure returns —
  file-not-found and checkout-mismatch — both return plain `None` and are
  named by the caller from outside the function; a decode failure is named
  from inside `source_at` itself, with its own note (the file and the decode
  reason), and the rest of the diff is still probed. The whole file
  contributes zero sites, because there is no source to walk.
* **does not parse.** `ast.parse` raising `SyntaxError` is caught in
  `mutation_sites` before any node is walked, so a file in this state also
  contributes zero sites for the whole file, named with the parse error and
  line rather than dropped in silence.
* **does not stay under the repository root.** `_contained(root, rel)`
  resolves `rel` against `root` with `os.path.realpath` and checks the
  result's common path is `root` itself; an absolute path or one carrying
  enough `..` to walk out fails this before any `open()` is attempted.
  `source_at` checks it first, as defense against a bogus path that somehow
  slipped past the diff parser rather than against a path the parser is
  expected to produce, and names the refusal rather than reading (or worse,
  reading someone else's file).

The same containment check runs a second time at the one place the probe
*writes*: `_with_mutation`, which is the actual `open(path, "w", ...)` call
against the scratch copy. It does not trust the read-site guard to have
already run — `os.path.join` does not clamp a `..`-bearing or absolute second
argument, so a site whose `path` was `../victim.py`, or simply absolute, would
otherwise write outside the scratch tree entirely, onto a file the operator
never asked this probe to touch. Both refusals raise before any file is
opened for writing.

The write itself is ordered against failure. Opening the scratch file with
`"w"` truncates it immediately, before the mutated text is computed by
`site.apply`; the truncating open and the restoring write share one
`try`/`finally` so that a failure computing the mutated text — an
`ast.unparse` edge case, or any other exception between the truncate and the
write — cannot leave the file holding zero bytes with nothing left to restore
it. Earlier, the truncating write sat outside that `try`, and a failure there
left the scratch copy corrupted: neither the original nor the mutation, and
the `finally` that was supposed to put the original back never ran because
the exception happened before it.

A candidate the `compile()` guard **declines** is named in the same way. An
`ast.unparse` round-trip that loses context is not a survivor and not a kill,
and dropping it silently makes a partial run over a line read as complete
coverage of that line — which is what the byte-offset bug in §1 did six times
without a word. Rare by construction; reported anyway, because AC-3's rule is
about the site list too, not only about the budget.

`--paths` narrows a run to the file under review. It has a second use: code
that is destructive to mutate — an installer resolving real user-owned
directories — stays out of a run that would otherwise reach it.

Run against its own introduction — one new file, so every line is a changed
line — the probe reported the following **against a 2026-09-10 draft of
`plugin/scripts/mutation_probe.py`, not against the delivered file**. The file
kept growing through review, so any site count quoted here would describe a
draft; run
`python3 plugin/scripts/mutation_probe.py --paths plugin/scripts/mutation_probe.py --sites-only`
for the current figure. What survives re-measurement is the shape: a new file
is the worst case for this scope rule, the default budget truncates it, and the
report says so.

An earlier attempt to date this block quoted the line count and site count of
the moment. Both were stale before the same working session ended, because the
edits that closed the last review round changed the file again — the sixth
wrong measured number in this document, in the sentence written to retire the
fifth. A forward-looking reproducible claim about a file still under edit
cannot be kept true by re-measuring; it has to stop carrying the number.

```
mutation probe: 169 site(s) from 1 changed file(s), 40 attempted in 291.1s
INCOMPLETE: 129 of 169 site(s) not attempted (--max-sites 40)
```

The block is dated rather than re-measured because the run above is a record of
what was read and acted on, and the fourteenth survivor in it has had a test
since — so a fresh run cannot reproduce the account either.

Of the 14 survivors in that run, 13 were read and left:
default-value constants no test pins, `frozen=True` on a dataclass nothing tries
to mutate, and a cluster of individually-redundant defensive conditions in the
hunk-header parser. The fourteenth was real — the line-to-symbol map had no test
at all, so the symbol every report line prints was unverified — and it now has
one. That is the intended shape of the loop: the probe reports, a human reads
each one, and most of them stay.

### 4. The probed tree is never written to

Mutations are applied to a copy of the tracked working tree in a temporary
directory, and the original file content is restored after every run even when
the run raises. An interrupted probe cannot leave a mutated line behind, and the
suite's own install-tree removal guard stays meaningful across a probe.

Nor is the scratch tree allowed to accumulate bytecode. `PYTHONDONTWRITEBYTECODE`
must reach every pytest child, and `-p no:cacheprovider` does not stand in for
it — that flag disables pytest's own cache, not CPython's. CPython validates a
`.pyc` on the source's mtime-*seconds* and size, and this runner writes one
mutation immediately after an unmutated run of the same file, so a mutation
that preserves the file's size (`Eq -> NotEq`, an equal-width integer shift)
and lands inside that second executes the stale bytecode of the unmutated
source. The suite passes, and a mutation the tests do kill is reported as a
survivor: the tool manufacturing the one error direction §2 forbids. It shipped
that way once — the environment was built and then dropped on the way to
`subprocess.run` — and a review reproduced a `2 -> 3` mutation reading
`survived` instead of `killed (full suite only)`. The same stale-`__pycache__`
mechanism is recorded in
`REQ__receipt-subsystem-failures-are-observable.md`.

`PYTHONDONTWRITEBYTECODE` stops the runner *creating* a `.pyc`; it says nothing
about one that is already in the probed repository. `git ls-files -o
--exclude-standard` reports `__pycache__/mod.cpython-3xx.pyc` as an ordinary
untracked file in any project that does not ignore it, and copying it hands the
runner the stale bytecode of the file it is about to mutate. Reproduced
2026-09-10 while re-running a fixture repro: run pytest once in the probed tree
first, and `Eq -> NotEq` reads `survived` for a mutation that tree kills
`2 failed, 1 passed`. Bytecode is therefore excluded from the copy by every
route rather than relied on not to be there.

### 5. It reports; it does not block

A survivor is not a bug report. Unreachable defensive branches and equivalent
arrangements survive legitimately — review round 5 of `8432d22` found exactly
such a case and correctly declined to demand a test for it, and
`REQ__receipt-subsystem-failures-are-observable.md` records a survivor that is
"정확한 사실". Failing a task on a survivor would have been wrong on day one. The
probe exits 0 with a survivor list; a human or a review lens decides.

## What it caught, and what it did not (2026-09-10)

Both defects below were reconstructed as pre-fix trees and probed. The result
is one of each, and the negative is the more informative half.

**Caught — a guard branch no test reaches (`6689dd7`).** The guard is
`handle_task_context`'s `task_control_status(...) == "open" and
_session_resumes(...)`. Two runs over the commit's own diff, `--paths
plugin/mcp/`, 15 sites each:

```
tree as committed          -> plugin/mcp/harness_server.py:1186 [drop operand 1 of And]  killed
the one test that reaches
that conjunct removed      -> plugin/mcp/harness_server.py:1186 [drop operand 1 of And]  SURVIVED
```

The second conjunct's drop is killed in both. This is the class named in
`REQ__guards-are-verified-where-they-run.md` row 2, and it is the class the
operator set is built around: *forcing a boolean test to a constant cannot find
it*, because flipping both operands at once dies on any test that exercises the
guard at all. Dropping one operand at a time is what sees it, which is why an
`if` whose test is a `BoolOp` is mutated by operand drops rather than by the
forced constants used for every other test.

Three further survivors are the same in both runs, so they are properties of
the tree as it stands rather than of the reconstruction: the `not held`
disjunct in `_session_resumes` (redundant — an empty `held` also fails the
status check), `handle_task_context`'s invalid-control early return, and the
`or current_session_id()` fallback in the identity line whose two-identity
version is the defect that function's docstring exists to describe. Reported,
not acted on here: §5.

**Not caught — the counts-slot reader that two review rounds passed
(`8432d22`).** In the tree that shipped that defect, `extract_qa_verdict` was
moved onto the aligned accessor and `normalize_receipt_completion` was left
reading `summary_lines[1]` off the raw list. That line **is not in the diff** —
it was not touched by the change that broke it — so changed-lines scoping never
offers it as a site. Widening the scope would not have helped either: the line
was covered by the unwrapped-review counts tests that already existed, so a
mutation of it dies. The defect was a missing *case* (a wrapped review final),
not an undiscriminated line, and mutation testing does not detect missing cases.
QA driving the real lens is what found it, and remains what finds that class.

That negative is a **checked property, not this paragraph**. The pre-fix state
is reconstructed in
`test_the_counts_slot_defect_is_out_of_reach_because_its_line_is_not_in_the_diff`,
which asserts that no site is offered at the defective line for that diff. A
claimed ceiling silently stops being true when site selection widens, and
nobody reads a REQ to find that out. Three things keep it from passing
vacuously: the same diff must still yield sites elsewhere, the defective line
must be a site the moment it is in scope (`1 -> 2`), and the reconstruction
must still contain the line it names.

It does **not** also catch hunk parsing that over-reads, though an earlier
draft of this paragraph and of the test's docstring claimed it did. Review
measured that: widening `range(start, start + count)` by one leaves this test
green, because the fixture's hunks do not abut the reader block. That property
is owned by (at least) `test_sites_come_from_the_changed_lines_and_nothing_else`,
`test_a_comment_or_string_only_change_yields_no_sites`,
`test_a_new_file_contributes_its_executable_lines`,
`test_a_changed_test_file_is_not_a_mutation_subject` and
`test_a_range_that_does_not_describe_the_checkout_offers_no_sites`. Naming the
wrong owner in the one tool built to stop tests claiming coverage they lack is
the error worth recording here.

**No exhaustive count is given for this list, on purpose.** A claim that a
list of tests is the *complete* set that reddens under a given mutation has to
be re-measured every time a test is added anywhere the mutation's effect could
reach, and this document has already shipped a stale version of that claim
more than once — most recently a "five, and the only five suite-wide" count
that a later round's added tests silently falsified. The list above is owned
by, not exhaustively bounded by; reproduce with the mutation in
`changed_python_lines`'s hunk loop (`range(start, start + count)` ->
`range(start, start + count + 1)`) against a scratch copy of the tree and a
full-suite run if the exhaustive set is ever needed again.

There is a probe that would have caught it — for each expression the diff
*replaced*, apply the replacement to the siblings of the old expression that the
diff left behind, and report the ones no test can tell apart. That is precisely
the "one origin, two readers" defect. It is deliberately not built here: it
mutates unchanged code, which is the cost model this design exists to avoid, and
it needs its own evidence that it does not produce noise. Recorded so the next
person does not have to re-derive it from the same incident.

## Known ceiling (deliberate)

- **A mutation runner finds tests that do not discriminate. It does not find
  designs that are wrong.** Both AC-4 defects illustrate the two sides of that
  line above.
- **Only the four operator families**: forced `if`/`while` tests, comparison
  swaps, boolean operator swaps and operand drops, and boolean/integer constant
  shifts. String constants are not mutated — a changed message is not executable
  behaviour and mutating it manufactures survivors nobody should act on.
- **A node must begin on a changed line.** An edit inside a multi-line
  expression whose first line did not change offers no site. Conservative in the
  same direction as everything else here.
- **Untracked, unstaged files are invisible *as subjects*.** They are invisible
  to `git diff` and therefore to the site list; stage a new file to probe it.
  They are not invisible as *tests* the same way: **the copy set is the test
  tree minus one rule** — the directories pytest itself would never walk
  into. `main` passes `tests_tree_files(repo_root, tests_dir)` — every file
  under `tests_dir` that rule does not prune, symlinks followed, `conftest.py`
  and fixture data included — to `copy_worktree` as `must_include`.

  Git was the first source of disagreement: three rounds fixed one mechanism
  each (tracked-only, then untracked-non-ignored, then a `.gitignore`d test
  directory) while its sibling stayed live. The round after them handed the
  **ranker's own output** to the copy and claimed here that this "closes it as
  a class, and a fourth mechanism nobody has thought of yet is covered because
  the ranker's own output is the input". **That claim was false, and QA
  reproduced two more mechanisms against the tree that carried it:**

  * `tests/local/boundary_test.py` — pytest's *other* default `python_files`
    pattern. Ignored by git, unmatched by a `test_*` ranker, so named by
    neither: `survived`, no note, against a tree that kills it
    `2 failed, 1 passed`.
  * `tests/deep -> ../shared` — a tracked directory symlink. `os.walk` does not
    follow it by default, so the ranker never descended, and `copy_worktree`'s
    `isfile` guard dropped the symlink itself. pytest follows it and collects
    `tests/deep/test_deep.py`. Same result: `survived`, no note.

  Both are the same false survivor the three git rounds produced, and the claim
  of a closed class is what makes them worth recording: a name rule is not a
  smaller kind of mistake than a git rule, and "the ranker's own output is the
  input" reads as a proof only while the ranker is assumed to be pytest.

  **This document then made the same kind of mistake a third time, about this
  very paragraph.** The text here used to read "the tree has no rule to be
  narrow about" and name the remaining ceiling as only a test file outside
  `tests_dir`. Both were false the moment the walk needed a prune at all: a
  glob-pattern rule over directory basenames — pytest's own `norecursedirs`,
  matched by `_pytest_prunes` — is exactly the kind of rule this paragraph had
  just finished arguing against, added to stop `--tests-dir .` from copying
  `.venv`, `node_modules`, and `build` wholesale (see the entry below). Naming
  it as a rule is not optional bookkeeping: a discovery lane measured it
  narrower than pytest in the same shape as the two mechanisms above — a
  project whose own `pyproject.toml` drops the default `.*` entry from
  `norecursedirs` to recurse into a dot-directory still had that directory
  pruned by the hardcoded list, so a `.gitignore`d killer test living inside
  it was named by neither `git` nor the prune. The failing test in that
  reproduction lives *inside* `tests_dir`
  (`test_a_project_overridden_norecursedirs_is_honored_not_the_hardcoded_default`),
  not outside it — the opposite of what "what is left" claimed.

  The fix, `_project_norecursedirs`, reads the project's own `norecursedirs`
  from the first of `pytest.toml` (`[pytest]`), `.pytest.toml` (`[pytest]`),
  `pytest.ini` (`[pytest]`), `.pytest.ini` (`[pytest]`), `pyproject.toml`
  (`[tool.pytest.ini_options]`, falling back within the same file to the
  native `[tool.pytest]` section when `ini_options` is silent — pinned by
  `test_the_native_tool_pytest_section_is_honored`), `tox.ini` (`[pytest]`),
  `setup.cfg` (`[tool:pytest]`). **That list of seven names, in that order, is
  pytest's own `config_names` in `_pytest/config/findpaths.py::locate_config`**
  — verified by reading that function in this checkout's pinned pytest, at
  `.venv/lib/python3.12/site-packages/_pytest/config/findpaths.py`, not by
  reading this module's own comment claiming to mirror it: an earlier round
  of this document did exactly that, checked the comment rather than pytest's
  source, and missed that three of the seven standard names — `pytest.toml`,
  `.pytest.toml`, `.pytest.ini` — were absent from an only-four-name
  candidates tuple, two of them outranking every name the probe did check.
  Reproduced end to end before the fix: a project declaring `norecursedirs`
  in `pytest.toml` with a `.gitignore`d killer test in `tests/.local/` had the
  probe fall back to the built-in default, prune the killer out of the copy
  set, and report a survivor for a mutation the real tree kills — the false
  survivor this whole mechanism exists to prevent, produced by the mechanism
  itself. The fix — all seven names read, in pytest's order — is pinned by
  `test_a_pytest_toml_norecursedirs_override_is_honored`,
  `test_a_dot_pytest_toml_norecursedirs_override_is_honored`,
  `test_a_dot_pytest_ini_norecursedirs_override_is_honored`, and
  `test_pytest_toml_outranks_pyproject_toml` for the precedence claim
  specifically.

  Reading the right seven names in the right order is not the same as
  reading them the way pytest does, and getting that second part wrong is
  what produced the false survivor above. **Pytest *commits* to the first
  candidate that qualifies for it and never reads a later one — even when the
  committed file is silent on `norecursedirs`, in which case pytest's own
  built-in default applies, not whatever a later file in the list happens to
  say.** `_project_norecursedirs` now follows the same commitment rule, and
  what counts as "qualifies" differs by file kind, verified against
  `load_config_dict_from_file` in the same pinned-pytest source: the four
  *dedicated* names (`pytest.toml`, `.pytest.toml`, `pytest.ini`,
  `.pytest.ini`) commit the moment the file exists, regardless of content — a
  bare, section-free `pytest.ini` still commits pytest to itself and to
  pytest's own built-in default, never to a later file. The precise
  attribution matters here, because an earlier round of this document got it
  wrong: `test_a_silent_bare_pytest_ini_stops_the_walk_before_pyproject_toml`
  writes `[pytest]\n`, so its `pytest.ini` also satisfies the *shared*-file
  commit rule (section present), and the same test passes whether or not the
  `dedicated` guard exists at all — measured, removing both `dedicated`
  guards from `_project_norecursedirs` leaves the full suite at the same pass
  count. The test that actually discriminates the two rules is
  `test_a_bare_pytest_ini_with_no_pytest_section_still_commits`, whose
  `pytest.ini` is a genuine 0 bytes — no `[pytest]` section at all — so only
  the dedicated-by-existence rule, not the shared-by-section rule, resolves
  it; its `tox.ini`/`setup.cfg` sibling on the shared side is
  `test_a_silent_tox_ini_pytest_section_stops_the_walk_before_setup_cfg`, and
  its `pytest.toml` sibling on the dedicated side is
  `test_a_pytest_toml_with_no_pytest_table_still_commits`. The three
  *shared*, multi-tool names (`pyproject.toml`, `tox.ini`, `setup.cfg`)
  commit only once their pytest-specific table or section is genuinely
  present, not merely once the file exists (an unrelated `pyproject.toml` with
  no pytest table at all does not commit, pinned by
  `test_a_pyproject_toml_without_a_pytest_table_does_not_stop_the_walk`).
  **A *declared-but-empty* `[tool.pytest]` table does not commit either** —
  verified directly against `_pytest/config/findpaths.py`:
  `load_config_dict_from_file` builds `toml_config` by dropping the
  `ini_options` key from `[tool.pytest]`'s own contents, and separately reads
  `ini_config = tool_pytest.get("ini_options", None)`; the file commits only
  when `toml_config` is non-empty or `ini_config is not None`. A bare
  `[tool.pytest]` has no keys at all, so `toml_config == {}` and
  `ini_config is None` — neither condition holds, and the walk continues to
  the next candidate. **The asymmetric case is `[tool.pytest.ini_options]`
  bare**: that *does* commit, because its one key (`ini_options`, mapping to
  `{}`) makes `ini_config` an empty dict, which is not `None`. Content commits
  a shared file, not table presence — pinned by
  `test_a_bare_tool_pytest_table_does_not_commit_pyproject_toml`. (An earlier
  round of this document, and of this fix, stated the opposite — that a
  declared-but-empty table commits the same as a bare dedicated file — on a
  rule relayed secondhand rather than read from `findpaths.py` directly. This
  was not a paperwork error: the wrong rule was implemented in
  `_project_norecursedirs` itself, and it reproduced as a live false survivor
  the same shape this whole mechanism exists to prevent — a declared-but-empty
  `[tool.pytest]` wrongly treated as committing pytest to `pyproject.toml`
  pruned a `.gitignore`d killer test that pytest itself would have collected,
  because pytest would have kept walking past that same empty section to a
  later file. It is the same root cause as the "checked the comment rather
  than pytest's source" mistake
  recorded above, one level deeper: that time the *file list* came from a
  summary; this time one *rule inside* an otherwise-correctly-sourced file
  list did. The test that pinned the wrong rule has since been inverted and
  renamed to pin the correct one.) A parse failure on a file that already
  committed also stops the walk right there, noted rather than fallen through
  — see below. This closes what an earlier round of this document found and
  left open as an unresolved residue: a repo with a genuinely section-free
  `pytest.ini` (see the attribution note above for why that has to be
  section-free, not merely `[pytest]\n`) and `norecursedirs =
  ["custom_dir"]` in `pyproject.toml` no longer resolves the `pyproject.toml`
  value; it commits to `pytest.ini` by name alone and falls back to the
  built-in default, the same as pytest itself would.

  One further regression from the seven-name expansion was found and closed
  in the same effort: `ini_options` is a `pyproject.toml`-only concept.
  Pytest's TOML-native `pytest.toml`/`.pytest.toml` treat every key directly
  under `[pytest]` as a config value, so a `[pytest.ini_options]` table there
  is just an unknown option pytest warns about and ignores; reading it the
  same way as `pyproject.toml`'s `[tool.pytest.ini_options]` resolved a list
  pytest itself never consults — pinned by
  `test_pytest_toml_does_not_read_an_ini_options_subtable` and its
  `.pytest.toml` sibling
  `test_dot_pytest_toml_does_not_read_an_ini_options_subtable`.

  A fourth outcome shape, beyond "the project's list," "the built-in
  default," and "committed-but-unresolvable-and-noted": `pyproject.toml` can
  declare a non-`ini_options` key directly under `[tool.pytest]` *and* a
  `[tool.pytest.ini_options]` table in the same file — **and both have to be
  non-empty, not merely present, for pytest to refuse the shape.**
  `findpaths.py`'s actual condition is `if toml_config and ini_config:`, where
  `ini_config = tool_pytest.get("ini_options", None)` — a truthiness check,
  not a presence check. A *bare* `[tool.pytest.ini_options]` header
  (`ini_config == {}`, falsy) beside real `[tool.pytest]` keys does **not**
  trip pytest's `UsageError`: `toml_config` alone is truthy, so pytest takes
  the native-TOML branch and resolves the `[tool.pytest]` keys, silently
  ignoring the empty `ini_options` header. An earlier round of this fix
  checked for mere *presence* (`"ini_options" in node`) rather than
  truthiness, which raised on exactly this shape — pruning a directory
  pytest's real `norecursedirs` never touches and dropping a `.gitignore`d
  killer test the way every other false-survivor instance in this section
  has. When both *are* non-empty, pytest resolves no config at all, so there
  is no "list pytest actually applies" for either half to agree or disagree
  with; reading one of the two anyway would answer a question pytest itself
  refuses to answer. `_project_norecursedirs` treats that shape as
  committed-but-unresolvable — noted, built-in default, walk does not fall
  through to `tox.ini` or any later candidate either — pinned by
  `test_pyproject_toml_declaring_both_native_keys_and_ini_options_is_named_not_resolved`,
  with the narrower, correct predicate specifically distinguished from the
  wider, wrong one by
  `test_a_bare_ini_options_beside_native_keys_still_resolves_the_native_keys`
  — a test that exists because every test delivered with the first version of
  this fix passed under either predicate, which is exactly how the wrong one
  shipped.

  A fifth closed instance, found only after the fourth was fixed and by the
  same method — reading `findpaths.py` rather than inferring a parallel rule:
  `setup.cfg` is pytest-specific only under `[tool:pytest]`. A bare `[pytest]`
  section there is a shape pytest's own `findpaths.py` calls `fail()` on
  outright, aborting the run, rather than reading it or silently ignoring it.
  `_project_norecursedirs`'s `has_section("tool:pytest")` check used to read a
  `[pytest]`-only `setup.cfg` as though the file were not pytest's at all —
  correct by accident (the built-in default is what both the old and the
  fixed behavior return) but reached by walking off the end of the candidate
  list rather than by naming the refusal, the same silent-substitution defect
  every other instance in this section names. It is now
  committed-but-unresolvable and noted, the same as the `UsageError` shape
  above — pinned by
  `test_a_setup_cfg_pytest_section_instead_of_tool_pytest_is_named`.

  This is still not pytest's actual rootdir/inifile algorithm — it additionally
  considers `-c`/`--rootdir` and ini-file content beyond `norecursedirs`,
  and, more consequentially, it walks up from the *common ancestor of pytest's
  own invocation arguments*, not from `repo_root` — see the residue below.

  **The ceiling here is stated as a property, not as a list — this same
  section has now been written as a closed enumeration five times and been
  wrong every time; this is the sixth attempt, and the fifth attempt is the
  reason the property itself has to be restated, not just the list beneath
  it.** The fifth attempt claimed every divergence "collapses to the hardcoded
  default" — the conservative direction, safe because a project that expected
  a narrower prune would just get more coverage than it asked for. Its own
  next bullet was the counterexample: the committed-but-fell-through case
  (closed above) resolved `pyproject.toml`'s list instead of the default,
  which is not the default and not conservative — it can prune a directory
  pytest never would, dropping a real test from the copy set, which is
  exactly the false-survivor direction this whole mechanism exists to
  prevent. That specific instance is closed now, but the property claim that
  it falsified has to be corrected on its own terms, independent of the fix:
  **this function's answer can diverge from pytest's in either direction, not
  only toward the safe one.** The residue below, verified against the frozen
  source directly rather than inferred from either a code comment or a prior
  round's summary:

  * **A project that invokes pytest with an explicit `-c <path>` naming a
    config file outside these seven standard names.** This function only
    inspects well-known filenames on disk, so a non-standard invocation
    cannot be observed here — and because it keeps checking its own seven
    names regardless, it can resolve a real, valid `norecursedirs` from one of
    them (or the hardcoded default, if none exist) while pytest itself is
    reading a completely different file via `-c`. Whichever of those two
    lists is wider determines whether this diverges toward more pruning or
    less; neither direction is guaranteed.
  * **`repo_root` is not where pytest actually starts looking, and for a
    targeted run it can start below `repo_root` entirely — unpinned; no test
    constructs a config file outside `repo_root` to exercise it.** Pytest does
    not search from `repo_root`; `determine_setup` computes
    `ancestor = get_common_ancestor(invocation_dir, dirs)` from `dirs =
    get_dirs_from_args(args)` — the common ancestor of pytest's own
    command-line file arguments — and only then calls
    `locate_config(invocation_dir, [ancestor])`, whose `for base in (argpath,
    *argpath.parents)` walks *upward* from that ancestor, not from
    `invocation_dir` directly, and falls back to `invocation_dir` only when
    `args` is empty. `PytestRunner.targeted` passes `site.targeted` — test
    file paths such as `tests/test_foo.py` — as positional args with
    `cwd=self.scratch`, so a targeted run's search starts at
    `<scratch>/tests` (below the tree root this function treats as
    `repo_root`) and walks *up through* `repo_root`, not from it; only the
    full-suite run and the baseline (`full`, `baseline_full`, no extra args)
    pass no file arguments, fall back to `invocation_dir`, and search from
    `repo_root` the way this function assumes every search does. A `tests/`
    directory carrying its own `pytest.ini` is an ordinary layout, not an
    exotic one — a project laid out that way governs its targeted-run search
    and its full-suite search with two different config files, and
    `_project_norecursedirs` resolves at most one of them, correctly or not,
    for both.
  * **The out-of-scope parts of pytest's actual algorithm**: `--rootdir`
    resolution, and any ini content this function does not parse because it
    only ever extracts `norecursedirs`. A project relying on either for its
    real config is invisible to this function the same way the `-c` case is.
  * **Not a failure, and not residue, but worth naming so a reader does not
    mistake it for one**: no candidate committing at all, which is the
    ordinary case for most projects and resolves to the hardcoded default
    correctly, the same answer pytest itself gives when none of its seven
    names exist either.

  Every other divergence a previous round of this document found is now
  closed: an unresolvable TOML value shape or invalid pytest config shape
  (`pytest.ini` containing `not an ini at all`; `pyproject.toml` containing
  `[[[not toml`; `[tool.pytest]` with `ini_options` spelled as a scalar
  instead of a table, `AttributeError` on the `.get()` chain; `norecursedirs
  = true`, `TypeError` on `tuple()` — all four raise inside the read, caught
  by one deliberately broad `except`, and now stop the walk with a note
  rather than falling through — pinned by
  `test_an_unparseable_pytest_config_shape_falls_back_to_the_default` and
  `test_a_non_iterable_norecursedirs_value_falls_back_to_the_default`
  alongside the two syntax-level cases); a `[tool.pytest]`-only section
  (closed above); a dedicated file outranking `pyproject.toml` (closed
  above); a committed-but-silent file wrongly falling through to a later one
  (closed above); a declared-but-empty table read as absent (closed above);
  both `[tool.pytest]` keys and a non-empty `[tool.pytest.ini_options]`
  declared together, and the narrower truthiness-not-presence predicate that
  distinguishes that shape from a merely bare `ini_options` header (closed
  above); `setup.cfg` declaring `[pytest]` instead of `[tool:pytest]` (closed
  above); a `norecursedirs` written as a bare TOML string instead of a list
  (`norecursedirs = ".*"` used to iterate per character, `('.', '*')`, and
  `'*'` alone matches every directory basename — now `shlex.split`, the same
  way pytest's own ini-`args` handling splits a scalar, pinned by
  `test_a_toml_string_norecursedirs_is_split_like_pytest_reads_it`,
  `test_a_project_overridden_norecursedirs_is_honored_as_a_toml_string_too`,
  and — the `shlex`-versus-`str.split` distinction specifically, since neither
  of those two fixtures contains a quoted entry with a space —
  `test_the_toml_scalar_split_is_shlex_not_str` and
  `test_the_ini_scalar_split_is_shlex_not_str`); `norecursedirs = []` read as
  "no override" instead of pytest's real "prune nothing" (the function now
  checks `value is not None` rather than truthiness — verified directly
  against `_project_norecursedirs(...)`, though no test in the delivered
  suite pins this specific value the way the string and mixed-type cases are
  pinned); and a shape that survives `tuple()` but is not a tuple of strings,
  which used to become a *third* outcome neither the project's list nor the
  default — a silent crash deep inside `fnmatch.fnmatch` on the next call,
  with no note pointing back at the config that caused it
  (`norecursedirs = [".*", 2024]`, a list holding a non-string; or a TOML
  *table* under the key, whose keys survive `tuple()` as an all-strings value
  that is not what pytest reads there at all) — both now rejected inside the
  same `try` that already caught unparseable input, pinned by
  `test_a_mixed_type_norecursedirs_list_is_named_not_silently_applied` and
  `test_a_toml_table_norecursedirs_is_named_not_silently_applied`.

  Every instance in the closed list above is **announced**:
  `_project_norecursedirs` takes a `notes` parameter and, at the point a
  candidate file commits but could not be resolved, names the file and the
  exception type, then stops — it no longer falls through to a later
  candidate after a parse failure, the same commitment rule applied to the
  failure path. The two residue items above are not announced, for different
  reasons. A config living outside the seven standard names (the `-c <path>`
  case, and the out-of-scope-algorithm case with it) cannot be announced
  because the function has no way to detect either happened — silent by
  construction, not by omission. The directory-prune note three paragraphs
  below then describes whichever list this function actually resolved,
  correct or not; its wording now says exactly that — "matched the
  `norecursedirs` this probe resolved," not "pytest's `norecursedirs`" — a
  phrasing this document previously flagged as still needing a fix and
  reported as unfixed. It has since been corrected, pinned by
  `test_the_prune_note_describes_what_this_probe_resolved_not_pytests_own_list`,
  which asserts both the absence of the old phrase and the presence of the new
  one. There is also a note now for a config file this probe found but could
  not resolve, pinned separately by
  `test_an_unresolvable_config_file_is_named_when_the_default_is_used_instead`.

  That note is not deduplicated across one run. `tests_tree_files` and
  `copy_worktree` each call `_project_norecursedirs(repo_root, notes=notes)`
  independently, sharing one `notes` list but not the resolution itself —
  reproduced directly: an unreadable `pytest.ini` produces the identical
  unresolvable-config note *twice* in one run's output, once from each
  caller. Each occurrence is individually accurate; nothing is silently
  dropped, which is the property this whole section cares about. It is a
  deliberate call, not an oversight — cross-call state to deduplicate it would
  be new state this module does not otherwise carry, to polish a cosmetic
  repeat rather than fix a correctness gap.

  A test file **outside `tests_dir`** that git cannot see is the one ceiling
  item in this mechanism that is not an instance of the property above —
  `--tests-dir` is its answer, and a suite that lives outside the operator's
  declared test directory *and* is ignored by git is out of scope.

  A directory the resolved list actually prunes is **named, not silent**:
  `tests_tree_files` appends one note per run listing what the prune kept the
  walk out of (up to five directories, with a count for the rest), because a
  narrowed walk must not read as complete coverage any more than a truncated
  run does — pinned by
  `test_a_pruned_directory_under_tests_dir_is_named_in_a_note`, and the
  override case by
  `test_a_project_overridden_norecursedirs_is_honored_not_the_hardcoded_default`.

  The **ranker** stays a heuristic, deliberately. `discover_tests` matches
  pytest's two default patterns and never consults a configured `python_files`,
  so a suite it does not recognise is targeted worse and escalates more. That
  costs one full-suite run per site and never an answer, because the tree it
  escalates to is the whole test directory. Only the copy set has to be a
  superset of what pytest collects — the distinction the previous round lost by
  giving both jobs to one function. It is pruned by the same
  `_project_norecursedirs` as the copy set, so the two never disagree about
  which directories pytest would walk, but no note fires from the ranker side
  — consistent with the ranker being a speed heuristic rather than the
  correctness boundary the copy set is.

  Reproduced on this repository, 2026-09-10:
  `tests_tree_files('.', 'tests')` is 86 files;
  `tests_tree_files('.', '.')` is 787 files, with a note naming 14 pruned
  directories under `./` (`.claude`, `.claude-plugin`, `.codex-plugin`,
  `.github`, `.omc`, and 9 more — `.venv` among them, matched by the same
  `.*` pattern; most of the 14 are nested, not at the repository root — a
  task-dir `.omc` and `plugin/.claude-plugin` are two examples). No project
  config here overrides
  `norecursedirs`, so both numbers are against the hardcoded default; a
  project that does override it would see a different, and correctly honored,
  set. The 787-vs-86 gap is what the prune keeps out of the `--tests-dir .`
  copy set, not what it lets through.

  **The prune is not confined to the `must_include` walk, and crediting only
  one mechanism for the historical copy-set blowup would misstate the fix —
  this paragraph itself needed a correction here for exactly that reason.**
  `copy_worktree` copies three things: the `must_include` superset
  `tests_tree_files` computes (documented above); every *tracked* file in the
  whole repository, on every run, which `norecursedirs` never touches, because
  a tracked file under a `build/`-named directory is still something someone
  committed and git already vouches for it; and every *untracked,
  not-`.gitignore`d* file in the whole repository — and this third route is
  now also pruned by `_project_norecursedirs`, not left to the git query
  alone. Verified directly: an untracked `.venv/lib/foo.py` placed in a fresh
  repository with no `.gitignore` entry for `.venv` at all is excluded from
  `copy_worktree`'s output, with a note naming the count and one example path
  under the pruned directory. This exact route is pinned by
  `test_a_tests_dir_of_dot_does_not_copy_directories_pytest_never_collects`,
  whose docstring says so directly: it writes `.venv/lib/site.py`,
  `.hidden/also_skipped.py`, `node_modules/pkg/index.js`, `build/out.py`, and
  `dist/out.py` as untracked files with no `.gitignore` covering any of them,
  so they can only reach `copy_worktree` through `git ls-files -o
  --exclude-standard`, and asserts none of them is in the scratch copy.

  `.omc` (797 files, `find .omc -type f | wc -l`) was already `.gitignore`d
  before this round (`.gitignore:1`), so the untracked-file route already
  excluded it regardless of the prune; the prune is what additionally kept it
  out of the `must_include` walk under `--tests-dir .`. `.venv` was not
  `.gitignore`d before this round, so its 587 non-bytecode files (`git
  ls-files -o | grep '^\.venv/' | grep -vc '\.pyc$'`, against a checkout with
  the new `.gitignore` line reverted) used to reach the untracked-file route
  on every probe run — about 11 MB by disk usage (`git ls-files -o -z | grep
  -zv '\.pyc$' | grep -z '^\.venv/' | du -ch --files0-from=-`). That route is
  now closed **twice over, redundantly**: by this diff's `.gitignore:42` line
  (`git ls-files -o --exclude-standard | grep -c '^\.venv/'` is `0` with it in
  place) and, independently, by the untracked-route prune verified above,
  since `.venv` itself matches the `.*` pattern the same way `.omc` does. An
  earlier draft of this paragraph said the prune "does not touch [the
  git-derived route] at all" and credited the `.gitignore` line alone; that
  was true of the code at the time it was written and is not true of the
  frozen code checked here — the untracked-route prune was added later in the
  same effort. Whichever mechanism closed it first, both now hold, and
  neither is solely responsible for the recovery — the same overstatement
  this section has now made about its own numbers more than once.

  A path the copy cannot place is **named, never dropped in silence** — AC-3's
  rule about truncation applied to the copy set, and it covers the git-derived
  paths too, not only `must_include`. "Cannot place" is resolved after the copy
  rather than during it: a path with nothing at its destination (a broken
  symlink named `test_*.py`, an indexed file deleted from the working tree, a
  submodule gitlink) is recorded, while a directory symlink is not — it is not a
  regular file either, but the walk placed its contents under the name pytest
  reads them by, and reporting it would be noise. On this repository the rule
  produces exactly one note per run — `gstack`, a submodule gitlink with nothing
  checked out — which is a true statement about the scratch tree and not an
  alarm. A path that does not resolve
  *under* the scratch directory is **refused instead**: `--tests-dir
  ../outside/tests` makes the walk return `../outside/tests/test_out.py`, and
  joining that onto `dest` writes above the scratch tree, where `shutil.rmtree`
  never reaches it and enough `..` reaches the real working tree that §4 forbids
  writing to at all. Recording is the right answer for a file that could not be
  copied; for a copy to a directory the operator never named it is too late,
  because the write has happened. The probe stops and says why.

  Following symlinks is what makes the walk able to loop (`tests/current -> .`
  is an ordinary layout), so the walk prunes a directory that resolves to one of
  **its own ancestors** — the loop condition itself. It is deliberately not a
  visited set: keyed on the resolved directory, whichever path arrives *second*
  is pruned, including the canonical one. With `tests/pkg/test_x.py` and
  `tests/link -> pkg`, `tests/link` sorts first and `tests/pkg/test_x.py` was
  never yielded, so the claim above was not exception-free. pytest collects
  both, so both are yielded; an aliased subtree is therefore copied once per
  alias, which costs scratch-tree bytes and never a missing test.
  `--exclude-standard` still keeps `.gitignore`d artifacts *outside* `tests_dir`
  out, so an ignored build tree is not copied to `/tmp` every run.
- **Line endings are assumed normalised.** Sources reach the module through
  universal-newline reads (`open(..., encoding="utf-8")`, `git show` with
  `text=True`), so `\n` is the whole line rule and a lone `\r` in a file read by
  some other route would be counted as no line break. `_char_offset` likewise
  decodes a byte prefix of the line, which raises if `ast` ever reported a
  column *inside* a character — it does not, at node boundaries. Both are
  bounded: a crash, not a wrong span.
- **A `--rev` range is probed only where the checkout still matches it.** §1;
  the files that moved on are named in the report rather than mutated.
- **A UTF-8 BOM reads as a parse failure, silently.** `source_at` opens files
  with `encoding="utf-8"`, not `utf-8-sig`, so a leading BOM survives into the
  string `ast.parse` receives; CPython's own compiler accepts a BOM and
  `ast.parse` does not, so a file that runs fine lands in the does-not-parse
  branch in §3 and contributes zero sites, named only by `mutation_sites`'s
  generic parse note. A reader checking whether a file was
  covered has no signal that the cause was specifically a BOM rather than
  invalid syntax.
- **Cost is dominated by process startup, not by the mutations.** Measured on
  the loaded host of 2026-09-10, a single `import pytest` cost 2.4s wall against
  0.1s CPU. Any budget claim is a claim about the host it was measured on.

## Enforcement

`tests/test_mutation_probe.py`. The mutations that turn each test red:

`contract_lint.check_doc_test_references` does not backstop this table: it
resolves each `test_...` id a doc names against the tests this repository
defines, so it catches a doc that names a test that no longer exists. It never
checks the reverse — that a delivered test is named by some doc — so a row
missing from this table, as the three below (the UTF-8 and does-not-parse
rows) were until this pass, is invisible to it. This table is the only check
on that direction, and it is manual.

| mutation | red 가 되는 테스트 |
|---|---|
| select sites from the whole file instead of the changed lines | `test_sites_come_from_the_changed_lines_and_nothing_else` |
| **no test** — deleting the `/dev/null` check survives; see below | `test_a_deleted_file_contributes_no_sites` (passes either way) |
| mutate string constants too | `test_a_comment_or_string_only_change_yields_no_sites` |
| treat changed test files as mutation subjects | `test_a_changed_test_file_is_not_a_mutation_subject` |
| ignore the path filter | `test_a_path_filter_keeps_a_changed_file_out_of_the_run` |
| rank on a symbol that names most of the suite | `test_a_symbol_naming_most_of_the_suite_is_not_the_targeted_signal` |
| hand every changed test file to every site | `test_the_targeted_selection_prefers_tests_naming_the_changed_symbol` |
| **drop the escalation and report a targeted survivor as a survivor** | `test_a_mutation_only_a_distant_test_kills_is_reported_killed_not_survived` |
| escalate a mutation the targeted selection already killed | `test_the_full_suite_is_not_run_for_a_mutation_the_subset_killed` |
| re-measure the clean full-suite baseline per site | `test_the_clean_full_suite_baseline_is_measured_once` |
| call a survivor a survivor when the tree is red without any mutation | `test_a_full_suite_that_is_red_without_any_mutation_yields_no_survivor_claim` |
| read a red targeted selection as a kill | `test_a_red_targeted_selection_escalates_instead_of_reading_as_killed` |
| silence the truncation notice | `test_a_site_list_over_budget_names_what_it_did_not_attempt`, `test_a_wall_clock_stop_names_what_it_did_not_attempt` |
| announce truncation on a complete run | `test_a_complete_run_claims_nothing_about_incompleteness` |
| apply the mutation to the probed tree instead of the copy | `test_the_probed_tree_is_never_written_to` |
| drop the environment on the way to the pytest child, so a `__pycache__` hides a size-preserving mutation | `test_a_size_preserving_mutation_is_not_hidden_by_stale_bytecode` |
| return an empty line-to-symbol map, or spill it one line past the function | `test_a_site_names_the_function_it_sits_in` |
| hide the diff's own test file behind a `--paths` filter | `test_a_path_filter_does_not_hide_the_test_file_the_diff_wrote` |
| force a boolean `if` test to a constant instead of dropping operands | `test_a_guard_branch_no_test_reaches_is_reported_as_a_survivor` |
| widen site selection past the changed lines, so the ceiling above quietly stops holding | `test_the_counts_slot_defect_is_out_of_reach_because_its_line_is_not_in_the_diff` |
| build the scratch tree from `git ls-files` alone, so the test the change wrote but nobody staged is missing from the targeted run *and* from the escalation | `test_a_test_file_written_but_not_yet_staged_is_in_the_probed_tree` |
| copy `.gitignore`d non-test artifacts into the scratch tree along with the untracked files | `test_a_test_file_written_but_not_yet_staged_is_in_the_probed_tree` |
| exclude a `.gitignore`d test file from the copy, so the ranker names a path the escalation cannot run | `test_a_gitignored_test_file_is_in_the_probed_tree` |
| drop `must_include` from the copy set, breaking the invariant for every git-invisibility mechanism at once | `test_every_test_the_ranker_can_name_is_in_the_probed_tree` |
| build the copy set from a *view* of the test tree — the ranker's output, the diff — instead of the tree itself | `test_the_scratch_copy_is_given_the_whole_test_tree_not_a_view_of_it` |
| match only `test_*.py`, so a `.gitignore`d `boundary_test.py` is in neither the copy set nor the ranker | `test_a_test_file_pytest_collects_under_its_other_default_name_is_in_the_probed_tree`, `test_the_scratch_copy_is_given_the_whole_test_tree_not_a_view_of_it` |
| stop following symlinks, so a suite reached through a directory symlink is missing from the copy | `test_a_test_directory_reached_through_a_symlink_is_in_the_probed_tree` |
| follow symlinks without pruning revisited directories, so `tests/current -> .` never returns | `test_a_symlink_loop_under_the_tests_dir_terminates` |
| copy the probed repository's own `__pycache__`, hiding a size-preserving mutation behind bytecode the runner never wrote | `test_the_scratch_copy_never_receives_bytecode` |
| discard git's exit status, so an unreadable `--rev` range reads as a complete run over an empty one | `test_an_input_the_probe_cannot_read_is_refused_not_reported_as_empty` |
| run with no discovered test file — every site escalating — and not say so | `test_a_tests_dir_that_matches_nothing_says_so` |
| read a `--rev` range's line numbers against working-tree source, offering mutations of live code under an audited commit's label | `test_a_range_that_does_not_describe_the_checkout_offers_no_sites` |
| skip a `must_include` path that is not a regular file without saying so | `test_a_must_include_path_that_is_not_a_regular_file_is_recorded` |
| copy a `must_include` path that resolves outside the scratch tree instead of refusing, so `--tests-dir ../outside/tests` writes above it and `rmtree` leaves it behind | `test_a_must_include_path_outside_the_scratch_tree_is_refused` |
| count columns in characters against `ast`'s UTF-8 byte columns, so a multibyte literal on the line drops every site on it | `test_a_multibyte_literal_on_the_line_does_not_drop_its_sites` |
| shift a span so the mutation lands on another occurrence, or past the end of the line, under the original site's label | `test_a_mutation_lands_on_the_span_its_label_names` |
| split lines with `str.splitlines`, so a form feed moves every following line's offsets | `test_a_form_feed_does_not_shift_the_lines_under_the_spans` |
| drop a candidate the `compile()` guard declined without naming it | `test_a_candidate_the_compile_guard_declines_is_named` |
| prune the walk by visited resolved directory, so an alias hides the canonical path behind it | `test_an_aliased_directory_and_its_canonical_path_are_both_in_the_tree` |
| build the changed-test prefix from an unnormalised `--tests-dir`, so `./tests` makes the diff's test files mutation subjects | `test_a_tests_dir_written_with_a_leading_dot_still_excludes_its_own_tests` |
| read a non-UTF-8 source as if it decoded, or drop it silently instead of naming it | `test_a_source_that_is_not_utf8_is_named_and_does_not_lose_the_rest` |
| let one non-UTF-8 file in a diff lose the rest of the diff's sites | `test_a_changed_line_that_is_not_utf8_does_not_lose_the_rest_of_the_diff` |
| offer zero sites for a file that does not parse without naming it | `test_a_file_that_does_not_parse_is_named_not_silently_skipped` |
| anchor the header parse on a preceding `--- ` line instead of `diff --git `, so a colluding removed/added line pair forges a header | `test_a_colluding_removed_and_added_line_pair_does_not_forge_a_header` |
| reattribute a hunk to a source line that merely starts `++ `, absent a colluding removed line | `test_an_added_line_shaped_like_a_diff_header_does_not_reattribute_hunks` |
| slice a `+++ ` target blindly instead of checking the `b/` prefix, so a non-ASCII filename `core.quotePath` quoted and octal-escaped reads as garbage | `test_a_non_ascii_filename_is_not_hidden_by_default_quote_path` |
| omit the explicit `--src-prefix=a/ --dst-prefix=b/`, so an operator's `diff.noprefix=true` strips two real characters off every path | `test_diff_noprefix_config_does_not_break_the_parse` |
| drop the note when a `+++ ` target does not carry the expected `b/` prefix, instead of naming it | `test_a_target_without_the_expected_prefix_is_named_not_sliced_blindly` |
| let `source_at` open a path that resolves outside the repository root instead of refusing it | `test_source_at_refuses_a_path_that_escapes_the_repo_root` |
| let `_with_mutation` write a relative site path that resolves outside the scratch tree, trusting an earlier read guard instead of checking again at the write site | `test_with_mutation_refuses_a_site_path_that_escapes_the_scratch_tree` |
| let `_with_mutation` write an absolute site path | `test_with_mutation_refuses_an_absolute_site_path` |
| move the truncating `open(path, "w", ...)` outside the `try`/`finally` that restores the original, so a failure computing the mutated text leaves the scratch file holding zero bytes | `test_a_failure_while_writing_the_mutation_does_not_leave_the_file_truncated` |
| refuse a directory symlink under `tests_dir` whose target resolves outside `repo_root`, breaking the legitimate cross-repo shared-suite layout `walk_tree` exists to serve | `test_a_test_tree_symlink_that_resolves_outside_the_repo_is_still_copied` |
| offer a site for a changed source module excluded from mutation subjects under `--tests-dir .` because its name matches pytest's test-collection convention, without naming the exclusion | `test_a_changed_source_module_excluded_under_dot_as_a_test_is_named` |
| fire the exclusion note for an ordinary changed test file living under `tests_dir` itself, turning the routine case into noise | `test_the_changed_tests_note_only_fires_outside_tests_dir` |
| split a scalar TOML `norecursedirs` with `str.split` instead of `shlex.split`, breaking a quoted entry with a space | `test_the_toml_scalar_split_is_shlex_not_str` |
| split a scalar ini `norecursedirs` with `str.split` instead of `shlex.split`, breaking a quoted entry with a space | `test_the_ini_scalar_split_is_shlex_not_str` |
| drop a changed path from `collect_sites` with no note when the checkout does not have it at all (distinct from a content mismatch) | `test_a_source_missing_from_the_checkout_is_named_not_dropped_in_silence` |
| offer a site for a changed line whose diff contributes nothing mutable | `test_a_changed_file_with_no_mutable_line_yields_no_sites` |
| call a mutation killed when neither the targeted selection nor the full suite noticed it | `test_a_mutation_no_suite_notices_is_reported_as_a_survivor` |
| misread the walk root itself (`<repo>/.`) as its own parent under `--tests-dir .`, pruning the whole walk at the first iteration | `test_a_tests_dir_of_dot_is_not_mistaken_for_a_symlink_cycle` |
| weaken real cycle detection while fixing the `--tests-dir .` false-positive, so a genuine ancestor cycle no longer terminates | `test_a_genuine_cycle_through_an_ancestor_still_terminates` |
| miss a `.gitignore`d test file matching pytest's other default `python_files` pattern at the repository root under `--tests-dir .` | `test_a_root_level_gitignored_test_file_is_found_with_tests_dir_dot` |
| copy directories pytest's own `norecursedirs` would never let it collect from (`.venv`, `node_modules`, `build`, ...), so `--tests-dir .` blows up the copy set with files no superset invariant required | `test_a_tests_dir_of_dot_does_not_copy_directories_pytest_never_collects` |
| let the changed-test exclusion still be reachable by prefix under `--tests-dir .`, instead of the set-membership check that replaced it entirely | `test_a_tests_dir_of_dot_does_not_make_the_diffs_test_file_a_subject` |
| let the shared-file commit rule (section presence) substitute for the dedicated-file commit rule (file existence), so a genuinely section-free `pytest.ini` is misread as not committing | `test_a_bare_pytest_ini_with_no_pytest_section_still_commits` |
| read a declared-but-empty `[tool.pytest]` table as committing `pyproject.toml`, instead of continuing to `tox.ini` the way pytest's own content-not-presence rule requires | `test_a_bare_tool_pytest_table_does_not_commit_pyproject_toml` |
| resolve one half of a `pyproject.toml` that declares both non-empty `[tool.pytest]` keys and a non-empty `[tool.pytest.ini_options]` table, instead of refusing the shape pytest itself raises `UsageError` on | `test_pyproject_toml_declaring_both_native_keys_and_ini_options_is_named_not_resolved` |
| widen the `UsageError` predicate from truthiness to mere presence, so a bare `[tool.pytest.ini_options]` header beside real `[tool.pytest]` keys is wrongly refused instead of resolved | `test_a_bare_ini_options_beside_native_keys_still_resolves_the_native_keys` |
| walk off the end of the candidate list for a `setup.cfg` declaring `[pytest]` instead of `[tool:pytest]`, reaching the built-in default by accident and without a note | `test_a_setup_cfg_pytest_section_instead_of_tool_pytest_is_named` |

One row above is a survivor, kept as one. Deleting the `+++ /dev/null` check
changes no observable behaviour, because `git diff -U0` emits `@@ -1,2 +0,0 @@`
for a removed file and a hunk that adds no lines contributes no sites — the
deleted-file property holds through a second mechanism. The check stays because
the garbage key `target[2:]` would otherwise produce is prevented only by that
accident of the diff format, which this module does not otherwise depend on.
This is §5 in miniature: the probe reported it, a human read it, and the answer
was "correct as written". Recorded rather than chased, and recorded rather than
claimed as covered.

`test_a_guard_branch_no_test_reaches_is_reported_as_a_survivor` is the
end-to-end case: a real diff, a real scratch copy, a real pytest, and a real
escalation. A survivor claim that has not been through the full suite is
exactly what §2 forbids, so the test that proves the tool finds a survivor is
not allowed to fake that path. (This paragraph said "the last row" until rows
were appended past it — a positional reference to a table that grows.)
