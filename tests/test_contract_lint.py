"""Focused tests for repository-confined contract path validation."""

from __future__ import annotations

import tempfile
import sys
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
