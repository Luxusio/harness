"""Focused tests for repository-confined contract path validation."""

from __future__ import annotations

import tempfile
import sys
import unittest
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parent.parent / "plugin" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import contract_lint


class ContractReferencePathTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.repo = Path(self.tempdir.name) / "repo"
        self.repo.mkdir()
        (self.repo / "plugin" / "skills" / "one").mkdir(parents=True)
        (self.repo / "plugin" / "skills" / "one" / "SKILL.md").write_text(
            "one\n", encoding="utf-8"
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def test_literal_and_wildcard_regular_files_pass(self):
        self.assertIsNone(
            contract_lint._reference_path_issue(
                str(self.repo), "plugin/skills/one/SKILL.md"
            )
        )
        self.assertIsNone(
            contract_lint._reference_path_issue(
                str(self.repo), "plugin/skills/*/SKILL.md"
            )
        )

    def test_zero_match_reports_one_bounded_issue(self):
        issue = contract_lint._reference_path_issue(
            str(self.repo), "plugin/missing/*.md"
        )
        self.assertEqual("does not match a regular file", issue)

    def test_absolute_parent_and_recursive_references_are_unsafe(self):
        for ref in ("/tmp/file.md", "plugin/../outside.md", "plugin/**/SKILL.md"):
            with self.subTest(ref=ref):
                issue = contract_lint._reference_path_issue(str(self.repo), ref)
                self.assertIn("unsafe", issue or "")

    def test_symlink_escape_and_non_regular_matches_do_not_count(self):
        outside = Path(self.tempdir.name) / "outside.md"
        outside.write_text("outside\n", encoding="utf-8")
        (self.repo / "plugin" / "escape.md").symlink_to(outside)
        (self.repo / "plugin" / "directory.md").mkdir()

        for ref in ("plugin/escape.md", "plugin/directory.md"):
            with self.subTest(ref=ref):
                issue = contract_lint._reference_path_issue(str(self.repo), ref)
                self.assertIn("unsafe", issue or "")

    def test_intermediate_symlink_escapes_are_rejected_before_enumeration(self):
        outside = Path(self.tempdir.name) / "outside-tree"
        (outside / "child").mkdir(parents=True)
        (outside / "child" / "item.md").write_text("outside\n", encoding="utf-8")

        literal_link = self.repo / "plugin" / "literal-link"
        literal_link.symlink_to(outside, target_is_directory=True)
        parents = self.repo / "plugin" / "parents"
        parents.mkdir()
        (parents / "wildcard-link").symlink_to(outside, target_is_directory=True)

        for ref in (
            "plugin/literal-link/*/item.md",
            "plugin/parents/*/*.md",
        ):
            with self.subTest(ref=ref):
                issue = contract_lint._reference_path_issue(str(self.repo), ref)
                self.assertIn("unsafe", issue or "")

    def test_over_cap_enumeration_is_bounded(self):
        second = self.repo / "plugin" / "skills" / "two"
        second.mkdir()
        (second / "SKILL.md").write_text("two\n", encoding="utf-8")

        issue = contract_lint._reference_path_issue(
            str(self.repo), "plugin/skills/*/SKILL.md", match_limit=1
        )
        self.assertEqual("exceeds bounded match limit (1)", issue)

    def test_sparse_nonmatching_directory_enumeration_is_bounded(self):
        sparse = self.repo / "plugin" / "sparse"
        sparse.mkdir()
        for index in range(contract_lint.CONTRACT_REFERENCE_MATCH_LIMIT + 1):
            (sparse / f"noise-{index}").mkdir()

        issue = contract_lint._reference_path_issue(
            str(self.repo), "plugin/sparse/target-*/*.md"
        )

        self.assertEqual(
            f"exceeds bounded match limit ({contract_lint.CONTRACT_REFERENCE_MATCH_LIMIT})",
            issue,
        )

    def test_lint_routes_reference_failures_to_bounded_soft_diagnostics(self):
        many = self.repo / "plugin" / "many"
        many.mkdir()
        for index in range(contract_lint.CONTRACT_REFERENCE_MATCH_LIMIT + 1):
            (many / f"item-{index}.md").write_text("x\n", encoding="utf-8")
        contracts = self.repo / "CONTRACTS.md"
        contracts.write_text(
            """[C-01](#c-01)
<!-- harness:managed-begin v1 -->
### C-01
**Title:** paths
**When:** always
**Enforced by:** `/tmp/escape.md`, `plugin/missing/*.md`, and `plugin/many/*.md`
**On violation:** soft
**Why:** integration coverage
<!-- harness:managed-end -->
""",
            encoding="utf-8",
        )

        report = contract_lint.lint(str(contracts), repo_root=str(self.repo))

        self.assertFalse(report.hard)
        self.assertEqual(len(report.soft), 3)
        rendered = "\n".join(report.soft)
        self.assertIn("is unsafe", rendered)
        self.assertIn("does not match a regular file", rendered)
        self.assertIn("exceeds bounded match limit", rendered)
        self.assertLess(max(map(len, report.soft)), 300)


class DocTestReferenceTests(unittest.TestCase):
    """The mechanical half of "a coverage claim must be reproducible".

    A doc that names a test is the only record that a guard branch was ever
    exercised; when the test is renamed the record silently stops resolving.
    """

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.repo = Path(self.tempdir.name) / "repo"
        (self.repo / "tests").mkdir(parents=True)
        (self.repo / "doc").mkdir()
        (self.repo / "tests" / "test_gate.py").write_text(
            "def test_gate_blocks_on_removal():\n    pass\n", encoding="utf-8"
        )
        self.doc = self.repo / "doc" / "note.md"

    def tearDown(self):
        self.tempdir.cleanup()

    def _issues(self):
        return contract_lint.check_doc_test_references(str(self.repo))

    def test_a_resolving_reference_is_silent(self):
        self.doc.write_text(
            "The branch is pinned by `test_gate_blocks_on_removal`.\n"
            "The module is `tests/test_gate.py`, also spelled `test_gate`.\n",
            encoding="utf-8",
        )
        self.assertEqual(self._issues(), [])

    def test_a_renamed_test_is_named_together_with_its_doc(self):
        self.doc.write_text(
            "prose\n\n| Mutation | red |\n|---|---|\n"
            "| drop the guard | `test_gate_blocks_on_remove` |\n",
            encoding="utf-8",
        )
        issues = self._issues()
        self.assertEqual(len(issues), 1, issues)
        self.assertIn("doc/note.md:5", issues[0])
        self.assertIn("test_gate_blocks_on_remove", issues[0])

    def test_prose_and_paths_that_merely_contain_test_do_not_fire(self):
        """Korean and English prose, config keys, and file paths are not claims."""
        (self.repo / "plugin").mkdir()
        (self.repo / "plugin" / "runner.py").write_text(
            'MANIFEST_KEYS = ("test_command",)\n', encoding="utf-8"
        )
        self.doc.write_text(
            "이 테스트는 `pytest -n auto` 로 돌린다. The test suite is green.\n"
            "`tests/test_gate.py` 와 `manifest.yaml` 의 `test_command` 를 본다.\n"
            "Latest run: `pytest tests/test_gate.py::test_gate_blocks_on_removal`.\n",
            encoding="utf-8",
        )
        self.assertEqual(self._issues(), [])

    def test_an_identifier_split_by_a_file_suffix_is_not_a_reference(self):
        """`test_x_run.py` must not be read as a reference to `test_x_ru`.

        A trailing `(?!\\.py)` alone lets the engine backtrack and match the
        truncated name, which is how the first draft reported a test that was
        never referenced.
        """
        self.doc.write_text(
            "`tests/test_gate.py` and `test_gate_blocks_on_removal.py`\n",
            encoding="utf-8",
        )
        self.assertEqual(self._issues(), [])

    def test_dated_release_notes_are_out_of_scope(self):
        """A dated record describes the tree of that day; renames do not falsify it.

        The other historical class — gitignored task directories under
        `doc/harness/` — is excluded by `_durable_docs` asking Git for the
        tracked set, which this Git-less fixture repo exercises on its
        walk fallback instead.
        """
        (self.repo / "doc" / "changes").mkdir()
        (self.repo / "doc" / "changes" / "2026-01-01-release.md").write_text(
            "shipped `test_gate_blocks_on_remove`\n", encoding="utf-8",
        )
        self.assertEqual(self._issues(), [])


class QuickModeScopeTests(unittest.TestCase):
    """`--quick` runs exactly what its docstring promises.

    Check 6 is a repo-wide scan: a Git query plus every tracked doc, test, and
    source file. `--quick` exists for callers on a latency budget
    (`setup_finalize.py`, the setup bootstrap), and running the scan there made
    the documented mode and the implemented mode disagree — 0.027s -> 0.159s on
    this repository, unbounded on a large one.
    """

    REPO = str(Path(__file__).resolve().parent.parent)

    def _main(self, *flags):
        argv = ["contract_lint.py", "--repo-root", self.REPO,
                "--path", str(Path(self.REPO) / "CONTRACTS.md"), "--quiet", *flags]
        with mock.patch.object(
            contract_lint, "check_doc_test_references", return_value=[],
        ) as scan, mock.patch.object(sys, "argv", argv):
            rc = contract_lint.main()
        return rc, scan

    def test_quick_mode_skips_the_repo_wide_doc_scan(self):
        rc, scan = self._main("--quick")
        self.assertEqual(rc, 0)
        scan.assert_not_called()

    def test_the_default_mode_still_runs_it(self):
        rc, scan = self._main()
        self.assertEqual(rc, 0)
        scan.assert_called_once_with(self.REPO)


if __name__ == "__main__":
    unittest.main()
