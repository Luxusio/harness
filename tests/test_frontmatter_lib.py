"""FM-01..04: Frontmatter parser promotion contract tests (AC-001).

Verifies that _lib.py public API (split_frontmatter, read_array_field,
read_scalar_field, set_scalar_field) produces identical output to the
private helpers in note_freshness.py on the existing doc fixtures.
"""
from __future__ import annotations

import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(REPO_ROOT, "plugin", "scripts")
sys.path.insert(0, SCRIPTS_DIR)

from _lib import (  # noqa: E402
    split_frontmatter,
    read_array_field,
    read_scalar_field,
    set_scalar_field,
)
import note_freshness as nf  # noqa: E402


def _collect_doc_fixtures():
    """Collect doc/changes/ + doc/common/ .md files."""
    paths = []
    for subdir in ("changes", "common"):
        d = os.path.join(REPO_ROOT, "doc", subdir)
        if not os.path.isdir(d):
            continue
        for root, _dirs, files in os.walk(d):
            for fn in files:
                if fn.endswith(".md"):
                    paths.append(os.path.join(root, fn))
    return paths


DOC_FIXTURES = _collect_doc_fixtures()


class TestFrontmatterLibPromotion(unittest.TestCase):
    """FM-01..04: Public API matches private helpers on all doc fixtures."""

    def test_FM01_split_frontmatter_identical_on_fixtures(self):
        """FM-01: split_frontmatter matches note_freshness._split_frontmatter on all fixtures."""
        for path in DOC_FIXTURES:
            with self.subTest(path=os.path.relpath(path, REPO_ROOT)):
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    text = f.read()
                lib_result = split_frontmatter(text)
                nf_result = nf._split_frontmatter(text)
                self.assertEqual(lib_result, nf_result,
                    f"split_frontmatter mismatch for {os.path.relpath(path, REPO_ROOT)}")

    def test_FM02_read_array_field_block_style(self):
        """FM-02: read_array_field parses block-style arrays correctly."""
        fm = "tags:\n  - harness\n  - setup\nfreshness: current\n"
        self.assertEqual(read_array_field(fm, "tags"), ["harness", "setup"])
        self.assertEqual(nf._read_array(fm, "tags"), ["harness", "setup"])

    def test_FM02_read_array_field_compact_style(self):
        """FM-02: read_array_field parses compact [a, b] style."""
        fm = "tags: [harness, setup]\n"
        self.assertEqual(read_array_field(fm, "tags"), ["harness", "setup"])
        self.assertEqual(nf._read_array(fm, "tags"), ["harness", "setup"])

    def test_FM02_read_array_field_missing(self):
        """FM-02: read_array_field returns [] for missing field."""
        fm = "freshness: current\n"
        self.assertEqual(read_array_field(fm, "tags"), [])
        self.assertEqual(nf._read_array(fm, "tags"), [])

    def test_FM03_read_scalar_field_present(self):
        """FM-03: read_scalar_field returns value for present field."""
        fm = "freshness: current\nupdated: 2026-04-22\n"
        self.assertEqual(read_scalar_field(fm, "freshness"), "current")
        self.assertEqual(nf._read_scalar(fm, "freshness"), "current")

    def test_FM03_read_scalar_field_missing(self):
        """FM-03: read_scalar_field returns None for missing field."""
        fm = "tags: [harness]\n"
        self.assertIsNone(read_scalar_field(fm, "freshness"))
        self.assertIsNone(nf._read_scalar(fm, "freshness"))

    def test_FM04_set_scalar_field_replace(self):
        """FM-04: set_scalar_field replaces existing field in-place."""
        fm = "freshness: current\nupdated: 2026-04-22\n"
        result_lib = set_scalar_field(fm, "freshness", "suspect")
        result_nf = nf._set_scalar(fm, "freshness", "suspect")
        self.assertEqual(result_lib, result_nf)
        self.assertIn("freshness: suspect", result_lib)
        self.assertNotIn("freshness: current", result_lib)

    def test_FM04_set_scalar_field_append(self):
        """FM-04: set_scalar_field appends field when not present."""
        fm = "tags: [harness]\n"
        result_lib = set_scalar_field(fm, "freshness", "suspect")
        result_nf = nf._set_scalar(fm, "freshness", "suspect")
        self.assertEqual(result_lib, result_nf)
        self.assertIn("freshness: suspect", result_lib)

    def test_FM04_set_scalar_identical_on_fixtures(self):
        """FM-04: set_scalar_field and _set_scalar identical on all doc fixtures."""
        for path in DOC_FIXTURES:
            with self.subTest(path=os.path.relpath(path, REPO_ROOT)):
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    text = f.read()
                fm, _body, _idx = split_frontmatter(text)
                if fm is None:
                    continue
                result_lib = set_scalar_field(fm, "freshness", "suspect")
                result_nf = nf._set_scalar(fm, "freshness", "suspect")
                self.assertEqual(result_lib, result_nf)

    def test_FM01_no_frontmatter_returns_none(self):
        """FM-01: docs without frontmatter return (None, text, -1)."""
        text = "# Just a heading\n\nSome body.\n"
        fm, body, idx = split_frontmatter(text)
        self.assertIsNone(fm)
        self.assertEqual(body, text)
        self.assertEqual(idx, -1)


if __name__ == "__main__":
    unittest.main()
