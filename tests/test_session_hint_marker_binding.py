"""Regression tests for Claude session -> active marker -> receipt binding.

Background: Claude Code passes no session id into the MCP server environment,
so ``current_session_id()`` resolved to ``default`` there and ``task_start``
wrote ``.active_sessions/default.json``. The SubagentStart/SubagentStop hooks
receive the real session id and read only ``<sid>.json``, so binding always
failed and ``RECEIPTS.jsonl`` was never written — silently, because the
lifecycle functions return ``{}`` without raising.

These tests pin the hint mechanism that keeps the marker filename and the
hook's session id in agreement.
"""
from __future__ import annotations

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
    def _call(self, repo_root: str, name: str, args: dict) -> dict:
        with mock.patch.object(
            harness_server, "find_repo_root", return_value=repo_root,
        ):
            return harness_server.call_tool(name, args)

    def _marker_names(self, repo_root: str) -> list[str]:
        sessions = Path(repo_root) / "doc/harness/tasks/.active_sessions"
        if not sessions.is_dir():
            return []
        return sorted(p.name for p in sessions.iterdir() if p.suffix == ".json")

    def test_task_start_binds_marker_to_hinted_session(self):
        """The outage regression: hook sid and marker filename must agree."""
        with tempfile.TemporaryDirectory() as tmp:
            harness_lib.write_session_hint(tmp, REAL_SID)
            result = self._call(tmp, "task_start", {"task_id": "TASK__hinted"})
            self.assertNotIn("isError", result)

            self.assertEqual(self._marker_names(tmp), [f"{REAL_SID}.json"])

            task_dir = str(Path(tmp) / "doc/harness/tasks/TASK__hinted")
            marker = json.loads(
                (Path(tmp) / "doc/harness/tasks/.active_sessions"
                 / f"{REAL_SID}.json").read_text(encoding="utf-8")
            )
            control = harness_lib.read_task_control(task_dir)
            self.assertEqual(marker["session_id"], REAL_SID)
            self.assertEqual(marker["run_id"], control["run_id"])

            # The hook-side resolution that was returning {} must now resolve.
            bound = harness_lib.resolve_session_task_binding(tmp, REAL_SID)
            self.assertEqual(
                os.path.realpath(bound.get("task_dir", "")),
                os.path.realpath(task_dir),
            )
            self.assertEqual(bound.get("run_id"), control["run_id"])

    def test_foreign_session_stays_unbound(self):
        """Exact-session isolation must survive the fix."""
        with tempfile.TemporaryDirectory() as tmp:
            harness_lib.write_session_hint(tmp, REAL_SID)
            self._call(tmp, "task_start", {"task_id": "TASK__isolated"})
            self.assertEqual(
                harness_lib.resolve_session_task_binding(tmp, "some-other-session"),
                {},
            )

    def test_without_hint_falls_back_to_default_marker(self):
        """No hint (Codex, hookless installs) keeps the legacy behavior."""
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(
                os.environ,
                {"CODEX_SESSION_ID": "", "CODEX_THREAD_ID": ""},
                clear=False,
            ):
                result = self._call(tmp, "task_start", {"task_id": "TASK__unhinted"})
            self.assertNotIn("isError", result)
            self.assertEqual(self._marker_names(tmp), ["default.json"])
            # And the pre-fix failure mode is still reproduced, by design:
            # "default" is never a valid lifecycle binding.
            self.assertEqual(
                harness_lib.resolve_session_task_binding(tmp, "default"), {},
            )


if __name__ == "__main__":
    unittest.main()
