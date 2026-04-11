#!/usr/bin/env python3
"""Tests for the `plan` subcommand of write_artifact.py.

Run from repo root:
    python3 -m unittest discover -s plugin-legacy/scripts/tests -v

The CLI's `_validate_task_dir` hardcodes the canonical tasks root, so tests
use real subdirectories under `doc/harness/tasks/TASK__test_<uuid>` and
tear them down in `tearDown`.
"""

import json
import os
import shutil
import subprocess
import unittest
import uuid


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
CLI = os.path.join(REPO_ROOT, "plugin-legacy", "scripts", "write_artifact.py")
TASKS_ROOT = os.path.join(REPO_ROOT, "doc", "harness", "tasks")


def _run(args, stdin=None):
    env = os.environ.copy()
    env.pop("CLAUDE_AGENT_NAME", None)
    env["HARNESS_SKIP_PREWRITE"] = "1"
    proc = subprocess.run(
        ["python3", CLI, "plan"] + args,
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
        cwd=REPO_ROOT,
    )
    return proc


def _make_task_dir(name, *, state="open", phase="write", source="plan-skill", write_token=True):
    slug = f"TASK__test_{name}_{uuid.uuid4().hex[:8]}"
    task_dir = os.path.join(TASKS_ROOT, slug)
    os.makedirs(task_dir, exist_ok=True)
    with open(os.path.join(task_dir, "TASK_STATE.yaml"), "w", encoding="utf-8") as fh:
        fh.write(f"task_id: {slug}\nstatus: created\nmaintenance_task: true\n")
    if write_token:
        token = {"state": state, "phase": phase, "source": source}
        with open(os.path.join(task_dir, "PLAN_SESSION.json"), "w", encoding="utf-8") as fh:
            json.dump(token, fh)
    return task_dir


class PlanSubcommandTests(unittest.TestCase):
    def setUp(self):
        self._dirs = []

    def tearDown(self):
        for d in self._dirs:
            shutil.rmtree(d, ignore_errors=True)

    def _task(self, *args, **kw):
        d = _make_task_dir(*args, **kw)
        self._dirs.append(d)
        return d

    # AC-002: valid plan write succeeds
    def test_valid_plan_write_succeeds(self):
        task_dir = self._task("valid")
        proc = _run(
            ["--task-dir", task_dir, "--artifact", "plan", "--input", "-"],
            stdin="# PLAN\nobjective: test\n",
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertTrue(os.path.isfile(os.path.join(task_dir, "PLAN.md")))
        self.assertTrue(os.path.isfile(os.path.join(task_dir, "PLAN.meta.json")))
        with open(os.path.join(task_dir, "PLAN.meta.json"), "r", encoding="utf-8") as fh:
            meta = json.load(fh)
        self.assertEqual(meta.get("author_role"), "plan-skill")

    # AC-003 (a): missing PLAN_SESSION rejects
    def test_missing_session_rejects(self):
        task_dir = self._task("nosess", write_token=False)
        proc = _run(
            ["--task-dir", task_dir, "--artifact", "plan", "--input", "-"],
            stdin="content",
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("requires active plan session token", proc.stderr)

    # AC-003 (c): phase=context rejects — CLI strict helper differs from hook gate
    def test_context_phase_rejects(self):
        task_dir = self._task("ctx", phase="context")
        proc = _run(
            ["--task-dir", task_dir, "--artifact", "plan", "--input", "-"],
            stdin="content",
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("phase=", proc.stderr)

    # AC-003 (d): wrong source rejects
    def test_wrong_source_rejects(self):
        task_dir = self._task("src", source="developer")
        proc = _run(
            ["--task-dir", task_dir, "--artifact", "plan", "--input", "-"],
            stdin="content",
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("source=", proc.stderr)

    # AC-006: audit append creates header and then appends without duplicating
    def test_audit_append_creates_header_then_appends(self):
        task_dir = self._task("audit")
        audit = os.path.join(task_dir, "AUDIT_TRAIL.md")
        row1 = "| 1 | ceo | Premise accepted | Mechanical | P6 | reasonable | none |\n"
        row2 = "| 2 | eng | ASCII diagram | Taste | P5 | simpler | SVG |\n"

        proc = _run(
            ["--task-dir", task_dir, "--artifact", "audit", "--append", "--input", "-"],
            stdin=row1,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        body = open(audit, "r", encoding="utf-8").read()
        self.assertIn("| # | phase | decision", body)
        self.assertIn("| 1 | ceo |", body)

        proc2 = _run(
            ["--task-dir", task_dir, "--artifact", "audit", "--append", "--input", "-"],
            stdin=row2,
        )
        self.assertEqual(proc2.returncode, 0, msg=proc2.stderr)
        body2 = open(audit, "r", encoding="utf-8").read()
        self.assertEqual(body2.count("| # | phase | decision"), 1)
        self.assertIn("| 1 | ceo |", body2)
        self.assertIn("| 2 | eng |", body2)

    # AC-007: malformed audit row rejects
    def test_audit_malformed_row_rejects(self):
        task_dir = self._task("bad")
        bad_row = "| 1 | ceo | only three | cols |\n"  # 4 cells, expected 7
        proc = _run(
            ["--task-dir", task_dir, "--artifact", "audit", "--append", "--input", "-"],
            stdin=bad_row,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("column", proc.stderr.lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
