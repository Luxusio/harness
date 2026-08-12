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
import importlib.util
import json
import hashlib
import os
import shutil
import tempfile
import unittest
from unittest import mock

import conftest
from conftest import REPO_ROOT, scratch_task_in_real_repo

ACTIVE_PATH = os.path.join(REPO_ROOT, "doc", "harness", "tasks", ".active")
LIB_PATH = os.path.join(REPO_ROOT, "plugin", "scripts", "_lib.py")
_spec = importlib.util.spec_from_file_location("active_marker_lib", LIB_PATH)
assert _spec and _spec.loader
active_marker_lib = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(active_marker_lib)


def _read_active(path: str = ACTIVE_PATH) -> str | None:
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
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

    def test_fixture_serializes_active_marker_for_xdist(self):
        src = inspect.getsource(scratch_task_in_real_repo)
        lock_src = inspect.getsource(conftest.active_marker_lock)
        self.assertIn("active_marker_lock(root)", src)
        self.assertIn("fcntl.flock", lock_src)


class ActiveMarkerResolutionTests(unittest.TestCase):
    def _task(self, repo: str, task_id: str, status: str) -> str:
        task = os.path.join(repo, "doc", "harness", "tasks", task_id)
        os.makedirs(task, exist_ok=True)
        close = None
        if status == "closed":
            close = "sha256:" + hashlib.sha256(b"RECEIPTS.jsonl\0<missing>\0").hexdigest()
        with open(os.path.join(task, "TASK.json"), "w", encoding="utf-8") as f:
            json.dump({
                "run_id": "0198c349-5800-7000-8000-000000000001",
                "execution_mode": "standard",
                "required_lenses": ["review-code", "qa-cli"],
                "close_receipt_fingerprint": close,
            }, f)
        return task

    def test_closed_session_marker_falls_back_to_live_legacy_marker(self):
        with tempfile.TemporaryDirectory() as repo:
            closed = self._task(repo, "TASK__closed", "closed")
            live = self._task(repo, "TASK__live", "implementing")
            active_marker_lib.write_active_marker(repo, closed, session_id="session-a")
            with open(os.path.join(repo, "doc", "harness", "tasks", ".active"), "w", encoding="utf-8") as f:
                f.write(live)
            self.assertEqual(
                active_marker_lib.resolve_active_task_dir(repo, session_id="session-a"),
                live,
            )

    def test_live_session_marker_keeps_precedence(self):
        with tempfile.TemporaryDirectory() as repo:
            session_task = self._task(repo, "TASK__session", "verifying")
            legacy_task = self._task(repo, "TASK__legacy", "implementing")
            active_marker_lib.write_active_marker(repo, session_task, session_id="session-a")
            with open(os.path.join(repo, "doc", "harness", "tasks", ".active"), "w", encoding="utf-8") as f:
                f.write(legacy_task)
            self.assertEqual(
                active_marker_lib.resolve_active_task_dir(repo, session_id="session-a"),
                session_task,
            )

    def test_active_iterator_excludes_closed_session_markers(self):
        with tempfile.TemporaryDirectory() as repo:
            closed = self._task(repo, "TASK__closed", "closed")
            live = self._task(repo, "TASK__live", "planning")
            active_marker_lib.write_active_marker(repo, closed, session_id="closed-session")
            active_marker_lib.write_active_marker(repo, live, session_id="live-session")
            self.assertEqual(list(active_marker_lib.iter_active_task_dirs(repo)), [live])

    def test_session_state_task_id_mismatch_falls_back_and_is_not_iterated(self):
        with tempfile.TemporaryDirectory() as repo:
            mismatched = self._task(repo, "TASK__marker-name", "implementing")
            with open(os.path.join(mismatched, "TASK.json"), "w", encoding="utf-8") as f:
                f.write("{}\n")
            legacy = self._task(repo, "TASK__legacy", "planning")
            active_marker_lib.write_active_marker(repo, mismatched, session_id="session-a")
            with open(os.path.join(repo, "doc", "harness", "tasks", ".active"), "w", encoding="utf-8") as f:
                f.write(legacy)
            self.assertEqual(
                active_marker_lib.resolve_active_task_dir(repo, session_id="session-a"),
                legacy,
            )
            self.assertEqual(list(active_marker_lib.iter_active_task_dirs(repo)), [legacy])

    def test_session_state_missing_task_id_falls_back_and_is_not_iterated(self):
        with tempfile.TemporaryDirectory() as repo:
            missing = self._task(repo, "TASK__marker-name", "implementing")
            with open(os.path.join(missing, "TASK.json"), "w", encoding="utf-8") as f:
                f.write("{}\n")
            legacy = self._task(repo, "TASK__legacy", "planning")
            active_marker_lib.write_active_marker(repo, missing, session_id="session-a")
            with open(os.path.join(repo, "doc", "harness", "tasks", ".active"), "w", encoding="utf-8") as f:
                f.write(legacy)
            self.assertEqual(
                active_marker_lib.resolve_active_task_dir(repo, session_id="session-a"),
                legacy,
            )
            self.assertEqual(list(active_marker_lib.iter_active_task_dirs(repo)), [legacy])

    def test_session_marker_leaf_and_payload_identity_fail_closed(self):
        with tempfile.TemporaryDirectory() as repo:
            session_task = self._task(repo, "TASK__session", "implementing")
            legacy = self._task(repo, "TASK__legacy", "planning")
            active_marker_lib.write_active_marker(repo, session_task, session_id="session-a")
            tasks = os.path.join(repo, "doc", "harness", "tasks")
            marker = os.path.join(tasks, ".active_sessions", "session-a.json")
            legacy_marker = os.path.join(tasks, ".active")
            with open(legacy_marker, "w", encoding="utf-8") as f:
                f.write(legacy)

            with open(marker, "r", encoding="utf-8") as f:
                valid = json.load(f)
            for field, value in (("session_id", "session-b"), ("task_id", "TASK__other")):
                with self.subTest(field=field):
                    payload = dict(valid)
                    payload[field] = value
                    with open(marker, "w", encoding="utf-8") as f:
                        json.dump(payload, f)
                    self.assertEqual(
                        active_marker_lib.resolve_active_task_dir(repo, session_id="session-a"),
                        legacy,
                    )
                    self.assertEqual(list(active_marker_lib.iter_active_task_dirs(repo)), [legacy])

            external = os.path.join(repo, "external-session.json")
            with open(external, "w", encoding="utf-8") as f:
                json.dump(valid, f)
            os.unlink(marker)
            os.symlink(external, marker)
            self.assertEqual(
                active_marker_lib.resolve_active_task_dir(repo, session_id="session-a"),
                legacy,
            )
            self.assertEqual(list(active_marker_lib.iter_active_task_dirs(repo)), [legacy])

    def test_symlinked_legacy_marker_is_not_followed(self):
        with tempfile.TemporaryDirectory() as repo:
            live = self._task(repo, "TASK__live", "implementing")
            tasks = os.path.join(repo, "doc", "harness", "tasks")
            os.makedirs(os.path.join(tasks, ".active_sessions"))
            external = os.path.join(repo, "external-active")
            with open(external, "w", encoding="utf-8") as f:
                f.write(live)
            os.symlink(external, os.path.join(tasks, ".active"))
            self.assertEqual(
                active_marker_lib.resolve_active_task_dir(repo, session_id="missing"),
                "",
            )
            self.assertEqual(list(active_marker_lib.iter_active_task_dirs(repo)), [])

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO marker regression requires POSIX")
    def test_fifo_session_and_legacy_markers_are_ignored_without_blocking(self):
        with tempfile.TemporaryDirectory() as repo:
            legacy = self._task(repo, "TASK__legacy", "planning")
            active_marker_lib.write_active_marker(repo, legacy, session_id="session-a")
            tasks = os.path.join(repo, "doc", "harness", "tasks")
            session_marker = os.path.join(tasks, ".active_sessions", "session-a.json")
            legacy_marker = os.path.join(tasks, ".active")

            os.unlink(session_marker)
            os.mkfifo(session_marker)
            self.assertEqual(
                active_marker_lib.resolve_active_task_dir(repo, session_id="session-a"),
                legacy,
            )
            self.assertEqual(list(active_marker_lib.iter_active_task_dirs(repo)), [legacy])

            os.unlink(session_marker)
            os.unlink(legacy_marker)
            os.mkfifo(legacy_marker)
            self.assertEqual(
                active_marker_lib.resolve_active_task_dir(repo, session_id="session-a"),
                "",
            )
            self.assertEqual(list(active_marker_lib.iter_active_task_dirs(repo)), [])

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO state regression requires POSIX")
    def test_symlinked_and_fifo_task_state_do_not_activate_task(self):
        with tempfile.TemporaryDirectory() as repo:
            task = self._task(repo, "TASK__unsafe-state", "implementing")
            fallback = self._task(repo, "TASK__fallback", "planning")
            active_marker_lib.write_active_marker(repo, task, session_id="session-a")
            tasks = os.path.join(repo, "doc", "harness", "tasks")
            with open(os.path.join(tasks, ".active"), "w", encoding="utf-8") as f:
                f.write(fallback)
            state = os.path.join(task, "TASK.json")
            external = os.path.join(repo, "external-state.json")
            with open(external, "w", encoding="utf-8") as f:
                f.write("task_id: TASK__unsafe-state\nstatus: implementing\n")

            os.unlink(state)
            os.symlink(external, state)
            self.assertEqual(
                active_marker_lib.resolve_active_task_dir(repo, session_id="session-a"),
                fallback,
            )

            os.unlink(state)
            os.mkfifo(state)
            self.assertEqual(
                active_marker_lib.resolve_active_task_dir(repo, session_id="session-a"),
                fallback,
            )

    def test_symlinked_active_sessions_root_never_writes_or_deletes_outside(self):
        with tempfile.TemporaryDirectory() as repo:
            task = self._task(repo, "TASK__live", "implementing")
            tasks = os.path.join(repo, "doc", "harness", "tasks")
            outside = os.path.join(repo, "outside-sessions")
            os.makedirs(outside)
            os.symlink(outside, os.path.join(tasks, ".active_sessions"))
            sentinel = os.path.join(outside, "session-a.json")
            with open(sentinel, "w", encoding="utf-8") as f:
                f.write("keep")

            with self.assertRaisesRegex(ValueError, "active session marker root"):
                active_marker_lib.write_active_marker(repo, task, session_id="session-a")
            active_marker_lib.clear_active_marker(repo, task, session_id="session-a")

            with open(sentinel, "r", encoding="utf-8") as f:
                self.assertEqual(f.read(), "keep")


class FixtureRoundTripTests(unittest.TestCase):
    """AC-002, AC-003, AC-004: live round-trip behavior."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="active-fixture-roundtrip-")
        self.tasks_dir = os.path.join(self.tmp, "doc", "harness", "tasks")
        os.makedirs(self.tasks_dir)
        self.active_path = os.path.join(self.tasks_dir, ".active")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_restores_prior_content_on_success(self):
        synthetic = "TASK__synthetic-prior-active\n"
        with open(self.active_path, "w", encoding="utf-8") as f:
            f.write(synthetic)
        before = _read_active(self.active_path)
        self.assertEqual(before, synthetic)

        with scratch_task_in_real_repo(
            "active-race-success", repo_root=self.tmp
        ) as task_dir:
            self.assertEqual(_read_active(self.active_path), task_dir)

        self.assertEqual(
            _read_active(self.active_path), before,
            "prior .active content was not restored after success exit",
        )

    def test_no_active_at_entry_means_no_active_at_exit(self):
        self.assertFalse(os.path.isfile(self.active_path))

        with scratch_task_in_real_repo(
            "active-race-empty", repo_root=self.tmp
        ) as task_dir:
            self.assertEqual(_read_active(self.active_path), task_dir)

        self.assertFalse(
            os.path.isfile(self.active_path),
            "fixture left a stray .active when none existed at entry",
        )

    def test_restores_on_body_exception(self):
        before = _read_active(self.active_path)

        class _Boom(Exception):
            pass

        with self.assertRaises(_Boom):
            with scratch_task_in_real_repo(
                "active-race-raise", repo_root=self.tmp
            ) as task_dir:
                self.assertEqual(_read_active(self.active_path), task_dir)
                raise _Boom("simulated fixture-body failure")

        self.assertEqual(
            _read_active(self.active_path), before,
            "fixture left .active corrupted after body raised",
        )

    def test_sequential_reentry_preserves_outer_active(self):
        before = _read_active(self.active_path)

        for slug in ("active-race-seq-a", "active-race-seq-b", "active-race-seq-c"):
            with scratch_task_in_real_repo(slug, repo_root=self.tmp) as task_dir:
                self.assertEqual(
                    _read_active(self.active_path), task_dir,
                    f"during {slug}: .active should point at scratch",
                )

        self.assertEqual(
            _read_active(self.active_path), before,
            "after sequential fixture entries, .active does not match starting state",
        )

    def test_no_stale_backup_files_after_success(self):
        tasks_dir = self.tasks_dir
        before = {p for p in os.listdir(tasks_dir) if ".fixture-backup." in p}

        with scratch_task_in_real_repo("active-race-cleanup", repo_root=self.tmp):
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
