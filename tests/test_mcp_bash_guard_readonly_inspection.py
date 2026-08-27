"""mcp_bash_guard: read-only inspection must not be denied, mutation must be.

The guard flattens the whole command to alphanumerics and denies the segment
when any protected artifact or lifecycle symbol name appears in it. That made
ordinary diagnosis impossible: an `echo` banner naming a symbol, or a compound
`for`/`if` command mentioning a gated path, was denied even though neither can
write anything.

These tests pin both directions at once — the false positives stay allowed and
every real mutation stays denied. The guard is the only control preventing a
forged PASS receipt (receipt entries carry no signature), so the negative cases
below are the load-bearing half of this file.
"""
from __future__ import annotations

import os
import unittest

from conftest import (  # type: ignore
    SCRIPTS_DIR,
    invoke_hook,
    parse_decision,
    scratch_task_in_real_repo,
)

GUARD = os.path.join(SCRIPTS_DIR, "mcp_bash_guard.py")

LIB = "plugin/scripts/_lib.py"
SERVER = "plugin/mcp/harness_server.py"


def _run(command):
    return invoke_hook(GUARD, "Bash", {"command": command})


def _decision(command):
    return parse_decision(_run(command).stdout)[0]


class ReadOnlyInspectionAllowedTests(unittest.TestCase):
    """The exact commands denied during the 2026-08-24 receipt diagnosis."""

    def test_echo_banner_naming_lifecycle_symbol(self):
        self.assertIsNone(_decision(
            f'echo "=== write_active_marker call ==="; grep -n '
            f'"write_active_marker" {SERVER}'
        ))

    def test_compound_loop_mentioning_gated_path(self):
        self.assertIsNone(_decision(
            f'for d in a b; do if [ -f "{LIB}" ]; then grep -n '
            f'"TASK_CONTROL_FIELDS" "{LIB}"; fi; done'
        ))

    def test_grep_for_receipt_constant(self):
        self.assertIsNone(_decision(f'grep -n "RECEIPTS_NAME" {LIB}'))

    def test_plain_readers_on_gated_paths(self):
        for command in (
            f"wc -l {LIB} {SERVER}",
            f"ls -la {LIB}",
            f"stat {LIB}",
            f"sed -n '1,20p' {LIB}",
            f"diff {LIB} {SERVER}",
            f'echo "record_subagent_receipt"',
            f"basename {LIB}",
        ):
            with self.subTest(command=command):
                self.assertIsNone(_decision(command), command)


class GitStagingAllowedTests(unittest.TestCase):
    """Staging/committing a lifecycle file is not a mutation of its content."""

    def test_add_and_commit_lifecycle_paths_allowed(self):
        for command in (
            "git add plugin/scripts/background_hook.py",
            f"git add {LIB} {SERVER}",
            "git commit -m 'fix: subagent lifecycle receipts'",
            "git status --porcelain",
            f"git diff --cached {LIB}",
        ):
            with self.subTest(command=command):
                self.assertIsNone(_decision(command), command)

    def test_working_tree_rewriting_git_subcommands_still_denied(self):
        """checkout/restore/rm can overwrite a protected artifact."""
        with scratch_task_in_real_repo("guard-ro-git") as task_dir:
            receipts = os.path.join(task_dir, "RECEIPTS.jsonl")
            for command in (
                f"git checkout HEAD -- {receipts}",
                f"git restore --source=HEAD {receipts}",
                f"git rm -f {receipts}",
            ):
                with self.subTest(command=command):
                    self.assertEqual(_decision(command), "deny", command)


class MutationStillDeniedTests(unittest.TestCase):
    """Regression guard: narrowing false positives must not open a hole."""

    def test_receipt_append_and_overwrite_denied(self):
        with scratch_task_in_real_repo("guard-ro-receipt") as task_dir:
            receipts = os.path.join(task_dir, "RECEIPTS.jsonl")
            for command in (
                f"echo '{{}}' >> {receipts}",
                f"echo x > {receipts}",
                f"tee {receipts}",
                f"cp /tmp/foo {receipts}",
                f"mv /tmp/foo {receipts}",
            ):
                with self.subTest(command=command):
                    self.assertEqual(_decision(command), "deny", command)

    def test_compound_command_does_not_grant_blanket_immunity(self):
        """A `for` wrapper must not launder a redirect inside its body."""
        with scratch_task_in_real_repo("guard-ro-compound") as task_dir:
            receipts = os.path.join(task_dir, "RECEIPTS.jsonl")
            self.assertEqual(
                _decision(f"for f in a b; do echo x >> {receipts}; done"),
                "deny",
            )

    def test_task_control_and_plan_mutation_denied(self):
        with scratch_task_in_real_repo("guard-ro-control") as task_dir:
            control = os.path.join(task_dir, "TASK.json")
            plan = os.path.join(task_dir, "PLAN.md")
            for command in (
                f"echo x > {control}",
                f"sed -i 's/a/b/' {plan}",
                f"truncate -s0 {control}",
            ):
                with self.subTest(command=command):
                    self.assertEqual(_decision(command), "deny", command)

    def test_python_inline_receipt_write_allowed(self):
        """Inline `python -c` code is not inspected — deliberately.

        Reading program semantics off a command line lost to every new spelling
        and produced fixes worse than the gap. Receipt integrity rests on hook
        ownership of RECEIPTS.jsonl and `task_verify` ordering, not here.
        """
        with scratch_task_in_real_repo("guard-ro-python") as task_dir:
            receipts = os.path.join(task_dir, "RECEIPTS.jsonl")
            self.assertIsNone(
                _decision(f"python3 -c \"open('{receipts}','a').write('x')\"")
            )

    def test_control_surface_mutation_still_denied(self):
        for command in (
            f"sed -i 's/a/b/' {LIB}",
            f"echo x >> {LIB}",
        ):
            with self.subTest(command=command):
                self.assertEqual(_decision(command), "deny", command)


if __name__ == "__main__":
    unittest.main()
