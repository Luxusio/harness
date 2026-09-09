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

    def test_reports_into_the_repo_named_by_the_payload(self):
        """The 2026-09 recurrence: hooks run from the *installed* tree.

        `~/.claude/harness-dev/plugin/scripts` has no `doc/harness` above it, so
        the script-relative walk resolved nothing and the breadcrumb added after
        2026-08-26 never fired — through a month of dead receipts. The payload's
        `cwd` is the only handle on the real repository.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Deliberately *not* under a doc/harness tree: this stands in for
            # the installed plugin tree the hook actually executes from.
            scripts = root / "installed" / "plugin" / "scripts"
            scripts.mkdir(parents=True)
            (scripts / "background_hook.py").write_text(
                HOOK.read_text(encoding="utf-8"), encoding="utf-8",
            )
            (scripts / "_lib.py").write_text(
                "raise PermissionError('receipt adapter binding requires its "
                "canonical module import')\n",
                encoding="utf-8",
            )
            repo = root / "repo"
            (repo / "doc" / "harness").mkdir(parents=True)

            proc = subprocess.run(
                [sys.executable, str(scripts / "background_hook.py"), "--event", "stop"],
                input=json.dumps({"cwd": str(repo)}).encode("utf-8"),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=30,
            )
            self.assertEqual(proc.returncode, 0)

            learnings = repo / "doc" / "harness" / "learnings.jsonl"
            self.assertTrue(
                learnings.exists(),
                "no breadcrumb in the payload repo: the outage is silent again",
            )
            rows = [
                json.loads(line)
                for line in learnings.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["key"], "receipt-subsystem-unavailable")
            self.assertIn("PermissionError", rows[0]["error"])

    def test_the_payload_repo_wins_over_the_scripts_own_harness_root(self):
        """Primacy, not just availability.

        A dev checkout runs the hook from a tree that *does* have `doc/harness`
        above it, so both resolvers answer — and only the payload's answer is
        the repository the session is working in. With the operands the other
        way round the breadcrumb lands in whichever tree the script happens to
        live under, which for the installed plugin is a directory no one reads.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scripts = root / "scripthome" / "plugin" / "scripts"
            scripts.mkdir(parents=True)
            # The script's own harness root, distinct from the payload's.
            (root / "scripthome" / "doc" / "harness").mkdir(parents=True)
            (scripts / "background_hook.py").write_text(
                HOOK.read_text(encoding="utf-8"), encoding="utf-8",
            )
            (scripts / "_lib.py").write_text(
                "raise PermissionError('receipt adapter binding requires its "
                "canonical module import')\n",
                encoding="utf-8",
            )
            repo = root / "repo"
            (repo / "doc" / "harness").mkdir(parents=True)

            proc = subprocess.run(
                [sys.executable, str(scripts / "background_hook.py"), "--event", "stop"],
                input=json.dumps({"cwd": str(repo)}).encode("utf-8"),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=30,
            )
            self.assertEqual(proc.returncode, 0)

            self.assertTrue(
                (repo / "doc" / "harness" / "learnings.jsonl").exists(),
                "the breadcrumb missed the repository named by the payload",
            )
            self.assertFalse(
                (root / "scripthome" / "doc" / "harness" / "learnings.jsonl").exists(),
                "the breadcrumb went to the script's tree, not the session's",
            )

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
