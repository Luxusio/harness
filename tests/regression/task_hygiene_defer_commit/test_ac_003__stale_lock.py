"""AC-003 — hygiene_scan._cleanup_stale_index_lock only removes a stale,
0-byte, age >= 60s, unheld .git/index.lock.

Run: python3 -m unittest tests.regression.task_hygiene_defer_commit.test_ac_003__stale_lock
"""
from __future__ import annotations

import fcntl
import importlib.util
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
HYGIENE = REPO / "plugin" / "scripts" / "hygiene_scan.py"
sys.path.insert(0, str(REPO / "plugin" / "scripts"))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


hs = _load("hygiene_scan", HYGIENE)


def _make_repo(td: str) -> Path:
    """Minimal repo-shaped tree (no real git init needed; the cleanup helper
    only reads .git/index.lock by path)."""
    root = Path(td)
    (root / ".git").mkdir(parents=True, exist_ok=True)
    return root


def _backdate(path: Path, age_secs: int) -> None:
    """Set mtime to N seconds in the past."""
    t = time.time() - age_secs
    os.utime(path, (t, t))


class TestStaleLockCleanup(unittest.TestCase):
    """All three guards must hold; if any fails, the lock is preserved."""

    def test_removes_stale_zero_byte_old_unheld_lock(self):
        with tempfile.TemporaryDirectory() as td:
            root = _make_repo(td)
            lock = root / ".git" / "index.lock"
            lock.write_bytes(b"")
            _backdate(lock, 90)
            self.assertTrue(lock.exists())

            removed = hs._cleanup_stale_index_lock(str(root))

            self.assertTrue(removed, "stale lock should have been removed")
            self.assertFalse(lock.exists(), "stale lock file should be gone")

    def test_keeps_lock_when_recent(self):
        with tempfile.TemporaryDirectory() as td:
            root = _make_repo(td)
            lock = root / ".git" / "index.lock"
            lock.write_bytes(b"")
            # mtime is "now" by default
            removed = hs._cleanup_stale_index_lock(str(root))

            self.assertFalse(removed, "fresh lock must be preserved")
            self.assertTrue(lock.exists())

    def test_keeps_lock_when_nonempty(self):
        with tempfile.TemporaryDirectory() as td:
            root = _make_repo(td)
            lock = root / ".git" / "index.lock"
            lock.write_bytes(b"PID 12345 owns me\n")
            _backdate(lock, 600)

            removed = hs._cleanup_stale_index_lock(str(root))

            self.assertFalse(removed, "non-empty lock must be preserved")
            self.assertTrue(lock.exists())

    def test_keeps_lock_when_held(self):
        with tempfile.TemporaryDirectory() as td:
            root = _make_repo(td)
            lock = root / ".git" / "index.lock"
            lock.write_bytes(b"")
            _backdate(lock, 600)

            fd = os.open(str(lock), os.O_RDWR)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                removed = hs._cleanup_stale_index_lock(str(root))
                self.assertFalse(removed, "held lock must not be removed")
                self.assertTrue(lock.exists())
            finally:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
                os.close(fd)

    def test_no_op_when_no_lock(self):
        with tempfile.TemporaryDirectory() as td:
            root = _make_repo(td)
            removed = hs._cleanup_stale_index_lock(str(root))
            self.assertFalse(removed)


if __name__ == "__main__":
    unittest.main()
