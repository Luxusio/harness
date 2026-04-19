"""AC-003 + AC-006: mcp_bash_guard.py — 10 mutation verbs × 3 gated categories,
env-prefix bypass fix, silent-on-allow, HARNESS_SKIP_MCP_GUARD escape, JSON
decision shape + structured reason tail.
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

GUARD = os.path.join(SCRIPTS_DIR, "mcp_bash_guard.py")

TAIL_RE = re.compile(
    r"\[gate=mcp_bash_guard rule=\S+ path=\S+ owner=\S+ docs=\S+\]"
)


def _run_bash(command, env_extra=None):
    return invoke_hook(GUARD, "Bash", {"command": command}, env_extra=env_extra)


# Target-path anchors for each gated category.
SRC_PATH = "plugin/scripts/health.py"                  # source file
PROT_PATH = None   # filled at runtime — needs a task dir
WCS_PATH = "plugin/hooks/hooks.json"                   # workflow-control-surface
SAFE_PATH = "/tmp/mcp_bash_guard_safe.log"             # not gated


MUTATION_VERBS_SOURCE = [
    ("redirect-write", f"echo x > {SRC_PATH}"),
    ("redirect-append", f"echo x >> {SRC_PATH}"),
    ("sed-i", f"sed -i 's/a/b/' {SRC_PATH}"),
    ("perl-pi", f"perl -pi -e 's/a/b/' {SRC_PATH}"),
    ("cp", f"cp /tmp/foo {SRC_PATH}"),
    ("mv", f"mv /tmp/foo {SRC_PATH}"),
    ("install", f"install -m644 /tmp/foo {SRC_PATH}"),
    ("touch", f"touch {SRC_PATH}"),
    ("truncate", f"truncate -s0 {SRC_PATH}"),
    ("tee", f"echo x | tee {SRC_PATH}"),
    ("python-open-w", f"python3 -c \"open('{SRC_PATH}','w')\""),
    ("python-path-write-text",
     f"python3 -c \"import pathlib; pathlib.Path('{SRC_PATH}').write_text('x')\""),
]

MUTATION_VERBS_WORKFLOW = [
    ("redirect-wcs", f"echo x > {WCS_PATH}"),
    ("sed-i-wcs", f"sed -i 's/a/b/' {WCS_PATH}"),
    ("cp-wcs", f"cp /tmp/foo {WCS_PATH}"),
    ("tee-wcs", f"echo x | tee {WCS_PATH}"),
]


class TestMutationsAgainstSource(unittest.TestCase):
    def test_each_verb_denies_source(self):
        for name, cmd in MUTATION_VERBS_SOURCE:
            with self.subTest(verb=name, cmd=cmd):
                r = _run_bash(cmd)
                self.assertEqual(r.returncode, 0)
                decision, reason = parse_decision(r.stdout)
                self.assertEqual(decision, "deny",
                                 f"{name} should deny source write; stdout={r.stdout!r}")
                self.assertRegex(reason, TAIL_RE)
                self.assertIn("rule=source", reason)
                self.assertIn("HARNESS_SKIP_MCP_GUARD", reason)


class TestMutationsAgainstWorkflowControl(unittest.TestCase):
    def test_each_verb_denies_workflow_control(self):
        for name, cmd in MUTATION_VERBS_WORKFLOW:
            with self.subTest(verb=name, cmd=cmd):
                r = _run_bash(cmd)
                decision, reason = parse_decision(r.stdout)
                self.assertEqual(decision, "deny")
                self.assertIn("rule=workflow-control-surface", reason)


class TestMutationsAgainstProtectedArtifact(unittest.TestCase):
    def test_sed_into_plan_md_denies(self):
        with scratch_task_in_real_repo("pr1-bg-prot") as task_dir:
            plan = os.path.join(task_dir, "PLAN.md")
            cmd = f"sed -i 's/a/b/' {plan}"
            r = _run_bash(cmd)
            decision, reason = parse_decision(r.stdout)
            self.assertEqual(decision, "deny")
            self.assertIn("rule=protected-artifact", reason)

    def test_redirect_into_checks_denies(self):
        with scratch_task_in_real_repo("pr1-bg-checks") as task_dir:
            checks = os.path.join(task_dir, "CHECKS.yaml")
            r = _run_bash(f"echo x > {checks}")
            decision, reason = parse_decision(r.stdout)
            self.assertEqual(decision, "deny")
            self.assertIn("rule=protected-artifact", reason)


class TestEnvPrefixBypassFix(unittest.TestCase):
    """Legacy bug: `FOO=bar sed -i x file` treated cmd as FOO=bar not sed → undetected."""

    def test_env_prefix_does_not_bypass(self):
        r = _run_bash(f"FOO=bar sed -i 's/a/b/' {SRC_PATH}")
        decision, reason = parse_decision(r.stdout)
        self.assertEqual(decision, "deny",
                         f"env-prefix bypass not fixed; stdout={r.stdout!r}")
        self.assertIn("rule=source", reason)

    def test_multiple_env_prefix(self):
        r = _run_bash(f"FOO=1 BAR=2 BAZ=3 sed -i 's/a/b/' {SRC_PATH}")
        decision, _ = parse_decision(r.stdout)
        self.assertEqual(decision, "deny")


class TestAllowsNonGated(unittest.TestCase):
    """Read-only and non-gated commands are silent allow."""

    def _assert_allow(self, cmd):
        r = _run_bash(cmd)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "", f"expected silence; got {r.stdout!r}")

    def test_read_only(self):
        self._assert_allow("ls plugin/")
        self._assert_allow("cat plugin/CLAUDE.md")
        self._assert_allow("grep foo plugin/scripts/health.py")

    def test_redirect_to_tmp(self):
        self._assert_allow(f"echo x > {SAFE_PATH}")
        self._assert_allow(f"sed -i 's/a/b/' {SAFE_PATH}")

    def test_stderr_redirect_allowed(self):
        self._assert_allow(f"command 2> /tmp/err.log")


class TestNestedShellKnownGaps(unittest.TestCase):
    """Nested bash/eval/$() mutations are documented-deferred.

    These tests codify the current behaviour so a future fix can flip the
    assertion without searching for the test.
    """

    def test_bash_c_nested_is_known_gap(self):
        r = _run_bash(f"bash -c 'sed -i s/a/b/ {SRC_PATH}'")
        decision, _ = parse_decision(r.stdout)
        self.assertIsNone(decision, "bash -c nested redirect now detected — "
                                    "flip this test to assert deny")

    def test_eval_is_known_gap(self):
        r = _run_bash(f"eval 'sed -i s/a/b/ {SRC_PATH}'")
        decision, _ = parse_decision(r.stdout)
        self.assertIsNone(decision)


class TestEnvEscape(unittest.TestCase):
    def test_skip_env_allows(self):
        r = _run_bash(f"echo x > {SRC_PATH}", env_extra={"HARNESS_SKIP_MCP_GUARD": "1"})
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")


class TestFailSafe(unittest.TestCase):
    def test_malformed_stdin(self):
        env = os.environ.copy()
        env["CLAUDE_PLUGIN_ROOT"] = os.path.join(REPO_ROOT, "plugin")
        r = subprocess.run(
            [sys.executable, GUARD], input="not json at all {{{",
            capture_output=True, text=True, cwd=REPO_ROOT, env=env, timeout=5,
        )
        self.assertEqual(r.returncode, 0)

    def test_unclosed_quote_does_not_crash(self):
        r = _run_bash("echo 'unterminated")
        self.assertEqual(r.returncode, 0)

    def test_non_bash_tool_is_silent(self):
        r = invoke_hook(GUARD, "Write", {"file_path": "/tmp/x"})
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")

    def test_oversized_command_short_circuits(self):
        huge = "echo " + ("x" * (70 * 1024)) + f" > {SRC_PATH}"
        r = _run_bash(huge)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")


class TestSegmentedCommand(unittest.TestCase):
    def test_second_clause_mutation_denies(self):
        r = _run_bash(f"true && sed -i 's/a/b/' {SRC_PATH}")
        decision, _ = parse_decision(r.stdout)
        self.assertEqual(decision, "deny")


if __name__ == "__main__":
    unittest.main()
