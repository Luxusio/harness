"""AC-002 + AC-006: prewrite_gate JSON decision output, MultiEdit, fail-safe,
env-escape, structured deny-reason tail.

Uses real subprocess invocation — no mocks.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
import os
import re
import subprocess
import sys
import tempfile
import unittest

from conftest import (  # type: ignore
    REPO_ROOT,
    SCRIPTS_DIR,
    invoke_hook,
    ledger_rows,
    make_tmp_harness_root,
    parse_decision,
    scratch_task_in_real_repo,
)

GATE = os.path.join(SCRIPTS_DIR, "prewrite_gate.py")


TAIL_RE = re.compile(
    r"\[gate=prewrite rule=\S+ path=\S+ owner=\S+ docs=\S+\]"
)


@contextmanager
def _isolated_task(name, *, plan=True, maintenance=False):
    from test_prewrite_gate_dormant import _write_task_state
    with tempfile.TemporaryDirectory() as tmp:
        root = make_tmp_harness_root(tmp)
        with scratch_task_in_real_repo(name, repo_root=root, plan=plan,
                                       maintenance=maintenance) as task_dir:
            _write_task_state(os.path.dirname(task_dir), name, "open")
            yield root, task_dir


class TestAllowSilent(unittest.TestCase):
    def test_non_write_tool_is_silent(self):
        r = invoke_hook(GATE, "Read", {"file_path": "/tmp/x"})
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")

    def test_bash_tool_is_silent(self):
        r = invoke_hook(GATE, "Bash", {"command": "ls"})
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")

    def test_missing_file_path_is_silent(self):
        r = invoke_hook(GATE, "Write", {})
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")

    def test_unrelated_review_detail_basename_is_not_harness_owned(self):
        with _isolated_task("pr1-unrelated-review-detail") as (root, task_dir):
            target = os.path.join(root, "app/data/REVIEWS.jsonl")
            r = invoke_hook(GATE, "Write", {"file_path": target}, cwd=root)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")

    def test_nested_task_looking_review_detail_path_is_not_harness_owned(self):
        with _isolated_task("pr1-nested-review-detail") as (root, task_dir):
            target = os.path.join(
                root,
                "fixtures/doc/harness/tasks/TASK__sample/REVIEWS.jsonl",
            )
            r = invoke_hook(GATE, "Write", {"file_path": target}, cwd=root)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")


class TestDenyProtectedArtifact(unittest.TestCase):
    def test_write_native_goal_control_denies(self):
        goal = os.path.join(REPO_ROOT, "doc/harness/goals/current.json")
        r = invoke_hook(GATE, "Write", {"file_path": goal})
        decision, reason = parse_decision(r.stdout)
        self.assertEqual(decision, "deny")
        self.assertIn("owner=legacy-data-migration", reason)
        self.assertIn('wbs(action="migrate")', reason)




    def test_write_plan_md_inside_task_denies(self):
        with _isolated_task("pr1-protected") as (root, task_dir):
            plan = os.path.join(task_dir, "PLAN.md")
            r = invoke_hook(GATE, "Write", {"file_path": plan}, cwd=root)
            self.assertEqual(r.returncode, 0)
            decision, reason = parse_decision(r.stdout)
            self.assertEqual(decision, "deny")
            self.assertIsNotNone(reason)
            self.assertRegex(reason, TAIL_RE)
            self.assertIn("C-05-protected-artifact", reason)
            self.assertIn("HARNESS_SKIP_PREWRITE", reason)

    def test_write_task_control_denies_with_mcp_owner(self):
        with _isolated_task("pr1-review-detail") as (root, task_dir):
            target = os.path.join(task_dir, "TASK.json")
            r = invoke_hook(GATE, "Write", {"file_path": target}, cwd=root)
            decision, reason = parse_decision(r.stdout)
            self.assertEqual(decision, "deny")
            self.assertIn("C-05-protected-artifact", reason)
            self.assertIn("owner=task-control-mcp", reason)
            self.assertIn("task control MCP tools", reason)

    def test_symlink_alias_to_task_control_is_still_protected(self):
        with _isolated_task("pr1-review-detail-alias") as (root, task_dir), \
             tempfile.TemporaryDirectory(dir=root) as alias_parent:
            alias = os.path.join(alias_parent, "task-alias")
            os.symlink(task_dir, alias, target_is_directory=True)
            target = os.path.join(alias, "TASK.json")
            r = invoke_hook(GATE, "Write", {"file_path": target}, cwd=root)
        decision, reason = parse_decision(r.stdout)
        self.assertEqual(decision, "deny")
        self.assertIn("C-05-protected-artifact", reason)
        self.assertIn("owner=task-control-mcp", reason)

class TestMultiEdit(unittest.TestCase):
    def test_apply_patch_native_goal_control_denies(self):
        goal = os.path.join(REPO_ROOT, "doc/harness/goals/GOAL__forged.json")
        patch = f"*** Begin Patch\n*** Add File: {goal}\n+{{}}\n*** End Patch"
        r = invoke_hook(GATE, "apply_patch", {"patch": patch})
        decision, reason = parse_decision(r.stdout)
        self.assertEqual(decision, "deny")
        self.assertIn("legacy-data-migration", reason)

    def test_multiedit_triggers_gate(self):
        """MultiEdit on a protected artifact must deny."""
        with _isolated_task("pr1-multiedit") as (root, task_dir):
            plan = os.path.join(task_dir, "PLAN.md")
            r = invoke_hook(GATE, "MultiEdit", {"file_path": plan}, cwd=root)
            decision, reason = parse_decision(r.stdout)
            self.assertEqual(decision, "deny")
            self.assertIn("PLAN.md", reason)

    def test_codex_apply_patch_checks_every_target(self):
        with _isolated_task("pr1-apply-patch") as (root, task_dir):
            plan = os.path.join(task_dir, "PLAN.md")
            patch = (
                "*** Begin Patch\n"
                "*** Update File: README.md\n"
                "@@\n-old\n+new\n"
                f"*** Update File: {plan}\n"
                "@@\n-old\n+new\n"
                "*** End Patch"
            )
            r = invoke_hook(GATE, "apply_patch", {"patch": patch}, cwd=root)
            decision, reason = parse_decision(r.stdout)
            self.assertEqual(decision, "deny")
            self.assertIn("PLAN.md", reason)

    def test_codex_apply_patch_crlf_target_is_not_bypassed(self):
        with _isolated_task("pr1-apply-patch-crlf") as (root, task_dir):
            plan = os.path.join(task_dir, "PLAN.md")
            patch = (
                "*** Begin Patch\r\n"
                "*** Update File: README.md\r\n"
                "@@\r\n-old\r\n+new\r\n"
                f"*** Update File: {plan}\r\n"
                "@@\r\n-old\r\n+new\r\n"
                "*** End Patch\r\n"
            )
            r = invoke_hook(GATE, "apply_patch", {"patch": patch}, cwd=root)
            decision, reason = parse_decision(r.stdout)
            self.assertEqual(decision, "deny")
            self.assertIn("PLAN.md", reason)


class TestPlanFirst(unittest.TestCase):
    def test_no_plan_blocks_source_write(self):
        with _isolated_task("pr1-noplan", plan=False) as (root, task_dir):
            # write target outside the task dir (source file)
            target = os.path.join(root, "some_src.py")
            r = invoke_hook(GATE, "Write", {"file_path": target}, cwd=root)
            decision, reason = parse_decision(r.stdout)
            self.assertEqual(decision, "deny")
            self.assertIn("C-02-plan-first", reason)

    def test_maintenance_bypass_allows_source_write(self):
        with _isolated_task("pr1-maint", plan=False, maintenance=True) as (root, task_dir):
            target = os.path.join(root, "tmp_src.py")
            r = invoke_hook(GATE, "Write", {"file_path": target}, cwd=root)
            # Maintenance skips the long plan, never execution assignment.
            self.assertEqual(r.returncode, 0)
            decision, reason = parse_decision(r.stdout)
            self.assertEqual(decision, "deny")
            self.assertIn("wbs-execution-assignment", reason)


class TestWorkflowControlSurface(unittest.TestCase):
    def test_non_maintenance_task_blocks_hooks_json(self):
        with _isolated_task("pr1-wcs") as (root, task_dir):
            target = os.path.join(root, "plugin/hooks/hooks.json")
            r = invoke_hook(GATE, "Write", {"file_path": target}, cwd=root)
            decision, reason = parse_decision(r.stdout)
            self.assertEqual(decision, "deny")
            self.assertIn("workflow-control-surface", reason)

    def test_maintenance_task_allows_hooks_json(self):
        with _isolated_task("pr1-wcs-maint", maintenance=True) as (root, task_dir):
            target = os.path.join(root, "plugin/hooks/hooks.json")
            r = invoke_hook(GATE, "Write", {"file_path": target}, cwd=root)
            self.assertEqual(r.returncode, 0)
            decision, reason = parse_decision(r.stdout)
            self.assertEqual(decision, "deny")
            self.assertIn("wbs-execution-assignment", reason)


class TestEnvEscape(unittest.TestCase):
    def test_skip_env_allows_and_logs_bypass(self):
        # The bypass row goes to the ledger of the checkout the gate runs in,
        # so the gate runs in a tmp one: from the real repo it appended to the
        # developer's gitignored `doc/harness/learnings.jsonl`.
        with tempfile.TemporaryDirectory() as tmp:
            root = make_tmp_harness_root(tmp)
            with scratch_task_in_real_repo("pr1-skip", repo_root=root) as task_dir:
                plan = os.path.join(task_dir, "PLAN.md")
                # Without the env var this would be deny; with it → silent allow.
                r = invoke_hook(
                    GATE, "Write", {"file_path": plan},
                    env_extra={"HARNESS_SKIP_PREWRITE": "1"},
                    cwd=root,
                )
                self.assertEqual(r.returncode, 0)
                self.assertEqual(r.stdout, "")
            rows = ledger_rows(root)
        self.assertEqual(
            [(row.get("type"), row.get("path")) for row in rows],
            [("gate-bypass", plan)],
            rows,
        )


class TestFailSafe(unittest.TestCase):
    def test_malformed_stdin_is_exit0(self):
        env = os.environ.copy()
        env["CLAUDE_PLUGIN_ROOT"] = os.path.join(REPO_ROOT, "plugin")
        r = subprocess.run(
            [sys.executable, GATE],
            input="not json at all {{{",
            capture_output=True, text=True, cwd=REPO_ROOT, env=env, timeout=5,
        )
        self.assertEqual(r.returncode, 0)

    def test_non_dict_payload_is_exit0(self):
        r = subprocess.run(
            [sys.executable, GATE],
            input='["array","not","dict"]',
            capture_output=True, text=True, cwd=REPO_ROOT, timeout=5,
        )
        self.assertEqual(r.returncode, 0)


class TestReasonFormat(unittest.TestCase):
    def test_every_deny_has_structured_tail(self):
        with _isolated_task("pr1-tail") as (root, task_dir):
            for basename in ("PLAN.md", "TASK.json"):
                target = os.path.join(task_dir, basename)
                r = invoke_hook(GATE, "Write", {"file_path": target}, cwd=root)
                decision, reason = parse_decision(r.stdout)
                self.assertEqual(decision, "deny", f"{basename} did not deny")
                self.assertRegex(reason, TAIL_RE,
                                 f"{basename} missing structured tail in reason")
                self.assertIn("HARNESS_SKIP_PREWRITE", reason,
                              f"{basename} missing escape hint")


if __name__ == "__main__":
    unittest.main()
