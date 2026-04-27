"""Regression tests for tests/conftest.py — `.active` marker safety.

Covers AC-001..AC-005 in TASK__active-marker-test-fixture-race:

- AC-001..AC-004: `scratch_task_in_real_repo` round-trips the real repo's
  `.active` marker through any code path (success, body exception,
  sequential re-entry, empty-at-entry) without corruption.
- AC-005: session-level safety net (`pytest_sessionstart` /
  `pytest_sessionfinish`) snapshots `.active` at session start and restores
  at session end, catching test paths that mutate `.active` outside the
  fixture (notably `test_harness_mcp_server.py` calling `task_close`
  against the real `find_repo_root()`).
"""

from __future__ import annotations

import inspect
import os
import shutil
import tempfile
import unittest
from unittest import mock

import conftest
from conftest import REPO_ROOT, scratch_task_in_real_repo

ACTIVE_PATH = os.path.join(REPO_ROOT, "doc", "harness", "tasks", ".active")


def _read_active() -> str | None:
    if not os.path.isfile(ACTIVE_PATH):
        return None
    with open(ACTIVE_PATH, "r", encoding="utf-8") as f:
        return f.read()


class FixtureContractTests(unittest.TestCase):
    """AC-001: source-level contract — no in-memory save/restore."""

    def test_fixture_uses_atomic_rename(self):
        src = inspect.getsource(scratch_task_in_real_repo)
        self.assertGreaterEqual(
            src.count("os.rename("), 2,
            f"expected ≥2 os.rename calls in fixture body, got:\n{src}",
        )
        self.assertNotIn(
            "f.write(prev_active)", src,
            "old in-memory save pattern still present",
        )


class FixtureRoundTripTests(unittest.TestCase):
    """AC-002, AC-003, AC-004: live round-trip behavior."""

    def test_restores_prior_content_on_success(self):
        # The session-level hook (pytest_sessionstart) has already moved the
        # real .active out of the way during the test session, so we
        # synthesize one here to exercise the success-path round-trip.
        synthetic = "TASK__synthetic-prior-active\n"
        had_existing = os.path.isfile(ACTIVE_PATH)
        if not had_existing:
            with open(ACTIVE_PATH, "w", encoding="utf-8") as f:
                f.write(synthetic)
        try:
            before = _read_active()
            self.assertEqual(before, synthetic if not had_existing else before)

            with scratch_task_in_real_repo("active-race-success") as task_dir:
                self.assertEqual(_read_active(), task_dir)

            self.assertEqual(
                _read_active(), before,
                "prior .active content was not restored after success exit",
            )
        finally:
            if not had_existing and os.path.isfile(ACTIVE_PATH):
                if _read_active() == synthetic:
                    os.unlink(ACTIVE_PATH)

    def test_no_active_at_entry_means_no_active_at_exit(self):
        real_backup = ACTIVE_PATH + ".test-empty-case-backup"
        pre_existing = os.path.isfile(ACTIVE_PATH)
        if pre_existing:
            os.rename(ACTIVE_PATH, real_backup)
        try:
            self.assertFalse(os.path.isfile(ACTIVE_PATH))

            with scratch_task_in_real_repo("active-race-empty") as task_dir:
                self.assertEqual(_read_active(), task_dir)

            self.assertFalse(
                os.path.isfile(ACTIVE_PATH),
                "fixture left a stray .active when none existed at entry",
            )
        finally:
            if pre_existing and os.path.isfile(real_backup):
                os.rename(real_backup, ACTIVE_PATH)

    def test_restores_on_body_exception(self):
        before = _read_active()

        class _Boom(Exception):
            pass

        with self.assertRaises(_Boom):
            with scratch_task_in_real_repo("active-race-raise") as task_dir:
                self.assertEqual(_read_active(), task_dir)
                raise _Boom("simulated fixture-body failure")

        self.assertEqual(
            _read_active(), before,
            "fixture left .active corrupted after body raised",
        )

    def test_sequential_reentry_preserves_outer_active(self):
        before = _read_active()

        for slug in ("active-race-seq-a", "active-race-seq-b", "active-race-seq-c"):
            with scratch_task_in_real_repo(slug) as task_dir:
                self.assertEqual(
                    _read_active(), task_dir,
                    f"during {slug}: .active should point at scratch",
                )

        self.assertEqual(
            _read_active(), before,
            "after sequential fixture entries, .active does not match starting state",
        )

    def test_no_stale_backup_files_after_success(self):
        tasks_dir = os.path.dirname(ACTIVE_PATH)
        before = {p for p in os.listdir(tasks_dir) if ".fixture-backup." in p}

        with scratch_task_in_real_repo("active-race-cleanup"):
            pass

        after = {p for p in os.listdir(tasks_dir) if ".fixture-backup." in p}
        self.assertEqual(
            after, before,
            f"stale sidecar files present: {after - before}",
        )


class SessionHookTests(unittest.TestCase):
    """AC-005: session-level safety net for out-of-fixture mutations.

    Test the hooks directly against a tmp dir (REPO_ROOT monkey-patched on
    conftest), so we don't perturb the real repo's `.active`.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="active-session-hook-")
        os.makedirs(os.path.join(self.tmp, "doc", "harness", "tasks"))
        self.tmp_active = os.path.join(self.tmp, "doc", "harness", "tasks", ".active")
        # Save the outer session's backup pointer so we don't clobber the
        # real pytest_sessionstart bookkeeping while exercising these tests.
        self._saved_session_backup = conftest._SESSION_ACTIVE_BACKUP
        conftest._SESSION_ACTIVE_BACKUP = None

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        conftest._SESSION_ACTIVE_BACKUP = self._saved_session_backup

    def test_session_hook_restores_active_after_external_deletion(self):
        """The MCP-test scenario: a test deletes the real `.active` (via
        task_close), and pytest_sessionfinish puts it back."""
        with open(self.tmp_active, "w", encoding="utf-8") as f:
            f.write("TASK__pre-existing\n")

        with mock.patch.object(conftest, "REPO_ROOT", self.tmp):
            conftest.pytest_sessionstart(session=None)
            # session_start should have moved the real .active to a sidecar.
            self.assertFalse(os.path.isfile(self.tmp_active))
            self.assertIsNotNone(conftest._SESSION_ACTIVE_BACKUP)
            self.assertTrue(os.path.isfile(conftest._SESSION_ACTIVE_BACKUP))

            # Simulate a test that *creates* a new `.active` then deletes it,
            # mirroring task_start + task_close mid-suite.
            with open(self.tmp_active, "w", encoding="utf-8") as f:
                f.write("TASK__scratch-mid-suite\n")
            os.unlink(self.tmp_active)
            self.assertFalse(os.path.isfile(self.tmp_active))

            # session_finish must restore the original content.
            conftest.pytest_sessionfinish(session=None, exitstatus=0)

        self.assertTrue(os.path.isfile(self.tmp_active))
        with open(self.tmp_active, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), "TASK__pre-existing\n")
        self.assertIsNone(conftest._SESSION_ACTIVE_BACKUP)

    def test_session_hook_no_op_when_no_active_at_start(self):
        """If `.active` is absent at session start, the hooks are a no-op."""
        self.assertFalse(os.path.isfile(self.tmp_active))

        with mock.patch.object(conftest, "REPO_ROOT", self.tmp):
            conftest.pytest_sessionstart(session=None)
            self.assertIsNone(conftest._SESSION_ACTIVE_BACKUP)

            conftest.pytest_sessionfinish(session=None, exitstatus=0)

        # Still absent after the round-trip.
        self.assertFalse(os.path.isfile(self.tmp_active))

    def test_session_hook_clears_in_flight_scratch_before_restore(self):
        """If a scratch `.active` is left in place at session end, the hook
        clears it before the rename so the original wins."""
        with open(self.tmp_active, "w", encoding="utf-8") as f:
            f.write("TASK__original\n")

        with mock.patch.object(conftest, "REPO_ROOT", self.tmp):
            conftest.pytest_sessionstart(session=None)
            # Simulate a stray scratch left mid-suite.
            with open(self.tmp_active, "w", encoding="utf-8") as f:
                f.write("TASK__stray-scratch\n")
            conftest.pytest_sessionfinish(session=None, exitstatus=0)

        with open(self.tmp_active, "r", encoding="utf-8") as f:
            self.assertEqual(
                f.read(), "TASK__original\n",
                "session hook should overwrite stray scratch with original",
            )


if __name__ == "__main__":
    unittest.main()
