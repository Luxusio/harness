"""`plugin/scripts/mutation_probe.py` — scoped mutation runner.

Properties pinned here, each with the mutation that turns the named test red
recorded in `doc/harness/REQ__mutation-scope-follows-the-diff.md`:

  * sites come from the changed lines and nothing else;
  * a survivor claim is never made on the targeted selection alone;
  * a bounded run says what it did not attempt;
  * AC-4's caught case, the never-written-to property, the copy-set
    invariant, and the `ast`/UTF-8 coordinate systems, alongside the parsing,
    containment, and escalation regressions below.

The escalation tests inject runners rather than launching pytest, because the
property under test is the decision logic, not the subprocess. The end-to-end
cases below do launch a real pytest against a throwaway repository — that is
the only way to show the thing actually finds an unreached guard branch.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "plugin", "scripts"))

import mutation_probe as mp  # noqa: E402


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True,
                          text=True, check=True)


def _repo(tmp_path, files):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "probe@example.com")
    _git(repo, "config", "user.name", "probe")
    _write(repo, files)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    return repo


def _write(repo, files):
    for rel, text in files.items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(textwrap.dedent(text), encoding="utf-8")


def _site(label="x", path="m.py", lineno=1, targeted=("tests/test_m.py",)):
    return mp.Site(path=path, lineno=lineno, symbol="f", label=label,
                   start=0, end=1, replacement="0", targeted=targeted)


MODULE = """\
    def widen(value):
        if value > 10:
            return "big"
        return "small"


    def untouched(value):
        # a comment
        return value + 1
"""


# --------------------------------------------------------------------------
# AC-1 — sites come from the changed lines
# --------------------------------------------------------------------------

def test_sites_come_from_the_changed_lines_and_nothing_else(tmp_path):
    repo = _repo(tmp_path, {"m.py": MODULE})
    (repo / "m.py").write_text(
        textwrap.dedent(MODULE).replace("value > 10", "value > 12"), encoding="utf-8")

    changed = mp.changed_python_lines(str(repo))
    assert changed == {"m.py": {2}}
    sites, files = mp.collect_sites(str(repo), None, "tests", 6)
    assert files == 1
    # Every site is on line 2. `untouched`'s `value + 1` is a perfectly good
    # mutation site and must not appear: mutating unchanged code is the cost
    # model this design exists to avoid.
    assert {s.lineno for s in sites} == {2}
    # `12 -> 13` probes the boundary; the two forced tests probe reachability.
    # The `>` itself is not swapped separately — an if-test forced to a constant
    # already covers both of its branches, and three sites on one `if` line
    # spend budget on near-duplicates.
    assert {s.label for s in sites} == {"if-test -> True", "if-test -> False",
                                        "12 -> 13"}


def test_a_comment_or_string_only_change_yields_no_sites(tmp_path):
    repo = _repo(tmp_path, {"m.py": MODULE})
    (repo / "m.py").write_text(
        textwrap.dedent(MODULE)
        .replace("# a comment", "# a different comment")
        .replace('return "big"', 'return "huge"'),
        encoding="utf-8")

    # Both lines are in the diff; neither is executable behaviour a test could
    # be expected to discriminate.
    assert mp.changed_python_lines(str(repo)) == {"m.py": {3, 8}}
    assert mp.collect_sites(str(repo), None, "tests", 6)[0] == []


def test_a_deleted_file_contributes_no_sites(tmp_path):
    repo = _repo(tmp_path, {"m.py": MODULE, "gone.py": "X = 1\n"})
    (repo / "gone.py").unlink()

    assert "gone.py" not in mp.changed_python_lines(str(repo))
    assert mp.collect_sites(str(repo), None, "tests", 6)[0] == []


def test_a_new_file_contributes_its_executable_lines(tmp_path):
    repo = _repo(tmp_path, {"m.py": MODULE})
    _write(repo, {"new.py": "def g(a):\n    return a == 3\n"})
    _git(repo, "add", "new.py")

    assert mp.changed_python_lines(str(repo)) == {"new.py": {1, 2}}
    labels = {s.label for s in mp.collect_sites(str(repo), None, "tests", 6)[0]}
    assert labels == {"Eq -> NotEq", "3 -> 4"}


def test_a_changed_file_with_no_mutable_line_yields_no_sites(tmp_path):
    repo = _repo(tmp_path, {"m.py": MODULE})
    (repo / "m.py").write_text(
        textwrap.dedent(MODULE) + "\n\ndef added():\n    return None\n", encoding="utf-8")

    assert mp.changed_python_lines(str(repo))["m.py"]
    assert mp.collect_sites(str(repo), None, "tests", 6)[0] == []


def test_a_changed_test_file_is_not_a_mutation_subject(tmp_path):
    repo = _repo(tmp_path, {"m.py": MODULE, "tests/test_m.py": "def test_a():\n    assert 1\n"})
    (repo / "tests" / "test_m.py").write_text("def test_a():\n    assert 2\n", encoding="utf-8")

    assert mp.changed_python_lines(str(repo)) == {"tests/test_m.py": {2}}
    assert mp.collect_sites(str(repo), None, "tests", 6)[0] == []


def test_a_site_names_the_function_it_sits_in(tmp_path):
    """The symbol is what a human reads first, and what targeting is built on.

    `LIMIT` sits on the line straight after `widen` ends, which is where an
    off-by-one in the line->symbol map shows up.
    """
    module = 'def widen(value):\n    if value > 10:\n        return "big"\n    return "small"\nLIMIT = 5\n'
    repo = _repo(tmp_path, {"m.py": module})
    (repo / "m.py").write_text(
        module.replace("value > 10", "value > 12").replace("LIMIT = 5", "LIMIT = 7"),
        encoding="utf-8")

    sites = mp.collect_sites(str(repo), None, "tests", 6)[0]
    assert {(s.lineno, s.symbol) for s in sites} == {
        (2, "widen"),
        (5, "<module>"),
    }


def test_a_path_filter_keeps_a_changed_file_out_of_the_run(tmp_path):
    repo = _repo(tmp_path, {"m.py": MODULE, "pkg/other.py": "def h(a):\n    return a == 1\n"})
    (repo / "m.py").write_text(
        textwrap.dedent(MODULE).replace("value > 10", "value > 12"), encoding="utf-8")
    (repo / "pkg" / "other.py").write_text("def h(a):\n    return a == 2\n", encoding="utf-8")

    assert {s.path for s in mp.collect_sites(str(repo), None, "tests", 6)[0]} == \
        {"m.py", "pkg/other.py"}
    filtered, files = mp.collect_sites(str(repo), None, "tests", 6, paths=("pkg/",))
    assert files == 1
    assert {s.path for s in filtered} == {"pkg/other.py"}


def test_a_path_filter_does_not_hide_the_test_file_the_diff_wrote(tmp_path):
    """Narrowing the subjects must not narrow the relevance evidence.

    `tests/test_written_for_it.py` is the file the diff wrote for this change
    and names `h` once; `tests/test_older.py` names it three times and is
    untouched. With one slot, the one the diff wrote wins — unless a `--paths`
    filter aimed at `pkg/` has already erased the fact that it was written.
    """
    repo = _repo(tmp_path, {
        "pkg/other.py": "def h(a):\n    return a == 1\n",
        "tests/test_older.py": "from pkg.other import h\n\n\ndef test_o():\n"
                               "    assert h(1)\n    assert h(1)\n",
        "tests/test_written_for_it.py": "def test_w():\n    assert True\n",
    })
    (repo / "pkg" / "other.py").write_text("def h(a):\n    return a == 2\n", encoding="utf-8")
    (repo / "tests" / "test_written_for_it.py").write_text(
        "from pkg.other import h\n\n\ndef test_w():\n    assert h(2)\n", encoding="utf-8")

    sites, _ = mp.collect_sites(str(repo), None, "tests", 1, paths=("pkg/",))
    assert sites and all(s.targeted == ("tests/test_written_for_it.py",) for s in sites)


def test_a_range_that_does_not_describe_the_checkout_offers_no_sites(tmp_path):
    """`--rev` line numbers and working-tree bytes are two coordinate systems.

    A range that does not end at the checked-out content numbers its lines
    against a commit, while the source read for mutation comes from the tree.
    Observed on this repository with the REQ's own headline invocation: line
    1279 of `plugin/scripts/_lib.py` was offered as `[1000 -> 1001]` and
    attributed to `6689dd7`, where that line is a string tuple with no mutable
    node — the integer lives at HEAD, in a function that commit never touched.
    A survivor there is noise wearing the label of a real finding, so the file
    is not probed at all and the report says which files it dropped.

    `other.py` is the control: it is in the same range and unchanged since, so
    the guard is per-file rather than a blanket refusal.
    """
    repo = _repo(tmp_path, {"mod.py": "X = 1\n", "other.py": "Y = 1\n"})
    _write(repo, {"mod.py": CACHED_MODULE,
                  "other.py": "def h(a):\n    return a == 5\n"})
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "audited")
    _write(repo, {"mod.py": textwrap.dedent(MODULE)})  # the file moved on since

    changed = mp.changed_python_lines(str(repo), "HEAD~1..HEAD")
    assert changed == {"mod.py": {1, 2}, "other.py": {1, 2}}
    notes: list = []
    sites, files = mp.collect_sites(str(repo), "HEAD~1..HEAD", "tests", 6, notes=notes)

    assert {s.path for s in sites} == {"other.py"} and files == 1, \
        [(s.path, s.lineno, s.label) for s in sites]
    assert any(note.startswith("mod.py: not probed") for note in notes), notes


def test_a_symbol_naming_most_of_the_suite_is_not_the_targeted_signal():
    """`main` names 33 of this repo's 75 test files; that is not a signal.

    Matching on it picks an arbitrary handful, pays one serial pytest run per
    file, and escalates to the full suite anyway. The module stem is the
    cheaper and better guess. Correctness never rides on this — the escalation
    owns that — so the only thing at stake is time.
    """
    bodies = {f"tests/test_{i}.py": "def test_x():\n    main()\n" for i in range(9)}
    bodies["tests/test_probe.py"] = (
        "from pkg.probe import main\n\n\ndef test_p():\n    assert main()\n")

    assert mp.select_targeted(bodies, "pkg/probe.py", {"main"}, set(), 3) == \
        ("tests/test_probe.py",)
    # A symbol only a few files name is still the signal it always was.
    few = {"tests/test_a.py": "def test_a():\n    widen(1)\n",
           "tests/test_b.py": "def test_b():\n    assert True\n"}
    assert mp.select_targeted(few, "pkg/probe.py", {"widen"}, set(), 3) == \
        ("tests/test_a.py",)


def test_the_targeted_selection_prefers_tests_naming_the_changed_symbol(tmp_path):
    repo = _repo(tmp_path, {
        "m.py": MODULE,
        "tests/test_widen.py": "from m import widen\n\ndef test_w():\n    assert widen(11)\n",
        "tests/test_other.py": "def test_o():\n    assert True\n",
    })
    (repo / "m.py").write_text(
        textwrap.dedent(MODULE).replace("value > 10", "value > 12"), encoding="utf-8")
    # The diff also touches an unrelated test file. Being in the diff promotes
    # a relevant test; it does not make an irrelevant one relevant.
    (repo / "tests" / "test_other.py").write_text(
        "def test_o():\n    assert True is True\n", encoding="utf-8")

    sites, _ = mp.collect_sites(str(repo), None, "tests", 6)
    assert all(site.targeted == ("tests/test_widen.py",) for site in sites)


# --------------------------------------------------------------------------
# AC-2 — a survivor is escalated before it is reported
# --------------------------------------------------------------------------

def test_a_mutation_only_a_distant_test_kills_is_reported_killed_not_survived():
    """The load-bearing case: the targeted guess missed, the full suite did not."""
    report = mp.probe([_site()], run_targeted=lambda s: True, run_full=lambda s: False,
                      baseline_full=lambda: True)
    assert [o.status for o in report.outcomes] == [mp.KILLED_FULL]
    assert report.survivors() == []


def test_a_mutation_no_suite_notices_is_reported_as_a_survivor():
    report = mp.probe([_site()], run_targeted=lambda s: True, run_full=lambda s: True,
                      baseline_full=lambda: True)
    assert [o.status for o in report.outcomes] == [mp.SURVIVED]
    assert "survivors" in report.render()


def test_the_full_suite_is_not_run_for_a_mutation_the_subset_killed():
    calls = []
    mp.probe([_site()], run_targeted=lambda s: False,
             run_full=lambda s: calls.append(s) or True,
             baseline_full=lambda: calls.append("baseline") or True)
    assert calls == []


def test_a_full_suite_that_is_red_without_any_mutation_yields_no_survivor_claim():
    report = mp.probe([_site()], run_targeted=lambda s: True, run_full=lambda s: True,
                      baseline_full=lambda: False)
    assert [o.status for o in report.outcomes] == [mp.UNKNOWN]
    assert report.survivors() == []
    assert "cannot be told from pre-existing failures" in report.render()


def test_the_clean_full_suite_baseline_is_measured_once():
    baselines = []
    mp.probe([_site(), _site(label="y")], run_targeted=lambda s: True,
             run_full=lambda s: True, baseline_full=lambda: baselines.append(1) or True)
    assert len(baselines) == 1


def test_a_red_targeted_selection_escalates_instead_of_reading_as_killed(tmp_path):
    """A subset that fails without a mutation makes every mutation look killed."""
    runner = mp.PytestRunner(str(tmp_path), sys.executable)
    runner._pytest = lambda extra: False  # the selection is red, mutated or not
    assert runner.targeted(_site()) is True  # -> escalate, do not claim a kill
    assert any("targeted selection red" in note for note in runner.notes)


# --------------------------------------------------------------------------
# AC-3 — the run is bounded and says what it skipped
# --------------------------------------------------------------------------

def test_a_site_list_over_budget_names_what_it_did_not_attempt():
    sites = [_site(label=str(i)) for i in range(10)]
    report = mp.probe(sites, run_targeted=lambda s: False, run_full=lambda s: True,
                      baseline_full=lambda: True, max_sites=3)
    assert len(report.outcomes) == 3
    assert report.not_attempted == 7
    assert "INCOMPLETE: 7 of 10 site(s) not attempted (--max-sites 3)" in report.render()


def test_a_wall_clock_stop_names_what_it_did_not_attempt():
    ticks = iter(range(100))
    report = mp.probe([_site(label=str(i)) for i in range(10)],
                      run_targeted=lambda s: False, run_full=lambda s: True,
                      baseline_full=lambda: True, budget_secs=2,
                      clock=lambda: next(ticks))
    assert report.not_attempted
    assert "INCOMPLETE" in report.render()
    assert "budget-secs 2 exhausted" in report.render()


def test_a_complete_run_claims_nothing_about_incompleteness():
    report = mp.probe([_site()], run_targeted=lambda s: False, run_full=lambda s: True,
                      baseline_full=lambda: True)
    assert report.not_attempted == 0
    assert "INCOMPLETE" not in report.render()


# --------------------------------------------------------------------------
# the real tree is never mutated
# --------------------------------------------------------------------------

def test_the_probed_tree_is_never_written_to(tmp_path):
    repo = _repo(tmp_path, {"m.py": MODULE})
    (repo / "m.py").write_text(
        textwrap.dedent(MODULE).replace("value > 10", "value > 12"), encoding="utf-8")
    before = (repo / "m.py").read_text(encoding="utf-8")

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    mp.copy_worktree(str(repo), str(scratch))
    runner = mp.PytestRunner(str(scratch), sys.executable)
    runner._pytest = lambda extra: (scratch / "m.py").read_text(encoding="utf-8") != before
    site = mp.collect_sites(str(repo), None, "tests", 6)[0][0]

    assert runner.full(site) is True  # the mutation reached the scratch copy
    assert (repo / "m.py").read_text(encoding="utf-8") == before
    assert (scratch / "m.py").read_text(encoding="utf-8") == before


# --------------------------------------------------------------------------
# the runner does not manufacture its own false survivors
# --------------------------------------------------------------------------

CACHED_MODULE = "def widen(value):\n    return value == 1\n"
CACHED_TESTS = """\
    from mod import widen


    def test_two_is_not_one():
        assert widen(2) is False


    def test_one_is_one():
        assert widen(1) is True
"""


def test_a_size_preserving_mutation_is_not_hidden_by_stale_bytecode(tmp_path):
    """`Eq -> NotEq` keeps the file's size, so a `__pycache__` hides it.

    CPython validates a `.pyc` on the source's mtime-*seconds* and size. The
    runner writes one mutation immediately after an unmutated run of the same
    file, so a mutation that preserves the size and lands inside that second
    would load the stale bytecode of the unmutated source: the suite passes,
    and the tool reports a mutation the tests do actually kill as a survivor.
    `-p no:cacheprovider` disables pytest's cache, not CPython's, so the only
    thing standing between this design and a false survivor of its own making
    is `PYTHONDONTWRITEBYTECODE` actually reaching the child process.

    Both halves are asserted because they fail differently. The outcome is the
    property AC-2 names, and it reproduces on every observed run of the
    unfixed code; the empty-`__pycache__` assertion is the mechanism, and it is
    red on any host regardless of how the two writes fall against the clock.

    The end-to-end guard case above cannot cover this: every mutation in it is
    a `drop operand ...`, which shortens the file.
    """
    repo = _repo(tmp_path, {
        "conftest.py": "",  # puts the repo root on sys.path for the fixture tests
        "mod.py": CACHED_MODULE,
        "tests/test_mod.py": CACHED_TESTS,
    })
    _write(repo, {"mod.py": "def widen(value):\n    return value == 2\n"})
    (repo / "tests" / "test_mod.py").write_text(
        textwrap.dedent(CACHED_TESTS).replace("widen(2) is False", "widen(2) is True")
        .replace("widen(1) is True", "widen(1) is False")
        .replace("two_is_not_one", "two_is_two").replace("one_is_one", "one_is_not_two"),
        encoding="utf-8")

    sites = [s for s in mp.collect_sites(str(repo), None, "tests", 6)[0]
             if s.label == "Eq -> NotEq"]
    assert len(sites) == 1 and sites[0].targeted == ("tests/test_mod.py",)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    mp.copy_worktree(str(repo), str(scratch),
                     must_include=mp.tests_tree_files(str(repo)))
    runner = mp.PytestRunner(str(scratch), sys.executable)
    report = mp.probe(sites, runner.targeted, runner.full, runner.baseline_full)

    assert [o.status for o in report.outcomes] == [mp.KILLED], report.render()
    assert list(scratch.rglob("__pycache__")) == []


UNSTAGED_TESTS = """\
    from mod import widen


    def test_two_is_two():
        assert widen(2) is True


    def test_three_is_not_two():
        assert widen(3) is False
"""


def test_a_test_file_written_but_not_yet_staged_is_in_the_probed_tree(tmp_path):
    """The ordinary workflow: edit the source, write its test, probe.

    `discover_tests` walks the real filesystem, so the new file is ranked
    `targeted`. A scratch copy built from `git ls-files` alone does not contain
    it: pytest exits non-zero on a path that is not there, the targeted baseline
    reads red, the site escalates — and the full suite it escalates to is also
    missing the killing test. Both stages are blind, so the escalation cannot
    rescue it, and `Eq -> NotEq` is reported as a survivor of a suite that kills
    it. That is the direction AC-2 forbids, produced by the tool itself.

    The ignored file is the other half, and it is a *non-test* artifact outside
    `tests/` on purpose: the invariant is "the test directory as the filesystem
    presents it", not "everything on disk", so an ignored build tree stays out
    of the copy and out of `/tmp`. An ignored file *under* `tests/` is the
    opposite case and is covered by
    `test_a_gitignored_test_file_is_in_the_probed_tree`.
    """
    repo = _repo(tmp_path, {
        "conftest.py": "",  # puts the repo root on sys.path for the fixture tests
        ".gitignore": "build/\n",
        "mod.py": CACHED_MODULE,
        "tests/test_base.py": "def test_base():\n    assert True\n",
    })
    _write(repo, {
        "mod.py": "def widen(value):\n    return value == 2\n",
        "tests/test_widen_new.py": UNSTAGED_TESTS,      # written, never staged
        "build/generated.py": "raise SystemExit('never copied')\n",
    })

    sites = [s for s in mp.collect_sites(str(repo), None, "tests", 6)[0]
             if s.label == "Eq -> NotEq"]
    assert len(sites) == 1 and sites[0].targeted == ("tests/test_widen_new.py",)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    mp.copy_worktree(str(repo), str(scratch),
                     must_include=mp.tests_tree_files(str(repo)))
    runner = mp.PytestRunner(str(scratch), sys.executable)
    report = mp.probe(sites, runner.targeted, runner.full, runner.baseline_full)

    # The outcome first: without the untracked file in the copy set this reads
    # `survived`, and the real tree kills it (measured, 2 failures).
    assert [o.status for o in report.outcomes] == [mp.KILLED], report.render()
    assert (scratch / "tests" / "test_widen_new.py").is_file()
    assert not (scratch / "build").exists()


IGNORED_TESTS = """\
    from mod import widen


    def test_two_is_two():
        assert widen(2) is True


    def test_three_is_not_two():
        assert widen(3) is False
"""


def test_a_gitignored_test_file_is_in_the_probed_tree(tmp_path):
    """The same defect as the test above, through the other half of git's edge.

    `--exclude-standard` closed the untracked-*non-ignored* half and left this
    one open, so the identical chain ran again: the ranker walks the filesystem
    and names `tests/local/test_widen.py`, the copy is built from git and does
    not contain it, pytest exits non-zero on the missing path, the targeted
    baseline reads red, the site escalates, and the full suite it escalates to
    is missing the same killing test. Measured on the unfixed code: `survived:
    1`, plus a `targeted selection red without any mutation` note reassuring
    the reader about an escalation that was blind for the same reason. The real
    tree kills it — `2 failed, 1 passed`.

    A `.gitignore`d test directory is an ordinary arrangement (a local-only
    integration suite, a generated matrix), and pytest has no idea git ignores
    it: in the real tree those tests are simply part of the suite. A copy that
    matches the real tree is the faithful one. If such a test is *failing*, the
    honest consequence is a red full-suite baseline and `unknown` outcomes,
    which the report names — not a survivor claim the escalation never checked.
    """
    repo = _repo(tmp_path, {
        "conftest.py": "",  # puts the repo root on sys.path for the fixture tests
        ".gitignore": "tests/local/\n",
        "mod.py": CACHED_MODULE,
        "tests/test_base.py": "def test_base():\n    assert True\n",
    })
    _write(repo, {
        "mod.py": "def widen(value):\n    return value == 2\n",
        "tests/local/test_widen.py": IGNORED_TESTS,     # ignored, so never staged
    })
    assert _git(repo, "status", "--porcelain").stdout.count("tests/local") == 0

    sites = [s for s in mp.collect_sites(str(repo), None, "tests", 6)[0]
             if s.label == "Eq -> NotEq"]
    assert len(sites) == 1 and sites[0].targeted == ("tests/local/test_widen.py",)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    mp.copy_worktree(str(repo), str(scratch),
                     must_include=mp.tests_tree_files(str(repo)))
    runner = mp.PytestRunner(str(scratch), sys.executable)
    report = mp.probe(sites, runner.targeted, runner.full, runner.baseline_full)

    assert [o.status for o in report.outcomes] == [mp.KILLED], report.render()
    assert report.notes == [], report.render()


def test_every_test_the_ranker_can_name_is_in_the_probed_tree(tmp_path):
    """The invariant, over every way a file can be invisible to git at once.

    Three rounds of this defect were three git-invisibility mechanisms
    (tracked-only, untracked-non-ignored, `.gitignore`d) patched one at a time,
    each fix closing one and leaving its sibling live. The property is not any
    one of them: it is that the ranker and the copy may not disagree about what
    "the suite" is. So this asserts the set relation over every mechanism at
    once, by set difference and not by name — a fourth mechanism added to the
    fixture is covered without touching the assertion. Mechanisms four and five
    were *not* git mechanisms (a filename pytest matches and this ranker did
    not, a directory symlink) and are pinned by their own end-to-end cases;
    `test_the_scratch_copy_is_given_the_whole_test_tree_not_a_view_of_it` is
    what covers the one nobody has thought to put in a fixture.
    """
    repo = _repo(tmp_path, {
        ".gitignore": "tests/ignored_dir/\n",
        "tests/test_tracked.py": "def test_t():\n    assert True\n",
        "tests/nested/.gitignore": "test_nested_ignored.py\n",
    })
    (repo / ".git" / "info" / "exclude").write_text(
        "tests/test_excluded.py\n", encoding="utf-8")
    _write(repo, {
        "tests/test_untracked.py": "def test_u():\n    assert True\n",
        "tests/ignored_dir/test_ignored.py": "def test_i():\n    assert True\n",
        "tests/nested/test_nested_ignored.py": "def test_n():\n    assert True\n",
        "tests/test_excluded.py": "def test_e():\n    assert True\n",
    })

    ranked = set(mp.discover_tests(str(repo)))
    # The fixture is not vacuous: every mechanism produced a file the ranker
    # can name, and all but one are invisible to the git-derived copy set.
    assert len(ranked) == 5, sorted(ranked)
    git_visible = set(_git(repo, "ls-files").stdout.split()) | set(
        _git(repo, "ls-files", "-o", "--exclude-standard").stdout.split())
    assert ranked - git_visible == {
        "tests/ignored_dir/test_ignored.py",
        "tests/nested/test_nested_ignored.py",
        "tests/test_excluded.py",
    }, sorted(ranked - git_visible)

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    mp.copy_worktree(str(repo), str(scratch),
                     must_include=mp.tests_tree_files(str(repo)))
    copied = {str(f.relative_to(scratch)) for f in scratch.rglob("*") if f.is_file()}
    assert ranked - copied == set(), sorted(ranked - copied)


def test_the_scratch_copy_is_given_the_whole_test_tree_not_a_view_of_it(tmp_path, monkeypatch):
    """The invariant on the wiring, where no git mechanism can reach it.

    The fixture above enumerates the ways git and the filesystem disagree
    *today*, and the round before this one answered them by handing the
    **ranker's** output to the copy — which only swapped a git rule for a name
    rule. `boundary_test.py` matches pytest's other default `python_files`
    pattern and no `test_*` ranker names it, so the class was still open.

    So this asserts the relation that has no rule to be narrow about: whatever
    is under `tests_dir` is what `main` hands to the copy, `conftest.py` and
    fixture datafiles included, and the ranker's universe is a subset of it
    rather than the source of it. A sixth divergence — a discovery rule that
    grows a suffix, a copy set rebuilt from the diff again — reddens here.
    """
    repo = _repo(tmp_path, {
        "m.py": MODULE,
        "tests/test_m.py": "def test_a():\n    assert 1\n",
        "tests/boundary_test.py": "def test_b():\n    assert 1\n",
        "tests/conftest.py": "",
        "tests/data/fixture.json": "{}",
    })
    (repo / "m.py").write_text(
        textwrap.dedent(MODULE).replace("value > 10", "value > 12"), encoding="utf-8")

    handed = []
    real_copy = mp.copy_worktree
    monkeypatch.setattr(mp, "copy_worktree", lambda root, dest, must_include=(), notes=None: (
        handed.append(set(must_include)),
        real_copy(root, dest, must_include=must_include, notes=notes))[1])
    monkeypatch.setattr(mp, "probe", lambda *a, **k: mp.Report())

    assert mp.main(["--repo-root", str(repo), "--python", sys.executable]) == 0
    assert handed == [{
        "tests/test_m.py", "tests/boundary_test.py",
        "tests/conftest.py", "tests/data/fixture.json",
    }], handed
    # The ranker sees pytest's two default patterns, and whatever it sees is a
    # subset of what the copy is given — never the other way round.
    ranked = set(mp.discover_tests(str(repo)))
    assert ranked == {"tests/test_m.py", "tests/boundary_test.py"}, sorted(ranked)
    assert ranked <= handed[0]


def test_the_scratch_copy_never_receives_bytecode(tmp_path):
    """A `.pyc` in the copy is the stale-cache false survivor by another route.

    `PYTHONDONTWRITEBYTECODE` stops the *runner* creating one. It does nothing
    about a `__pycache__` already in the probed repository, which
    `ls-files -o --exclude-standard` reports as an ordinary untracked file
    whenever the project does not ignore it. Reproduced 2026-09-10 by running
    pytest once in a fixture repo before probing it: the stale bytecode of the
    unmutated `mod.py` was copied, CPython validated it against a mutation of
    the same size and second, and `Eq -> NotEq` read `survived` for a mutation
    the tree kills `2 failed, 1 passed`.
    """
    repo = _repo(tmp_path, {"m.py": MODULE, "tests/test_m.py": "def test_a():\n    assert 1\n"})
    _write(repo, {
        "__pycache__/m.cpython-312.pyc": "stale",
        "tests/__pycache__/test_m.cpython-312.pyc": "stale",
    })
    assert "__pycache__" in _git(repo, "ls-files", "-o", "--exclude-standard").stdout

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    notes: list = []
    mp.copy_worktree(str(repo), str(scratch),
                     must_include=mp.tests_tree_files(str(repo)), notes=notes)

    assert list(scratch.rglob("*.pyc")) == [], list(scratch.rglob("*.pyc"))
    assert list(scratch.rglob("__pycache__")) == []
    assert (scratch / "tests" / "test_m.py").is_file()
    # Excluded on purpose is not "could not be copied": no note for these.
    assert notes == [], notes


def test_a_symlink_loop_under_the_tests_dir_terminates(tmp_path):
    """`followlinks=True` is what sees a symlinked suite, and what can loop.

    A self-referential directory symlink is not exotic (`tests/current -> .` in
    a versioned layout). Without the resolved-directory prune the walk never
    returns, and the probe hangs before it runs anything.
    """
    repo = _repo(tmp_path, {"tests/test_m.py": "def test_a():\n    assert 1\n"})
    os.symlink(".", repo / "tests" / "here")

    assert mp.tests_tree_files(str(repo)) == ["tests/test_m.py"]


def test_a_must_include_path_outside_the_scratch_tree_is_refused(tmp_path):
    """A copy set that escapes `dest` is refused, not recorded.

    `--tests-dir ../outside/tests` is enough: `discover_tests` returns paths
    relative to `--repo-root`, so the walk yields `../outside/tests/test_out.py`
    and the join onto `dest` lands *above* the scratch directory. Nothing about
    that is caught by the git-invisibility fixtures — they all use the default
    `tests_dir` and real regular files — and the wiring test asserts only the
    hand-off, not where the bytes went.

    Recording it would be too late. `shutil.rmtree(scratch)` never reaches the
    write, and enough `..` reaches the real working tree, which is the one thing
    §4 of the REQ forbids outright. So this is the one `must_include` failure
    that stops the run instead of becoming a note.
    """
    repo = _repo(tmp_path, {"m.py": MODULE})
    _write(tmp_path, {"outside/tests/test_out.py": "def test_o():\n    assert True\n"})
    escaping = mp.discover_tests(str(repo), "../outside/tests")
    assert escaping == ["../outside/tests/test_out.py"], escaping

    # The scratch directory is nested so the escaped write lands somewhere the
    # naive join really would create: `<box>/outside/tests/test_out.py`.
    scratch = tmp_path / "box" / "scratch"
    scratch.mkdir(parents=True)
    notes: list = []
    try:
        mp.copy_worktree(str(repo), str(scratch), must_include=escaping, notes=notes)
    except ValueError as exc:
        assert "../outside/tests/test_out.py" in str(exc), exc
    else:
        raise AssertionError("an escaping must_include path was copied")
    assert notes == [], notes
    assert not (tmp_path / "box" / "outside").exists(), \
        sorted(str(f) for f in (tmp_path / "box").rglob("*"))
    assert list(scratch.rglob("*")) == [], list(scratch.rglob("*"))

    # And `main` turns it into a stated refusal rather than a traceback.
    (repo / "m.py").write_text(
        textwrap.dedent(MODULE).replace("value > 10", "value > 12"), encoding="utf-8")
    assert mp.main(["--repo-root", str(repo), "--tests-dir", "../outside/tests",
                    "--python", sys.executable]) == 1


def test_a_must_include_path_that_is_not_a_regular_file_is_recorded(tmp_path):
    """A ranked path the copy cannot place becomes a note, never silence.

    A broken symlink named `test_*.py` is ranked by `discover_tests` — it walks
    filenames, not stat results — and then skipped by the copy's `isfile`
    guard. Left silent, that is the invariant's own failure mode with no
    reader-visible trace: the ranker names a path the scratch tree lacks, the
    targeted baseline reads red, and the escalation is blind for the same
    reason. It cannot be copied, so the run continues — but AC-3's rule applies,
    and it says what it did not do.
    """
    repo = _repo(tmp_path, {"tests/test_real.py": "def test_r():\n    assert True\n"})
    os.symlink("nowhere.py", repo / "tests" / "test_broken.py")

    ranked = mp.discover_tests(str(repo))
    assert ranked == ["tests/test_broken.py", "tests/test_real.py"], ranked

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    notes: list = []
    mp.copy_worktree(str(repo), str(scratch), must_include=ranked, notes=notes)

    copied = {str(f.relative_to(scratch)) for f in scratch.rglob("*") if f.is_file()}
    assert "tests/test_real.py" in copied and "tests/test_broken.py" not in copied, copied
    assert len(notes) == 1 and "tests/test_broken.py" in notes[0], notes


def test_an_input_the_probe_cannot_read_is_refused_not_reported_as_empty(tmp_path, capsys):
    """A run that could not read its input must not read as a complete one.

    `git diff not-a-rev..also-not` exits 128, and discarding that returncode
    left an empty diff — so `mutation probe: no mutable changed lines`, exit 0,
    indistinguishable from an audited range with nothing mutable in it. Same
    principle as the truncation notice, one level up: the report may not claim
    coverage of something it never looked at.

    The two operator-typable neighbours are here for the same reason. A
    `--repo-root` that is not a directory and a `--python` that is not an
    executable each answered with a traceback, and a *relative* `--python` is
    the easy one to get wrong twice — it is what this repository's own suite
    uses, and the child runs with `cwd=<scratch>`, so it must be resolved
    against the operator's cwd or refused, never silently resolved against the
    scratch tree.
    """
    repo = _repo(tmp_path, {"m.py": MODULE})
    (repo / "m.py").write_text(
        textwrap.dedent(MODULE).replace("value > 10", "value > 12"), encoding="utf-8")

    assert mp.main(["--repo-root", str(repo), "--rev", "not-a-rev..also-not",
                    "--python", sys.executable]) == 1
    out = capsys.readouterr().out
    assert "cannot run" in out and "no mutable changed lines" not in out, out
    # Naming the range and quoting git's own reason, not merely refusing: the
    # operator has to be able to see *which* input was unreadable and why.
    assert "not-a-rev..also-not" in out and "bad revision" in out, out

    assert mp.main(["--repo-root", str(tmp_path / "nowhere"),
                    "--python", sys.executable]) == 1
    assert mp.main(["--repo-root", str(repo), "--python", "no/such/python"]) == 1
    out = capsys.readouterr().out
    assert out.count("cannot run") == 2, out

    # A directory that exists but is not a repository is the same class, and
    # used to render as a clean empty result too.
    plain = tmp_path / "plain"
    plain.mkdir()
    assert mp.main(["--repo-root", str(plain), "--python", sys.executable]) == 1


def test_a_tests_dir_that_matches_nothing_says_so(tmp_path):
    """Zero discovered tests is legal, expensive, and must not be silent.

    Every site's targeted stage is empty, so every one escalates to a full
    suite. The answer stays honest; a mistyped `--tests-dir` just pays minutes
    for it, and the report is the only place that can say why.
    """
    repo = _repo(tmp_path, {"m.py": MODULE, "tests/test_m.py": "def test_a():\n    assert 1\n"})
    (repo / "m.py").write_text(
        textwrap.dedent(MODULE).replace("value > 10", "value > 12"), encoding="utf-8")

    notes: list = []
    sites, _files = mp.collect_sites(str(repo), None, "nope", 6, notes=notes)
    assert sites and all(site.targeted == () for site in sites)
    assert len(notes) == 1 and "nope/" in notes[0], notes

    notes = []
    mp.collect_sites(str(repo), None, "tests", 6, notes=notes)
    assert notes == [], notes


# --------------------------------------------------------------------------
# AC-4 — the failure class it exists for, end to end
# --------------------------------------------------------------------------

GUARD = """\
    def session_resumes(status, held, target):
        if status == "open" and held == target:
            return True
        return False
"""

# Every case gives an open task, exactly as every fixture did in the tree that
# shipped the real defect. The conjunct is therefore never observed and no
# assertion here changes when it is deleted.
GUARD_TESTS = """\
    from guard import session_resumes


    def test_a_session_holding_the_target_resumes():
        assert session_resumes("open", "/t", "/t") is True


    def test_a_session_holding_another_task_does_not_resume():
        assert session_resumes("open", "/other", "/t") is False
"""


def test_a_guard_branch_no_test_reaches_is_reported_as_a_survivor(tmp_path):
    """The class named in `7c41aac`: a conjunct every fixture satisfied.

    Run end to end — real diff, real scratch copy, real pytest, real
    escalation — because a survivor claim that has not been through the full
    suite is exactly the noise AC-2 forbids.
    """
    repo = _repo(tmp_path, {
        "conftest.py": "",  # puts the repo root on sys.path for the fixture tests
        "guard.py": "def session_resumes():\n    return False\n",
        "tests/test_guard.py": "def test_placeholder():\n    assert True\n",
    })
    _write(repo, {"guard.py": GUARD, "tests/test_guard.py": GUARD_TESTS})

    # The two conjunct drops only. The rest of the rewritten fixture is real
    # diff too, and every extra site is another pytest process: measured 0.3s
    # on an idle host and over 3s on a loaded one.
    sites = [s for s in mp.collect_sites(str(repo), None, "tests", 6)[0]
             if "drop operand" in s.label]
    assert len(sites) == 2
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    mp.copy_worktree(str(repo), str(scratch),
                     must_include=mp.tests_tree_files(str(repo)))
    runner = mp.PytestRunner(str(scratch), sys.executable)
    report = mp.probe(sites, runner.targeted, runner.full, runner.baseline_full)

    survivors = {o.site.label for o in report.survivors()}
    assert "drop operand 1 of And" in survivors, report.render()
    # And the reachable half of the same guard is killed, so this is a signal
    # and not a tool that reports everything.
    assert "drop operand 2 of And" not in survivors, report.render()


# --------------------------------------------------------------------------
# AC-4 — the ceiling, pinned as a property rather than asserted in prose
# --------------------------------------------------------------------------

# The counts-slot reader of `8432d22`, reduced to the two readers that matter.
# It is a *shared* constant: the diff below rewires the verdict reader and
# leaves this text byte-identical, exactly as the shipped change did.
COUNTS_READER = '''
def normalize_review_counts(raw_summary):
    summary_lines = raw_summary.splitlines()
    counts = (
        summary_lines[1].strip()
        if len(summary_lines) > 1 else ""
    )
    return counts.startswith("FINDING_COUNTS: ")
'''

COUNTS_DEFECT_LINE = "        summary_lines[1].strip()"

BINDER_BEFORE = '''\
NOTICE = "[harness: subagent output matched"


def extract_qa_verdict(value):
    lines = str(value or "").splitlines()
    if not lines:
        return ""
    return lines[0].strip()

''' + COUNTS_READER

BINDER_AFTER = '''\
NOTICE = "[harness: subagent output matched"


def _verdict_aligned_lines(value):
    lines = str(value or "").splitlines()
    if not lines or not lines[0].startswith(NOTICE):
        return lines
    index = 1
    while index < len(lines) and not lines[index].strip():
        index += 1
    return lines[index:]


def extract_qa_verdict(value):
    lines = _verdict_aligned_lines(value)
    if not lines:
        return ""
    return lines[0].strip()

''' + COUNTS_READER


def test_the_counts_slot_defect_is_out_of_reach_because_its_line_is_not_in_the_diff(tmp_path):
    """The honest ceiling, as a checked property instead of a paragraph.

    `8432d22`'s first implementation offset-aligned `extract_qa_verdict` and
    left `normalize_receipt_completion` reading `summary_lines[1]`, so a
    wrapped review final read a blank separator as its counts line. Two review
    rounds passed that tree. A scoped mutation runner cannot reach it, and the
    reason is not that the scope is too narrow: the defective line **was not in
    that diff at all** — the change that broke it never touched it. It was a
    missing *case*, and mutation testing finds tests that do not discriminate,
    not cases nobody wrote.

    That claim is the sort that stops being true quietly — if site selection
    later widens past the changed lines, nobody finds out from a REQ paragraph.
    (Hunk parsing is not this test's property, though an earlier draft of this
    docstring claimed it was: widening `changed_python_lines` by one line per
    hunk leaves this test green. The tests the REQ names as owners of that
    property redden under it; the list is owned-by, not exhaustive — an
    earlier exhaustive count here was itself falsified by a later round's
    added tests, which is exactly the failure this note now avoids repeating.)
    So it is asserted here, and asserted
    for the right reason: not "this run produced no findings" (a broken probe
    passes that), but "no site at *that line*, while the same line is a site the
    moment it is in scope, and the same diff yields sites elsewhere".
    """
    repo = _repo(tmp_path, {"binder.py": BINDER_BEFORE})
    _write(repo, {"binder.py": BINDER_AFTER})

    # The fixture pins its own content: if the reconstruction stops containing
    # the defective line, or stops leaving it out of the diff, this test says so
    # rather than passing on an empty premise.
    after = BINDER_AFTER.splitlines()
    assert after.count(COUNTS_DEFECT_LINE) == 1
    defect_lineno = after.index(COUNTS_DEFECT_LINE) + 1
    reader_start = after.index("def normalize_review_counts(raw_summary):") + 1
    changed = mp.changed_python_lines(str(repo))
    assert "binder.py" in changed, "the reconstruction produced no diff at all"
    assert not (changed["binder.py"] & set(range(reader_start, len(after) + 1)))

    sites, _files = mp.collect_sites(str(repo), None, "tests", 6)
    # The probe is alive on this diff — the rewired verdict reader is covered.
    assert sites, "no sites at all: the negative below would be vacuous"
    assert all(site.lineno < reader_start for site in sites), \
        [(s.lineno, s.label) for s in sites]

    # And the line is not un-mutable — `1 -> 2` is offered the instant it is in
    # scope. Nothing about this line defeats the operator set; only the diff
    # boundary keeps it out, which is what makes this a ceiling and not a bug.
    in_scope = mp.mutation_sites("binder.py", BINDER_AFTER, {defect_lineno})
    assert [s.label for s in in_scope] == ["1 -> 2"]


PATTERN_TESTS = """\
    from mod import widen


    def test_two_is_two():
        assert widen(2) is True


    def test_three_is_not_two():
        assert widen(3) is False
"""


def test_a_test_file_pytest_collects_under_its_other_default_name_is_in_the_probed_tree(tmp_path):
    """`*_test.py` — pytest's other default `python_files` pattern.

    Mechanism four of the defect the three git rounds kept re-opening, reached
    by changing one filename. The file is invisible to git (ignored) *and* to a
    ranker that matches only `test_*`, so it lands in neither the copy set nor
    the diff: both stages blind again, and `Eq -> NotEq` reads `survived` for a
    mutation the real tree kills. What decides the copy set may not be a name
    rule narrower than pytest's own.
    """
    repo = _repo(tmp_path, {
        "conftest.py": "",  # puts the repo root on sys.path for the fixture tests
        ".gitignore": "tests/local/\n",
        "mod.py": CACHED_MODULE,
        "tests/test_base.py": "def test_base():\n    assert True\n",
    })
    _write(repo, {
        "mod.py": "def widen(value):\n    return value == 2\n",
        "tests/local/boundary_test.py": PATTERN_TESTS,   # ignored, so never staged
    })

    sites = [s for s in mp.collect_sites(str(repo), None, "tests", 6)[0]
             if s.label == "Eq -> NotEq"]
    assert len(sites) == 1
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    mp.copy_worktree(str(repo), str(scratch),
                     must_include=mp.tests_tree_files(str(repo)))
    runner = mp.PytestRunner(str(scratch), sys.executable)
    report = mp.probe(sites, runner.targeted, runner.full, runner.baseline_full)

    # `== KILLED`, not merely "not a survivor": the two halves of the fix fail
    # this line differently. A copy set narrower than pytest gives `survived`;
    # a *ranker* narrower than pytest gives `killed (full suite only)` — still
    # correct, and still a full suite paid for a file a name rule could not see.
    assert [o.status for o in report.outcomes] == [mp.KILLED], report.render()
    assert (scratch / "tests" / "local" / "boundary_test.py").is_file()


def test_a_test_directory_reached_through_a_symlink_is_in_the_probed_tree(tmp_path):
    """The same false survivor without git being wrong about anything.

    `tests/deep` is a tracked symlink to a directory outside the repository.
    pytest follows it and collects `tests/deep/test_deep.py`; `os.walk` does not
    follow it by default, so the ranker never names the file, and the copy's
    `isfile` guard drops the symlink itself. Nothing under it reaches the
    scratch tree and the escalation runs a suite the killing test is missing
    from — `survived`, for a mutation the real tree kills.
    """
    _write(tmp_path, {"shared/test_deep.py": PATTERN_TESTS})
    repo = _repo(tmp_path, {
        "conftest.py": "",  # puts the repo root on sys.path for the fixture tests
        "mod.py": CACHED_MODULE,
        "tests/test_base.py": "def test_base():\n    assert True\n",
    })
    os.symlink("../../shared", repo / "tests" / "deep")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "symlinked suite")
    _write(repo, {"mod.py": "def widen(value):\n    return value == 2\n"})

    sites = [s for s in mp.collect_sites(str(repo), None, "tests", 6)[0]
             if s.label == "Eq -> NotEq"]
    assert len(sites) == 1
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    notes: list = []
    mp.copy_worktree(str(repo), str(scratch),
                     must_include=mp.tests_tree_files(str(repo)), notes=notes)
    runner = mp.PytestRunner(str(scratch), sys.executable)
    report = mp.probe(sites, runner.targeted, runner.full, runner.baseline_full)

    assert [o.status for o in report.outcomes] == [mp.KILLED], report.render()
    assert (scratch / "tests" / "deep" / "test_deep.py").is_file()
    # The symlink is not "a path the copy could not place": its contents are
    # there, under the name pytest reads them by. Reporting it would be noise.
    assert notes == [], notes


# --------------------------------------------------------------------------
# spans are read in `ast`'s coordinates
# --------------------------------------------------------------------------

ASCII_GUARD = 'def f(msg, count):\n    if msg == "blocked" and count > 0:\n        return 1\n'
MULTIBYTE_GUARD = ASCII_GUARD.replace('"blocked"', '"\ucc28\ub2e8\ub428"')


def test_a_multibyte_literal_on_the_line_does_not_drop_its_sites():
    """`ast` reports columns in UTF-8 bytes; the spans index a `str`.

    Unconverted, every span on a line with a multibyte literal before the node
    is shifted by the byte/character delta. Here the literal is 3 characters
    and 9 bytes: all six mutants of the ASCII line failed the `compile()` guard
    on its twin and were dropped, so the line read as fully covered while
    nothing on it had been attempted. This repository had 13 such candidates
    live, in `install.py` and three test files.
    """
    notes: list = []
    ascii_sites = mp.mutation_sites("m.py", ASCII_GUARD, {2}, notes=notes)
    multi_sites = mp.mutation_sites("m.py", MULTIBYTE_GUARD, {2}, notes=notes)

    assert [s.label for s in ascii_sites] == [s.label for s in multi_sites]
    assert len(multi_sites) == 6, [s.label for s in multi_sites]
    assert notes == [], notes
    guard_line = MULTIBYTE_GUARD.split("\n")[1]
    for site in multi_sites:
        # Not merely "the span is non-empty": it has to be the node's own text.
        assert MULTIBYTE_GUARD[site.start:site.end] in guard_line


REPEATED_COMPARISONS = (
    'def f(a):\n    return ["\uac00\ub098\ub2e4\ub77c\ub9c8\ubc14\uc0ac\uc544", a == 3, a == 3, a == 3]\n'
)


def test_a_mutation_lands_on_the_span_its_label_names():
    """The shifted-span failure that is worse than a dropped site.

    A shift that still compiles reports a verdict against a site it never
    mutated. With the multibyte literal first on the line, the three identical
    comparisons shifted onto each other: the first site's `Eq -> NotEq` mutated
    the *third* comparison, and the last one resolved to an empty span past the
    end of the line, so `Site.apply` **appended** `a != 3` to the module and
    left line 2 untouched — under the label `m.py:2 [Eq -> NotEq]`.

    So this asserts where the mutated source differs, not that it differs.
    """
    sites = [s for s in mp.mutation_sites("m.py", REPEATED_COMPARISONS, {2})
             if s.label == "Eq -> NotEq"]
    assert len(sites) == 3

    for index, site in enumerate(sites):
        assert REPEATED_COMPARISONS[site.start:site.end] == "a == 3"
        mutated = site.apply(REPEATED_COMPARISONS)
        assert mutated.count("a != 3") == 1
        # Nothing appended, and the *n*th comparison is the one that moved.
        assert len(mutated.split("\n")) == len(REPEATED_COMPARISONS.split("\n"))
        comparisons = mutated.split("\n")[1].split(", ")[1:]
        assert [c.startswith("a != 3") for c in comparisons] == \
            [i == index for i in range(3)], mutated


def test_a_form_feed_does_not_shift_the_lines_under_the_spans():
    r"""The same disagreement in the other coordinate.

    `str.splitlines` breaks on form feed (and `\x0b`, `\x85`, `\u2028`); the
    tokenizer `ast` reports against does not. One `\f` between two statements
    put every following line's offsets one line early, which is the shifted-span
    failure above with a different cause: the `2 == 3` span was dropped by the
    `compile()` guard and `3 -> 4` mutated a space into `y = (2 ==43)`.
    """
    source = "x = 1\n\x0c\ny = (2 == 3)\n"

    spans = [source[s.start:s.end] for s in mp.mutation_sites("m.py", source, {3})]
    assert sorted(spans) == ["2", "2 == 3", "3"], spans


def test_a_candidate_the_compile_guard_declines_is_named(monkeypatch):
    """AC-3, applied to the site list rather than to the run.

    A candidate the probe declines to attempt is not a survivor and not a kill;
    silently dropping it makes a partial run over a line read as complete
    coverage of it, and `Report.render` speaks only about `not_attempted`. The
    guard is genuinely rare — `ast.unparse` is forced to lose the round-trip
    here — but the byte-offset bug above fired it six times on one line without
    a word.
    """
    monkeypatch.setattr(mp.ast, "unparse", lambda node: "a ==== 3")
    notes: list = []

    sites = mp.mutation_sites("m.py", "def f(a):\n    return a == 3\n", {2}, notes=notes)

    # The constant shift carries its own replacement text and is unaffected.
    assert [s.label for s in sites] == ["3 -> 4"]
    assert len(notes) == 1, notes
    assert "not attempted" in notes[0] and "Eq -> NotEq" in notes[0], notes


def test_a_file_that_does_not_parse_is_named_not_silently_skipped():
    """The same rule as the compile guard, one file up instead of one candidate.

    A UTF-8 BOM reaches this branch on a file CPython compiles happily, because
    `source_at` opens with `utf-8` rather than `utf-8-sig`, so the BOM survives
    into the string. Without the note the whole file contributes zero sites and
    the run says nothing — a file that was never attempted reading as a file
    that was covered, which is AC-3's failure at file granularity.
    """
    notes: list = []
    bommed = "\ufeff" + "def f(a):\n    return a == 3\n"

    sites = mp.mutation_sites("m.py", bommed, {2}, notes=notes)

    assert sites == []
    assert len(notes) == 1, notes
    assert "m.py" in notes[0] and "no site was offered" in notes[0], notes
    assert "UTF-8 BOM" in notes[0], notes
    assert "save as UTF-8 without BOM" in notes[0], notes
    # A file that parses produces no such note — the diagnostic must not fire
    # on the healthy path, or it becomes noise like every other one here.
    clean: list = []
    assert mp.mutation_sites("m.py", "def f(a):\n    return a == 3\n", {2}, notes=clean)
    assert clean == []


def test_an_ordinary_syntax_error_keeps_the_generic_parse_note():
    notes: list = []

    sites = mp.mutation_sites("m.py", "def f(:\n    return 3\n", {2}, notes=notes)

    assert sites == []
    assert len(notes) == 1, notes
    assert "m.py: not parsed, so no site was offered" in notes[0], notes
    assert "invalid syntax" in notes[0], notes
    assert "UTF-8 BOM" not in notes[0], notes


def test_a_changed_line_that_is_not_utf8_does_not_lose_the_rest_of_the_diff(tmp_path):
    """The instance the first attempt at this guard missed.

    `source_at`'s decode guard only runs for a file whose *hunks* are ASCII. The
    normal case is the opposite: a new latin-1 file, or an edit to a line that
    already carries the byte, puts it in `git diff -U0`'s own output, where
    `_git`'s decode raised before any per-file guard could name the path — the
    whole run exited 1 and every other file in the diff went with it, while the
    comment and the sibling test's name claimed the rest survived.

    Everything this module parses out of git is ASCII, so `surrogateescape`
    changes no parse and makes the note the outcome for both instances.
    """
    repo = _repo(tmp_path, {"m.py": MODULE})
    _write(repo, {"m.py": MODULE.replace("> 10", "> 11")})
    (repo / "legacy.py").write_bytes(b"# -*- coding: latin-1 -*-\nNAME = '\xe9'\n")
    _git(repo, "add", "-A")

    # The parse survives the undecodable bytes in git's own output.
    changed = mp.changed_python_lines(str(repo))
    assert "m.py" in changed, changed
    assert "legacy.py" in changed, changed

    notes: list = []
    sites, _files = mp.collect_sites(str(repo), None, "tests", 6, notes=notes)

    assert sites, "the readable file must still be probed"
    assert {s.path for s in sites} == {"m.py"}, sites
    assert any("legacy.py" in n and "not valid UTF-8" in n for n in notes), notes


def test_a_source_that_is_not_utf8_is_named_and_does_not_lose_the_rest(tmp_path):
    """One unreadable file must not cost the whole diff.

    `UnicodeDecodeError` is a `ValueError`, not an `OSError`, so it slipped past
    the read guard and aborted the run with a message naming no path — every
    other file in the diff lost with it. A legal source carrying a latin-1
    coding declaration is enough to reach it.
    """
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "legacy.py").write_bytes(
        b"# -*- coding: latin-1 -*-\nNAME = '\xe9'\n"
    )
    (repo / "pkg" / "fine.py").write_text("def f(a):\n    return a == 3\n")

    notes: list = []
    unreadable = mp.source_at(str(repo), "pkg/legacy.py", None, notes=notes)
    good = mp.source_at(str(repo), "pkg/fine.py", None, notes=notes)

    assert unreadable is mp._UNREADABLE
    assert good is not None and good is not mp._UNREADABLE
    assert len(notes) == 1, notes
    assert "pkg/legacy.py" in notes[0] and "not valid UTF-8" in notes[0], notes
    # The reason must be its own, not the revision-mismatch note: reporting a
    # decode failure as "the checkout does not match" sends the operator to
    # check out a revision that would not help.
    assert "does not match" not in notes[0], notes


def test_an_aliased_directory_and_its_canonical_path_are_both_in_the_tree(tmp_path):
    """The walk's prune is a cycle check, not a visited set.

    Keyed on the resolved directory, whichever path arrives *second* is pruned —
    including the canonical one. With `tests/link -> pkg`, `tests/link` sorts
    first and `tests/pkg/test_x.py` was never yielded, so the copy set was not
    "the test directory as the filesystem presents it" after all. pytest
    collects both.
    """
    repo = _repo(tmp_path, {"tests/pkg/test_x.py": "def test_x():\n    assert 1\n"})
    os.symlink("pkg", repo / "tests" / "link")
    os.symlink(".", repo / "tests" / "here")  # and a cycle is still cut

    assert mp.tests_tree_files(str(repo)) == [
        "tests/link/test_x.py",
        "tests/pkg/test_x.py",
    ]


def test_a_tests_dir_written_with_a_leading_dot_still_excludes_its_own_tests(tmp_path, capsys):
    """`--tests-dir ./tests` must not turn the diff's test files into subjects.

    `walk_tree` normalises through `os.path.relpath`, so the discovered paths
    are `tests/...` while the prefix built from the raw argument was `./tests/`.
    `changed_tests` came out empty: the changed test file became a mutation
    subject, which AC-1 forbids, and the promotion of the test the diff wrote
    was lost at the same time.
    """
    repo = _repo(tmp_path, {
        "m.py": "def widen(value):\n    return value > 10\n",
        "tests/test_m.py": "import m\n\n\ndef test_widen():\n    assert m.widen(11)\n",
    })
    _write(repo, {
        "m.py": "def widen(value):\n    return value > 12\n",
        "tests/test_m.py": "import m\n\n\ndef test_widen():\n    assert m.widen(13)\n",
    })

    assert mp.main(["--repo-root", str(repo), "--tests-dir", "./tests",
                    "--sites-only"]) == 0
    out = capsys.readouterr().out.splitlines()

    assert not [line for line in out if line.startswith("tests/test_m.py:")], out
    assert [line for line in out if line.startswith("m.py:")], out
    # And the test the diff wrote is still the promoted relevance signal.
    assert all("tests/test_m.py" in line for line in out if line.startswith("m.py:")), out


def test_a_tests_dir_of_dot_does_not_make_the_diffs_test_file_a_subject(tmp_path, capsys):
    """`test_prefix = tests_dir.rstrip("/") + os.sep` gave `"./"` for
    `tests_dir == "."`, but `walk_tree` returns `os.path.relpath` paths
    (`test_top.py`) that can never start with `"./"` — so `changed_tests` was
    always empty under `--tests-dir .`. A top-level test file the diff itself
    changed then became a mutation subject (AC-1 forbids that), and the
    promotion of the test the diff wrote as the targeted signal was lost at
    the same time. Only reachable once `--tests-dir .` became a supported
    configuration.
    """
    repo = _repo(tmp_path, {
        "m.py": "def widen(value):\n    return value > 10\n",
        "test_top.py": "import m\n\n\ndef test_widen():\n    assert m.widen(11)\n",
    })
    _write(repo, {
        "m.py": "def widen(value):\n    return value > 12\n",
        "test_top.py": "import m\n\n\ndef test_widen():\n    assert m.widen(13)\n",
    })

    assert mp.main(["--repo-root", str(repo), "--tests-dir", ".",
                    "--sites-only"]) == 0
    out = capsys.readouterr().out.splitlines()

    assert not [line for line in out if line.startswith("test_top.py:")], out
    assert [line for line in out if line.startswith("m.py:")], out
    assert all("test_top.py" in line for line in out if line.startswith("m.py:")), out


# --------------------------------------------------------------------------
# FIX 1 — `+++ ` header parse is anchored to a preceding `--- `, and a site
# path is never trusted to stay inside the tree it is read from or written to
# --------------------------------------------------------------------------

def test_an_added_line_shaped_like_a_diff_header_does_not_reattribute_hunks(tmp_path):
    """A `+++ ` *content* line — an added source line whose own text starts
    `++ ` — must not be read as a new file header, in the single-added-line
    form where no colluding removed line precedes it.

    Git renders an added line by prefixing it with `+`, so a line whose text
    is `++ b/b.py` appears in the diff as `+++ b/b.py`: syntactically
    identical to a real header. Unanchored, `changed_python_lines` treated it
    as one — every hunk in `a.py` after that line was reattributed to `b.py`,
    an untouched file, and `a.py`'s own remaining changed line contributed
    nothing. See
    `test_a_colluding_removed_and_added_line_pair_does_not_forge_a_header`
    for the two-line form, where a removed line renders `--- ...` immediately
    before the forged `+++ ...` and can fool an anchor that only checks for
    a preceding `--- ` line rather than a preceding `diff --git ` section.
    """
    repo = _repo(tmp_path, {
        "a.py": """\
            NOTE = \"\"\"
            placeholder
            \"\"\"

            def widen(value):
                if value > 10:
                    return "big"
                return "small"
            """,
        "b.py": "UNTOUCHED = 1\n",
    })
    (repo / "a.py").write_text(
        textwrap.dedent("""\
            NOTE = \"\"\"
            ++ b/b.py
            \"\"\"

            def widen(value):
                if value > 12:
                    return "big"
                return "small"
            """),
        encoding="utf-8")

    changed = mp.changed_python_lines(str(repo))
    assert "b.py" not in changed, changed
    assert changed == {"a.py": {2, 6}}, changed

    sites, files = mp.collect_sites(str(repo), None, "tests", 6)
    assert files == 1
    assert {s.path for s in sites} == {"a.py"}, sites
    # Line 2 sits inside a triple-quoted string — no executable content — so
    # only line 6's real change offers a site.
    assert {s.lineno for s in sites} == {6}, sites


def test_a_colluding_removed_and_added_line_pair_does_not_forge_a_header(tmp_path):
    """Anchoring `+++ ` on a preceding `--- ` line is not enough: a REMOVED
    source line whose own text starts `-- ` renders as `--- ...` (git's `-`
    prefix plus the line's own two dashes), and under `-U0` a one-line
    replacement places the removed line immediately before the added one —
    so a `-- old marker` / `++ b/elsewhere.py` edit produces the exact
    `--- .../+++ ...` pair a `--- `-anchored parser accepts as a real header.

    The only line git cannot let content forge is `diff --git ` at column 0:
    every content line carries a `+`, `-`, or space prefix, so a raw
    `diff --git ` line is always a true section boundary. The parser must
    anchor there instead.
    """
    repo = _repo(tmp_path, {
        "a.py": """\
            DOC = \"\"\"
            -- old marker
            \"\"\"

            def f(a):
                x = 0
                return a == 1
            """,
        "elsewhere.py": "UNTOUCHED = 1\n",
    })
    (repo / "a.py").write_text(
        textwrap.dedent("""\
            DOC = \"\"\"
            ++ b/elsewhere.py
            \"\"\"

            def f(a):
                x = 0
                return a == 2
            """),
        encoding="utf-8")

    changed = mp.changed_python_lines(str(repo))
    assert "elsewhere.py" not in changed, changed
    assert changed == {"a.py": {2, 7}}, changed

    sites, files = mp.collect_sites(str(repo), None, "tests", 6)
    assert files == 1
    assert {s.path for s in sites} == {"a.py"}, sites
    assert {s.lineno for s in sites} == {7}, sites


def test_source_at_refuses_a_path_that_escapes_the_repo_root(tmp_path):
    """Defense in depth: even if a bogus path slipped past the diff parser,
    `source_at` must not open a file outside `repo_root`."""
    repo = _repo(tmp_path, {"a.py": "X = 1\n"})
    victim = tmp_path / "victim.py"
    victim.write_text("SECRET = 1\n", encoding="utf-8")

    notes: list = []
    result = mp.source_at(str(repo), "../victim.py", None, notes=notes)
    assert result is mp._OUTSIDE_REPO, result
    assert any("victim.py" in n for n in notes), notes


def test_with_mutation_refuses_a_site_path_that_escapes_the_scratch_tree(tmp_path):
    """The write site is the one that matters: `_with_mutation` must refuse a
    relative site path whose real path resolves outside the scratch tree,
    rather than depend on an earlier read failing first."""
    repo = _repo(tmp_path, {"a.py": "X = 1\n"})
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    mp.copy_worktree(str(repo), str(scratch))
    victim = tmp_path / "victim.py"
    victim.write_text("SECRET = 1\n", encoding="utf-8")

    runner = mp.PytestRunner(str(scratch), sys.executable)
    site = _site(path="../victim.py")
    try:
        runner._with_mutation(site, [])
    except ValueError as exc:
        assert "victim.py" in str(exc) or "escapes" in str(exc), exc
    else:
        raise AssertionError("an escaping site path was written")
    assert victim.read_text(encoding="utf-8") == "SECRET = 1\n"


def test_with_mutation_refuses_an_absolute_site_path(tmp_path):
    repo = _repo(tmp_path, {"a.py": "X = 1\n"})
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    mp.copy_worktree(str(repo), str(scratch))
    victim = tmp_path / "abs_victim.py"
    victim.write_text("SECRET = 1\n", encoding="utf-8")

    runner = mp.PytestRunner(str(scratch), sys.executable)
    site = _site(path=str(victim))
    try:
        runner._with_mutation(site, [])
    except ValueError:
        pass
    else:
        raise AssertionError("an absolute site path was written")
    assert victim.read_text(encoding="utf-8") == "SECRET = 1\n"


# --------------------------------------------------------------------------
# FIX 2 — the diff header is parsed under a fixed convention, not the
# operator's git config, and a target that does not carry it is named
# --------------------------------------------------------------------------

def test_a_non_ascii_filename_is_not_hidden_by_default_quote_path(tmp_path):
    """`core.quotePath` defaults to true, which git-escapes a non-ASCII path
    inside quotes: `+++ "b/caf\\303\\251.py"`. Sliced blindly, `target[2:]`
    takes a quote-and-octal-escape mess, not a real path, and the file
    contributes zero sites with no word about why."""
    repo = _repo(tmp_path, {"m.py": MODULE})
    _write(repo, {"café.py": "X = 1\n"})
    _git(repo, "add", "café.py")

    assert mp.changed_python_lines(str(repo)) == {"café.py": {1}}


def test_diff_noprefix_config_does_not_break_the_parse(tmp_path):
    """`diff.noprefix=true` in the operator's gitconfig chops the `a/`/`b/`
    off every path git prints; sliced blindly, that took two real characters
    off every path and the whole run found nothing."""
    repo = _repo(tmp_path, {"m.py": MODULE})
    _git(repo, "config", "diff.noprefix", "true")
    (repo / "m.py").write_text(
        textwrap.dedent(MODULE).replace("value > 10", "value > 12"), encoding="utf-8")

    assert mp.changed_python_lines(str(repo)) == {"m.py": {2}}


def test_a_target_without_the_expected_prefix_is_named_not_sliced_blindly(tmp_path, monkeypatch):
    class FakeDone:
        returncode = 0
        stderr = ""
        stdout = (
            "diff --git a/weird.py b/weird.py\n"
            "--- a/weird.py\n"
            "+++ weird.py\n"
            "@@ -1 +1 @@\n"
            "-X = 1\n"
            "+X = 2\n"
        )

    monkeypatch.setattr(mp, "_git", lambda repo_root, *a: FakeDone())
    notes: list = []
    changed = mp.changed_python_lines(str(tmp_path), notes=notes)
    assert changed == {}, changed
    assert any("weird.py" in n for n in notes), notes


def test_a_source_missing_from_the_checkout_is_named_not_dropped_in_silence(tmp_path, monkeypatch):
    """`collect_sites`'s `source is None` branch only spoke when the file
    existed on disk with mismatched content; a path the checkout does not
    have *at all* was dropped without a word."""
    repo = _repo(tmp_path, {"a.py": "X = 1\n"})

    def fake_changed(repo_root, rev=None, notes=None):
        return {"missing.py": {1}}

    monkeypatch.setattr(mp, "changed_python_lines", fake_changed)
    notes: list = []
    sites, files = mp.collect_sites(str(repo), None, "tests", 6, notes=notes)
    assert sites == []
    assert any("missing.py" in n for n in notes), notes


# --------------------------------------------------------------------------
# FIX 3 — `--tests-dir .` must not be mistaken for a symlink cycle
# --------------------------------------------------------------------------

def test_a_tests_dir_of_dot_is_not_mistaken_for_a_symlink_cycle(tmp_path):
    """`_contains_itself` compared `realpath(dirpath)` against
    `realpath(dirname(dirpath))`; for the walk root `<repo>/.` the dirname is
    `<repo>` itself, so the root read as its own parent and the whole walk was
    pruned at the first iteration — `tests_tree_files`/`discover_tests`
    silently returned nothing for a repo whose tests live at top level."""
    repo = _repo(tmp_path, {"test_top.py": "def test_a():\n    assert True\n"})

    assert mp._contains_itself(str(repo) + os.sep + ".") is False
    assert mp.tests_tree_files(str(repo), ".") == ["test_top.py"]
    assert mp.discover_tests(str(repo), ".") == ["test_top.py"]


def test_a_genuine_cycle_through_an_ancestor_still_terminates(tmp_path):
    """The normpath fix must not weaken real cycle detection: `self -> .`
    still resolves to an ancestor and the walk still terminates."""
    repo = _repo(tmp_path, {"tests/test_m.py": "def test_a():\n    assert 1\n"})
    os.symlink(".", repo / "tests" / "self")

    assert mp.tests_tree_files(str(repo)) == ["tests/test_m.py"]


def test_a_root_level_gitignored_test_file_is_found_with_tests_dir_dot(tmp_path):
    """The false-survivor reproduction: a repo whose tests live at the top
    level, with a `.gitignore`d test file matching pytest's other default
    `python_files` pattern (`*_test.py`), must still be in the copy set when
    `--tests-dir .` is used."""
    repo = _repo(tmp_path, {
        "widen.py": "def widen(value):\n"
                    "    if value > 10:\n"
                    "        return 'big'\n"
                    "    return 'small'\n",
        ".gitignore": "boundary_test.py\n",
    })
    _write(repo, {
        "boundary_test.py": "from widen import widen\n\n\n"
                             "def test_boundary():\n"
                             "    assert widen(11) == 'big'\n"
                             "    assert widen(9) == 'small'\n",
    })

    assert "boundary_test.py" in mp.tests_tree_files(str(repo), ".")


def test_a_tests_dir_of_dot_does_not_copy_directories_pytest_never_collects(tmp_path):
    """`--tests-dir .` must stay a superset of what pytest actually collects,
    not of the whole filesystem tree it happens to sit in — at the layer
    that actually matters, the scratch copy `copy_worktree` produces, not
    only the `tests_tree_files` list fed into it as `must_include`.

    `tests_tree_files`'s own walk is pruned (`walk_tree`'s `_pytest_prunes`),
    but `copy_worktree` unions that against `git ls-files -o
    --exclude-standard`, which does not prune anything: git has no concept of
    `norecursedirs`. An untracked, un-gitignored `.venv`/`node_modules` still
    reaches the scratch tree through that second route even when the first
    one is fixed — asserting only on `tests_tree_files` cannot see this.

    pytest's own default `norecursedirs` — `*.egg`, `.*`, `_darcs`, `build`,
    `CVS`, `dist`, `node_modules`, `venv`, `{arch}` — means it never walks
    into a `.venv`, a `node_modules`, or a `build` directory. Copying them
    into the scratch tree anyway cannot be required by the superset
    invariant, since pytest was never going to collect anything from them
    either.

    **Historical measurement, dated rather than re-derived:** on 2026-09-10,
    before the `walk_tree` prune this test exists to pin, `tests_tree_files`
    on the real harness repo returned 86 files for `'tests'` and 2759 for
    `'.'` (1949 of them under `.venv`/`.omc` alone) — the magnitude that
    motivated this fix. The second number necessarily shrinks on any
    already-pruned tree, including this repository's own current one, so
    re-measuring it does not describe the defect this note exists to justify;
    it is kept as a fixed, dated fact rather than a claim about the tree's
    present size.
    """
    repo = _repo(tmp_path, {"test_top.py": "def test_a():\n    assert True\n"})
    _write(repo, {
        ".venv/lib/site.py": "X = 1\n",
        ".hidden/also_skipped.py": "Y = 1\n",
        "node_modules/pkg/index.js": "x",
        "build/out.py": "Z = 1\n",
        "dist/out.py": "Z = 1\n",
    })

    must_include = mp.tests_tree_files(str(repo), ".")
    assert must_include == ["test_top.py"]

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    mp.copy_worktree(str(repo), str(scratch), must_include=must_include)
    copied = {str(f.relative_to(scratch)) for f in scratch.rglob("*") if f.is_file()}
    assert not any(p.startswith((".venv", ".hidden", "node_modules", "build", "dist"))
                   for p in copied), copied


def test_a_project_overridden_norecursedirs_is_honored_not_the_hardcoded_default(tmp_path):
    """Hardcoding pytest's *default* `norecursedirs` reopens the exact
    false-survivor class `tests_tree_files` exists to close, for any project
    that overrides the list in its own config.

    Here `pyproject.toml` drops the default `.*` entry, so pytest itself
    *would* walk into `tests/.local/`. A `.gitignore`d killer test living
    there is then named by neither `git` nor the hardcoded prune — the exact
    "named by neither" shape `tests_tree_files`'s own docstring describes.
    """
    repo = _repo(tmp_path, {
        "pyproject.toml": '[tool.pytest.ini_options]\n'
                           'norecursedirs = ["*.egg", "_darcs", "CVS", "{arch}"]\n',
        "widen.py": "def widen(value):\n"
                    "    if value > 10:\n"
                    "        return 'big'\n"
                    "    return 'small'\n",
        "tests/test_weak.py": "from widen import widen\n\n\n"
                              "def test_weak():\n"
                              "    assert widen(50) == 'big'\n",
        ".gitignore": "tests/.local/\n",
    })
    _write(repo, {
        "tests/.local/test_kill.py": "from widen import widen\n\n\n"
                                     "def test_boundary():\n"
                                     "    assert widen(13) == 'big'\n"
                                     "    assert widen(12) == 'small'\n",
    })

    assert "tests/.local/test_kill.py" in mp.tests_tree_files(str(repo), "tests")
    assert "tests/.local/test_kill.py" in mp.discover_tests(str(repo), "tests")


def test_a_toml_string_norecursedirs_is_split_like_pytest_reads_it(tmp_path):
    """pytest accepts a scalar string for `norecursedirs`, not only a list —
    `norecursedirs = ".*"` is valid TOML and valid pytest config, and pytest
    splits it on whitespace exactly as the ini format does. Returning the raw
    TOML string un-split turned the two-character string `".*"` into the tuple
    `('.', '*')` when iterated, and `'*'` — a glob matching every directory
    basename — pruned the whole tree one level down. Reopens the false
    survivor the config read exists to close, worse than never reading the
    config at all.
    """
    repo = _repo(tmp_path, {
        "pyproject.toml": '[tool.pytest.ini_options]\n'
                           'norecursedirs = ".*"\n',
        "widen.py": "def widen(value):\n"
                    "    if value > 10:\n"
                    "        return 'big'\n"
                    "    return 'small'\n",
        "tests/test_a.py": "def test_a():\n    assert True\n",
        "tests/sub/test_b.py": "def test_b():\n    assert True\n",
    })

    files = mp.tests_tree_files(str(repo), "tests")
    assert "tests/sub/test_b.py" in files, files
    assert "tests/test_a.py" in files, files


def test_the_toml_scalar_split_is_shlex_not_str(tmp_path):
    """Distinguishes `shlex.split` from `str.split` — the other tests using a
    scalar string (`".*"`, `"*.egg _darcs CVS {arch}"`) pass under either,
    because neither fixture contains a quoted entry with a space in it. This
    one does: `str.split('"my tests" other')` is `['"my', 'tests"', 'other']`
    (three tokens, quote characters attached); `shlex.split` is `('my tests',
    'other')` (two tokens, quotes consumed as grouping, the inner space
    preserved) — the behavior `_pytest.config` actually gives an `args`-typed
    ini value. Revert the TOML branch to `str.split` and this is the test
    that goes red, not the other two.
    """
    repo = _repo(tmp_path, {
        "pyproject.toml": "[tool.pytest.ini_options]\n"
                           "norecursedirs = '\"my tests\" other'\n",
    })

    assert mp._project_norecursedirs(str(repo)) == ("my tests", "other")


def test_the_ini_scalar_split_is_shlex_not_str(tmp_path):
    """Ini-branch sibling of the TOML shlex test — same distinguishing
    fixture, `pytest.ini`'s `[pytest]` section instead of `pyproject.toml`."""
    repo = _repo(tmp_path, {
        "pytest.ini": '[pytest]\nnorecursedirs = "my tests" other\n',
    })

    assert mp._project_norecursedirs(str(repo)) == ("my tests", "other")


def test_a_project_overridden_norecursedirs_is_honored_as_a_toml_string_too(tmp_path):
    """Same fixture as the list-spelled override test, TOML-string spelled —
    pytest treats `norecursedirs = "*.egg _darcs CVS {arch}"` and
    `norecursedirs = ["*.egg", "_darcs", "CVS", "{arch}"]` as the same
    config; both must drop `.*` from the prune the same way."""
    repo = _repo(tmp_path, {
        "pyproject.toml": '[tool.pytest.ini_options]\n'
                           'norecursedirs = "*.egg _darcs CVS {arch}"\n',
        "widen.py": "def widen(value):\n"
                    "    if value > 10:\n"
                    "        return 'big'\n"
                    "    return 'small'\n",
        "tests/test_weak.py": "from widen import widen\n\n\n"
                              "def test_weak():\n"
                              "    assert widen(50) == 'big'\n",
        ".gitignore": "tests/.local/\n",
    })
    _write(repo, {
        "tests/.local/test_kill.py": "from widen import widen\n\n\n"
                                     "def test_boundary():\n"
                                     "    assert widen(13) == 'big'\n"
                                     "    assert widen(12) == 'small'\n",
    })

    assert "tests/.local/test_kill.py" in mp.tests_tree_files(str(repo), "tests")


def test_an_unparseable_pytest_config_shape_falls_back_to_the_default(tmp_path):
    """`[tool.pytest]` with `ini_options` spelled as a scalar (not a table) is
    invalid pytest config, but it is not invalid TOML — `tomllib` parses it
    fine, and `_project_norecursedirs` chained `.get()` straight onto the
    resulting string, raising `AttributeError` and killing the whole run over
    a config file this probe only needed to read, never validate."""
    repo = _repo(tmp_path, {
        "pyproject.toml": '[tool.pytest]\nini_options = "x"\n',
    })

    assert mp._project_norecursedirs(str(repo)) == mp._NORECURSEDIRS


def test_a_non_iterable_norecursedirs_value_falls_back_to_the_default(tmp_path):
    """`norecursedirs = true` is valid TOML with an invalid pytest value;
    `tuple(True)` raises `TypeError`, which must fall through to the default
    like any other config this probe declines to make sense of."""
    repo = _repo(tmp_path, {
        "pyproject.toml": "[tool.pytest.ini_options]\nnorecursedirs = true\n",
    })

    assert mp._project_norecursedirs(str(repo)) == mp._NORECURSEDIRS


def test_a_mixed_type_norecursedirs_list_is_named_not_silently_applied(tmp_path):
    """`value = tuple(value)` inside the `try` catches a shape that raises
    *inside* `tuple()` — but `[".*", 2024]` survives `tuple()` fine (a tuple
    may hold an int) and only fails later, inside `fnmatch.fnmatch`, deep in
    `_pytest_prunes`. Returned with no note, this is a third outcome the
    widened `except` doesn't see: neither the project's real list nor the
    built-in default, and the caller finds out only when the walk itself
    crashes.
    """
    repo = _repo(tmp_path, {
        "pyproject.toml": '[tool.pytest.ini_options]\n'
                           'norecursedirs = [".*", 2024]\n',
        "tests/test_real.py": "def test_a():\n    assert True\n",
    })

    notes: list = []
    patterns = mp._project_norecursedirs(str(repo), notes=notes)
    assert patterns == mp._NORECURSEDIRS, patterns
    assert any("pyproject.toml" in n and "default" in n for n in notes), notes
    # And the walk itself must not crash on the original bad value either.
    assert mp.tests_tree_files(str(repo), "tests") == ["tests/test_real.py"]


def test_a_toml_table_norecursedirs_is_named_not_silently_applied(tmp_path):
    """A TOML *table* spelled where pytest expects an array/string is valid
    TOML and survives `tuple()` too — `tuple({"a": 1})` is `("a",)`, the
    table's keys, silently accepted as directory-name patterns with no
    relationship to what the project meant."""
    repo = _repo(tmp_path, {
        "pyproject.toml": '[tool.pytest.ini_options.norecursedirs]\n'
                           'not_a_real_option = true\n',
        "tests/test_real.py": "def test_a():\n    assert True\n",
    })

    notes: list = []
    patterns = mp._project_norecursedirs(str(repo), notes=notes)
    assert patterns == mp._NORECURSEDIRS, patterns
    assert any("pyproject.toml" in n and "default" in n for n in notes), notes


def test_the_native_tool_pytest_section_is_honored(tmp_path):
    """`[tool.pytest]` (no `.ini_options`) is pytest's current native-TOML
    section against the pinned pytest version, not a legacy spelling —
    `norecursedirs` declared there directly must be honored the same as the
    `[tool.pytest.ini_options]` spelling, or this probe stays narrower than
    pytest's real collection for any project using it."""
    repo = _repo(tmp_path, {
        "pyproject.toml": '[tool.pytest]\n'
                           'norecursedirs = ["*.egg", "_darcs", "CVS", "{arch}"]\n',
        "widen.py": "def widen(value):\n"
                    "    if value > 10:\n"
                    "        return 'big'\n"
                    "    return 'small'\n",
        "tests/test_weak.py": "from widen import widen\n\n\n"
                              "def test_weak():\n"
                              "    assert widen(50) == 'big'\n",
        ".gitignore": "tests/.local/\n",
    })
    _write(repo, {
        "tests/.local/test_kill.py": "from widen import widen\n\n\n"
                                     "def test_boundary():\n"
                                     "    assert widen(13) == 'big'\n"
                                     "    assert widen(12) == 'small'\n",
    })

    assert "tests/.local/test_kill.py" in mp.tests_tree_files(str(repo), "tests")


def test_a_pytest_toml_norecursedirs_override_is_honored(tmp_path):
    """`pytest.toml` outranks every file this probe previously read. Against
    pytest 9's own config-file precedence (`_pytest/config/findpaths.py`),
    `pytest.toml`, `.pytest.toml`, `pytest.ini`, and `.pytest.ini` are all
    preferred over `pyproject.toml`, and this function checked none of them —
    a project spelling its override there got the built-in default, pruned a
    `.gitignore`d killer test out of the copy set, and reported a survivor for
    a mutation the real tree (which pytest actually collects the killer for)
    kills. Same fixture shape as the `pyproject.toml` override tests, `pytest
    .toml`'s own top-level `[pytest]` table instead of `[tool.pytest]`."""
    repo = _repo(tmp_path, {
        "pytest.toml": '[pytest]\n'
                       'norecursedirs = ["*.egg", "_darcs", "CVS", "{arch}"]\n',
        "widen.py": "def widen(value):\n"
                    "    if value > 10:\n"
                    "        return 'big'\n"
                    "    return 'small'\n",
        "tests/test_weak.py": "from widen import widen\n\n\n"
                              "def test_weak():\n"
                              "    assert widen(50) == 'big'\n",
        ".gitignore": "tests/.local/\n",
    })
    _write(repo, {
        "tests/.local/test_kill.py": "from widen import widen\n\n\n"
                                     "def test_boundary():\n"
                                     "    assert widen(13) == 'big'\n"
                                     "    assert widen(12) == 'small'\n",
    })

    assert "tests/.local/test_kill.py" in mp.tests_tree_files(str(repo), "tests")


def test_a_dot_pytest_toml_norecursedirs_override_is_honored(tmp_path):
    """`.pytest.toml` sibling of the `pytest.toml` test — same top-level
    `[pytest]` table, the dotfile spelling."""
    repo = _repo(tmp_path, {
        ".pytest.toml": '[pytest]\n'
                        'norecursedirs = ["*.egg", "_darcs", "CVS", "{arch}"]\n',
        "tests/test_weak.py": "def test_weak():\n    assert True\n",
        ".gitignore": "tests/.local/\n",
    })
    _write(repo, {"tests/.local/test_kill.py": "def test_boundary():\n    assert True\n"})

    assert "tests/.local/test_kill.py" in mp.tests_tree_files(str(repo), "tests")


def test_a_dot_pytest_ini_norecursedirs_override_is_honored(tmp_path):
    """`.pytest.ini` sibling of the `pytest.ini` handling — same `[pytest]`
    ini section, the dotfile spelling."""
    repo = _repo(tmp_path, {
        ".pytest.ini": '[pytest]\n'
                       'norecursedirs = *.egg _darcs CVS {arch}\n',
        "tests/test_weak.py": "def test_weak():\n    assert True\n",
        ".gitignore": "tests/.local/\n",
    })
    _write(repo, {"tests/.local/test_kill.py": "def test_boundary():\n    assert True\n"})

    assert "tests/.local/test_kill.py" in mp.tests_tree_files(str(repo), "tests")


def test_pytest_toml_outranks_pyproject_toml(tmp_path):
    """Precedence, not just presence: with both files declaring conflicting
    lists, `pytest.toml` must win — the same order pytest itself checks
    config files in, `pytest.toml` before `pyproject.toml`."""
    repo = _repo(tmp_path, {
        "pytest.toml": '[pytest]\nnorecursedirs = ["FROM_PYTEST_TOML"]\n',
        "pyproject.toml": '[tool.pytest.ini_options]\n'
                           'norecursedirs = ["FROM_PYPROJECT"]\n',
    })

    assert mp._project_norecursedirs(str(repo)) == ("FROM_PYTEST_TOML",)


def test_pytest_toml_does_not_read_an_ini_options_subtable(tmp_path):
    """`ini_options` is a `pyproject.toml`-only concept. pytest's own
    TOML-native `pytest.toml`/`.pytest.toml` treat every key directly under
    `[pytest]` as a config value — `[pytest.ini_options]` is just an unknown
    option pytest warns about and ignores, falling back to its own built-in
    `norecursedirs`. Reading `ini_options` for these two files anyway pruned
    a directory pytest never prunes and reported a survivor for a mutation
    the real tree kills — the obvious mistake for a project migrating a
    `[tool.pytest.ini_options]` block from `pyproject.toml`.
    """
    repo = _repo(tmp_path, {
        "pytest.toml": '[pytest.ini_options]\n'
                       'norecursedirs = ["custom_dir"]\n',
        "tests/custom_dir/test_kill.py": "def test_k():\n    assert True\n",
    })

    files = mp.tests_tree_files(str(repo), "tests")
    assert "tests/custom_dir/test_kill.py" in files, files


def test_dot_pytest_toml_does_not_read_an_ini_options_subtable(tmp_path):
    """`.pytest.toml` sibling of the `pytest.toml` regression test."""
    repo = _repo(tmp_path, {
        ".pytest.toml": '[pytest.ini_options]\n'
                        'norecursedirs = ["custom_dir"]\n',
        "tests/custom_dir/test_kill.py": "def test_k():\n    assert True\n",
    })

    files = mp.tests_tree_files(str(repo), "tests")
    assert "tests/custom_dir/test_kill.py" in files, files


def test_a_silent_bare_pytest_ini_stops_the_walk_before_pyproject_toml(tmp_path):
    """pytest commits to the first *existing* candidate in its own
    precedence order and never reads a later file once committed, even when
    the committed file is silent on `norecursedirs` — `pytest.ini` before
    `pyproject.toml`. Falling through to `pyproject.toml` after a bare,
    present `pytest.ini` resolves a list pytest itself never uses, pruning a
    directory pytest actually collects from: the false-survivor direction.
    """
    repo = _repo(tmp_path, {
        "pytest.ini": "[pytest]\n",
        "pyproject.toml": '[tool.pytest.ini_options]\n'
                           'norecursedirs = ["custom_dir"]\n',
        "tests/custom_dir/test_kill.py": "def test_k():\n    assert True\n",
    })

    files = mp.tests_tree_files(str(repo), "tests")
    assert "tests/custom_dir/test_kill.py" in files, files
    assert mp._project_norecursedirs(str(repo)) == mp._NORECURSEDIRS


def test_pyproject_toml_declaring_both_native_keys_and_ini_options_is_named_not_resolved(tmp_path):
    """`_pytest/config/findpaths.py:129-135`: when `toml_config` (non-
    `ini_options` keys directly under `[tool.pytest]`) AND `ini_config`
    (`[tool.pytest.ini_options]`) are BOTH present, pytest raises
    `UsageError` and the whole run dies — it does not resolve a config, so
    there is no "list pytest actually applies" for this probe to agree or
    disagree with. Committing and reading one of the two anyway would answer
    a question pytest itself refuses to answer. Treated the same as any
    other config shape this probe cannot make sense of: committed-but-failed,
    noted, built-in default, and the walk does not fall through to a later
    candidate either — `tox.ini` declaring a real override here must not be
    read, the same as a truly malformed value would not fall through past
    `pyproject.toml`.
    """
    repo = _repo(tmp_path, {
        "pyproject.toml": '[tool.pytest]\n'
                           'addopts = "-ra"\n\n'
                           '[tool.pytest.ini_options]\n'
                           'norecursedirs = ["custom_dir"]\n',
        "tox.ini": "[pytest]\nnorecursedirs = other_dir\n",
        "tests/custom_dir/test_kill.py": "def test_k():\n    assert True\n",
        "tests/other_dir/test_other.py": "def test_o():\n    assert True\n",
    })

    notes: list = []
    files = mp.tests_tree_files(str(repo), "tests", notes=notes)
    assert "tests/custom_dir/test_kill.py" in files, files
    assert "tests/other_dir/test_other.py" in files, files
    assert mp._project_norecursedirs(str(repo)) == mp._NORECURSEDIRS
    assert any("pyproject.toml" in n for n in notes), notes


def test_a_bare_ini_options_beside_native_keys_still_resolves_the_native_keys(tmp_path):
    """`findpaths.py:129`'s actual condition is `if toml_config and ini_config`
    — `ini_config` has to be *truthy*, not merely present. A non-`ini_options`
    key under `[tool.pytest]` alongside a *bare* `[tool.pytest.ini_options]`
    (an empty table, `ini_config == {}`, falsy) does not trip pytest's
    `UsageError`: `toml_config` alone is truthy, so pytest takes the
    `if toml_config:` branch and resolves the native-TOML config, ignoring
    the empty `ini_options` header entirely. The `UsageError` predicate must
    test `node.get("ini_options")` (truthy), not `"ini_options" in node`
    (merely present) — the previous predicate raised into the `except` for
    this shape, pruned a directory pytest's real `norecursedirs` never
    touches, and dropped a gitignored killer test out of both
    `tests_tree_files` and `copy_worktree`'s untracked route.
    """
    repo = _repo(tmp_path, {
        "pyproject.toml": '[tool.pytest]\n'
                           'norecursedirs = ["custom_dir"]\n\n'
                           '[tool.pytest.ini_options]\n',
    })
    # A dot-directory: pruned by the built-in default's `.*` entry (the wrong,
    # committed-but-unresolvable fallback), but not by the resolved
    # `("custom_dir",)` (the correct, native-config answer) — the fixture
    # that actually distinguishes the two outcomes, since a killer test named
    # `custom_dir` itself would be pruned by *both*.
    _write(repo, {"tests/.hidden/test_kill.py": "def test_k():\n    assert True\n"})

    assert mp._project_norecursedirs(str(repo)) == ("custom_dir",)
    assert "tests/.hidden/test_kill.py" in mp.tests_tree_files(str(repo), "tests")


def test_a_bare_pytest_ini_with_no_pytest_section_still_commits(tmp_path):
    """The test above (`..._stops_the_walk_before_pyproject_toml`) writes
    `"pytest.ini": "[pytest]\\n"` — the section is *present*, so the *shared*-
    file commit rule (`has_section`) would also commit on it, meaning that
    test passes whether or not the `dedicated` guard exists at all: measured,
    removing both `dedicated` guards from `_project_norecursedirs` leaves the
    full test file at the same pass count. This fixture removes the section
    entirely — a 0-byte `pytest.ini` — so the two rules diverge:
    `findpaths.py` returns `{}` (committed) for `pytest.ini`/`.pytest.ini` by
    *name*, regardless of section presence, while the shared-file rule would
    require the section and find none. Only the `dedicated` branch gets this
    right; this is the test that reddens without it.
    """
    repo = _repo(tmp_path, {
        "pytest.ini": "",
        "pyproject.toml": '[tool.pytest.ini_options]\n'
                           'norecursedirs = ["custom_dir"]\n',
        "tests/custom_dir/test_kill.py": "def test_k():\n    assert True\n",
    })

    files = mp.tests_tree_files(str(repo), "tests")
    assert "tests/custom_dir/test_kill.py" in files, files
    assert mp._project_norecursedirs(str(repo)) == mp._NORECURSEDIRS


def test_a_pytest_toml_with_no_pytest_table_still_commits(tmp_path):
    """TOML-branch sibling of the bare-`pytest.ini` test above: `pytest.toml`
    commits the moment the file exists — `findpaths.py` returns `{}` for
    `pytest.toml`/`.pytest.toml` by name even when `[pytest]` is absent or
    empty — unlike `pyproject.toml`, which only commits once its
    `[tool.pytest...]` table is non-empty (see
    `test_a_bare_tool_pytest_table_does_not_commit_pyproject_toml`). An
    unrelated table (`[other]`, no `[pytest]` at all) is the fixture that
    actually distinguishes the dedicated rule from the shared one.
    """
    repo = _repo(tmp_path, {
        "pytest.toml": "[other]\nfoo = 1\n",
        "pyproject.toml": '[tool.pytest.ini_options]\n'
                           'norecursedirs = ["custom_dir"]\n',
        "tests/custom_dir/test_kill.py": "def test_k():\n    assert True\n",
    })

    files = mp.tests_tree_files(str(repo), "tests")
    assert "tests/custom_dir/test_kill.py" in files, files
    assert mp._project_norecursedirs(str(repo)) == mp._NORECURSEDIRS


def test_a_setup_cfg_pytest_section_instead_of_tool_pytest_is_named(tmp_path):
    """`setup.cfg` is pytest-specific only under `[tool:pytest]` — a bare
    `[pytest]` section there is a shape `_pytest/config/findpaths.py` calls
    `fail()` on outright, aborting the whole pytest run, rather than reading
    it or silently ignoring it. This function's `has_section("tool:pytest")`
    check reads a `[pytest]`-only `setup.cfg` as though the file were not
    pytest's at all — the same class of misread the `UsageError` fix above
    closes, just one file kind over, and the one the docstring's "entire
    residue" claim did not account for. Answer stays correct (the built-in
    default, same as before), but it must be named, not silently reached by
    walking off the end of the candidate list."""
    repo = _repo(tmp_path, {
        "setup.cfg": "[pytest]\nnorecursedirs = custom_dir\n",
    })

    notes: list = []
    assert mp._project_norecursedirs(str(repo), notes=notes) == mp._NORECURSEDIRS
    assert any("setup.cfg" in n for n in notes), notes


def test_a_pyproject_toml_without_a_pytest_table_does_not_stop_the_walk(tmp_path):
    """Unlike a dedicated pytest file, `pyproject.toml` only commits pytest
    to itself when a pytest-specific table is actually present. An unrelated
    `pyproject.toml` (another tool's config, no `[tool.pytest...]` at all)
    must not stop the walk before a later candidate that does declare an
    override — the presence of the *file* is not the presence of pytest's
    *section* in it.
    """
    repo = _repo(tmp_path, {
        "pyproject.toml": "[tool.black]\nline-length = 100\n",
        "tox.ini": '[pytest]\nnorecursedirs = ["*.egg", "_darcs", "CVS", "{arch}"]\n',
        "widen.py": "def widen(value):\n"
                    "    if value > 10:\n"
                    "        return 'big'\n"
                    "    return 'small'\n",
        "tests/test_weak.py": "from widen import widen\n\n\n"
                              "def test_weak():\n"
                              "    assert widen(50) == 'big'\n",
        ".gitignore": "tests/.local/\n",
    })
    _write(repo, {
        "tests/.local/test_kill.py": "from widen import widen\n\n\n"
                                     "def test_boundary():\n"
                                     "    assert widen(13) == 'big'\n"
                                     "    assert widen(12) == 'small'\n",
    })

    assert "tests/.local/test_kill.py" in mp.tests_tree_files(str(repo), "tests")


def test_a_bare_tool_pytest_table_does_not_commit_pyproject_toml(tmp_path):
    """A declared-but-*empty* `[tool.pytest]` does **not** commit pytest to
    `pyproject.toml` — verified against `_pytest/config/findpaths.py`:
    `toml_config = {k: v for k, v in tool_pytest.items() if k != "ini_options"}`
    and `ini_config = tool_pytest.get("ini_options", None)`; a bare
    `[tool.pytest]` gives `toml_config == {}` and `ini_config is None`, so
    `load_config_dict_from_file` returns `None` for this file and
    `locate_config` keeps walking to `tox.ini`. Content commits, not table
    presence — the opposite of what an earlier round of this fix assumed
    (and what this test used to assert, inverted here after that round's
    premise was checked against the source and found wrong).

    Note the asymmetry this pins: a bare `[tool.pytest.ini_options]` (empty
    dict, not absent) **does** commit, because `{}` is not `None` — checked
    by `test_a_project_overridden_norecursedirs_is_honored_not_the_hardcoded_default`'s
    siblings elsewhere in this file, which declare real content under
    `ini_options` rather than leaving it bare.
    """
    repo = _repo(tmp_path, {
        "pyproject.toml": "[tool.pytest]\n",
        "tox.ini": "[pytest]\nnorecursedirs = custom_dir\n",
        "tests/custom_dir/test_kill.py": "def test_k():\n    assert True\n",
    })

    files = mp.tests_tree_files(str(repo), "tests")
    assert "tests/custom_dir/test_kill.py" not in files, files
    assert mp._project_norecursedirs(str(repo)) == ("custom_dir",)


def test_a_silent_tox_ini_pytest_section_stops_the_walk_before_setup_cfg(tmp_path):
    """Ini-branch sibling of the bare-`pytest.ini` test: once `tox.ini`'s
    `[pytest]` section is present (even silent on `norecursedirs`), the walk
    must not fall through to `setup.cfg`."""
    repo = _repo(tmp_path, {
        "tox.ini": "[pytest]\naddopts = -ra\n",
        "setup.cfg": "[tool:pytest]\nnorecursedirs = custom_dir\n",
        "tests/custom_dir/test_kill.py": "def test_k():\n    assert True\n",
    })

    files = mp.tests_tree_files(str(repo), "tests")
    assert "tests/custom_dir/test_kill.py" in files, files
    assert mp._project_norecursedirs(str(repo)) == mp._NORECURSEDIRS


def test_the_changed_tests_note_only_fires_outside_tests_dir(tmp_path):
    """The exclusion note (added for the `--tests-dir .` false-negative case,
    where a *source* module is mistaken for a test) must not fire for the
    ordinary case of a changed test file actually living under `tests_dir` —
    that exclusion is expected on every routine task that touches a test, and
    a note on every one of them is noise, not signal."""
    repo = _repo(tmp_path, {
        "m.py": "def widen(value):\n    return value > 10\n",
        "tests/test_m.py": "import m\n\n\ndef test_widen():\n    assert m.widen(11)\n",
    })
    (repo / "m.py").write_text("def widen(value):\n    return value > 12\n", encoding="utf-8")
    (repo / "tests" / "test_m.py").write_text(
        "import m\n\n\ndef test_widen():\n    assert m.widen(13)\n", encoding="utf-8")

    notes: list = []
    mp.collect_sites(str(repo), None, "tests", 6, notes=notes)
    assert not any("tests/test_m.py" in n and "excluded" in n for n in notes), notes


def test_a_changed_source_module_excluded_under_dot_as_a_test_is_named(tmp_path):
    """Under `--tests-dir .`, a changed *source* module whose name matches
    pytest's own test-collection convention (`test_*.py`/`*_test.py`) is
    correctly excluded as a mutation subject — pytest would import it as a
    test module too — but the exclusion is a real loss of coverage on a file
    that is not actually a test suite, and AC-3's say-what-you-skipped rule
    applies to it the same as any other exclusion this module makes.
    """
    repo = _repo(tmp_path, {
        "pkg/test_harness.py": "def widen(value):\n"
                               "    if value > 10:\n"
                               "        return 'big'\n"
                               "    return 'small'\n",
    })
    (repo / "pkg" / "test_harness.py").write_text(
        "def widen(value):\n"
        "    if value > 12:\n"
        "        return 'big'\n"
        "    return 'small'\n",
        encoding="utf-8")

    notes: list = []
    sites, _files = mp.collect_sites(str(repo), None, ".", 6, notes=notes)
    assert sites == [], sites
    assert any("pkg/test_harness.py" in n for n in notes), notes


def test_a_pruned_directory_under_tests_dir_is_named_in_a_note(tmp_path):
    """A narrowed copy set must never read as complete coverage: a directory
    `norecursedirs` actually prunes (correctly, here — no config override) is
    still named, so a reader knows a test living inside it would not be
    found."""
    repo = _repo(tmp_path, {"tests/test_real.py": "def test_a():\n    assert True\n"})
    _write(repo, {"tests/.hidden/test_maybe.py": "def test_b():\n    assert True\n"})

    notes: list = []
    files = mp.tests_tree_files(str(repo), "tests", notes=notes)
    assert "tests/.hidden/test_maybe.py" not in files
    assert any("tests/.hidden" in n for n in notes), notes


def test_the_prune_note_describes_what_this_probe_resolved_not_pytests_own_list(tmp_path):
    """"matched pytest's `norecursedirs`" is a claim about pytest's actual
    behavior, and it is false whenever `_project_norecursedirs` silently fell
    back to the built-in default instead of resolving the project's real
    list — an unreadable/malformed config, a config shape that raises, an
    explicitly empty `norecursedirs = []` (which in pytest means prune
    nothing), or a project whose real config lives outside the four files
    this probe checks. The note must describe this probe's own resolution,
    not assert something about pytest it cannot back up.
    """
    repo = _repo(tmp_path, {"tests/test_real.py": "def test_a():\n    assert True\n"})
    _write(repo, {"tests/.hidden/test_maybe.py": "def test_b():\n    assert True\n"})

    notes: list = []
    mp.tests_tree_files(str(repo), "tests", notes=notes)
    assert not any("matched pytest's" in n for n in notes), notes
    assert any("this probe resolved" in n for n in notes), notes


def test_an_unresolvable_config_file_is_named_when_the_default_is_used_instead(tmp_path):
    """The silent half of the fallback: a candidate config file *exists* and
    would have been authoritative, but this probe could not make sense of it
    (here: `norecursedirs = true`, valid TOML, invalid pytest value) and used
    its built-in default instead. That substitution must be named, not just
    the directories the default then pruned."""
    repo = _repo(tmp_path, {
        "pyproject.toml": "[tool.pytest.ini_options]\nnorecursedirs = true\n",
        "tests/test_real.py": "def test_a():\n    assert True\n",
    })

    notes: list = []
    mp.tests_tree_files(str(repo), "tests", notes=notes)
    assert any("pyproject.toml" in n and "default" in n for n in notes), notes


def test_a_test_tree_symlink_that_resolves_outside_the_repo_is_still_copied(tmp_path):
    """Pins the ceiling `copy_worktree`'s docstring states, so a future
    change cannot quietly turn it into a guard without a test failing first.

    A directory symlink whose target resolves outside `repo_root` is not
    refused: `test_a_test_directory_reached_through_a_symlink_is_in_the_probed_tree`
    already requires the opposite — sharing a suite that lives outside the
    repository through a symlink (`tests/deep -> ../../shared`) is a
    supported layout this module exists to serve, and a containment check at
    the copy site would refuse that legitimate case along with an accidental
    one; the two are not distinguishable from the path alone. This test
    exercises the same shape with an untracked, non-suite file, so the two
    tests together cover "does the intended feature still work" and "is a
    stray outside-repo symlink still a no-op ceiling, not a crash."
    """
    repo = _repo(tmp_path, {"m.py": "X = 1\n", "tests/test_m.py": "def test_a():\n    assert True\n"})
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "shared.py").write_text("SHARED = 1\n", encoding="utf-8")
    os.symlink(str(outside), repo / "tests" / "link")

    must_include = mp.tests_tree_files(str(repo))
    assert "tests/link/shared.py" in must_include, must_include

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    mp.copy_worktree(str(repo), str(scratch), must_include=must_include)

    assert (scratch / "tests" / "link" / "shared.py").read_text(encoding="utf-8") == "SHARED = 1\n"


# --------------------------------------------------------------------------
# FIX 4 — a failure while writing the mutation must not leave the scratch
# file corrupted (truncated, neither original nor mutated)
# --------------------------------------------------------------------------

def test_a_failure_while_writing_the_mutation_does_not_leave_the_file_truncated(
        tmp_path, monkeypatch):
    """Opening the scratch file with `"w"` truncates it immediately, before
    the mutated text is computed. The write used to sit outside the
    `try`/`finally` that restores the original, so a failure computing the
    mutated text (here injected via `Site.apply`) left the file holding zero
    bytes — not the original, not the mutation, and never restored."""
    repo = _repo(tmp_path, {"m.py": "X = 1\n"})
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    mp.copy_worktree(str(repo), str(scratch))
    runner = mp.PytestRunner(str(scratch), sys.executable)
    site = _site(path="m.py")

    def boom(self, source):
        raise RuntimeError("boom")

    monkeypatch.setattr(mp.Site, "apply", boom)
    try:
        runner._with_mutation(site, [])
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected the injected failure to propagate")

    assert (scratch / "m.py").read_text(encoding="utf-8") == "X = 1\n"
