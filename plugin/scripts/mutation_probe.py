#!/usr/bin/env python3
"""Mutate the lines a diff changed; report the mutations no test noticed.

Why scoped to the diff: measured on this repository, the full suite is ~17s and
`plugin/scripts/_lib.py` alone is 4200+ lines. Whole-module mutation is minutes
per file; changed-lines scoping is not an optimisation here, it is the only
shape that fits inside a task loop. See
`doc/harness/REQ__mutation-scope-follows-the-diff.md`.

Two properties are load-bearing:

  * A mutation that survives the *targeted* test selection is re-run against the
    full suite before it is reported as a survivor. A narrow selection produces
    false survivors, and a false survivor is noise that gets the tool switched
    off. Cheap-and-wrong towards "killed" is acceptable; towards "survived" is
    not.
  * The run is bounded and says what it did not attempt. A truncated run must
    never read as complete coverage.

Nothing here blocks anything. Survivors are frequently deliberate — unreachable
defensive branches, equivalent arrangements — so this reports and a human or a
review lens decides.

Usage:
    python3 plugin/scripts/mutation_probe.py            # working tree vs HEAD
    python3 plugin/scripts/mutation_probe.py --rev A..B
    python3 plugin/scripts/mutation_probe.py --sites-only

Stdlib only, like `contract_lint.py`. Exit code is 0 unless the probe itself
could not run.
"""
from __future__ import annotations

import argparse
import ast
import configparser
import fnmatch
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
from dataclasses import dataclass, field

DEFAULT_MAX_SITES = 40
DEFAULT_BUDGET_SECS = 300.0
DEFAULT_MAX_TEST_FILES = 6
GENERIC_SYMBOL_SHARE = 0.25
DEFAULT_RUN_TIMEOUT = 300.0

# Distinguishes "this file could not be read" from "the checkout does not
# describe the range end". Both yield no source, but only one of them is
# about the revision, and the caller's note must not claim the wrong reason.
_UNREADABLE = object()

# A path the diff named that does not stay under `repo_root` — absolute, or
# escaping through `..`. Refused rather than read, and distinct from
# `_UNREADABLE` because the reason is containment, not encoding.
_OUTSIDE_REPO = object()


def _contained(root, rel):
    """True when `rel`, joined onto `root`, resolves to a path under `root`."""
    if os.path.isabs(rel):
        return False
    root = os.path.realpath(root)
    target = os.path.realpath(os.path.join(root, rel))
    return os.path.commonpath([root, target]) == root

# One mutation per operator, chosen for the two failure classes this exists for:
# a guard branch no test reaches (forced if/while tests) and a positional or
# boundary read no test discriminates (comparison, boolean, constant).
_COMPARE_SWAP = {
    ast.Eq: ast.NotEq, ast.NotEq: ast.Eq,
    ast.Lt: ast.GtE, ast.GtE: ast.Lt,
    ast.Gt: ast.LtE, ast.LtE: ast.Gt,
    ast.In: ast.NotIn, ast.NotIn: ast.In,
    ast.Is: ast.IsNot, ast.IsNot: ast.Is,
}


# --------------------------------------------------------------------------
# changed lines
# --------------------------------------------------------------------------

_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def _git(repo_root, *args):
    # `surrogateescape`, not strict: a diff whose *changed line* carries a
    # non-UTF-8 byte — the normal case for a new latin-1 file — made this decode
    # raise inside `subprocess.run`, before any per-file guard could name the
    # path, and the whole run exited having lost every other file in the diff.
    # Not everything this module parses out of git is ASCII: `git ls-files -z`
    # prints a non-ASCII path's raw bytes verbatim, and even a `+++` target is
    # ASCII only because `core.quotePath` escapes it — escaping this module has
    # to defeat, not rely on. Tolerating undecodable bytes here is what lets a
    # per-file guard downstream see the bytes and name the path, instead of the
    # whole run dying in this call before any guard runs.
    return subprocess.run(
        ["git", *args], cwd=repo_root, capture_output=True, text=True,
        errors="surrogateescape", check=False,
    )


def changed_python_lines(repo_root, rev=None, notes=None):
    """{repo-relative path: {line numbers added on the new side}}.

    Deleted files never appear (nothing to mutate). A new file appears with all
    of its lines. A hunk that only removes lines contributes none.
    """
    # `-c core.quotePath=false` and explicit prefixes make the header parse
    # independent of the operator's gitconfig. Without the first, a non-ASCII
    # path (the default `core.quotePath=true`) is quoted and octal-escaped —
    # `+++ "b/caf\303\251.py"` — and `target[2:]` takes the quote, not a path.
    # Without the second, `diff.noprefix=true` strips the `a/`/`b/` prefix
    # `target[2:]` assumes, and every path in the run loses its first two
    # characters. Command-line prefixes override the config either way.
    args = ["diff", "-U0", "--no-color", "--no-ext-diff",
            "--src-prefix=a/", "--dst-prefix=b/"]
    args += rev.split() if rev else ["HEAD"]
    done = _git(repo_root, "-c", "core.quotePath=false", *args, "--", "*.py")
    if done.returncode != 0:
        # A run that could not read its input must not read as a complete run
        # over an empty input. `git diff bad-rev..worse` exits 128, and an empty
        # stdout then renders as "no mutable changed lines" — a typo and an
        # audited range with nothing in it become the same output. AC-3's rule
        # about a bounded run reading as complete coverage, one level up.
        detail = (done.stderr or done.stdout).strip().splitlines()
        raise ValueError(
            f"git diff {' '.join(args[4:])} failed in {repo_root}"
            + (f": {detail[0]}" if detail else "")
        )
    out = done.stdout
    changed: dict[str, set[int]] = {}
    path = None
    # Anchored on `diff --git `, not on a preceding `--- ` line. A `--- `
    # anchor is itself forgeable: a REMOVED source line whose own text starts
    # `-- ` renders as `--- ...` (git's `-` prefix plus the line's own two
    # dashes), and under `-U0` a one-line replacement places that removed line
    # immediately before an added line whose text starts `++ ` — which
    # renders `+++ ...` — so the colluding pair is indistinguishable from a
    # real header to anything anchored one line back. `diff --git ` at column
    # 0 is the one line content cannot forge: every content line in a `-U0`
    # diff carries a `+` or `-` prefix, so only a true section boundary can
    # ever start a raw output line with `diff --git `. The `--- `/`+++ ` pair
    # is honored only once, immediately inside that section, before any hunk
    # line has been seen for it.
    in_file_header = False
    seen_minus = False
    for line in out.splitlines():
        if line.startswith("diff --git "):
            in_file_header = True
            seen_minus = False
            path = None  # a file with no `+++ ` (e.g. a mode-only change)
            continue
        if in_file_header and not seen_minus and line.startswith("--- "):
            seen_minus = True
            continue
        if in_file_header and seen_minus and line.startswith("+++ "):
            in_file_header = False
            seen_minus = False
            target = line[4:].strip()
            if target == "/dev/null":
                path = None
            elif target.startswith("b/"):
                path = target[2:]
            else:
                # The prefix this parser depends on is missing — a gitconfig
                # this call did not anticipate, not a target to slice blindly.
                path = None
                if notes is not None:
                    notes.append(
                        f"{target}: diff header did not carry the expected "
                        "'b/' prefix; not probed"
                    )
            continue
        if path is None or not line.startswith("@@"):
            continue
        m = _HUNK.match(line)
        if not m:
            continue
        start = int(m.group(1))
        count = 1 if m.group(2) is None else int(m.group(2))
        if count:
            changed.setdefault(path, set()).update(range(start, start + count))
    return {p: lines for p, lines in changed.items() if lines}


def range_end(rev):
    """Revision the diff's *new-side* line numbers describe, or None.

    None means the working tree, which is what `--rev A` and the default both
    diff against. For anything else the changed line numbers belong to a commit,
    while the source this module reads belongs to the checkout — two coordinate
    systems, and mixing them offers mutations on unrelated live code under the
    label of the audited range.
    """
    if not rev:
        return None
    parts = rev.split()
    if len(parts) > 1:
        return parts[-1]  # `git diff A B`
    for sep in ("...", ".."):
        if sep in parts[0]:
            return parts[0].split(sep, 1)[1] or "HEAD"
    return None  # `git diff A` — the new side is the working tree


def source_at(repo_root, path, end, notes=None):
    """Subject source, `None`, or `_UNREADABLE`.

    `None` means the checkout cannot describe `end`: the working tree is the
    only content this probe can mutate and run, so a file whose checked-out
    bytes differ from the range end is not probed at all. That covers the
    historical range and the dirty-file case with one check.

    `_UNREADABLE` means the file itself could not be read as UTF-8, which is a
    different reason and gets a different note — the caller must not report a
    decode failure as a revision mismatch.

    `_OUTSIDE_REPO` means `path` does not stay under `repo_root` — refused
    before any read is attempted, since the read itself is the thing this
    guards against.
    """
    if not _contained(repo_root, path):
        if notes is not None:
            notes.append(
                f"{path}: not probed — this path does not stay under the "
                "repository root; refused rather than read"
            )
        return _OUTSIDE_REPO
    try:
        with open(os.path.join(repo_root, path), encoding="utf-8") as fh:
            source = fh.read()
    except OSError:
        return None
    except UnicodeDecodeError as exc:
        # `UnicodeDecodeError` is a `ValueError`, not an `OSError`, so widening
        # the clause above would not have caught it: a legal source carrying
        # `# -*- coding: latin-1 -*-` aborted the whole run and named no path.
        # The same rule as the parse note and the compile note, one layer up —
        # a file the probe declined to read is named, and the rest of the diff
        # is still probed rather than lost with it.
        if notes is not None:
            notes.append(
                f"{path}: not probed — it is not valid UTF-8 "
                f"({exc.reason} at byte {exc.start}); this probe reads sources "
                "as UTF-8, so it cannot offer sites for it"
            )
        return _UNREADABLE
    if end is None:
        return source
    shown = _git(repo_root, "show", f"{end}:{path}")
    if shown.returncode != 0 or shown.stdout != source:
        return None
    return source


# --------------------------------------------------------------------------
# sites
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Site:
    path: str
    lineno: int
    symbol: str
    label: str
    start: int
    end: int
    replacement: str
    targeted: tuple[str, ...] = ()

    def apply(self, source):
        return source[:self.start] + self.replacement + source[self.end:]

    def describe(self):
        where = f"{self.path}:{self.lineno}"
        return f"{where}  {self.symbol}  [{self.label}]"


def _line_index(source):
    r"""`(char offset of each line start, line text)`, in `ast`'s coordinates.

    Two disagreements with `ast` live here, and both of them move a span onto
    text the site does not name:

      * `str.splitlines` breaks on form feed, `\x0b`, `\x85`, `\u2028` and more;
        CPython's tokenizer breaks on `\n` alone. A `\f` between two statements
        shifted every line after it by one. (`ast` has the same problem and
        keeps `_splitlines_no_ff` for it.) Sources reach this module through
        universal-newline reads, so `\n` is the whole rule.
      * columns are counted here in characters and reported by `ast` in **UTF-8
        bytes** — see `_char_offset`.
    """
    lines = source.split("\n")
    starts, total = [], 0
    for line in lines:
        starts.append(total)
        total += len(line) + 1  # the newline `split` consumed
    return starts, lines


def _char_offset(index, lineno, col):
    """One `ast` (line, column) as an offset into the source *string*.

    `col_offset` and `end_col_offset` are UTF-8 byte offsets. Every offset
    downstream — `Site.start`/`Site.end`, `Site.apply`, the `compile()` guard,
    the mutated file the runner writes — is a character offset into the same
    `str`, so the two unit systems are reconciled here, once, at the only
    boundary where `ast`'s numbers enter. Converting per line rather than
    carrying bytes through `Site` keeps one coordinate system everywhere else;
    this is what `ast.get_source_segment` does for the same reason.

    Unconverted, a line with a multibyte literal *before* the node shifted every
    span on it: `if msg == "차단됨" and count > 0:` offered 0 sites where its
    ASCII twin offers 6 (each mutant failed `compile()` and was dropped), and a
    shift that still compiled applied the mutation to a different occurrence, or
    past the end of the line, under the original site's label.
    """
    starts, lines = index
    return starts[lineno - 1] + len(lines[lineno - 1].encode()[:col].decode())


def _span(index, node):
    return (_char_offset(index, node.lineno, node.col_offset),
            _char_offset(index, node.end_lineno, node.end_col_offset))


def _symbol_map(tree):
    """line number -> enclosing `def`/`class` name (innermost)."""
    names: dict[int, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for line in range(node.lineno, (node.end_lineno or node.lineno) + 1):
            names[line] = node.name
    return names


def _candidates(tree, lines):
    """(node, label, replacement-node-or-text) for nodes starting on `lines`."""
    # A non-boolean if/while test owns its position: forcing it to a constant
    # already probes both branches, so the same expression is not also mutated
    # as a comparison or a constant. Without this, `if a == b:` produces three
    # near-duplicate sites and eats the budget.
    #
    # A boolean test is the exception, and deliberately so: the recurring
    # failure this exists for is a *conjunct* no test reaches
    # (`status == "open" and resumes(...)`, where every fixture passed an open
    # task). Forcing the whole test flips both operands at once and dies on any
    # test that exercises the guard at all, so it cannot see that. Dropping one
    # operand at a time can.
    forced = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.If, ast.While)) or node.test.lineno not in lines:
            continue
        if isinstance(node.test, ast.BoolOp):
            continue
        forced.add(id(node.test))
        for literal in ("True", "False"):
            yield node.test, f"if-test -> {literal}", literal
    for node in ast.walk(tree):
        if getattr(node, "lineno", None) not in lines or id(node) in forced:
            continue
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            swap = _COMPARE_SWAP.get(type(node.ops[0]))
            if swap is not None:
                mutated = ast.Compare(left=node.left, ops=[swap()],
                                      comparators=node.comparators)
                yield node, f"{type(node.ops[0]).__name__} -> {swap.__name__}", mutated
        elif isinstance(node, ast.BoolOp):
            swap = ast.Or if isinstance(node.op, ast.And) else ast.And
            yield node, f"{type(node.op).__name__} -> {swap.__name__}", ast.BoolOp(
                op=swap(), values=node.values)
            for dropped in range(len(node.values)):
                rest = [v for i, v in enumerate(node.values) if i != dropped]
                mutated = rest[0] if len(rest) == 1 else ast.BoolOp(op=node.op, values=rest)
                yield node, f"drop operand {dropped + 1} of {type(node.op).__name__}", mutated
        elif isinstance(node, ast.Constant):
            # Strings and docstrings are deliberately not mutated: a changed
            # comment or message is not executable behaviour, and mutating it
            # manufactures survivors nobody should act on.
            if node.value is True or node.value is False:
                yield node, f"{node.value} -> {not node.value}", str(not node.value)
            elif isinstance(node.value, int) and not isinstance(node.value, bool):
                yield node, f"{node.value} -> {node.value + 1}", str(node.value + 1)


def mutation_sites(path, source, lines, notes=None):
    """Mutation sites for the changed `lines` of one file, in file order."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        # Same rule as the compile guard below, one level up: a file the probe
        # declined to read must not read as a file it covered. A UTF-8 BOM
        # reaches this branch on a file CPython itself compiles happily, since
        # `source_at` opens with `utf-8` rather than `utf-8-sig`, so the whole
        # file contributes zero sites; name that cause in its note.
        if notes is not None:
            if source.startswith("\ufeff"):
                notes.append(
                    f"{path}: not parsed, so no site was offered for it — "
                    "leading UTF-8 BOM; save as UTF-8 without BOM"
                )
            else:
                notes.append(
                    f"{path}: not parsed, so no site was offered for it — "
                    f"{exc.msg} (line {exc.lineno})"
                )
        return []
    index = _line_index(source)
    symbols = _symbol_map(tree)
    sites = []
    declined = []
    for node, label, mutated in _candidates(tree, lines):
        start, end = _span(index, node)
        text = mutated if isinstance(mutated, str) else ast.unparse(mutated)
        if text == source[start:end]:
            continue
        candidate = Site(path=path, lineno=node.lineno,
                         symbol=symbols.get(node.lineno, "<module>"),
                         label=label, start=start, end=end, replacement=text)
        try:
            compile(candidate.apply(source), path, "exec")
        except SyntaxError:
            # An `ast.unparse` round-trip that lost context. Rare, and never a
            # survivor — but a candidate the probe declined to attempt is
            # exactly what AC-3 says must not be silent: dropping a whole line's
            # worth of them (which the byte/character offset bug did) made a
            # partial run read as complete coverage of that line, and
            # `Report.render` only speaks about `not_attempted`.
            declined.append(f"{candidate.lineno} [{label}]")
            continue
        sites.append(candidate)
    if declined and notes is not None:
        notes.append(
            f"{path}: {len(declined)} candidate mutation(s) not attempted — the "
            f"mutated source did not compile ({', '.join(declined)})"
        )
    return sorted(sites, key=lambda s: (s.lineno, s.start, s.label))


# --------------------------------------------------------------------------
# targeted test selection
# --------------------------------------------------------------------------

# pytest's own default `python_files`. The ranker matching a narrower rule than
# this one is what produced mechanism four of the copy-set defect.
_TEST_NAME = re.compile(r"^(test_.*|.*_test)\.py$")
# `__pycache__` above all: copying the source tree's bytecode into the scratch
# tree hands the runner a stale `.pyc` for the file it is about to mutate,
# which is the false survivor §4 of the REQ exists to forbid.
_SKIP_DIRS = {"__pycache__", ".git", ".pytest_cache"}


def _is_bytecode(rel):
    return rel.endswith((".pyc", ".pyo")) or "__pycache__" in rel.split(os.sep)


# pytest's own *built-in default* `norecursedirs` (see `_pytest.config`):
# pytest never walks into a directory whose basename matches one of these
# glob patterns. Used only as the fallback when `_project_norecursedirs`
# finds no project config that overrides it.
_NORECURSEDIRS = ("*.egg", ".*", "_darcs", "build", "CVS", "dist",
                   "node_modules", "venv", "{arch}")


def _pytest_prunes(name, patterns=_NORECURSEDIRS):
    return any(fnmatch.fnmatch(name, pattern) for pattern in patterns)


def _pytest_prunes_path(rel, patterns=_NORECURSEDIRS):
    """True when a *directory component* of `rel` matches `_pytest_prunes`.

    Only directory components — `rel`'s own basename is a file, never itself
    subject to `norecursedirs` (that list prunes directories pytest does not
    walk into, not files pytest would not collect once inside one).
    """
    parts = rel.split("/")
    return any(_pytest_prunes(part, patterns) for part in parts[:-1])


def _project_norecursedirs(repo_root, notes=None):
    """`norecursedirs` from the project's own pytest config, or the built-in
    default when no config declares it.

    Hardcoding only the built-in default reopened the false-survivor class
    `tests_tree_files` exists to close: `--tests-dir .` pruned `.*` even for a
    project whose own `pyproject.toml` drops that entry to recurse into a
    dot-directory, so a `.gitignore`d killer test living there was named by
    neither `git` nor this walk — the exact shape `tests_tree_files`'s own
    docstring describes as the defect three earlier rounds closed.

    Checked, in the order pytest itself prefers a config file
    (`_pytest/config/findpaths.py`): `pytest.toml` (`[pytest]`), `.pytest.toml`
    (`[pytest]`), `pytest.ini` (`[pytest]`), `.pytest.ini` (`[pytest]`),
    `pyproject.toml` (`[tool.pytest.ini_options]`, then `[tool.pytest]` — the
    latter is pytest's current native-TOML section against the pinned pytest
    version, not a legacy spelling, checked only after the former returns
    silently so a malformed `ini_options` still raises), `tox.ini`
    (`[pytest]`), `setup.cfg` (`[tool:pytest]`).

    Pytest *commits* to the first candidate that exists for it, and never
    reads a later one — even when the committed file is silent on
    `norecursedirs`, in which case pytest's own built-in default applies for
    that key, not whatever a later file in the list happens to say. This
    function follows the same commitment rule, not a "first one that
    *declares the key*" rule: falling through to a later file after an
    earlier one committed used to resolve a list pytest itself never
    consults, pruning a directory pytest actually collects from — the
    false-survivor direction this task exists to close. What counts as
    "commits" differs by file kind, matching
    `_pytest.config.findpaths.load_config_dict_from_file`:

      * The four dedicated names (`pytest.toml`, `.pytest.toml`, `pytest.ini`,
        `.pytest.ini`) commit the moment the *file* exists, regardless of
        content — a bare, empty `pytest.ini` still commits pytest to it and
        to pytest's own built-in `norecursedirs`, never to a later file.
      * The three shared, multi-tool names (`pyproject.toml`, `tox.ini`,
        `setup.cfg`) commit only once their pytest-specific table/section is
        actually present (`[tool.pytest]`/`[tool.pytest.ini_options]`,
        `[pytest]`, `[tool:pytest]`); an unrelated `pyproject.toml` with no
        pytest table at all does not commit and the walk continues.

    An explicit empty `norecursedirs` (`[]`/`""`) on a committed file is still
    a real answer — "prune nothing" — not an absent one, so it is returned
    as-is rather than treated as silence. This is a narrower approximation of
    pytest's actual rootdir/inifile algorithm (which additionally considers
    `-c`/`--rootdir` and ini-file content this function does not parse for
    non-`norecursedirs` purposes); it answers one question — what would this
    project's own committed file say about `norecursedirs` — without pytest
    itself as a dependency.

    A caller passing `notes` is told, at the point it happens, whenever this
    function's own answer might not be pytest's real one: a candidate file
    exists but this probe could not resolve `norecursedirs` from it (invalid
    shape, unreadable, undecodable — see the `except` clause below), treated
    as committed-but-failed rather than falling through further. It is *not*
    told about the two cases that are not failures: no candidate commits at
    all (ordinary — most projects never override this), and a project that
    invokes pytest with an explicit `-c <path>` naming a config file outside
    these seven standard names — this function only inspects well-known
    filenames on disk, so a non-standard invocation cannot be observed here,
    and it cannot report what it cannot observe. Those two, plus the
    out-of-scope parts of pytest's real algorithm named two paragraphs above
    (`-c`/`--rootdir` and non-`norecursedirs` ini content), are the entire
    residue between this function's answer and pytest's own: every other
    divergence a previous round found (a TOML value shape this probe could
    not make sense of, a `[tool.pytest]`-only section, a dedicated file
    outranking `pyproject.toml`, a committed-but-silent file wrongly falling
    through to a later one, a declared-but-empty table read as absent, both
    `[tool.pytest]` keys and `[tool.pytest.ini_options]` non-empty at once —
    a shape pytest itself refuses with `UsageError` rather than resolving,
    treated here as committed-but-unresolvable rather than silently reading
    one half of it, and distinct from a merely-*present*-but-empty
    `ini_options` beside real `[tool.pytest]` keys, which pytest resolves
    without complaint — and `setup.cfg` declaring `[pytest]` instead of
    `[tool:pytest]`, a shape pytest also refuses outright, previously walked
    past as though the file were not pytest's) is closed. Any prune note
    built from this function's
    return value must describe what *this probe* resolved, never assert that
    it matches
    pytest's own effective config — the two can diverge in exactly the ways
    named here.
    """
    # The fourth element marks a "dedicated" pytest file — one that commits
    # the moment it exists, independent of its content — versus a "shared"
    # multi-tool file, which commits only once its pytest-specific
    # table/section is actually present. See the docstring above.
    candidates = (
        (os.path.join(repo_root, "pytest.toml"), "toml", ("pytest",), True),
        (os.path.join(repo_root, ".pytest.toml"), "toml", ("pytest",), True),
        (os.path.join(repo_root, "pytest.ini"), "ini", "pytest", True),
        (os.path.join(repo_root, ".pytest.ini"), "ini", "pytest", True),
        (os.path.join(repo_root, "pyproject.toml"), "toml", ("tool", "pytest"), False),
        (os.path.join(repo_root, "tox.ini"), "ini", "pytest", False),
        (os.path.join(repo_root, "setup.cfg"), "ini", "tool:pytest", False),
    )
    for path, kind, section, dedicated in candidates:
        if not os.path.isfile(path):
            continue
        try:
            if kind == "toml":
                with open(path, "rb") as fh:
                    data = tomllib.load(fh)
                # `section` is the key path to the pytest table: `("pytest",)`
                # for a dedicated `pytest.toml`/`.pytest.toml` (the table sits
                # at the top level), `("tool", "pytest")` for `pyproject.toml`
                # (it sits under `[tool]`). Chained `.get()` calls, so a node
                # that is not a table anywhere along the path raises
                # `AttributeError` naturally and lands in the `except` below.
                # `pyproject.toml` commits by CONTENT, not by table presence —
                # verified against `_pytest/config/findpaths.py`:
                # `load_config_dict_from_file` builds
                # `toml_config = {k: v for k, v in tool_pytest.items() if k !=
                # "ini_options"}` and `ini_config = tool_pytest.get(
                # "ini_options", None)`, and returns `None` (not committed)
                # unless `toml_config` is non-empty or `ini_config is not
                # None`. For every shape that does not itself raise, that
                # condition is exactly `bool(tool_pytest)`: a bare
                # `[tool.pytest]` has no keys at all, so `toml_config == {}`
                # and `ini_config is None` — not committed, the walk keeps
                # going — while a bare `[tool.pytest.ini_options]` has one key
                # (`ini_options`, mapped to `{}`), so `ini_config` is `{}`,
                # which is *not* `None` — committed. `node.get(key, {})` and a
                # truthiness check on the final table reproduces this
                # correctly; a non-table node reached along the path raises
                # `AttributeError` naturally on the next `.get()` and lands in
                # the `except` below, the same as a malformed `ini_options`.
                node = data
                for key in section:
                    node = node.get(key, {})
                if not dedicated and not node:
                    continue
                if (not dedicated and isinstance(node, dict)
                        and node.get("ini_options") and set(node) - {"ini_options"}):
                    # `findpaths.py`'s actual condition is `if toml_config and
                    # ini_config`: `ini_config` has to be *truthy*, not merely
                    # present. `node.get("ini_options")` (truthy check), not
                    # `"ini_options" in node` (mere presence) — a *bare*
                    # `[tool.pytest.ini_options]` (`ini_config == {}`, falsy)
                    # beside a real `[tool.pytest]` key does not trip pytest's
                    # `UsageError`; pytest takes `toml_config` and resolves
                    # the native config, ignoring the empty header. The wider
                    # presence check raised into the `except` for that shape,
                    # substituting the built-in default for a list pytest
                    # never even considered wrong.
                    #
                    # When it IS both truthy, pytest raises `UsageError` and
                    # the whole run dies — it resolves no config at all, so
                    # there is no "list pytest actually applies" for either
                    # half to agree or disagree with. Reading one of the two
                    # anyway would answer a question pytest itself refuses to
                    # answer; treated as committed-but-unresolvable instead,
                    # the same as any other shape this probe cannot make
                    # sense of.
                    raise ValueError(
                        "pyproject.toml declares both non-empty [tool.pytest] "
                        "keys and a non-empty [tool.pytest.ini_options] — "
                        "pytest itself refuses this shape (UsageError)"
                    )
                # `ini_options` is a `pyproject.toml`-only concept: pytest's
                # TOML-native `pytest.toml`/`.pytest.toml` treat every key
                # directly under `[pytest]` as a config value, so an
                # `[pytest.ini_options]` table there is just an unknown
                # option pytest warns about and ignores — reading it the same
                # way as `pyproject.toml`'s `[tool.pytest.ini_options]` would
                # resolve a list pytest itself never consults. Checked only
                # for the shared `pyproject.toml` candidate.
                value = None
                if not dedicated:
                    value = node.get("ini_options", {}).get("norecursedirs")
                if value is None:
                    value = node.get("norecursedirs")
                # pytest accepts `norecursedirs` as a scalar string, not only
                # a list — `norecursedirs = ".*"` is valid TOML *and* valid
                # pytest config, split the same way `_pytest.config` splits an
                # ini `args`-typed value (`shlex.split`, so a quoted entry
                # with a space survives) as the ini format below does.
                # Returned as-is, the two-character string `".*"` iterates as
                # `('.', '*')`, and `'*'` is a glob matching every directory
                # basename: worse than never reading the config, because it
                # prunes the whole tree one level down instead of merely
                # falling back to the built-in default.
                if isinstance(value, str):
                    value = shlex.split(value)
            else:
                cp = configparser.ConfigParser()
                cp.read(path)
                if not dedicated and not cp.has_section(section):
                    if section == "tool:pytest" and cp.has_section("pytest"):
                        # `setup.cfg` is pytest-specific only under
                        # `[tool:pytest]` — a bare `[pytest]` section there is
                        # a shape `findpaths.py` calls `fail()` on outright,
                        # aborting the whole pytest run. Walking past it as
                        # though the file were not pytest's at all would be
                        # correct by accident (the built-in default is also
                        # what this returns) without ever saying so; treated
                        # as committed-but-unresolvable instead, so it is
                        # named the same way any other shape this probe
                        # cannot make sense of is.
                        raise ValueError(
                            "setup.cfg declares [pytest] instead of "
                            "[tool:pytest] — pytest itself refuses this shape"
                        )
                    # Same commitment rule as the TOML shared case: `tox.ini`
                    # and `setup.cfg` are multi-tool files, so the section
                    # itself — not just the file — has to be present to
                    # count as pytest's. `.get(section, ..., fallback=None)`
                    # below would already tolerate a missing section
                    # silently; this `continue` is what keeps that silence
                    # from being read as "committed but empty".
                    continue
                raw = cp.get(section, "norecursedirs", fallback=None)
                value = shlex.split(raw) if raw is not None else None
            if value is not None:
                # `value = tuple(value)` alone only catches a shape that
                # raises *inside* `tuple()` — `[".*", 2024]` survives it (a
                # tuple may hold an int) and only fails later, deep inside
                # `fnmatch.fnmatch` in `_pytest_prunes`, with this function
                # long since returned and no note said why. A TOML *table*
                # where pytest expects an array/string survives `tuple()` the
                # same way: `tuple({"a": 1})` is `("a",)`, the table's own
                # keys — all strings, so a plain post-hoc "all elements are
                # str" check does not catch it either. Both are rejected here,
                # in the same `try`, so they land in the `except`/note path
                # below instead of a third outcome that is neither the
                # project's real list nor the built-in default.
                if isinstance(value, dict):
                    raise TypeError(f"norecursedirs is a table, not a list or string: {value!r}")
                value = tuple(value)
                if not all(isinstance(v, str) for v in value):
                    raise TypeError(
                        f"norecursedirs element(s) not all strings: {value!r}"
                    )
        except (OSError, ValueError, UnicodeDecodeError, TypeError,
                AttributeError, configparser.Error, tomllib.TOMLDecodeError) as exc:
            # A config file this probe declined to make sense of must not
            # silently substitute the built-in default without a word, but it
            # also must not fall through to a later candidate: this file
            # committed pytest to itself (dedicated by existence, or shared
            # with its section already confirmed present above), so treating
            # it as "as if it never existed" would resolve a later file
            # pytest itself would never read either. Deliberately broad:
            # valid TOML can still hold an invalid pytest config shape
            # (`ini_options` spelled as a scalar instead of a table raises
            # `AttributeError` on the `.get()` chain; `norecursedirs = true`
            # raises `TypeError` on `tuple()`) and neither of those is this
            # probe's business to validate.
            if notes is not None:
                notes.append(
                    f"{os.path.relpath(path, repo_root)}: `norecursedirs` "
                    f"could not be resolved from this file ({type(exc).__name__}); "
                    "this probe's built-in `norecursedirs` default was used "
                    "instead"
                )
            return _NORECURSEDIRS
        # Reached only once this candidate has committed (dedicated by
        # existence, or shared with its section confirmed present): pytest
        # reads no further file after this one, silent on `norecursedirs` or
        # not, so neither does this function.
        return value if value is not None else _NORECURSEDIRS
    return _NORECURSEDIRS


def walk_tree(repo_root, root, patterns=_NORECURSEDIRS, pruned=None):
    """Repo-relative paths of the files under `root`, as pytest would see them.

    Symlinks are followed, because pytest follows them: a directory symlink
    under `tests/` is an ordinary way to share a suite, and `os.walk`'s default
    `followlinks=False` made the probe blind to everything under one. Only a
    directory that contains the walk is pruned, so `tests/self -> .` terminates.

    The prune is the loop condition itself and not a visited set, because a
    resolved-path `seen` prunes whichever path *arrives second* — with
    `tests/pkg/test_x.py` and `tests/link -> pkg`, `tests/link` sorts first and
    the canonical `tests/pkg/test_x.py` was never yielded. An alias is what
    pytest collects both of, and this claims to be the tree as the filesystem
    presents it, so both are yielded; only a cycle is cut.

    A directory symlink pointing outside `repo_root` is walked and yielded
    like any other path, not just one pointing inside it — see
    `copy_worktree`'s docstring for why that is a stated ceiling rather than
    something this module tries to contain: sharing a suite that lives
    outside the repository through a symlink is a supported layout, not an
    accident to guard against.

    Bytecode, VCS/cache directories, and directories `patterns` names are
    excluded — see `_SKIP_DIRS` and `_pytest_prunes`. `pruned`, if given, is
    appended with the repo-relative path of each directory `patterns` pruned,
    so a caller with `notes` can say what a narrowed walk did not cover.
    """
    for dirpath, dirnames, filenames in os.walk(root, followlinks=True):
        if _contains_itself(dirpath):
            dirnames[:] = []
            continue
        keep = []
        for d in sorted(dirnames):
            if d in _SKIP_DIRS:
                continue
            if _pytest_prunes(d, patterns):
                if pruned is not None:
                    pruned.append(os.path.relpath(os.path.join(dirpath, d), repo_root))
                continue
            keep.append(d)
        dirnames[:] = keep
        for name in sorted(filenames):
            if _is_bytecode(name):
                continue
            yield os.path.relpath(os.path.join(dirpath, name), repo_root)


def _contains_itself(dirpath):
    """True when `dirpath` resolves to one of its own ancestors — a cycle.

    `tests/current -> .` is an ordinary layout, and mutual aliases
    (`a/x -> ../b`, `b/y -> ../a`) loop without either one pointing at its own
    parent, so the whole ancestor chain is checked rather than one level.
    """
    # Without this, the walk root itself — `<repo>/.` — read as its own
    # parent: `os.path.dirname('<repo>/.')` is `<repo>`, so the root was
    # misread as a symlink cycle and `--tests-dir .` pruned the whole walk at
    # the first iteration, silently.
    dirpath = os.path.normpath(dirpath)
    resolved = os.path.realpath(dirpath)
    parent = os.path.dirname(dirpath)
    while parent and parent != dirpath:
        if os.path.realpath(parent) == resolved:
            return True
        dirpath, parent = parent, os.path.dirname(parent)
    return False


def tests_tree_files(repo_root, tests_dir="tests", notes=None):
    """Every file under `tests_dir` — the copy set, and *not* a name rule.

    **This is the invariant `copy_worktree` carries.** The scratch tree has to
    contain what pytest would collect, and the only cheap thing that is a
    superset of pytest's collection is the test directory itself: no filename
    pattern to be narrower than pytest's, no git status to disagree with the
    filesystem, no `conftest.py` or fixture datafile left behind.

    Three rounds of the false-survivor defect were three ways git disagreed
    with the filesystem, and the fix after them handed the *ranker's* output to
    the copy — which swapped a git rule for a name rule and left the class open:
    a `.gitignore`d `boundary_test.py` (pytest's other default pattern) was
    named by neither, so the targeted run and the escalation were blind at once
    and a mutation the real tree kills `2 failed, 1 passed` was reported
    `survived`. The lesson each round taught the same way: whatever decides the
    copy set must not be narrower than pytest.

    The ranker below may stay narrower, and that is not a ceiling — a test it
    fails to name is still *in the tree it escalates to*, so the cost is one
    full-suite run, never a wrong answer.

    The walk itself is pruned against `_project_norecursedirs(repo_root)` —
    the project's own `norecursedirs` when this probe could resolve it, its
    built-in default otherwise — because a directory pytest would never
    collect from cannot be required by the superset invariant either. When
    `notes` is given, each pruned directory is named there, and so is a
    config file this probe found but could not resolve `norecursedirs` from
    (see `_project_norecursedirs`): a narrowed walk must not read as complete
    coverage, and the note describes what *this probe* resolved and applied,
    never a claim that it matches pytest's own effective config — the two can
    diverge in ways this probe cannot always detect.
    """
    patterns = _project_norecursedirs(repo_root, notes=notes)
    pruned = [] if notes is not None else None
    files = sorted(walk_tree(repo_root, os.path.join(repo_root, tests_dir),
                             patterns=patterns, pruned=pruned))
    if pruned:
        shown = ", ".join(sorted(pruned)[:5])
        more = f", and {len(pruned) - 5} more" if len(pruned) > 5 else ""
        notes.append(
            f"{len(pruned)} director{'y' if len(pruned) == 1 else 'ies'} under "
            f"{tests_dir}/ matched the `norecursedirs` this probe resolved and "
            f"were not walked ({shown}{more}); a test collected from inside one "
            "would not be found by this probe either"
        )
    return files


def discover_tests(repo_root, tests_dir="tests"):
    """Test files under `tests_dir` by pytest's default `python_files` rule.

    `test_*.py` and `*_test.py`, walking the *real* filesystem. This is the
    **ranker's** universe — `select_targeted` names files out of it — and it is
    a speed heuristic, not a correctness boundary: a suite whose files match a
    configured `python_files` this does not know about is targeted worse and
    escalates more, and the escalation still runs the whole scratch tree, which
    `tests_tree_files` fills. Pruned by the same `_project_norecursedirs` as
    `tests_tree_files`, so the two never disagree about which directories
    pytest would walk — but no note fires here, consistent with this being a
    ranking heuristic rather than the correctness boundary.
    """
    patterns = _project_norecursedirs(repo_root)
    return sorted(p for p in walk_tree(repo_root, os.path.join(repo_root, tests_dir),
                                       patterns=patterns)
                  if _TEST_NAME.match(os.path.basename(p)))


def read_test_bodies(repo_root, tests):
    """{test path: source text}, read once for a whole run.

    `select_targeted` used to open and scan every discovered test file on every
    call, and it was then called once per site; `collect_sites` now memoises
    the selection per `(path, site.symbol)` pair, so a repeated site on the
    same pair costs nothing further here. Measured on this repository at 75
    test files on 2026-09-10, the unmemoised version spent 52.8s of the 53.5s
    a 169-site `--sites-only` run took. (A byte count was not quoted here: the
    file it would have described was under active edit in the same task this
    fix landed in, and a number that was true when measured would have gone
    stale on the next commit.)
    """
    bodies = {}
    for test in tests:
        try:
            with open(os.path.join(repo_root, test), encoding="utf-8") as fh:
                bodies[test] = fh.read()
        except OSError:
            continue
    return bodies


def select_targeted(test_bodies, path, symbols, changed_tests, limit):
    """Test files most likely to observe a mutation in `symbols` of `path`.

    Ranked by relevance — tests naming an enclosing symbol, then tests naming
    the module — and a test the diff itself touched is promoted above its
    peers. Relevance comes first: a diff that edits five test files must not
    hand all five to every site in it, which measured 6 unrelated files on
    every `background_hook.py` site of the `6689dd7` diff.

    A symbol that names a large share of the suite is dropped from the symbol
    rank rather than used: `main` appears in 33 of this repository's 75 test
    files and `run` in 52, so matching on one picks six arbitrary files, pays
    six serial pytest runs, and escalates to the full suite anyway. The module
    stem is both cheaper and a better signal there. The cutoff never falls
    below the default selection width, because a symbol that matches no more
    files than a selection would run costs nothing to keep.

    The ranking only has to be good enough to kill most mutations cheaply.
    Anything it misses is caught by the full-suite escalation, which is why a
    wrong guess here costs time and never correctness.
    """
    stem = os.path.splitext(os.path.basename(path))[0]
    stem_re = re.compile(rf"\b{re.escape(stem)}\b")
    cutoff = max(DEFAULT_MAX_TEST_FILES,
                 int(len(test_bodies) * GENERIC_SYMBOL_SHARE))
    symbol_hits: dict[str, int] = {}
    for symbol in symbols:
        if symbol == "<module>":
            continue
        word = re.compile(rf"\b{re.escape(symbol)}\b")
        found = {test: len(word.findall(text))
                 for test, text in test_bodies.items() if word.search(text)}
        if len(found) > cutoff:
            continue  # not a discriminator; fall through to the module stem
        for test, hits in found.items():
            symbol_hits[test] = symbol_hits.get(test, 0) + hits
    scored = []
    for test, text in test_bodies.items():
        hits = symbol_hits.get(test, 0)
        if hits:
            rank = 2
        elif stem_re.search(text):
            rank, hits = 1, len(stem_re.findall(text))
        else:
            continue
        scored.append((rank + (1 if test in changed_tests else 0), hits, test))
    scored.sort(key=lambda item: (-item[0], -item[1], item[2]))
    return tuple(test for _rank, _hits, test in scored[:limit])


# --------------------------------------------------------------------------
# probe loop
# --------------------------------------------------------------------------

KILLED = "killed"
KILLED_FULL = "killed (full suite only)"
SURVIVED = "survived"
UNKNOWN = "unknown (full suite red without mutation)"


@dataclass
class Outcome:
    site: Site
    status: str


@dataclass
class Report:
    outcomes: list = field(default_factory=list)
    total_sites: int = 0
    not_attempted: int = 0
    stop_reason: str = ""
    files: int = 0
    notes: list = field(default_factory=list)
    seconds: float = 0.0

    def survivors(self):
        return [o for o in self.outcomes if o.status == SURVIVED]

    def render(self):
        counts: dict[str, int] = {}
        for outcome in self.outcomes:
            counts[outcome.status] = counts.get(outcome.status, 0) + 1
        lines = [
            f"mutation probe: {self.total_sites} site(s) from {self.files} changed file(s), "
            f"{len(self.outcomes)} attempted in {self.seconds:.1f}s"
        ]
        for status in (KILLED, KILLED_FULL, SURVIVED, UNKNOWN):
            if counts.get(status):
                lines.append(f"  {status}: {counts[status]}")
        if self.not_attempted:
            # C-13's weight contract in reverse: a bounded run that reads as
            # complete coverage is worse than no run at all.
            lines.append(
                f"INCOMPLETE: {self.not_attempted} of {self.total_sites} site(s) "
                f"not attempted ({self.stop_reason})"
            )
        survivors = self.survivors()
        if survivors:
            lines.append("survivors (not necessarily bugs — review each):")
            for outcome in survivors:
                lines.append(f"  {outcome.site.describe()}")
                lines.append(f"      targeted: {', '.join(outcome.site.targeted) or '(none)'}")
        for note in self.notes:
            lines.append(f"note: {note}")
        return "\n".join(lines)


def probe(sites, run_targeted, run_full, baseline_full, *,
          max_sites=DEFAULT_MAX_SITES, budget_secs=DEFAULT_BUDGET_SECS,
          clock=time.monotonic, files=0):
    """Run every site, escalating anything the targeted selection did not kill.

    `run_targeted(site)` / `run_full(site)` return True when the suite passed,
    i.e. when the mutation was *not* noticed. `baseline_full()` returns True
    when the unmutated tree passes the full suite; it is consulted lazily,
    because it costs a full run and is only needed once something escalates.
    """
    report = Report(total_sites=len(sites), files=files)
    started = clock()
    attempted = 0
    baseline_ok = None
    for site in sites:
        if attempted >= max_sites:
            report.stop_reason = f"--max-sites {max_sites}"
            break
        if attempted and clock() - started >= budget_secs:
            report.stop_reason = f"--budget-secs {budget_secs:g} exhausted"
            break
        attempted += 1
        if not run_targeted(site):
            report.outcomes.append(Outcome(site, KILLED))
            continue
        if baseline_ok is None:
            baseline_ok = baseline_full()
            if not baseline_ok:
                report.notes.append(
                    "the unmutated tree does not pass the full suite; survivors "
                    "cannot be told from pre-existing failures"
                )
        if not baseline_ok:
            report.outcomes.append(Outcome(site, UNKNOWN))
            continue
        # The escalation. A targeted selection is a guess; a survivor claim is
        # not allowed to rest on one.
        report.outcomes.append(
            Outcome(site, SURVIVED if run_full(site) else KILLED_FULL)
        )
    report.not_attempted = len(sites) - attempted
    if report.not_attempted and not report.stop_reason:
        report.stop_reason = "stopped early"
    report.seconds = clock() - started
    return report


# --------------------------------------------------------------------------
# execution against a scratch copy
# --------------------------------------------------------------------------

def copy_worktree(repo_root, dest, must_include=(), notes=None):
    """Copy the working tree — tracked, untracked-not-ignored, `must_include`.

    Mutations are applied here and never to `repo_root`: an interrupted run
    must not leave a mutated line in the real tree, and the suite's own
    install-tree guard has to stay meaningful across a probe.

    **`must_include` carries the invariant: the copy set must contain what
    pytest would collect.** Callers pass `tests_tree_files(...)` — the whole
    test directory as the filesystem presents it — because the full-suite
    escalation runs whatever this tree holds, and a killing test missing from it
    makes the targeted run and the escalation blind at once. That is a false
    survivor the escalation cannot rescue: the tool manufacturing the one error
    direction this design forbids.

    Git decides the *bulk* of the copy and is wrong about its *edges*, so it is
    not allowed to decide alone. `git ls-files` reports an index; pytest walks a
    filesystem with no idea what is tracked or ignored. Every path where the two
    disagree used to be a missing test — tracked-only, then
    untracked-non-ignored, then `.gitignore`d test directories, three rounds of
    one defect. Naming the *ranker's* output as the copy set closed those three
    and left the class open, because it replaced a git rule with a name rule:
    `tests/local/boundary_test.py` matches pytest's other default
    `python_files` pattern and neither git nor a `test_*` ranker names it.
    `tests_tree_files` is the fix that has no rule to be narrow about.

    `--exclude-standard` still keeps `.gitignore`d artifacts *outside*
    `tests_dir` out, so an ignored build tree does not get copied to `/tmp` on
    every run.

    A path the copy cannot place is **named**, never dropped in silence — the
    same rule the truncation notice follows, and it applies to the git-derived
    paths too, not only to `must_include`. Two ways it happens, and they are
    not the same class:

      * Nothing is at the destination afterwards — a broken symlink named
        `test_*.py`, an indexed file deleted from the working tree, a submodule
        gitlink. It is skipped and recorded, because a reader has to be told the
        targeted run and the escalation both ran without it. A tracked
        *directory* symlink is not this case: it is not a regular file either,
        but `tests_tree_files` walks through it and the copy holds its contents
        under the name pytest reads them by, so reporting it would be noise.
      * The path does not stay under `dest` — `--tests-dir ../outside/tests`
        makes the walk return `../outside/tests/test_out.py`, which joins onto
        `dest` as a write *outside* the scratch directory. That is refused, not
        recorded. `shutil.rmtree(scratch)` would not reach it, and enough `..`
        reaches the real working tree, which §4 of the REQ forbids absolutely.
        Recording is the right answer for a file that could not be copied; it is
        the wrong answer for a copy to a directory the operator never named,
        because by then the write has already happened.
    **Stated ceiling, not a guard:** a directory symlink whose target resolves
    outside `repo_root` (`tests/deep -> ../../shared`, or further still) is
    copied like any other path `walk_tree` names. This is not an oversight —
    `test_a_test_directory_reached_through_a_symlink_is_in_the_probed_tree`
    pins the opposite as a requirement: sharing a suite that lives outside the
    repository through a symlink is an ordinary layout this module exists to
    support, the same reason `walk_tree` follows symlinks at all. A
    `source_at`/`_with_mutation`-style containment check here would refuse
    that legitimate case along with a genuinely accidental one, and the two
    are not distinguishable from the path alone. Anything reachable through a
    tracked or `must_include`d symlink is, by that fact, already something the
    operator's own working tree exposes to this probe.

    Untracked directories the `norecursedirs` this probe resolved (see
    `_project_norecursedirs`) would not walk into are excluded from the
    *untracked* query only — never from `tracked` or `required` — because git
    already answers "does this matter" unambiguously for a tracked path
    (someone committed a source file under `build/`, it is still a mutation
    subject) in a way `norecursedirs` cannot override; the prune only tells us
    what pytest would not collect on its own, which is relevant solely to
    files git does not already vouch for.
    """
    patterns = _project_norecursedirs(repo_root, notes=notes)
    tracked = _git(repo_root, "ls-files", "-z").stdout.split("\0")
    untracked_raw = _git(repo_root, "ls-files", "-o", "--exclude-standard", "-z").stdout.split("\0")
    pruned_untracked = [p for p in untracked_raw if p and _pytest_prunes_path(p, patterns)]
    untracked = [p for p in untracked_raw if p and p not in set(pruned_untracked)]
    if pruned_untracked and notes is not None:
        notes.append(
            f"{len(pruned_untracked)} untracked file(s) under a directory the "
            "`norecursedirs` this probe resolved would not walk into were not "
            f"copied (e.g. {pruned_untracked[0]})"
        )
    required = {p for p in must_include if p}
    root = os.path.realpath(dest)
    escaping = sorted(
        rel for rel in required
        if os.path.commonpath([root, os.path.realpath(os.path.join(dest, rel))]) != root
    )
    if escaping:
        raise ValueError(
            "must_include path(s) would be copied outside the scratch tree "
            f"{dest}: {', '.join(escaping)} — the probe writes only inside its "
            "own temporary directory (use a --tests-dir under --repo-root)"
        )
    skipped = []
    for rel in sorted({p for p in [*tracked, *untracked, *required] if p}):
        if _is_bytecode(rel):
            # Never, by any route. A repository that does not `.gitignore`
            # `__pycache__` hands `ls-files -o --exclude-standard` the stale
            # `.pyc` of the very file about to be mutated, and CPython's
            # mtime-seconds+size validation then hides a size-preserving
            # mutation exactly as §4 of the REQ describes. Reproduced here on
            # 2026-09-10 by running pytest in a fixture repo before probing it:
            # `survived`, for a mutation the tree kills.
            continue
        src = os.path.join(repo_root, rel)
        if not os.path.isfile(src):
            skipped.append(rel)
            continue
        target = os.path.join(dest, rel)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copy2(src, target)
    # Resolved after the copy, not during it: a directory symlink is skipped
    # here and placed by the walk of its contents, and a path that ended up in
    # the scratch tree by either route is not something the copy failed at.
    dropped = [rel for rel in skipped if not os.path.exists(os.path.join(dest, rel))]
    if dropped and notes is not None:
        notes.append(
            f"{len(dropped)} path(s) could not be copied into the scratch tree "
            f"({', '.join(dropped)}); the targeted run and the full-suite "
            "escalation both ran without them"
        )
    return dest


class PytestRunner:
    """Runs pytest in the scratch tree, applying and undoing one mutation."""

    def __init__(self, scratch, python, timeout=DEFAULT_RUN_TIMEOUT, notes=None):
        self.scratch = scratch
        self.python = python
        self.timeout = timeout
        self.notes = notes if notes is not None else []
        self._targeted_baseline: dict[tuple, bool] = {}

    def _pytest(self, extra):
        # `PYTHONDONTWRITEBYTECODE` must reach the child, and `-p
        # no:cacheprovider` does not stand in for it: that flag disables
        # pytest's own cache, not CPython's bytecode cache. A `__pycache__`
        # written into the scratch tree by one run is validated on the next by
        # source mtime-seconds and size, so a size-preserving mutation
        # (`Eq -> NotEq`, `2 -> 3`) applied within the same second as the
        # preceding unmutated compile is never loaded — the run executes stale
        # bytecode and reports the mutation as unnoticed. That is a false
        # survivor manufactured by the tool itself, which is the one direction
        # this design forbids.
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        cmd = [self.python, "-m", "pytest", "-q", "-x", "--no-header",
               "-p", "no:cacheprovider", *extra]
        try:
            done = subprocess.run(cmd, cwd=self.scratch, env=env,
                                  capture_output=True, text=True,
                                  timeout=self.timeout, check=False)
        except subprocess.TimeoutExpired:
            return False  # a mutation that hangs the suite is a noticed one
        return done.returncode == 0

    def _with_mutation(self, site, extra):
        # Defense in depth: `_with_mutation` is the one place that actually
        # opens `site.path` for writing, so it does not get to assume an
        # upstream guard already refused an escaping path. `os.path.join`
        # does not clamp `..` or an absolute second argument, so a site whose
        # path was `../victim.py` — or simply absolute — would read and write
        # outside the scratch tree entirely.
        if not _contained(self.scratch, site.path):
            raise ValueError(
                f"mutation site path escapes the scratch tree: {site.path!r}"
            )
        path = os.path.join(self.scratch, site.path)
        with open(path, encoding="utf-8") as fh:
            original = fh.read()
        try:
            # Opening with `"w"` truncates immediately, before `site.apply`
            # runs. Both the truncating write and the run must share the same
            # `finally` that restores `original` — a failure computing the
            # mutated text used to happen *outside* this `try`, leaving the
            # file holding whatever the truncating open left it as, with
            # nothing left to restore it.
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(site.apply(original))
            return self._pytest(extra)
        finally:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(original)

    def targeted(self, site):
        if not site.targeted:
            return True  # nothing to run cheaply — escalate
        if site.targeted not in self._targeted_baseline:
            ok = self._pytest(["-n", "0", *site.targeted])
            self._targeted_baseline[site.targeted] = ok
            if not ok:
                self.notes.append(
                    "targeted selection red without any mutation "
                    f"({', '.join(site.targeted)}); those sites went to the full suite"
                )
        if not self._targeted_baseline[site.targeted]:
            return True
        return self._with_mutation(site, ["-n", "0", *site.targeted])

    def full(self, site):
        return self._with_mutation(site, [])

    def baseline_full(self):
        return self._pytest([])


def collect_sites(repo_root, rev, tests_dir, max_test_files, paths=(), notes=None):
    notes = [] if notes is None else notes
    end = range_end(rev)
    changed = changed_python_lines(repo_root, rev, notes=notes)
    tests = discover_tests(repo_root, tests_dir)
    if not tests:
        # Cheap to say and expensive to discover by watching: with no ranked
        # test file every site's targeted stage is empty, so every one of them
        # escalates to a full suite. A mistyped `--tests-dir` still answers
        # correctly — it just pays minutes to do it, silently.
        notes.append(
            f"no test file discovered under {tests_dir}/ — every site escalates "
            "to the full suite (check --tests-dir)"
        )
    # A test file, not a directory prefix: `tests_dir.rstrip("/") + os.sep`
    # gave `"./"` for `--tests-dir .`, but `walk_tree` returns `os.path.relpath`
    # paths (`test_top.py`) that can never start with `"./"` — so under
    # `--tests-dir .` this was always empty, silently. It also cannot become
    # `""` for that case, because every changed path "starts with" the empty
    # string and the exclusion would swallow every source file, not just
    # tests. Naming the set of test files pytest's own convention would
    # collect — `discover_tests`'s output, already computed above as `tests` —
    # answers "is this changed path a test file" correctly regardless of
    # where `tests_dir` sits, prefix or no prefix.
    #
    # Read before `--paths` narrows the subjects. A filter aimed at one source
    # file must not also hide the test file the same diff wrote for it, which
    # is the strongest relevance signal there is: measured on the `6689dd7`
    # reconstruction, losing it sent 10 of 15 mutations to a full-suite
    # escalation that a 1-second targeted run would have settled.
    changed_tests = {p for p in changed if p in set(tests)}
    # Defensible, not free: excluding a changed path here trusts pytest's own
    # naming convention (`test_*.py`/`*_test.py`) to mean "this is a test",
    # which is correct under the default `tests_dir` but can misfire once
    # `tests_dir` is widened to the whole repository (`--tests-dir .`) — a
    # source module that merely happens to be named `test_harness.py` loses
    # mutation coverage the same way a real test file is meant to. AC-3's
    # say-what-you-skipped rule applies to this exclusion the same as any
    # other this module makes.
    #
    # Noted only when the excluded path is not under `tests_dir` itself,
    # because `changed_tests` only ever holds a path `discover_tests` found —
    # and for any real subdirectory `tests_dir` (the default `"tests"`, say),
    # `discover_tests` walked *from* that directory, so every member is
    # already under it by construction: the ordinary case of a changed test
    # file living where it should is not surprising and does not need a note
    # per file, unlike the `--tests-dir .` case this note exists for, where
    # `tests_dir` names the whole repository and "under it" is not a
    # meaningful boundary to begin with.
    test_prefix = "" if tests_dir == os.curdir else tests_dir.rstrip("/") + os.sep
    for excluded in sorted(changed_tests):
        if test_prefix and excluded.startswith(test_prefix):
            continue
        notes.append(
            f"{excluded}: excluded as a mutation subject — its name matches "
            "pytest's test-file convention under this --tests-dir"
        )
    if paths:
        changed = {p: lines for p, lines in changed.items()
                   if any(p.startswith(prefix) for prefix in paths)}
    test_bodies = read_test_bodies(repo_root, tests)
    # Sites cluster hard on a few symbols — far more sites than distinct
    # (file, symbol) pairs — so the selection is memoised per pair rather than
    # recomputed per site. No count is quoted here on purpose: the figure this
    # comment used to carry ("60") is one the REQ retracted, because it came
    # from applying a range's line numbers to working-tree content.
    selections: dict[tuple, tuple] = {}
    sites = []
    for path in sorted(changed):
        # Test files are not mutation subjects: breaking an assertion measures
        # nothing about whether the tests discriminate the product code.
        if path in changed_tests:
            continue
        source = source_at(repo_root, path, end, notes=notes)
        if source is _UNREADABLE or source is _OUTSIDE_REPO:
            continue
        if source is None:
            if os.path.isfile(os.path.join(repo_root, path)):
                notes.append(
                    f"{path}: not probed — the checkout does not match {end}, so the "
                    "range's line numbers do not describe the source that would be "
                    "mutated (check out that revision to probe this file)"
                )
            else:
                # A path the diff named that the checkout simply does not
                # have — distinct from a content mismatch, and previously the
                # one branch here that dropped a file with no note at all.
                notes.append(
                    f"{path}: not probed — this file does not exist in the checkout"
                )
            continue
        for site in mutation_sites(path, source, changed[path], notes=notes):
            key = (path, site.symbol)
            if key not in selections:
                selections[key] = select_targeted(
                    test_bodies, path, {site.symbol}, changed_tests, max_test_files)
            sites.append(Site(**{**site.__dict__, "targeted": selections[key]}))
    return sites, len({s.path for s in sites})


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--repo-root", default=".")
    p.add_argument("--rev", default=None,
                   help="Git range (e.g. A..B). Default: working tree vs HEAD")
    p.add_argument("--tests-dir", default="tests")
    p.add_argument("--paths", nargs="*", default=(),
                   help="Only mutate changed files under these path prefixes. "
                        "Aims a bounded run at the file under review, and keeps "
                        "code that is destructive to mutate (an installer) out "
                        "of a run that would otherwise reach it.")
    p.add_argument("--max-sites", type=int, default=DEFAULT_MAX_SITES)
    p.add_argument("--budget-secs", type=float, default=DEFAULT_BUDGET_SECS)
    p.add_argument("--max-test-files", type=int, default=DEFAULT_MAX_TEST_FILES)
    p.add_argument("--python", default=sys.executable,
                   help="Interpreter used to run pytest in the scratch copy")
    p.add_argument("--sites-only", action="store_true",
                   help="List the sites and exit without running anything")
    args = p.parse_args(argv)

    repo_root = os.path.abspath(args.repo_root)
    # Normalised once, here, because `walk_tree` returns paths through
    # `os.path.relpath` while `collect_sites` compares them against a prefix
    # built from this string: with `--tests-dir ./tests` the walk says
    # `tests/test_m.py` and the prefix said `./tests/`, so `changed_tests` came
    # out empty and the diff's own test files became mutation subjects.
    tests_dir = os.path.normpath(args.tests_dir)
    # Operator-typable inputs get an answer, not a traceback. A `--python` that
    # is a relative path is resolved here rather than left to fail in the child,
    # which runs with `cwd=<scratch>` and would resolve it against a directory
    # the operator never typed.
    python = args.python
    if os.sep in python:
        python = os.path.abspath(python)
    if not os.path.isdir(repo_root):
        print(f"mutation probe: cannot run — --repo-root {repo_root} is not a directory")
        return 1
    notes: list = []
    try:
        sites, files = collect_sites(repo_root, args.rev, tests_dir,
                                     args.max_test_files, tuple(args.paths), notes=notes)
    except ValueError as exc:
        print(f"mutation probe: cannot run — {exc}")
        return 1
    if args.sites_only:
        for site in sites:
            print(f"{site.describe()} -> {site.targeted or '(none)'}")
        print(f"{len(sites)} site(s) in {files} file(s)")
        for note in notes:
            print(f"note: {note}")
        return 0
    # Checked here rather than with `--repo-root`: `--sites-only` never starts
    # a child, so refusing it for an interpreter it will not use is a refusal
    # the operator cannot act on.
    if not (os.path.isfile(python) or shutil.which(python)):
        print(f"mutation probe: cannot run — --python {args.python} is not an executable "
              f"(resolved to {python})")
        return 1
    if not sites:
        print("mutation probe: no mutable changed lines")
        for note in notes:
            print(f"note: {note}")
        return 0

    scratch = tempfile.mkdtemp(prefix="mutation-probe-")
    try:
        # The whole test tree, not the diff and not the ranker's view of it:
        # the copy set has to be a superset of what pytest would collect. See
        # `copy_worktree` and `tests_tree_files`.
        try:
            copy_worktree(repo_root, scratch, notes=notes,
                          must_include=tests_tree_files(repo_root, tests_dir, notes=notes))
        except ValueError as exc:
            print(f"mutation probe: cannot run — {exc}")
            return 1
        runner = PytestRunner(scratch, python, notes=notes)
        report = probe(sites, runner.targeted, runner.full, runner.baseline_full,
                       max_sites=args.max_sites, budget_secs=args.budget_secs,
                       files=files)
        report.notes = notes + report.notes
        print(report.render())
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
