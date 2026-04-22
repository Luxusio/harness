"""RS-01..06: maintain_restore.py tests (AC-011, AC-012)."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(REPO_ROOT, "plugin", "scripts")
sys.path.insert(0, SCRIPTS_DIR)

from maintain_restore import restore, _strip_sha7_suffix  # noqa: E402


def _init_git(tmp_path):
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"],
                   cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"],
                   cwd=str(tmp_path), capture_output=True)
    return tmp_path


class TestRestore(unittest.TestCase):
    """RS-01..04: restore() function tests."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.repo = self._td.name
        _init_git(self.repo)

    def tearDown(self):
        self._td.cleanup()

    def _p(self, *parts):
        return os.path.join(self.repo, *parts)

    def _mkdir(self, *parts):
        path = self._p(*parts)
        os.makedirs(path, exist_ok=True)
        return path

    def _write(self, rel_path, content):
        full = self._p(*rel_path.split("/"))
        with open(full, "w") as f:
            f.write(content)
        return full

    def _git(self, *args):
        subprocess.run(list(args), cwd=self.repo, capture_output=True)

    def test_RS01_restore_moves_back(self):
        """RS-01: restore moves file from _archive/ back to original location."""
        self._mkdir("doc", "changes", "_archive")
        self._write("doc/changes/_archive/foo.md", "# foo\n")
        self._git("git", "add", ".")
        self._git("git", "commit", "-m", "archive foo")

        rc = restore("doc/changes/_archive/foo.md", self.repo)
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.exists(self._p("doc", "changes", "foo.md")),
                        "Restored file should be at original location")
        self.assertFalse(os.path.exists(self._p("doc", "changes", "_archive", "foo.md")),
                         "Archive file should be gone")

    def test_RS02_non_archive_path_rejected(self):
        """RS-02: path not under _archive/ → exit non-zero, no mutation."""
        self._mkdir("doc", "changes")
        foo = self._write("doc/changes/foo.md", "# foo\n")
        self._git("git", "add", ".")
        self._git("git", "commit", "-m", "init")

        rc = restore("doc/changes/foo.md", self.repo)
        self.assertNotEqual(rc, 0, "Non-archive path must return non-zero exit")
        self.assertTrue(os.path.exists(foo), "File must be untouched")

    def test_RS03_refuses_overwrite_if_dest_exists(self):
        """RS-03: restore refuses if destination already exists."""
        self._mkdir("doc", "changes", "_archive")
        self._write("doc/changes/_archive/foo.md", "# foo archived\n")
        self._write("doc/changes/foo.md", "# foo original\n")
        self._git("git", "add", ".")
        self._git("git", "commit", "-m", "init")

        rc = restore("doc/changes/_archive/foo.md", self.repo)
        self.assertNotEqual(rc, 0, "Should refuse overwrite")
        self.assertTrue(os.path.exists(self._p("doc", "changes", "_archive", "foo.md")),
                        "Archive must be untouched")
        self.assertTrue(os.path.exists(self._p("doc", "changes", "foo.md")),
                        "Original must be untouched")

    def test_RS04_git_history_preserved(self):
        """RS-04: git log --follow on restored path shows pre-archive commits."""
        self._mkdir("doc", "changes")
        self._write("doc/changes/foo.md", "# foo v1\n")
        self._git("git", "add", ".")
        self._git("git", "commit", "-m", "original commit for foo")

        self._mkdir("doc", "changes", "_archive")
        subprocess.run(
            ["git", "mv", "doc/changes/foo.md", "doc/changes/_archive/foo.md"],
            cwd=self.repo, capture_output=True,
        )
        self._git("git", "commit", "-m", "archive foo")

        rc = restore("doc/changes/_archive/foo.md", self.repo)
        self.assertEqual(rc, 0)

        r = subprocess.run(
            ["git", "log", "--follow", "--oneline", "doc/changes/foo.md"],
            cwd=self.repo, capture_output=True, text=True,
        )
        self.assertIn("original commit for foo", r.stdout,
                      "git log --follow must show pre-archive commit after restore")


class TestRestoreCLI(unittest.TestCase):
    """RS-05: CLI tests."""

    def test_RS05_help_shows_example(self):
        """RS-05 / AC-012: --help shows usage with concrete example and recovery hint."""
        result = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS_DIR, "maintain_restore.py"), "--help"],
            capture_output=True, text=True, timeout=5,
        )
        combined = result.stdout + result.stderr
        self.assertIn("maintain_restore.py", combined)
        self.assertIn("_archive", combined)
        self.assertIn("git log", combined, "--help must include git log recovery hint")


class TestRestoreLOC(unittest.TestCase):
    """RS-06: LOC budget."""

    def test_RS06_loc_budget(self):
        """RS-06: maintain_restore.py script body <= 100 code LOC."""
        script_path = os.path.join(SCRIPTS_DIR, "maintain_restore.py")
        with open(script_path, encoding="utf-8") as f:
            lines = f.readlines()
        code_lines = [l for l in lines if l.strip() and not l.strip().startswith("#")]
        self.assertLessEqual(len(code_lines), 100,
            f"maintain_restore.py has {len(code_lines)} code lines (soft budget <=75, hard cap <=100)")


class TestStripSha7Suffix(unittest.TestCase):
    """_strip_sha7_suffix tests."""

    def test_removes_timestamp(self):
        """_strip_sha7_suffix removes .archived-YYYYMMDDTHHMMSSZ suffix."""
        self.assertEqual(_strip_sha7_suffix("foo.archived-20260422T120000Z.md"), "foo.md")

    def test_leaves_plain_name(self):
        """_strip_sha7_suffix leaves plain names unchanged."""
        self.assertEqual(_strip_sha7_suffix("foo.md"), "foo.md")


if __name__ == "__main__":
    unittest.main()
