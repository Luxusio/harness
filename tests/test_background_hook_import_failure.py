"""A receipt subsystem that cannot import itself must say so.

Regression for the 2026-08-26 outage: a stale `__pycache__` entry in the loaded
plugin tree made `subagent_lifecycle` raise PermissionError from its
receipt-adapter binding. `background_hook` caught it, exited 0, and wrote
nothing anywhere, so every symptom was an absence. Three sessions attributed it
to three different causes before the import itself was instrumented.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HOOK = REPO / "plugin" / "scripts" / "background_hook.py"


class TestImportFailureIsReported(unittest.TestCase):
    def test_import_failure_writes_a_breadcrumb_and_exits_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scripts = root / "plugin" / "scripts"
            scripts.mkdir(parents=True)
            (root / "doc" / "harness").mkdir(parents=True)
            (scripts / "background_hook.py").write_text(
                HOOK.read_text(encoding="utf-8"), encoding="utf-8",
            )
            # A dependency that raises exactly the way the real outage did.
            (scripts / "_lib.py").write_text(
                "raise PermissionError('receipt adapter binding requires its "
                "canonical module import')\n",
                encoding="utf-8",
            )

            proc = subprocess.run(
                [sys.executable, str(scripts / "background_hook.py"), "--event", "stop"],
                input=b"{}",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=30,
            )

            # C-12: a hook must never block the session.
            self.assertEqual(proc.returncode, 0)

            learnings = root / "doc" / "harness" / "learnings.jsonl"
            self.assertTrue(
                learnings.exists(),
                "import failure left no breadcrumb; the outage is silent again",
            )
            rows = [
                json.loads(line)
                for line in learnings.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(len(rows), 1)
            entry = rows[0]
            self.assertEqual(entry["source"], "background_hook:import")
            self.assertEqual(entry["key"], "receipt-subsystem-unavailable")
            self.assertIn("PermissionError", entry["error"])
            # The breadcrumb has to name the consequence, not just the exception:
            # an absent receipt is otherwise indistinguishable from "no agent ran".
            self.assertIn("task_close", entry["insight"])
            self.assertIn("__pycache__", entry["insight"])

    def test_no_breadcrumb_when_imports_succeed(self):
        """The reporter must not fire on the healthy path."""
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        before = ""
        learnings = REPO / "doc" / "harness" / "learnings.jsonl"
        if learnings.exists():
            before = learnings.read_text(encoding="utf-8")

        proc = subprocess.run(
            [sys.executable, str(HOOK), "--event", "stop"],
            input=b"{}",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            env=env,
            cwd=str(REPO),
        )
        self.assertEqual(proc.returncode, 0)

        after = learnings.read_text(encoding="utf-8") if learnings.exists() else ""
        added = after[len(before):]
        self.assertNotIn("receipt-subsystem-unavailable", added)


if __name__ == "__main__":
    unittest.main()
