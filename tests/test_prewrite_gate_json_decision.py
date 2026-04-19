"""AC-002 + AC-006: prewrite_gate JSON decision output, MultiEdit, fail-safe,
env-escape, structured deny-reason tail.

Uses real subprocess invocation — no mocks.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import unittest

from conftest import (  # type: ignore
    REPO_ROOT,
    SCRIPTS_DIR,
    invoke_hook,
    parse_decision,
    scratch_task_in_real_repo,
)

GATE = os.path.join(SCRIPTS_DIR, "prewrite_gate.py")


TAIL_RE = re.compile(
    r"\[gate=prewrite rule=\S+ path=\S+ owner=\S+ docs=\S+\]"
)


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


class TestDenyProtectedArtifact(unittest.TestCase):
    def test_write_plan_md_inside_task_denies(self):
        with scratch_task_in_real_repo("pr1-protected") as task_dir:
            plan = os.path.join(task_dir, "PLAN.md")
            r = invoke_hook(GATE, "Write", {"file_path": plan})
            self.assertEqual(r.returncode, 0)
            decision, reason = parse_decision(r.stdout)
            self.assertEqual(decision, "deny")
            self.assertIsNotNone(reason)
            self.assertRegex(reason, TAIL_RE)
            self.assertIn("C-05-protected-artifact", reason)
            self.assertIn("HARNESS_SKIP_PREWRITE", reason)

    def test_write_checks_yaml_denies(self):
        with scratch_task_in_real_repo("pr1-checks") as task_dir:
            checks = os.path.join(task_dir, "CHECKS.yaml")
            r = invoke_hook(GATE, "Write", {"file_path": checks})
            decision, reason = parse_decision(r.stdout)
            self.assertEqual(decision, "deny")
            self.assertIn("CHECKS.yaml", reason)
            self.assertIn("update_checks.py", reason)


class TestMultiEdit(unittest.TestCase):
    def test_multiedit_triggers_gate(self):
        """MultiEdit on a protected artifact must deny (current gate hole pre-PR1)."""
        with scratch_task_in_real_repo("pr1-multiedit") as task_dir:
            handoff = os.path.join(task_dir, "HANDOFF.md")
            r = invoke_hook(GATE, "MultiEdit", {"file_path": handoff})
            decision, reason = parse_decision(r.stdout)
            self.assertEqual(decision, "deny")
            self.assertIn("HANDOFF.md", reason)


class TestPlanFirst(unittest.TestCase):
    def test_no_plan_blocks_source_write(self):
        with scratch_task_in_real_repo("pr1-noplan", plan=False) as task_dir:
            # write target outside the task dir (source file)
            target = os.path.join(REPO_ROOT, "some_src.py")
            r = invoke_hook(GATE, "Write", {"file_path": target})
            decision, reason = parse_decision(r.stdout)
            self.assertEqual(decision, "deny")
            self.assertIn("C-02-plan-first", reason)

    def test_maintenance_bypass_allows_source_write(self):
        with scratch_task_in_real_repo("pr1-maint", plan=False, maintenance=True):
            target = os.path.join(REPO_ROOT, "tmp_src.py")
            r = invoke_hook(GATE, "Write", {"file_path": target})
            # MAINTENANCE marker → allow even without PLAN.md (source file path)
            self.assertEqual(r.returncode, 0)
            self.assertEqual(r.stdout, "")


class TestWorkflowControlSurface(unittest.TestCase):
    def test_non_maintenance_task_blocks_hooks_json(self):
        with scratch_task_in_real_repo("pr1-wcs") as task_dir:
            target = os.path.join(REPO_ROOT, "plugin/hooks/hooks.json")
            r = invoke_hook(GATE, "Write", {"file_path": target})
            decision, reason = parse_decision(r.stdout)
            self.assertEqual(decision, "deny")
            self.assertIn("workflow-control-surface", reason)

    def test_maintenance_task_allows_hooks_json(self):
        with scratch_task_in_real_repo("pr1-wcs-maint", maintenance=True):
            target = os.path.join(REPO_ROOT, "plugin/hooks/hooks.json")
            r = invoke_hook(GATE, "Write", {"file_path": target})
            self.assertEqual(r.returncode, 0)
            self.assertEqual(r.stdout, "")


class TestEnvEscape(unittest.TestCase):
    def test_skip_env_allows_and_logs_bypass(self):
        with scratch_task_in_real_repo("pr1-skip") as task_dir:
            plan = os.path.join(task_dir, "PLAN.md")
            # Without the env var this would be deny; with it → silent allow.
            r = invoke_hook(
                GATE, "Write", {"file_path": plan},
                env_extra={"HARNESS_SKIP_PREWRITE": "1"},
            )
            self.assertEqual(r.returncode, 0)
            self.assertEqual(r.stdout, "")


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
        with scratch_task_in_real_repo("pr1-tail") as task_dir:
            for basename in ("PLAN.md", "HANDOFF.md", "CHECKS.yaml", "DOC_SYNC.md"):
                target = os.path.join(task_dir, basename)
                r = invoke_hook(GATE, "Write", {"file_path": target})
                decision, reason = parse_decision(r.stdout)
                self.assertEqual(decision, "deny", f"{basename} did not deny")
                self.assertRegex(reason, TAIL_RE,
                                 f"{basename} missing structured tail in reason")
                self.assertIn("HARNESS_SKIP_PREWRITE", reason,
                              f"{basename} missing escape hint")


if __name__ == "__main__":
    unittest.main()
