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
import tempfile
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
    def test_write_claude_subagent_transcript_denies(self):
        transcript = os.path.expanduser(
            "~/.claude/projects/project/session/subagents/agent-review-code.jsonl"
        )
        r = invoke_hook(GATE, "Write", {"file_path": transcript})
        decision, reason = parse_decision(r.stdout)
        self.assertEqual(decision, "deny")
        self.assertIn("runtime-owned receipt provenance", reason)

    def test_write_symlink_to_codex_rollout_denies(self):
        sessions = os.path.expanduser("~/.codex/sessions")
        with tempfile.TemporaryDirectory() as tmp:
            alias = os.path.join(tmp, "codex-sessions")
            os.symlink(sessions, alias, target_is_directory=True)
            rollout = os.path.join(
                alias, "2026/08/13/rollout-runtime-thread.jsonl",
            )
            r = invoke_hook(GATE, "Write", {"file_path": rollout})
        decision, reason = parse_decision(r.stdout)
        self.assertEqual(decision, "deny")
        self.assertIn("runtime-owned receipt provenance", reason)

    def test_symlinked_runtime_roots_protect_physical_transcripts(self):
        for env_name, subtree in (
            ("CODEX_HOME", "sessions/2026/08/13/rollout-runtime-thread.jsonl"),
            ("CLAUDE_CONFIG_DIR", "projects/project/session/subagents/agent-review.jsonl"),
        ):
            with self.subTest(env_name=env_name), tempfile.TemporaryDirectory() as tmp:
                physical = os.path.join(tmp, "physical")
                alias = os.path.join(tmp, "alias")
                os.makedirs(physical)
                os.symlink(physical, alias, target_is_directory=True)
                target = os.path.join(physical, subtree)
                r = invoke_hook(
                    GATE, "Write", {"file_path": target}, env_extra={env_name: alias},
                )
            decision, reason = parse_decision(r.stdout)
            self.assertEqual(decision, "deny")
            self.assertIn("runtime-owned receipt provenance", reason)

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

class TestMultiEdit(unittest.TestCase):
    def test_multiedit_triggers_gate(self):
        """MultiEdit on a protected artifact must deny."""
        with scratch_task_in_real_repo("pr1-multiedit") as task_dir:
            plan = os.path.join(task_dir, "PLAN.md")
            r = invoke_hook(GATE, "MultiEdit", {"file_path": plan})
            decision, reason = parse_decision(r.stdout)
            self.assertEqual(decision, "deny")
            self.assertIn("PLAN.md", reason)

    def test_codex_apply_patch_checks_every_target(self):
        with scratch_task_in_real_repo("pr1-apply-patch") as task_dir:
            plan = os.path.join(task_dir, "PLAN.md")
            patch = (
                "*** Begin Patch\n"
                "*** Update File: README.md\n"
                "@@\n-old\n+new\n"
                f"*** Update File: {plan}\n"
                "@@\n-old\n+new\n"
                "*** End Patch"
            )
            r = invoke_hook(GATE, "apply_patch", {"patch": patch})
            decision, reason = parse_decision(r.stdout)
            self.assertEqual(decision, "deny")
            self.assertIn("PLAN.md", reason)

    def test_codex_apply_patch_crlf_target_is_not_bypassed(self):
        with scratch_task_in_real_repo("pr1-apply-patch-crlf") as task_dir:
            plan = os.path.join(task_dir, "PLAN.md")
            patch = (
                "*** Begin Patch\r\n"
                "*** Update File: README.md\r\n"
                "@@\r\n-old\r\n+new\r\n"
                f"*** Update File: {plan}\r\n"
                "@@\r\n-old\r\n+new\r\n"
                "*** End Patch\r\n"
            )
            r = invoke_hook(GATE, "apply_patch", {"patch": patch})
            decision, reason = parse_decision(r.stdout)
            self.assertEqual(decision, "deny")
            self.assertIn("PLAN.md", reason)


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
            for basename in ("PLAN.md", "RECEIPTS.jsonl"):
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
