"""C-05 cross-checkout protection for the prewrite gate.

A ``harness:batch`` lead runs with its cwd in a linked worktree
(``<main>/.claude/worktrees/<name>``). The gate's control root is then the
worktree, so the main checkout and sibling worktrees are outside it. For such
an out-of-root target the gate resolves the target's own Harness root and
applies only the protected-artifact rules there: TASK.json, PLAN.md,
RECEIPTS.jsonl, task-local REVIEWS.jsonl, goal control JSON, and the focus
markers are denied; plan-first and scope lock are not applied to another
checkout. See doc/harness/patterns/prewrite-gate.md "Cross-checkout protection".
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "plugin" / "scripts"
GATE = SCRIPTS / "prewrite_gate.py"
SID = "c0ffee00-1111-4222-8333-444455556666"
MANIFEST = "version: 5\nsource_git_roots: []\n"
TASKS = "doc/harness/tasks"

# (relative path inside a checkout, owner= value the in-root C-05 deny uses)
PROTECTED = (
    (f"{TASKS}/TASK__x/TASK.json", "task-control-mcp"),
    (f"{TASKS}/TASK__x/PLAN.md", "plan-skill"),
    (f"{TASKS}/TASK__x/RECEIPTS.jsonl", "receipt-lifecycle-hook"),
    (f"{TASKS}/TASK__x/REVIEWS.jsonl", "review-detail-writer"),
    (f"{TASKS}/.active_sessions/{SID}.json", "task-control-runtime"),
    (f"{TASKS}/.active", "task-control-runtime"),
    ("doc/harness/goals/current.json", "legacy-data-migration"),
)
IN_ROOT_TASK_JSON_TEXT = (
    "TASK.json is owned by task control MCP tools. Use the owning skill or MCP "
    "tool (e.g. wbs action=plan for PLAN.md)."
)


def _git(cwd, *args):
    subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
        env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
    )


def _tool_input(tool, path):
    if tool == "MultiEdit":
        return {"file_path": str(path), "edits": [{"old_string": "a", "new_string": "b"}]}
    if tool == "Edit":
        return {"file_path": str(path), "old_string": "a", "new_string": "b"}
    if tool == "apply_patch":
        return {"patch": f"*** Begin Patch\n*** Update File: {path}\n@@\n-a\n+b\n*** End Patch\n"}
    return {"file_path": str(path), "content": "{}\n"}


def _gate(cwd: Path, path, tool="Write"):
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_ROOT"] = str(REPO_ROOT / "plugin")
    for name in ("HARNESS_SKIP_PREWRITE", "HARNESS_DISABLE_SCOPE_LOCK"):
        env.pop(name, None)
    payload = {
        "hook_event_name": "PreToolUse", "session_id": SID, "cwd": str(cwd),
        "tool_name": tool, "tool_input": _tool_input(tool, path),
    }
    result = subprocess.run(
        [sys.executable, str(GATE)], input=json.dumps(payload),
        capture_output=True, text=True, cwd=str(cwd), env=env, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    if not result.stdout.strip():
        return None, ""
    hso = json.loads(result.stdout).get("hookSpecificOutput") or {}
    return hso.get("permissionDecision"), hso.get("permissionDecisionReason") or ""


def _load_gate_module():
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location("prewrite_gate_cross_checkout_ut", GATE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CrossCheckoutFixture(unittest.TestCase):
    """One main checkout, two in-tree linked worktrees, one out-of-tree worktree."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.mkdtemp(prefix="prewrite-xco-")
        base = Path(os.path.realpath(cls._tmp))
        main = base / "main"
        main.mkdir()
        _git(main, "init", "-q", "-b", "master")
        _git(main, "config", "user.email", "t@example.com")
        _git(main, "config", "user.name", "t")
        (main / "doc/harness").mkdir(parents=True)
        (main / "doc/harness/manifest.yaml").write_text(MANIFEST, encoding="utf-8")
        (main / ".gitignore").write_text(
            "doc/harness/tasks/\ndoc/harness/goals/\n.claude/worktrees/\n", encoding="utf-8",
        )
        (main / "src").mkdir()
        (main / "src/app.py").write_text("x = 1\n", encoding="utf-8")
        _git(main, "add", "-A")
        _git(main, "commit", "-q", "-m", "init")
        worktrees = {}
        for name, path in (
            ("lead", main / ".claude/worktrees/lead"),
            ("sibling", main / ".claude/worktrees/sibling"),
            ("elsewhere", base / "elsewhere"),
        ):
            _git(main, "worktree", "add", "-q", "-b", f"wt-{name}", str(path))
            worktrees[name] = Path(os.path.realpath(path))
        cls.base, cls.main = base, main
        cls.lead, cls.sibling, cls.elsewhere = (
            worktrees["lead"], worktrees["sibling"], worktrees["elsewhere"],
        )
        for root in (main, cls.sibling, cls.elsewhere):
            for rel, _owner in PROTECTED:
                target = root / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("{}\n", encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)


class TestWorktreeCwdProtectsOtherCheckouts(CrossCheckoutFixture):
    def assert_c05_deny(self, cwd, root, rel, owner, tool="Write"):
        decision, reason = _gate(cwd, root / rel, tool)
        self.assertEqual(decision, "deny", f"{tool} {root / rel}: {reason}")
        self.assertIn("rule=C-05-protected-artifact", reason)
        self.assertIn(f"owner={owner}", reason)
        self.assertIn(f"path={rel}", reason)
        self.assertIn(f"another Harness checkout: {root}", reason)

    def test_main_checkout_protected_artifacts_denied_from_worktree(self):
        for rel, owner in PROTECTED:
            for tool in ("Write", "Edit", "MultiEdit"):
                with self.subTest(rel=rel, tool=tool):
                    self.assert_c05_deny(self.lead, self.main, rel, owner, tool)

    def test_sibling_worktree_protected_artifacts_denied_from_worktree(self):
        for rel, owner in PROTECTED:
            with self.subTest(rel=rel):
                self.assert_c05_deny(self.lead, self.sibling, rel, owner)

    def test_apply_patch_path_is_checked_across_checkouts(self):
        rel, owner = PROTECTED[0]
        self.assert_c05_deny(self.lead, self.main, rel, owner, "apply_patch")

    def test_cross_checkout_deny_keeps_the_in_root_owner_and_sentence(self):
        target = self.main / f"{TASKS}/TASK__x/TASK.json"
        in_root = _gate(self.main, target)
        cross = _gate(self.lead, target)
        self.assertEqual(in_root[0], "deny")
        self.assertEqual(cross[0], "deny")
        self.assertIn(IN_ROOT_TASK_JSON_TEXT, in_root[1])
        self.assertIn(IN_ROOT_TASK_JSON_TEXT, cross[1])
        self.assertNotIn("another Harness checkout", in_root[1])

    def test_relative_traversal_into_main_checkout_denied(self):
        rel = f"{TASKS}/TASK__x/TASK.json"
        relative = os.path.relpath(self.main / rel, self.lead)
        self.assertTrue(relative.startswith(".."))
        decision, reason = _gate(self.lead, relative)
        self.assertEqual(decision, "deny")
        self.assertIn("rule=C-05-protected-artifact", reason)

    def test_out_of_root_symlink_to_foreign_artifact_denied(self):
        # Neither alias basename is protected: only the realpath candidate
        # matches, and task-local REVIEWS.jsonl needs the realpath'd root.
        outside = self.base / "plain-links"
        outside.mkdir(exist_ok=True)
        for name, rel, owner in (
            ("receipts-alias.json", f"{TASKS}/TASK__x/RECEIPTS.jsonl", "receipt-lifecycle-hook"),
            ("reviews-alias.json", f"{TASKS}/TASK__x/REVIEWS.jsonl", "review-detail-writer"),
        ):
            with self.subTest(rel=rel):
                alias = outside / name
                if not alias.is_symlink():
                    os.symlink(self.main / rel, alias)
                decision, reason = _gate(self.lead, alias)
                self.assertEqual(decision, "deny")
                self.assertIn(f"owner={owner}", reason)
                self.assertIn(f"path={rel}", reason)

    def test_main_cwd_denies_out_of_tree_worktree_artifacts(self):
        for rel, owner in PROTECTED:
            with self.subTest(rel=rel):
                self.assert_c05_deny(self.main, self.elsewhere, rel, owner)

    def test_denied_calls_leave_the_main_checkout_untouched(self):
        before = {
            rel: (self.main / rel).read_text(encoding="utf-8") for rel, _owner in PROTECTED
        }
        for rel, _owner in PROTECTED:
            self.assertEqual(_gate(self.lead, self.main / rel)[0], "deny")
        after = {
            rel: (self.main / rel).read_text(encoding="utf-8") for rel, _owner in PROTECTED
        }
        self.assertEqual(before, after)
        self.assertFalse((self.main / "doc/harness/learnings.jsonl").exists())


class TestUnchangedBehavior(CrossCheckoutFixture):
    def test_foreign_source_file_is_allowed_without_plan_first_or_scope_lock(self):
        # Only C-05 crosses checkouts: plan-first, workflow-control-surface and
        # scope-lock belong to the checkout that owns the active task, so the
        # main checkout's ordinary source stays a silent allow from a worktree.
        for target in (
            self.main / "src/app.py",
            self.main / "plugin/scripts/prewrite_gate.py",
            self.sibling / "src/app.py",
        ):
            with self.subTest(target=str(target)):
                self.assertEqual(_gate(self.lead, target), (None, ""))

    def test_non_harness_target_is_unaffected(self):
        plain = self.base / "plain"
        for target in (
            plain / f"{TASKS}/TASK__x/TASK.json",
            plain / "doc/harness/goals/current.json",
            plain / "notes.txt",
        ):
            with self.subTest(target=str(target)):
                self.assertEqual(_gate(self.lead, target), (None, ""))

    def test_worktree_own_protected_artifact_is_still_denied_in_root(self):
        decision, reason = _gate(self.lead, self.lead / f"{TASKS}/TASK__x/PLAN.md")
        self.assertEqual(decision, "deny")
        self.assertIn("rule=C-05-protected-artifact", reason)
        self.assertNotIn("another Harness checkout", reason)


class TestFailSafe(CrossCheckoutFixture):
    """C-12: an error in the cross-checkout branch degrades to today's allow."""

    def _resolution_failing_outside(self, gate):
        real = gate.harness_root_resolution
        lead = str(self.lead)

        def cwd_only(start=None):
            if os.path.realpath(str(start)) == lead:
                return real(start)
            raise RuntimeError("boom")

        return cwd_only

    def test_foreign_check_error_degrades_to_allow_and_logs_to_cwd_root(self):
        gate = _load_gate_module()
        target = str(self.main / f"{TASKS}/TASK__x/TASK.json")
        out = io.StringIO()
        with mock.patch.object(
            gate, "harness_root_resolution", side_effect=self._resolution_failing_outside(gate),
        ), mock.patch.object(gate, "_log_gate_error") as logged, redirect_stdout(out):
            gate._check_path({"cwd": str(self.lead)}, target)
        self.assertEqual(out.getvalue(), "")
        logged.assert_called_once()
        self.assertEqual(logged.call_args.kwargs.get("repo_root"), str(self.lead))

    def test_protected_classifier_error_degrades_to_allow(self):
        gate = _load_gate_module()
        target = str(self.sibling / f"{TASKS}/TASK__x/PLAN.md")
        out = io.StringIO()
        with mock.patch.object(gate, "_is_protected_artifact", side_effect=ValueError("bad")), \
                mock.patch.object(gate, "_log_gate_error"), \
                redirect_stdout(out):
            gate._check_path({"cwd": str(self.lead)}, target)
        self.assertEqual(out.getvalue(), "")

    def test_foreign_error_does_not_drop_a_later_in_root_deny(self):
        # One apply_patch touching a foreign path first and the lead's own
        # TASK.json second: the first path's error must not end the loop.
        gate = _load_gate_module()
        foreign = self.main / f"{TASKS}/TASK__x/TASK.json"
        own = self.lead / f"{TASKS}/TASK__x/TASK.json"
        patch = (
            "*** Begin Patch\n"
            f"*** Update File: {foreign}\n@@\n-a\n+b\n"
            f"*** Update File: {own}\n@@\n-a\n+b\n"
            "*** End Patch\n"
        )
        data = {"cwd": str(self.lead), "tool_name": "apply_patch", "tool_input": {"patch": patch}}
        out = io.StringIO()
        with mock.patch.dict(os.environ, {}, clear=False), \
                mock.patch.object(gate, "read_hook_input", return_value=data), \
                mock.patch.object(
                    gate, "harness_root_resolution",
                    side_effect=self._resolution_failing_outside(gate),
                ), \
                mock.patch.object(gate, "_log_gate_error"), \
                redirect_stdout(out):
            os.environ.pop("HARNESS_SKIP_PREWRITE", None)
            self.assertEqual(gate.main(), 0)
        hso = json.loads(out.getvalue())["hookSpecificOutput"]
        self.assertEqual(hso["permissionDecision"], "deny")
        self.assertIn("rule=C-05-protected-artifact", hso["permissionDecisionReason"])
        self.assertNotIn("another Harness checkout", hso["permissionDecisionReason"])


if __name__ == "__main__":
    unittest.main()
