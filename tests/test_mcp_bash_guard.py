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
import tempfile
import unittest
from pathlib import Path

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


def _run_codex_shell(command, env_extra=None):
    return invoke_hook(GUARD, "shell", {"cmd": command}, env_extra=env_extra)


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
    # No `python -c` rows: inline code is no longer inspected. See
    # doc/common/REQ__process__bash-guard-script-execution.md.
]

MUTATION_VERBS_WORKFLOW = [
    ("redirect-wcs", f"echo x > {WCS_PATH}"),
    ("sed-i-wcs", f"sed -i 's/a/b/' {WCS_PATH}"),
    ("cp-wcs", f"cp /tmp/foo {WCS_PATH}"),
    ("tee-wcs", f"echo x | tee {WCS_PATH}"),
]


class TestMutationsAgainstSource(unittest.TestCase):
    def test_codex_shell_alias_denies_source(self):
        r = _run_codex_shell(f"touch {SRC_PATH}")
        decision, reason = parse_decision(r.stdout)
        self.assertEqual(decision, "deny")
        self.assertIn("rule=source", reason)

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
                self.assertIn("$harness:run", reason)


class TestMutationsAgainstWorkflowControl(unittest.TestCase):
    def test_each_verb_denies_workflow_control(self):
        for name, cmd in MUTATION_VERBS_WORKFLOW:
            with self.subTest(verb=name, cmd=cmd):
                r = _run_bash(cmd)
                decision, reason = parse_decision(r.stdout)
                self.assertEqual(decision, "deny")
                self.assertIn("rule=workflow-control-surface", reason)


class TestMutationsAgainstProtectedArtifact(unittest.TestCase):
    def test_claude_subagent_transcript_mutation_is_denied(self):
        transcript = os.path.expanduser(
            "~/.claude/projects/project/session/subagents/agent-review-code.jsonl"
        )
        for command in (
            f"echo forged >> {transcript}",
            f"tee -a {transcript}",
        ):
            with self.subTest(command=command):
                r = _run_bash(command)
                decision, reason = parse_decision(r.stdout)
                self.assertEqual(decision, "deny")
                self.assertIn("rule=protected-artifact", reason)

    def test_codex_rollout_mutation_is_denied(self):
        rollout = os.path.expanduser(
            "~/.codex/sessions/2026/08/13/rollout-runtime-thread.jsonl"
        )
        for command in (
            f"echo forged >> {rollout}",
            f"truncate -s 0 {rollout}",
        ):
            with self.subTest(command=command):
                r = _run_bash(command)
                decision, reason = parse_decision(r.stdout)
                self.assertEqual(decision, "deny")
                self.assertIn("rule=protected-artifact", reason)

    def test_sed_into_plan_md_denies(self):
        with scratch_task_in_real_repo("pr1-bg-prot") as task_dir:
            plan = os.path.join(task_dir, "PLAN.md")
            cmd = f"sed -i 's/a/b/' {plan}"
            r = _run_bash(cmd)
            decision, reason = parse_decision(r.stdout)
            self.assertEqual(decision, "deny")
            self.assertIn("rule=protected-artifact", reason)

    def test_alternate_redirect_operators_deny(self):
        """Every redirect spelling routes its target, not just the enumerated ones.

        `punctuation_chars=True` emits the operator as one token, and
        `_INLINE_REDIRECT_RE` then captured the operator's own trailing
        punctuation as the path (`>|` matched with group(2) == "|"), so the real
        target — the next token — was never inspected. `echo x >| PLAN.md`
        truncated a protected artifact through the gate. `>>|` leaked the same
        way and was missed by the first fix, which enumerated spellings.
        """
        with scratch_task_in_real_repo("pr1-altredir") as task_dir:
            receipts = os.path.join(task_dir, "RECEIPTS.jsonl")
            for op in (">|", "&>", "&>>", ">&", ">>|", "&>|", "2>", "1>>"):
                with self.subTest(op=op):
                    decision, reason = parse_decision(
                        _run_bash(f"echo x {op} {receipts}").stdout
                    )
                    self.assertEqual(decision, "deny")
                    self.assertIn("rule=protected-artifact", reason)

    def test_fd_duplication_and_devnull_still_allow(self):
        """The shape rule must not turn ordinary redirects into denies."""
        for command in (
            "echo hello 2>&1",
            "pytest -q 2>&1 | tail -5",
            "echo hi > /tmp/out.txt",
            "echo hi &> /dev/null",
            "echo hi >| /tmp/out.txt",
        ):
            with self.subTest(command=command):
                decision, _ = parse_decision(_run_bash(command).stdout)
                self.assertIsNone(decision)

    def test_copy_into_task_directory_denies(self):
        """A directory destination hides the effective target.

        `cp forged doc/harness/tasks/T/` writes `<dir>/<source basename>`, a path
        that never appears as a token. Classifying only the last operand let a
        one-call receipt forgery through with the guard silent.
        """
        with scratch_task_in_real_repo("pr1-dirdest") as task_dir:
            with tempfile.TemporaryDirectory() as tmp:
                for artifact in ("RECEIPTS.jsonl", "PLAN.md", "TASK.json"):
                    source = Path(tmp) / artifact
                    source.write_text("forged\n", encoding="utf-8")
                    for verb in ("cp", "mv", "install", "rsync"):
                        with self.subTest(verb=verb, artifact=artifact):
                            decision, reason = parse_decision(
                                _run_bash(f"{verb} {source} {task_dir}/").stdout
                            )
                            self.assertEqual(decision, "deny")
                            self.assertIn("rule=protected-artifact", reason)

    def test_newline_does_not_launder_a_mutator(self):
        """A newline must split segments.

        `shlex(whitespace_split=True)` consumes newlines, so the "\\n" entry in
        the boundary set never matched and a multi-line command collapsed into
        one segment. Dispatch is on the first command word, so a leading
        `echo start` walked `cp`, `tee`, `sed -i` or `dd` onto a protected
        artifact with the guard silent.
        """
        with scratch_task_in_real_repo("pr1-newline") as task_dir:
            receipts = os.path.join(task_dir, "RECEIPTS.jsonl")
            for second in (
                f"tee {receipts}",
                f"sed -i s/a/b/ {receipts}",
                f"truncate -s0 {receipts}",
                f"echo forged > {receipts}",
            ):
                with self.subTest(second=second):
                    decision, reason = parse_decision(
                        _run_bash(f"echo start\n{second}").stdout
                    )
                    self.assertEqual(decision, "deny")
                    self.assertIn("rule=protected-artifact", reason)

    def test_subshell_does_not_launder_a_verb(self):
        """`(` is a boundary, not a command word.

        `punctuation_chars=True` emits it as its own token, so the segment's
        command word became `"("`, dispatch fell through, and `( cp … )` wrote a
        protected artifact with the guard silent.
        """
        with scratch_task_in_real_repo("pr1-subshell") as task_dir:
            receipts = os.path.join(task_dir, "RECEIPTS.jsonl")
            for command in (
                f"( cp /tmp/f {receipts} )",
                f"(cp /tmp/f {receipts})",
                f"true && ( sed -i s/a/b/ {receipts} )",
                f"( ( tee {receipts} ) )",
            ):
                with self.subTest(command=command):
                    decision, reason = parse_decision(_run_bash(command).stdout)
                    self.assertEqual(decision, "deny")
                    self.assertIn("rule=protected-artifact", reason)

    def test_long_and_split_in_place_spellings_deny(self):
        """`--in-place`, separated `perl -i -pe`, and `sort`/`diff -o` all write."""
        with scratch_task_in_real_repo("pr1-inplace") as task_dir:
            receipts = os.path.join(task_dir, "RECEIPTS.jsonl")
            for command in (
                f"sed --in-place 's/a/b/' {receipts}",
                f"sed --in-place=.bak 's/a/b/' {receipts}",
                f"perl -i -pe 's/a/b/' {receipts}",
                f"perl -i.bak -pe 's/a/b/' {receipts}",
                f"sort -o {receipts} /tmp/f",
                f"diff -o {receipts} /tmp/a /tmp/b",
            ):
                with self.subTest(command=command):
                    decision, reason = parse_decision(_run_bash(command).stdout)
                    self.assertEqual(decision, "deny")
                    self.assertIn("rule=protected-artifact", reason)

    def test_command_carrying_wrappers_deny(self):
        """A one-word wrapper must not hide the verb behind it."""
        with scratch_task_in_real_repo("pr1-wrappers") as task_dir:
            receipts = os.path.join(task_dir, "RECEIPTS.jsonl")
            for command in (
                f"sudo cp /tmp/f {receipts}",
                f"doas cp /tmp/f {receipts}",
                f"timeout 5 cp /tmp/f {receipts}",
                f"stdbuf -o0 cp /tmp/f {receipts}",
                f"setsid cp /tmp/f {receipts}",
                f"echo a | xargs -I{{}} cp /tmp/f {receipts}",
            ):
                with self.subTest(command=command):
                    decision, reason = parse_decision(_run_bash(command).stdout)
                    self.assertEqual(decision, "deny")
                    self.assertIn("rule=protected-artifact", reason)

    def test_option_taking_prefix_wrappers_deny(self):
        """`nice -n5` / `exec -a foo` must not become the command word.

        COMMAND_PREFIX_WORDS advances only while the token *is* a prefix word,
        so an option value stopped the scan and dispatch saw `-n5`/`foo`.
        """
        with scratch_task_in_real_repo("pr1-niceexec") as task_dir:
            receipts = os.path.join(task_dir, "RECEIPTS.jsonl")
            for command in (
                f"nice -n5 cp /tmp/f {receipts}",
                f"nice -n 5 tee -a {receipts}",
                f"nice -n5 sed -i s/a/b/ {receipts}",
                f"exec -a foo cp /tmp/f {receipts}",
                f"nohup nice -n5 tee -a {receipts}",
            ):
                with self.subTest(command=command):
                    decision, reason = parse_decision(_run_bash(command).stdout)
                    self.assertEqual(decision, "deny")
                    self.assertIn("rule=protected-artifact", reason)

    def test_nested_shell_spellings_deny(self):
        """`-c` is rarely written alone.

        Matching a standalone `-c` token and taking the next one missed `-lc`
        clusters, `-c --`, and every shell other than bash/sh.
        """
        with scratch_task_in_real_repo("pr1-nestedshell") as task_dir:
            receipts = os.path.join(task_dir, "RECEIPTS.jsonl")
            for command in (
                f'bash -lc "cp /tmp/f {receipts}"',
                f'sh -ec "cp /tmp/f {receipts}"',
                f'bash -xc "cp /tmp/f {receipts}"',
                f'bash -c -- "cp /tmp/f {receipts}"',
                f'dash -c "cp /tmp/f {receipts}"',
                f'/bin/bash -lc "cp /tmp/f {receipts}"',
            ):
                with self.subTest(command=command):
                    decision, reason = parse_decision(_run_bash(command).stdout)
                    self.assertEqual(decision, "deny")
                    self.assertIn("rule=protected-artifact", reason)

    def test_extra_boundary_tokens_split(self):
        """`|&` and `;;` are boundaries.

        Missing them laundered a following verb, and worse, moved
        `_last_non_option` off the destination of a preceding one — a trailing
        `|& cat` turned a denied `cp` into an allow.
        """
        with scratch_task_in_real_repo("pr1-boundary") as task_dir:
            receipts = os.path.join(task_dir, "RECEIPTS.jsonl")
            for command in (
                f"grep x f |& cp /tmp/f {receipts}",
                f"cp /tmp/f {receipts} |& cat",
                f"case a in a) cp /tmp/f {receipts};; esac",
            ):
                with self.subTest(command=command):
                    decision, reason = parse_decision(_run_bash(command).stdout)
                    self.assertEqual(decision, "deny")
                    self.assertIn("rule=protected-artifact", reason)

    def test_escaped_quote_does_not_swallow_the_next_line(self):
        """`echo it\\'s` must not leave the quote tracker inside a quote."""
        with scratch_task_in_real_repo("pr1-escquote") as task_dir:
            receipts = os.path.join(task_dir, "RECEIPTS.jsonl")
            for first in ("echo it\\'s fine", 'echo it\\"s fine'):
                with self.subTest(first=first):
                    decision, reason = parse_decision(
                        _run_bash(f"{first}\ncp /tmp/f {receipts}").stdout
                    )
                    self.assertEqual(decision, "deny")
                    self.assertIn("rule=protected-artifact", reason)

    def test_multi_operand_verbs_classify_every_operand(self):
        """`sed -i`, `perl -i`, `touch`, `truncate` rewrite all their operands."""
        with scratch_task_in_real_repo("pr1-multiop") as task_dir:
            receipts = os.path.join(task_dir, "RECEIPTS.jsonl")
            for command in (
                f"sed -i s/a/b/ {receipts} /tmp/pad",
                f"perl -i -pe s/a/b/ {receipts} /tmp/pad",
                f"touch {receipts} /tmp/pad",
                f"truncate -s0 {receipts} /tmp/pad",
                f"coproc cp /tmp/f {receipts}",
            ):
                with self.subTest(command=command):
                    decision, reason = parse_decision(_run_bash(command).stdout)
                    self.assertEqual(decision, "deny")
                    self.assertIn("rule=protected-artifact", reason)

    def test_trailing_redirect_does_not_move_the_destination(self):
        """Redirect operands are not the verb's operands.

        They stayed in the token list, so `_last_non_option` picked the redirect
        target: `cp SRC <receipt> 2>/dev/null` allowed while the same command
        without the redirect denies. `2>/dev/null` is the natural spelling for a
        caller who wants no noise.
        """
        with scratch_task_in_real_repo("pr1-redirop") as task_dir:
            receipts = os.path.join(task_dir, "RECEIPTS.jsonl")
            for command in (
                f"cp /tmp/f {receipts} 2>/dev/null",
                f"cp /tmp/f {receipts} >/dev/null 2>&1",
                f"install -m 644 /tmp/f {receipts} 2>/dev/null",
                f"rsync /tmp/f {receipts} 2>/dev/null",
                f"cp /tmp/f {receipts} < /dev/null",
                f"cp /tmp/RECEIPTS.jsonl {task_dir}/ 2>/dev/null",
            ):
                with self.subTest(command=command):
                    decision, reason = parse_decision(_run_bash(command).stdout)
                    self.assertEqual(decision, "deny")
                    self.assertIn("rule=protected-artifact", reason)

    def test_quoted_operator_literal_is_not_a_redirect(self):
        """`shlex(posix=True)` discards quoting, so `'<'` looked like an operator.

        Stripping it consumed the *next* token — the real target — so
        `sed -i s/a/b/ '<' <receipt>` allowed while the write still landed
        (GNU sed continues past the unreadable operand).
        """
        with scratch_task_in_real_repo("pr1-quoteop") as task_dir:
            receipts = os.path.join(task_dir, "RECEIPTS.jsonl")
            for command in (
                f"sed -i s/a/b/ '<' {receipts}",
                f"touch '<' {receipts}",
                f"truncate -s0 '<' {receipts}",
                f"tee '<' {receipts}",
                f"rm '<' {receipts}",
                f"perl -pi -e s/a/b/ '<' {receipts}",
                f"sed -i s/a/b/ '<<' {receipts}",
            ):
                with self.subTest(command=command):
                    decision, reason = parse_decision(_run_bash(command).stdout)
                    self.assertEqual(decision, "deny")
                    self.assertIn("rule=protected-artifact", reason)

    def test_backslash_escaped_fake_operator_denies(self):
        r"""The all-False fallback was itself the bypass.

        Non-posix shlex emits a lone `\` as its own token, so `sed -i s/a/b/ \<
        F` made the two lexers disagree on count; the fallback then treated `<`
        as a real operator and ate the following token — the target.
        """
        with scratch_task_in_real_repo("pr1-bslash") as task_dir:
            receipts = os.path.join(task_dir, "RECEIPTS.jsonl")
            for command in (
                f"sed -i s/a/b/ \\< {receipts}",
                f"touch \\< {receipts}",
                f"tee \\< {receipts}",
                f"rm \\< {receipts}",
            ):
                with self.subTest(command=command):
                    decision, reason = parse_decision(_run_bash(command).stdout)
                    self.assertEqual(decision, "deny")
                    self.assertIn("rule=protected-artifact", reason)

    def test_lexer_desync_generators_do_not_disable_quote_awareness(self):
        r"""Quote-awareness must not have an attacker-supplied off switch.

        `_tokenize` drops falsy tokens, so a posix lex discards `''` while the
        non-posix lex keeps it. One empty quoted word desynchronised the two by
        one, the old all-False fallback turned quote-awareness off for the whole
        line, and `sed -i s/a/b/ '' '<' <receipt>` walked through — the bypass
        precondition was two characters inside the string being inspected.
        """
        with scratch_task_in_real_repo("pr1-desync") as task_dir:
            receipts = os.path.join(task_dir, "RECEIPTS.jsonl")
            for command in (
                f"sed -i s/a/b/ '' '<' {receipts}",
                f'sed -i s/a/b/ "" "<" {receipts}',
                f"tee '' '<' {receipts}",
                f"perl -i -pe 1 '' '<' {receipts}",
                f"truncate -s0 '' '<' {receipts}",
                f"touch '' '<' {receipts}",
                f"cp /tmp/f '' '<' {receipts}",
                f"touch '' \\< {receipts}",
            ):
                with self.subTest(command=command):
                    decision, reason = parse_decision(_run_bash(command).stdout)
                    self.assertEqual(decision, "deny")
                    self.assertIn("rule=protected-artifact", reason)

    def test_empty_quoted_word_does_not_over_block(self):
        """An empty quoted word is ordinary; it must not deny by itself."""
        for command in ("echo ''", "echo '' > /tmp/out", "grep -n '' /tmp/a"):
            with self.subTest(command=command):
                decision, _ = parse_decision(_run_bash(command).stdout)
                self.assertIsNone(decision)

    def test_quoted_control_operator_does_not_split(self):
        """A quoted `|` is an argument, not a boundary.

        Splitting there left the artifact as the next segment's command word,
        where no verb branch matches, so the write was never classified.
        """
        with scratch_task_in_real_repo("pr1-quotedsep") as task_dir:
            receipts = os.path.join(task_dir, "RECEIPTS.jsonl")
            for command in (
                f"sed -i s/a/b/ '|' {receipts}",
                f"sed -i s/a/b/ ';' {receipts}",
                f"tee '|' {receipts}",
                f"rm ';' {receipts}",
            ):
                with self.subTest(command=command):
                    decision, reason = parse_decision(_run_bash(command).stdout)
                    self.assertEqual(decision, "deny")
                    self.assertIn("rule=protected-artifact", reason)

    def test_real_boundaries_still_split(self):
        """Consulting the quote flags must not suppress a true split."""
        for command in ("true && pytest -q", "echo a | grep b", "true; ls"):
            with self.subTest(command=command):
                decision, _ = parse_decision(_run_bash(command).stdout)
                self.assertIsNone(decision)

    def test_redirect_shaped_search_pattern_allows(self):
        """The same defect in the other direction: `grep -n '2>'` is a read.

        This is the first command a contributor working on the guard runs, and
        it was denied with a false `rule=source` mutation claim.
        """
        for command in (
            "grep -n '2>' plugin/scripts/mcp_bash_guard.py",
            "grep -n '>>' plugin/scripts/health.py",
            "rg '>' plugin/scripts/health.py",
        ):
            with self.subTest(command=command):
                decision, _ = parse_decision(_run_bash(command).stdout)
                self.assertIsNone(decision)

    def test_attached_sed_script_option_denies(self):
        """`-es/a/b/` and `-f/tmp/s` supply the script without a separate token."""
        with scratch_task_in_real_repo("pr1-attachedsed") as task_dir:
            receipts = os.path.join(task_dir, "RECEIPTS.jsonl")
            for command in (
                f"sed -i -es/a/b/ {receipts}",
                f"sed -i -f/tmp/s.sed {receipts}",
            ):
                with self.subTest(command=command):
                    decision, reason = parse_decision(_run_bash(command).stdout)
                    self.assertEqual(decision, "deny")
                    self.assertIn("rule=protected-artifact", reason)

    def test_trailing_value_options_do_not_move_the_destination(self):
        """GNU getopt permutes, so value options can trail the operands."""
        with scratch_task_in_real_repo("pr1-trailopt") as task_dir:
            receipts = os.path.join(task_dir, "RECEIPTS.jsonl")
            for command in (
                f"cp /tmp/f {receipts} -S bak",
                f"cp /tmp/f {receipts} --suffix bak",
                f"install /tmp/f {receipts} -m 644",
                f"rsync /tmp/f {receipts} --log-file /tmp/l",
                f"cp -t{task_dir} /tmp/RECEIPTS.jsonl",
            ):
                with self.subTest(command=command):
                    decision, reason = parse_decision(_run_bash(command).stdout)
                    self.assertEqual(decision, "deny")
                    self.assertIn("rule=protected-artifact", reason)

    def test_bash_cluster_with_c_not_last_denies(self):
        """`bash -cl '<cmd>'` runs the script; requiring `c` last was wrong."""
        with scratch_task_in_real_repo("pr1-cluster") as task_dir:
            receipts = os.path.join(task_dir, "RECEIPTS.jsonl")
            for command in (
                f"bash -cx 'cp /tmp/f {receipts}'",
                f"bash -cl 'cp /tmp/f {receipts}'",
                f"bash -cvx 'cp /tmp/f {receipts}'",
            ):
                with self.subTest(command=command):
                    decision, reason = parse_decision(_run_bash(command).stdout)
                    self.assertEqual(decision, "deny")
                    self.assertIn("rule=protected-artifact", reason)

    def test_shell_value_options_do_not_hide_the_script(self):
        """An option's value was mistaken for the `-c` script."""
        with scratch_task_in_real_repo("pr1-shellopt") as task_dir:
            receipts = os.path.join(task_dir, "RECEIPTS.jsonl")
            for command in (
                f"bash -o errexit -c 'cp /tmp/f {receipts}'",
                f"bash -O extglob -c 'cp /tmp/f {receipts}'",
                f"bash --rcfile /dev/null -c 'cp /tmp/f {receipts}'",
                f"busybox sh -c 'cp /tmp/f {receipts}'",
            ):
                with self.subTest(command=command):
                    decision, reason = parse_decision(_run_bash(command).stdout)
                    self.assertEqual(decision, "deny")
                    self.assertIn("rule=protected-artifact", reason)

    def test_sed_script_from_option_denies(self):
        """With `--expression=`/`--file=` the first operand is already the file."""
        with scratch_task_in_real_repo("pr1-sedopt") as task_dir:
            plan = os.path.join(task_dir, "PLAN.md")
            for command in (
                f"sed -i --expression=s/a/b/ {plan}",
                f"sed -i --file=/tmp/s.sed {plan}",
                f"sed --in-place --file=/tmp/x {plan}",
                f"sed -i -e s/a/b/ {plan}",
            ):
                with self.subTest(command=command):
                    decision, reason = parse_decision(_run_bash(command).stdout)
                    self.assertEqual(decision, "deny")
                    self.assertIn("rule=protected-artifact", reason)

    def test_redirect_target_is_classified_after_expansion(self):
        """`V=<plan>; echo hi > $V` writes the artifact."""
        with scratch_task_in_real_repo("pr1-redirvar") as task_dir:
            plan = os.path.join(task_dir, "PLAN.md")
            decision, reason = parse_decision(
                _run_bash(f"V={plan}; echo hi > $V").stdout
            )
            self.assertEqual(decision, "deny")
            self.assertIn("rule=protected-artifact", reason)

    def test_perl_value_taking_switches_are_not_in_place(self):
        """`-Iinc` contains an `i` but is a library path, not in-place."""
        for command in (
            "perl -Iinc -pe print plugin/scripts/_lib.py",
            "perl -MList::Util -pe print plugin/scripts/_lib.py",
            "perl -ne print plugin/scripts/_lib.py",
        ):
            with self.subTest(command=command):
                decision, _ = parse_decision(_run_bash(command).stdout)
                self.assertIsNone(decision)

    def test_multi_operand_verbs_do_not_over_block(self):
        for command in ("touch /tmp/ok", "truncate -s0 /tmp/ok",
                        "sed -i s/a/b/ /tmp/ok", "git restore --staged plugin/scripts/_lib.py",
                        "echo it\\'s fine"):
            with self.subTest(command=command):
                decision, _ = parse_decision(_run_bash(command).stdout)
                self.assertIsNone(decision)

    def test_read_only_commands_naming_an_artifact_allow(self):
        """Two execution heuristics denied reads and named the wrong file.

        A nested-runtime keyword match and a command-substitution + `-o` match
        both emitted a synthetic `goals/current.json` target for commands that
        write nothing.
        """
        with scratch_task_in_real_repo("pr1-readonly") as task_dir:
            receipts = os.path.join(task_dir, "RECEIPTS.jsonl")
            for command in (
                f'bash -c "grep -n write {receipts}"',
                f'bash -c "cat {receipts} | grep -c write"',
                f"grep -o x {receipts} ; echo $(git rev-parse --short HEAD)",
            ):
                with self.subTest(command=command):
                    decision, _ = parse_decision(_run_bash(command).stdout)
                    self.assertIsNone(decision)

    def test_wrappers_do_not_over_block(self):
        for command in ("sudo ls /tmp", "timeout 5 pytest -q", "( echo hi ) > /tmp/o"):
            with self.subTest(command=command):
                decision, _ = parse_decision(_run_bash(command).stdout)
                self.assertIsNone(decision)

    def test_line_continuation_keeps_verb_and_target_together(self):
        """`\\` + newline is a join, not a split."""
        with scratch_task_in_real_repo("pr1-continuation") as task_dir:
            receipts = os.path.join(task_dir, "RECEIPTS.jsonl")
            decision, reason = parse_decision(
                _run_bash(f"cp /tmp/f \\\n  {receipts}").stdout
            )
            self.assertEqual(decision, "deny")
            self.assertIn("rule=protected-artifact", reason)

    def test_nested_deny_names_the_real_path(self):
        """A deny reason must name the file the command actually touches."""
        with scratch_task_in_real_repo("pr1-nestedpath") as task_dir:
            receipts = os.path.join(task_dir, "RECEIPTS.jsonl")
            decision, reason = parse_decision(
                _run_bash(f'bash -c "echo x > {receipts}"').stdout
            )
            self.assertEqual(decision, "deny")
            self.assertIn("RECEIPTS.jsonl", reason)
            self.assertNotIn("goals/current.json", reason)

    def test_glob_named_decoy_does_not_fail_the_guard_open(self):
        """A self-matching glob name must not crash the guard into an allow.

        `glob("/tmp/x*y")` returns `["/tmp/x*y"]` when a file is literally named
        that, so expanding recursively re-entered with an identical token until
        RecursionError — which main()'s catch-all swallowed into sys.exit(0),
        allowing the *entire* command. One `touch` disabled every deny.
        """
        with scratch_task_in_real_repo("pr1-globhaz") as task_dir:
            receipts = os.path.join(task_dir, "RECEIPTS.jsonl")
            with tempfile.TemporaryDirectory() as tmp:
                decoy = os.path.join(tmp, "x*y")
                Path(decoy).write_text("", encoding="utf-8")
                for command in (
                    f"cat /tmp/f | tee {decoy} {receipts}",
                    f"echo x > {receipts}; echo y > {decoy}",
                ):
                    with self.subTest(command=command):
                        decision, reason = parse_decision(_run_bash(command).stdout)
                        self.assertEqual(decision, "deny")
                        self.assertIn("rule=protected-artifact", reason)

    def test_leading_shell_control_word_does_not_launder(self):
        """`time cp …` and `{ cp …; }` dispatched on the prefix, not the verb."""
        with scratch_task_in_real_repo("pr1-prefix") as task_dir:
            receipts = os.path.join(task_dir, "RECEIPTS.jsonl")
            for prefix in ("time", "!", "command", "nohup"):
                with self.subTest(prefix=prefix):
                    decision, reason = parse_decision(
                        _run_bash(f"{prefix} cp /tmp/x {receipts}").stdout
                    )
                    self.assertEqual(decision, "deny")
                    self.assertIn("rule=protected-artifact", reason)

    def test_multiline_quoted_argument_is_not_a_command(self):
        """Line splitting must not turn a quoted message body into segments."""
        for command in (
            'git commit -m "line one\nmentions subagent_lifecycle.py"',
            "git commit -m 'first\nrecord_subagent_receipt in the body'",
        ):
            with self.subTest(command=command.splitlines()[0]):
                decision, _ = parse_decision(_run_bash(command).stdout)
                self.assertIsNone(decision)

    def test_glob_in_artifact_basename_denies(self):
        """The shell expands the glob after the gate decided.

        The artifact must exist for the pattern to resolve — with no match the
        shell leaves the pattern literal and creates a file named
        `RECEIPT?.jsonl`, which is not a protected artifact and needs no deny.
        """
        with scratch_task_in_real_repo("pr1-glob") as task_dir:
            Path(task_dir, "RECEIPTS.jsonl").write_text("{}\n", encoding="utf-8")
            for pattern in ("RECEIPT?.jsonl", "RECEIPTS.js*", "RECEIP[T]S.jsonl"):
                with self.subTest(pattern=pattern):
                    decision, reason = parse_decision(
                        _run_bash(f"echo forged >> {task_dir}/{pattern}").stdout
                    )
                    self.assertEqual(decision, "deny")
                    self.assertIn("rule=protected-artifact", reason)

    def test_target_directory_option_denies(self):
        """`-t <dir>` names the destination; last-operand logic saw the source."""
        with scratch_task_in_real_repo("pr1-tflag") as task_dir:
            with tempfile.TemporaryDirectory() as tmp:
                source = Path(tmp) / "RECEIPTS.jsonl"
                source.write_text("forged\n", encoding="utf-8")
                for form in (
                    f"cp -t {task_dir} {source}",
                    f"cp --target-directory={task_dir} {source}",
                    f"mv -t {task_dir} {source}",
                    f"install -t {task_dir} {source}",
                ):
                    with self.subTest(form=form):
                        decision, reason = parse_decision(_run_bash(form).stdout)
                        self.assertEqual(decision, "deny")
                        self.assertIn("rule=protected-artifact", reason)

    def test_directory_content_copy_denies(self):
        """`dir/.` and `dir/*` copy contents; the basename is not the filename."""
        with scratch_task_in_real_repo("pr1-contents") as task_dir:
            with tempfile.TemporaryDirectory() as tmp:
                src = Path(tmp) / "srcdir"
                src.mkdir()
                (src / "RECEIPTS.jsonl").write_text("forged\n", encoding="utf-8")
                for form in (
                    f"cp -r {src}/. {task_dir}",
                    f"cp -a {src}/* {task_dir}/",
                ):
                    with self.subTest(form=form):
                        decision, reason = parse_decision(_run_bash(form).stdout)
                        self.assertEqual(decision, "deny")
                        self.assertIn("rule=protected-artifact", reason)

    def test_copy_into_unprotected_directory_allows(self):
        """The reconstruction must not deny ordinary copies."""
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "notes.txt"
            source.write_text("x\n", encoding="utf-8")
            dest = Path(tmp) / "sub"
            dest.mkdir()
            decision, _ = parse_decision(_run_bash(f"cp {source} {dest}/").stdout)
            self.assertIsNone(decision)

    def test_ordinary_script_execution_allows(self):
        """The defect this replaced: a literal path that does not resolve."""
        for command in (
            "python3 scripts/gen.py",
            "python3 manage.py migrate",
            "python3 tools/build_docs.py --check",
            # Documented allow rows with no other coverage: 105fda4 removed both
            # from the deny list, so without these a future tightening could
            # re-deny stdin execution with the suite still green.
            "printf '%s' 'pass' | python3 -",
            "python3 -c \"exec(__import__('base64').b64decode('cGFzcw=='))\"",
        ):
            with self.subTest(command=command):
                decision, _ = parse_decision(_run_bash(command).stdout)
                self.assertIsNone(decision)

    def test_redirect_into_subagent_receipt_denies_with_hook_hint(self):
        with scratch_task_in_real_repo("receipt-prot") as task_dir:
            receipt = os.path.join(task_dir, "RECEIPTS.jsonl")
            r = _run_bash(f"echo x > {receipt}")
            decision, reason = parse_decision(r.stdout)
            self.assertEqual(decision, "deny")
            self.assertIn("rule=protected-artifact", reason)
            self.assertIn("review and QA lifecycle hook", reason)

    def test_redirect_into_active_markers_denies(self):
        for target in (
            "doc/harness/tasks/.active",
            "doc/harness/tasks/.active_sessions/session.json",
        ):
            with self.subTest(target=target):
                r = _run_bash(f"echo x > {target}")
                decision, reason = parse_decision(r.stdout)
                self.assertEqual(decision, "deny")
                self.assertIn("rule=protected-artifact", reason)

    def test_redirect_into_native_goal_control_denies(self):
        for target in (
            "doc/harness/goals/current.json",
            "doc/harness/goals/GOAL__forged.json",
        ):
            with self.subTest(target=target):
                r = _run_bash(f"echo '{{}}' > {target}")
                decision, reason = parse_decision(r.stdout)
                self.assertEqual(decision, "deny")
                self.assertIn("rule=protected-artifact", reason)

    def test_link_export_of_native_goal_control_denies(self):
        for command in (
            "ln doc/harness/goals/current.json /tmp/harness-goal-alias",
            "ln -s doc/harness/goals/current.json /tmp/harness-goal-symlink",
            "link doc/harness/goals/current.json /tmp/harness-goal-link",
            "cp -l doc/harness/goals/current.json /tmp/harness-goal-cp-link",
            "cp --link doc/harness/goals/current.json /tmp/harness-goal-cp-long",
            "bash -c 'ln doc/harness/goals/current.json /tmp/harness-goal-nested'",
            "bash -c 'link doc/harness/goals/current.json /tmp/harness-goal-link-nested'",
            "bash -c 'cp -l doc/harness/goals/current.json /tmp/harness-goal-cp-nested'",
        ):
            with self.subTest(command=command):
                r = _run_bash(command)
                decision, reason = parse_decision(r.stdout)
                self.assertEqual(decision, "deny")
                self.assertIn("rule=protected-artifact", reason)

    def test_existing_external_goal_hardlink_alias_denies(self):
        goals = Path(REPO_ROOT) / "doc/harness/goals"
        goals.mkdir(parents=True, exist_ok=True)
        source = goals / "GOAL__guard-alias-test.json"
        alias = Path(REPO_ROOT) / "doc/harness/checkpoints/.goal-existing-alias"
        # The alias must land on the repo's own device: os.link cannot cross
        # filesystems, so a tmpdir would not exercise the guard at all.
        alias.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("{}\n", encoding="utf-8")
        alias.unlink(missing_ok=True)
        os.link(source, alias)
        try:
            r = _run_bash(f"printf '{{}}' > {alias}")
            decision, reason = parse_decision(r.stdout)
            self.assertEqual(decision, "deny")
            self.assertIn("rule=protected-artifact", reason)
        finally:
            alias.unlink(missing_ok=True)
            source.unlink(missing_ok=True)

    def test_source_mutation_of_native_goal_control_denies(self):
        for command in (
            "mv doc/harness/goals/current.json /tmp/harness-goal-moved",
            "rm doc/harness/goals/current.json",
            "unlink doc/harness/goals/current.json",
            "chmod 600 doc/harness/goals/current.json",
            "chown 0 doc/harness/goals/current.json",
            "bash -c 'mv doc/harness/goals/current.json /tmp/harness-goal-moved-nested'",
            "bash -c 'rm doc/harness/goals/current.json'",
        ):
            with self.subTest(command=command):
                r = _run_bash(command)
                decision, reason = parse_decision(r.stdout)
                self.assertEqual(decision, "deny")
                self.assertIn("rule=protected-artifact", reason)

    def test_python_read_only_goal_inspection_allows(self):
        for command in (
            "python3 -c \"p='first'; p='second'; print(p)\"",
        ):
            with self.subTest(command=command):
                r = _run_bash(command)
                decision, _ = parse_decision(r.stdout)
                self.assertIsNone(decision)

    def test_read_only_goal_commands_allow(self):
        for command in (
            "tail doc/harness/goals/current.json",
            "diff /tmp/a doc/harness/goals/current.json",
            "find doc/harness/goals/current.json -print",
            "git diff -- doc/harness/goals/current.json",
            "git --no-pager diff -- doc/harness/goals/current.json",
            f"git -C {REPO_ROOT} diff -- doc/harness/goals/current.json",
        ):
            with self.subTest(command=command):
                decision, _ = parse_decision(_run_bash(command).stdout)
                self.assertIsNone(decision)

    def test_harmless_inline_runtime_allows(self):
        for command in ('node -e "console.log(process.version)"', 'echo "$(printf x)" -o /tmp/foo'):
            with self.subTest(command=command):
                self.assertIsNone(parse_decision(_run_bash(command).stdout)[0])

    def test_shell_indirect_goal_writes_deny(self):
        for command in (
            "eval 'printf {} > doc/harness/goals/current.json'",
            "printf '{}' | dd of=doc/harness/goals/current.json",
            "bash -c \"eval 'printf {} > doc/harness/goals/current.json'\"",
        ):
            with self.subTest(command=command):
                r = _run_bash(command)
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


class TestNestedShellHandling(unittest.TestCase):

    def test_bash_c_nested_mutation_denies(self):
        r = _run_bash(f"bash -c 'sed -i s/a/b/ {SRC_PATH}'")
        decision, reason = parse_decision(r.stdout)
        self.assertEqual(decision, "deny")
        self.assertIn("rule=source", reason)

    def test_eval_recurses_into_mutation_guard(self):
        r = _run_bash(f"eval 'sed -i s/a/b/ {SRC_PATH}'")
        decision, reason = parse_decision(r.stdout)
        self.assertEqual(decision, "deny")
        self.assertIn("rule=source", reason)


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

    def test_oversized_command_fails_closed(self):
        huge = "echo " + ("x" * (70 * 1024)) + f" > {SRC_PATH}"
        r = _run_bash(huge)
        self.assertEqual(r.returncode, 0)
        decision, reason = parse_decision(r.stdout)
        self.assertEqual(decision, "deny")
        self.assertIn("uninspectable oversized command", reason)


class TestSegmentedCommand(unittest.TestCase):
    def test_second_clause_mutation_denies(self):
        r = _run_bash(f"true && sed -i 's/a/b/' {SRC_PATH}")
        decision, _ = parse_decision(r.stdout)
        self.assertEqual(decision, "deny")


if __name__ == "__main__":
    unittest.main()
