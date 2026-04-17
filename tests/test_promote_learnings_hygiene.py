"""Tests for promote_learnings.py hygiene audits (AC-009..AC-011)."""
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone, timedelta

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(REPO_ROOT, "tests", "fixtures", "gstack_adoption")

sys.path.insert(0, os.path.join(REPO_ROOT, "plugin", "scripts"))


class TestAuditStaleFiles(unittest.TestCase):
    """AC-009: _audit_stale_files flags missing file paths, warn-only stderr."""

    def setUp(self):
        import importlib
        import promote_learnings
        importlib.reload(promote_learnings)
        self.module = promote_learnings

    def test_flags_missing_files(self):
        """Entries with non-existent files[] paths should be flagged."""
        entries = [
            {
                "ts": "2026-04-17T00:00:00Z",
                "key": "test-key",
                "files": ["nonexistent/path/file.py"],
            }
        ]
        buf = io.StringIO()
        old_stderr = sys.stderr
        sys.stderr = buf
        try:
            count = self.module._audit_stale_files(entries, REPO_ROOT)
        finally:
            sys.stderr = old_stderr
        self.assertGreater(count, 0, "Should flag missing file")
        self.assertIn("stale-file", buf.getvalue())

    def test_existing_file_not_flagged(self):
        """Entries with existing files[] paths should not be flagged."""
        entries = [
            {
                "ts": "2026-04-17T00:00:00Z",
                "key": "test-key",
                "files": ["plugin/scripts/prewrite_gate.py"],
            }
        ]
        buf = io.StringIO()
        old_stderr = sys.stderr
        sys.stderr = buf
        try:
            count = self.module._audit_stale_files(entries, REPO_ROOT)
        finally:
            sys.stderr = old_stderr
        self.assertEqual(count, 0, "Should not flag existing file")

    def test_empty_files_field_no_warning(self):
        """Entries without files[] should not generate warnings."""
        entries = [{"ts": "2026-04-17T00:00:00Z", "key": "test-key"}]
        buf = io.StringIO()
        old_stderr = sys.stderr
        sys.stderr = buf
        try:
            count = self.module._audit_stale_files(entries, REPO_ROOT)
        finally:
            sys.stderr = old_stderr
        self.assertEqual(count, 0)

    def test_learnings_jsonl_not_mutated(self):
        """learnings.jsonl should not be modified by _audit_stale_files."""
        with tempfile.TemporaryDirectory() as d:
            learn_path = os.path.join(d, "learnings.jsonl")
            entries = [{"key": "k", "files": ["nonexistent.py"], "ts": "2026-04-17T00:00:00Z"}]
            original = json.dumps(entries[0])
            with open(learn_path, "w") as f:
                f.write(original + "\n")
            mtime_before = os.path.getmtime(learn_path)
            import time; time.sleep(0.01)
            # _audit_stale_files doesn't touch the file
            buf = io.StringIO()
            old_stderr = sys.stderr
            sys.stderr = buf
            try:
                self.module._audit_stale_files(entries, d)
            finally:
                sys.stderr = old_stderr
            mtime_after = os.path.getmtime(learn_path)
            self.assertEqual(mtime_before, mtime_after, "learnings.jsonl should not be mutated")


class TestAuditContradictions(unittest.TestCase):
    """AC-010: _audit_contradictions flags recent same-key conflicts."""

    def setUp(self):
        import importlib
        import promote_learnings
        importlib.reload(promote_learnings)
        self.module = promote_learnings

    def _make_entry(self, key, insight, days_ago, source="run"):
        ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
        return {"key": key, "insight": insight, "ts": ts, "source": source}

    def test_recent_same_key_flagged(self):
        """Two entries with same key within 30 days should be flagged."""
        entries = [
            self._make_entry("my-key", "insight A", days_ago=5),
            self._make_entry("my-key", "insight B", days_ago=1),
        ]
        buf = io.StringIO()
        old_stderr = sys.stderr
        sys.stderr = buf
        try:
            count = self.module._audit_contradictions(entries)
        finally:
            sys.stderr = old_stderr
        self.assertGreater(count, 0, "Should flag recent contradiction")
        self.assertIn("contradiction", buf.getvalue())

    def test_old_different_source_not_flagged(self):
        """Entries >30 days apart with different sources should not be flagged."""
        entries = [
            self._make_entry("old-key", "insight A", days_ago=60, source="run"),
            self._make_entry("old-key", "insight B", days_ago=1, source="develop"),
        ]
        buf = io.StringIO()
        old_stderr = sys.stderr
        sys.stderr = buf
        try:
            count = self.module._audit_contradictions(entries)
        finally:
            sys.stderr = old_stderr
        self.assertEqual(count, 0, "Old entries with different sources should not be flagged")

    def test_same_source_recent_flagged(self):
        """Same-source entries with same key should be flagged."""
        entries = [
            self._make_entry("dup-key", "version 1", days_ago=10, source="qa-cli"),
            self._make_entry("dup-key", "version 2", days_ago=2, source="qa-cli"),
        ]
        buf = io.StringIO()
        old_stderr = sys.stderr
        sys.stderr = buf
        try:
            count = self.module._audit_contradictions(entries)
        finally:
            sys.stderr = old_stderr
        self.assertGreater(count, 0)

    def test_single_entry_no_warning(self):
        """Single entry per key should not trigger contradiction."""
        entries = [self._make_entry("unique-key", "insight", days_ago=1)]
        buf = io.StringIO()
        old_stderr = sys.stderr
        sys.stderr = buf
        try:
            count = self.module._audit_contradictions(entries)
        finally:
            sys.stderr = old_stderr
        self.assertEqual(count, 0)


class TestHygieneOutputBudget(unittest.TestCase):
    """AC-011: hygiene audit on 200-entry fixture stays < 100 lines."""

    def setUp(self):
        import importlib
        import promote_learnings
        importlib.reload(promote_learnings)
        self.module = promote_learnings

    def test_200_entry_fixture_under_100_lines(self):
        """Hygiene output on 200-entry fixture should be < 100 stderr lines."""
        learn_path = os.path.join(FIXTURES, "learnings_200.jsonl")
        entries = self.module._load_entries(learn_path)
        self.assertGreaterEqual(len(entries), 100, "Fixture should have >= 100 entries")

        buf = io.StringIO()
        old_stderr = sys.stderr
        sys.stderr = buf
        try:
            self.module._audit_stale_files(entries, REPO_ROOT)
            self.module._audit_contradictions(entries)
        finally:
            sys.stderr = old_stderr

        stderr_lines = [l for l in buf.getvalue().splitlines() if l.strip()]
        self.assertLess(len(stderr_lines), 100,
                        f"Hygiene output should be < 100 lines, got {len(stderr_lines)}")

    def test_run_pipeline_exits_0_with_hygiene(self):
        """run() should return 0 regardless of hygiene warnings."""
        import io as _io
        # Redirect stdout to suppress output
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        sys.stdout = _io.StringIO()
        sys.stderr = _io.StringIO()
        try:
            result = self.module.run(REPO_ROOT, threshold=100, dry_run=True)
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr
        self.assertEqual(result, 0, "run() must return 0 regardless of hygiene warnings")


if __name__ == "__main__":
    unittest.main()
