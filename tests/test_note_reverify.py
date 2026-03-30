"""Tests for WS-1: note auto-reverify (note_reverify + _lib note metadata helpers).

Run with: python -m unittest discover -s tests -p 'test_*.py'
No external deps — stdlib only.
"""

import os
import sys
import tempfile
import textwrap
import unittest

# Point at plugin/scripts
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "scripts"))
os.environ["HARNESS_SKIP_STDIN"] = "1"

from _lib import parse_note_metadata, set_note_freshness, now_iso
from note_reverify import (
    collect_suspect_notes,
    paths_overlap,
    run_verification_command,
    reverify_suspect_notes,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_note(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(textwrap.dedent(content).lstrip())


def _make_task_state(task_dir, touched_paths=None):
    """Write a minimal TASK_STATE.yaml into task_dir."""
    os.makedirs(task_dir, exist_ok=True)
    tp_str = ""
    if touched_paths:
        inline = ", ".join(f'"{p}"' for p in touched_paths)
        tp_str = f"touched_paths: [{inline}]"
    else:
        tp_str = "touched_paths: []"
    content = f"task_id: TASK__test\nstatus: implemented\n{tp_str}\nverification_targets: []\n"
    with open(os.path.join(task_dir, "TASK_STATE.yaml"), "w") as fh:
        fh.write(content)


# ---------------------------------------------------------------------------
# parse_note_metadata tests
# ---------------------------------------------------------------------------

class TestParseNoteMetadata(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.doc_root = os.path.join(self.tmp.name, "doc", "common")
        os.makedirs(self.doc_root)

    def tearDown(self):
        self.tmp.cleanup()

    def _note(self, name, content):
        path = os.path.join(self.doc_root, name)
        _write_note(path, content)
        return path

    def test_parses_inline_invalidated_by_paths(self):
        path = self._note("obs-api.md", """
            # OBS test
            freshness: current
            invalidated_by_paths: [src/api.py, src/models.py]
            verification_command: echo ok
        """)
        meta = parse_note_metadata(path)
        self.assertEqual(meta["freshness"], "current")
        self.assertEqual(meta["invalidated_by_paths"], ["src/api.py", "src/models.py"])
        self.assertEqual(meta["verification_command"], "echo ok")

    def test_parses_block_invalidated_by_paths(self):
        path = self._note("obs-block.md", """
            # OBS block
            freshness: suspect
            invalidated_by_paths:
              - src/auth.py
              - src/middleware.py
        """)
        meta = parse_note_metadata(path)
        self.assertEqual(meta["freshness"], "suspect")
        self.assertEqual(meta["invalidated_by_paths"], ["src/auth.py", "src/middleware.py"])

    def test_missing_file_returns_empty(self):
        meta = parse_note_metadata("/nonexistent/path.md")
        self.assertEqual(meta["freshness"], None)
        self.assertEqual(meta["invalidated_by_paths"], [])

    def test_no_invalidated_by_paths_returns_empty_list(self):
        path = self._note("no-inv.md", """
            # REQ test
            freshness: current
            verified_at: 2026-01-01T00:00:00Z
        """)
        meta = parse_note_metadata(path)
        self.assertEqual(meta["invalidated_by_paths"], [])
        self.assertEqual(meta["freshness"], "current")

    def test_no_substring_false_positive(self):
        """src/api.py in invalidated_by_paths should NOT match changed file src/api-v2.py.

        This is the key correctness test: structural parse prevents false positives
        that the old substring-on-content approach would have produced.
        """
        path = self._note("obs-api-v1.md", """
            # OBS api v1
            freshness: current
            invalidated_by_paths: [src/api.py]
        """)
        meta = parse_note_metadata(path)
        inv = meta["invalidated_by_paths"]
        # Structural check: exact comparison, not substring
        self.assertIn("src/api.py", inv)
        # paths_overlap uses exact / prefix — src/api-v2.py should NOT overlap src/api.py
        self.assertFalse(
            paths_overlap(inv, ["src/api-v2.py"]),
            "src/api-v2.py must NOT match invalidated_by_paths entry src/api.py"
        )


# ---------------------------------------------------------------------------
# set_note_freshness tests
# ---------------------------------------------------------------------------

class TestSetNoteFreshness(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.doc_root = os.path.join(self.tmp.name, "doc", "common")
        os.makedirs(self.doc_root)

    def tearDown(self):
        self.tmp.cleanup()

    def _note(self, name, content):
        path = os.path.join(self.doc_root, name)
        _write_note(path, content)
        return path

    def test_updates_existing_freshness_field(self):
        path = self._note("n1.md", "# N1\nfreshness: current\n")
        result = set_note_freshness(path, "suspect")
        self.assertTrue(result)
        with open(path) as f:
            content = f.read()
        self.assertIn("freshness: suspect", content)
        self.assertNotIn("freshness: current", content)

    def test_inserts_freshness_if_missing(self):
        path = self._note("n2.md", "# N2\nsome: content\n")
        result = set_note_freshness(path, "current")
        self.assertTrue(result)
        meta = parse_note_metadata(path)
        self.assertEqual(meta["freshness"], "current")

    def test_updates_verified_at_when_provided(self):
        path = self._note("n3.md", "# N3\nfreshness: suspect\nverified_at: 2020-01-01T00:00:00Z\n")
        ts = "2026-03-30T12:00:00Z"
        set_note_freshness(path, "current", verified_at=ts)
        meta = parse_note_metadata(path)
        self.assertEqual(meta["freshness"], "current")
        self.assertEqual(meta["verified_at"], ts)

    def test_nonexistent_file_returns_false(self):
        result = set_note_freshness("/nonexistent.md", "current")
        self.assertFalse(result)


# ---------------------------------------------------------------------------
# paths_overlap tests
# ---------------------------------------------------------------------------

class TestPathsOverlap(unittest.TestCase):

    def test_exact_match(self):
        self.assertTrue(paths_overlap(["src/api.py"], ["src/api.py"]))

    def test_prefix_match_inv_is_parent(self):
        """inv=src/api, task=src/api/module.py → overlap (inv is directory prefix)."""
        self.assertTrue(paths_overlap(["src/api"], ["src/api/module.py"]))

    def test_prefix_match_task_is_parent(self):
        """inv=src/api/module.py, task=src/api → overlap."""
        self.assertTrue(paths_overlap(["src/api/module.py"], ["src/api"]))

    def test_no_substring_false_positive(self):
        """src/api.py vs src/api-v2.py should NOT overlap."""
        self.assertFalse(paths_overlap(["src/api.py"], ["src/api-v2.py"]))

    def test_no_match(self):
        self.assertFalse(paths_overlap(["src/foo.py"], ["src/bar.py"]))

    def test_empty_inv(self):
        self.assertFalse(paths_overlap([], ["src/api.py"]))

    def test_empty_task(self):
        self.assertFalse(paths_overlap(["src/api.py"], []))

    def test_multiple_paths(self):
        self.assertTrue(paths_overlap(
            ["src/auth.py", "src/models.py"],
            ["tests/test_foo.py", "src/models.py"]
        ))


# ---------------------------------------------------------------------------
# run_verification_command tests
# ---------------------------------------------------------------------------

class TestRunVerificationCommand(unittest.TestCase):

    def test_success_command(self):
        ok, output = run_verification_command("true")
        self.assertTrue(ok)

    def test_failure_command(self):
        ok, output = run_verification_command("false")
        self.assertFalse(ok)

    def test_echo_command(self):
        ok, output = run_verification_command("echo hello_test")
        self.assertTrue(ok)
        self.assertIn("hello_test", output)

    def test_timeout(self):
        ok, output = run_verification_command("sleep 10", timeout=1)
        self.assertFalse(ok)
        self.assertIn("timed out", output.lower())


# ---------------------------------------------------------------------------
# reverify_suspect_notes integration tests
# ---------------------------------------------------------------------------

class TestReverifySuspectNotes(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = self.tmp.name
        self.doc_base = os.path.join(self.base, "doc", "common")
        self.task_dir = os.path.join(self.base, "doc", "harness", "tasks", "TASK__test")
        os.makedirs(self.doc_base)
        os.makedirs(self.task_dir)

    def tearDown(self):
        self.tmp.cleanup()

    def _note(self, name, content):
        path = os.path.join(self.doc_base, name)
        _write_note(path, content)
        return path

    def test_success_path_recovers_suspect_to_current(self):
        """suspect + passing verification_command → freshness becomes current."""
        path = self._note("obs-ok.md", """
            # OBS ok
            freshness: suspect
            invalidated_by_paths: [src/api.py]
            verification_command: true
        """)
        _make_task_state(self.task_dir, touched_paths=["src/api.py"])

        results = reverify_suspect_notes(
            self.task_dir,
            doc_base=os.path.join(self.base, "doc"),
        )

        statuses = {os.path.basename(p): s for p, s in results}
        self.assertEqual(statuses.get("obs-ok.md"), "recovered")

        meta = parse_note_metadata(path)
        self.assertEqual(meta["freshness"], "current")
        self.assertIsNotNone(meta["verified_at"])

    def test_failure_path_stays_suspect(self):
        """suspect + failing verification_command → stays suspect."""
        path = self._note("obs-fail.md", """
            # OBS fail
            freshness: suspect
            invalidated_by_paths: [src/api.py]
            verification_command: false
        """)
        _make_task_state(self.task_dir, touched_paths=["src/api.py"])

        results = reverify_suspect_notes(
            self.task_dir,
            doc_base=os.path.join(self.base, "doc"),
        )

        statuses = {os.path.basename(p): s for p, s in results}
        self.assertEqual(statuses.get("obs-fail.md"), "failed")

        meta = parse_note_metadata(path)
        self.assertEqual(meta["freshness"], "suspect")

    def test_no_command_path_is_not_collected(self):
        """Note without verification_command is not collected as a candidate."""
        self._note("obs-no-cmd.md", """
            # OBS no cmd
            freshness: suspect
            invalidated_by_paths: [src/api.py]
        """)
        _make_task_state(self.task_dir, touched_paths=["src/api.py"])

        results = reverify_suspect_notes(
            self.task_dir,
            doc_base=os.path.join(self.base, "doc"),
        )
        # Should not appear in results at all (collect_suspect_notes filters it out)
        names = [os.path.basename(p) for p, _ in results]
        self.assertNotIn("obs-no-cmd.md", names)

    def test_no_path_overlap_skips_note(self):
        """Note with non-overlapping invalidated_by_paths is skipped."""
        self._note("obs-other.md", """
            # OBS other
            freshness: suspect
            invalidated_by_paths: [src/unrelated.py]
            verification_command: true
        """)
        _make_task_state(self.task_dir, touched_paths=["src/api.py"])

        results = reverify_suspect_notes(
            self.task_dir,
            doc_base=os.path.join(self.base, "doc"),
        )
        statuses = {os.path.basename(p): s for p, s in results}
        self.assertEqual(statuses.get("obs-other.md"), "skipped")

    def test_empty_doc_base_is_noop(self):
        """doc/ absent → no-op, empty result."""
        results = reverify_suspect_notes(
            self.task_dir,
            doc_base=os.path.join(self.base, "nonexistent_doc"),
        )
        self.assertEqual(results, [])

    def test_max_notes_limit(self):
        """Only max_notes notes are attempted (not skipped ones)."""
        for i in range(5):
            self._note(f"obs-{i}.md", f"""
                # OBS {i}
                freshness: suspect
                invalidated_by_paths: [src/api.py]
                verification_command: true
            """)
        _make_task_state(self.task_dir, touched_paths=["src/api.py"])

        results = reverify_suspect_notes(
            self.task_dir,
            doc_base=os.path.join(self.base, "doc"),
            max_notes=2,
        )
        attempted = [(p, s) for p, s in results if s != "skipped"]
        self.assertLessEqual(len(attempted), 2)

    def test_structural_no_false_positive(self):
        """Path src/api-v2.py must NOT trigger reverify of note with src/api.py."""
        path = self._note("obs-api.md", """
            # OBS api
            freshness: suspect
            invalidated_by_paths: [src/api.py]
            verification_command: true
        """)
        # task touched src/api-v2.py, not src/api.py
        _make_task_state(self.task_dir, touched_paths=["src/api-v2.py"])

        results = reverify_suspect_notes(
            self.task_dir,
            doc_base=os.path.join(self.base, "doc"),
        )
        statuses = {os.path.basename(p): s for p, s in results}
        # Must be skipped (no overlap), NOT recovered
        self.assertEqual(statuses.get("obs-api.md"), "skipped")
        # Freshness must still be suspect
        meta = parse_note_metadata(path)
        self.assertEqual(meta["freshness"], "suspect")


if __name__ == "__main__":
    unittest.main()
