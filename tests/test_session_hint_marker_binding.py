"""Regression tests for exact session hints and active task markers."""
from __future__ import annotations

def call_execution(*args, **kwargs):
    from test_harness_mcp_server import call_execution as execute
    return execute(*args, **kwargs)

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
SERVER_PATH = REPO_ROOT / "plugin" / "mcp" / "harness_server.py"
sys.path.insert(0, str(REPO_ROOT / "plugin" / "scripts"))

# Loaded by path, matching tests/test_harness_mcp_server.py: the MCP server
# lives outside plugin/scripts/, so a bare import would read as third-party.
# Reuse an existing instance — the control-writer authority binds exactly once
# per process, so re-executing the module raises PermissionError.
if "harness_server" in sys.modules:
    harness_server = sys.modules["harness_server"]
else:
    spec = importlib.util.spec_from_file_location("harness_server", SERVER_PATH)
    assert spec and spec.loader
    harness_server = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = harness_server
    spec.loader.exec_module(harness_server)
import _lib as harness_lib  # type: ignore  # noqa: E402


REAL_SID = "a1b2c3d4-5e6f-7890-abcd-ef1234567890"


class SessionHintTests(unittest.TestCase):
    def test_rejects_unusable_values(self):
        """default/empty/non-sanitizing ids must never become a marker name."""
        with tempfile.TemporaryDirectory() as tmp:
            for bad in ("", "   ", "default", "a/b", "a b", "../escape", None):
                self.assertFalse(
                    harness_lib.write_session_hint(tmp, bad), repr(bad),
                )
                self.assertEqual(harness_lib.read_session_hint(tmp), "", repr(bad))

    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(harness_lib.write_session_hint(tmp, REAL_SID))
            self.assertEqual(harness_lib.read_session_hint(tmp), REAL_SID)

    def test_missing_hint_reads_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(harness_lib.read_session_hint(tmp), "")

    def test_corrupt_hint_degrades_to_empty(self):
        """A damaged hint must fall back, never raise into a hook."""
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(harness_lib.write_session_hint(tmp, REAL_SID))
            hint = Path(harness_lib._session_hint_path(tmp))
            hint.write_text("default\n", encoding="utf-8")
            self.assertEqual(harness_lib.read_session_hint(tmp), "")
            hint.write_text("with space\n", encoding="utf-8")
            self.assertEqual(harness_lib.read_session_hint(tmp), "")


class TaskStartMarkerBindingTests(unittest.TestCase):
    def _call(self, repo_root, name, args, session_id=""):
        with mock.patch.object(harness_server, "find_repo_root", return_value=repo_root), mock.patch.dict(
            os.environ, {"HARNESS_SESSION_ID": session_id, "CLAUDE_SESSION_ID": "", "HARNESS_RUNTIME": "claude"},
        ):
            return call_execution(harness_server, name, args)

    def _marker_names(self, repo_root):
        sessions = Path(repo_root) / "doc/harness/tasks/.active_sessions"
        return sorted(p.name for p in sessions.glob("*.json")) if sessions.is_dir() else []

    def test_shared_hint_never_supplies_server_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness_lib.write_session_hint(tmp, REAL_SID)
            result = self._call(tmp, "task_start", {"task_id": "TASK__hinted"})
            self.assertNotIn("isError", result)
            self.assertEqual(self._marker_names(tmp), [])
            self.assertEqual(harness_lib.resolve_session_task_binding(tmp, REAL_SID), {})
            self.assertFalse((Path(tmp) / "doc/harness/tasks/.active").exists())

    def test_process_identity_binds_only_its_current_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness_lib.write_session_hint(tmp, "foreign-session")
            result = self._call(tmp, "task_start", {"task_id": "TASK__exact"}, REAL_SID)
            self.assertNotIn("isError", result)
            self.assertEqual(self._marker_names(tmp), [REAL_SID + ".json"])
            task_dir = str(Path(tmp) / "doc/harness/tasks/TASK__exact")
            bound = harness_lib.resolve_session_task_binding(tmp, REAL_SID)
            self.assertEqual(bound["task_dir"], task_dir)
            self.assertEqual(bound["run_id"], harness_lib.read_task_control(task_dir)["run_id"])
            self.assertEqual(harness_lib.resolve_session_task_binding(tmp, "foreign-session"), {})
            self.assertFalse((Path(tmp) / "doc/harness/tasks/.active").exists())

    def test_no_identity_defers_without_default_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._call(tmp, "task_start", {"task_id": "TASK__deferred"})
            self.assertNotIn("isError", result)
            self.assertEqual(self._marker_names(tmp), [])
            self.assertEqual(harness_lib.resolve_session_task_binding(tmp, "default"), {})


if __name__ == "__main__":
    unittest.main()
