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
    def test_read_only_lifecycle_source_references_are_allowed(self):
        for command in (
            "uv run pytest -q tests/test_subagent_lifecycle.py",
            "git diff -- plugin/scripts/subagent_lifecycle.py",
            "sed -n '1,20p' plugin/scripts/background_hook.py",
        ):
            with self.subTest(command=command):
                r = _run_bash(command)
                decision, _reason = parse_decision(r.stdout)
                self.assertNotEqual(decision, "deny")

    def test_claude_subagent_transcript_mutation_is_denied(self):
        transcript = os.path.expanduser(
            "~/.claude/projects/project/session/subagents/agent-review-code.jsonl"
        )
        for command in (
            f"echo forged >> {transcript}",
            f"python3 -c 'open(\"{transcript}\", \"a\").write(\"forged\")'",
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

    def test_direct_lifecycle_hook_invocation_is_denied(self):
        for command in (
            "python3 plugin/scripts/background_hook.py --event stop",
            "plugin/scripts/background_hook.py --event stop",
            "bash plugin/scripts/background_hook.py --event stop",
            "HARNESS_SKIP_MCP_GUARD=1 python3 plugin/scripts/background_hook.py --event stop",
            "python3 plugin/scripts/subagent_lifecycle.py",
            "python3 -c 'import subagent_lifecycle'",
            "python3 -c 'from subagent_lifecycle import mark_subagent_stop'",
            "python3 -c 'import codex_lifecycle_watcher as watcher'",
            "python3 -c \"__import__('subagent_lifecycle')\"",
            "python3 -m subagent_lifecycle",
            "/usr/bin/env python3 plugin/scripts/subagent_lifecycle.py",
            "uv run python3 plugin/scripts/background_hook.py --event stop",
            "PYTHONPATH=plugin/scripts python3 -c 'import plugin.scripts.subagent_lifecycle as b'",
            "python3 -m plugin.scripts.subagent_lifecycle",
            "/usr/bin/env -u X python3 plugin/scripts/background_hook.py --event stop",
            "uv --directory . run python3 plugin/scripts/background_hook.py --event stop",
            "uv run --directory . python3 plugin/scripts/background_hook.py --event stop",
            "uv run --project . python3 plugin/scripts/background_hook.py --event stop",
            "python3 -X dev plugin/scripts/background_hook.py --event stop",
            "uv run --color auto python3 plugin/scripts/background_hook.py --event stop",
            "uv run --cache-dir /tmp/uv-cache python3 plugin/scripts/background_hook.py --event stop",
            "uv run --extra demo python3 plugin/scripts/background_hook.py --event stop",
            "uv run --group dev python3 plugin/scripts/background_hook.py --event stop",
            "python3 -W -m plugin/scripts/background_hook.py --event stop",
            "python3 -W -c plugin/scripts/background_hook.py --event stop",
            "python3 -X -m plugin/scripts/background_hook.py --event stop",
            "python3 -c \"from importlib import import_module as load; load('subagent_lifecycle').mark_subagent_stop\"",
            "PYTHONPATH=plugin/scripts python3 -c \"m=__import__('plugin.scripts',fromlist=['subagent_lifecycle']).subagent_lifecycle;getattr(m,'record_'+'subagent_'+'receipt')\"",
            "python3 -c 'from _lib import record_subagent_receipt'",
            "python3 -c 'from _lib import restore_receipt_streams'",
            "python3 -c 'from _lib import reset_receipt_streams_for_new_run'",
            "python3 -c 'from _lib import _bind_runtime_receipt_adapter'",
            "python3 -c \"name='subagent_'+'lifecycle'; __import__(name)\"",
            "python3 -c \"$(printf '%s' 'import subagent_lifecycle')\"",
        ):
            with self.subTest(command=command):
                r = _run_bash(command)
                decision, reason = parse_decision(r.stdout)
                self.assertEqual(decision, "deny")
                self.assertIn("rule=protected-artifact", reason)
                self.assertIn("lifecycle receipt entrypoint", reason)

    def test_sed_into_plan_md_denies(self):
        with scratch_task_in_real_repo("pr1-bg-prot") as task_dir:
            plan = os.path.join(task_dir, "PLAN.md")
            cmd = f"sed -i 's/a/b/' {plan}"
            r = _run_bash(cmd)
            decision, reason = parse_decision(r.stdout)
            self.assertEqual(decision, "deny")
            self.assertIn("rule=protected-artifact", reason)

    def test_script_execution_is_not_inspected(self):
        """Running a script is left to agent discipline, not gated.

        The gate used to read the script off disk and AST-scan it for receipt
        writers. That inspection was removed: heredocs, `PYTHONPATH` +
        `sitecustomize.py`, out-of-tree paths, and a trailing `-m` each defeated
        it, so it denied ordinary commands without actually stopping a
        determined caller. Direct file mutation of protected artifacts is still
        gated; deciding what a script does once it runs is not.
        """
        with tempfile.TemporaryDirectory() as tmp:
            helper = Path(tmp) / "innocent.py"
            helper.write_text(
                "from _lib import record_subagent_receipt\nprint(record_subagent_receipt)\n",
                encoding="utf-8",
            )
            r = _run_bash(f"python3 {helper}")
            decision, _ = parse_decision(r.stdout)
            self.assertIsNone(decision)

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

    def test_python_c_shelling_out_denies(self):
        """`os.system`/`subprocess` carried the path past the inline AST parse."""
        with scratch_task_in_real_repo("pr1-shellout") as task_dir:
            receipts = os.path.join(task_dir, "RECEIPTS.jsonl")
            for form in (
                f"""python3 -c "import os;os.system('cp /tmp/x {receipts}')" """,
                f"""python3 -c "import subprocess as s;s.call(['cp','/tmp/x','{receipts}'])" """,
            ):
                with self.subTest(form=form.strip()):
                    decision, reason = parse_decision(_run_bash(form.strip()).stdout)
                    self.assertEqual(decision, "deny")
                    self.assertIn("rule=protected-artifact", reason)

    def test_python_c_benign_subprocess_allows(self):
        """Only paths reachable from a shell-out are classified."""
        decision, _ = parse_decision(
            _run_bash("""python3 -c "import subprocess;subprocess.run(['pytest','-q'])" """.strip()).stdout
        )
        self.assertIsNone(decision)

    def test_copy_into_unprotected_directory_allows(self):
        """The reconstruction must not deny ordinary copies."""
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "notes.txt"
            source.write_text("x\n", encoding="utf-8")
            dest = Path(tmp) / "sub"
            dest.mkdir()
            decision, _ = parse_decision(_run_bash(f"cp {source} {dest}/").stdout)
            self.assertIsNone(decision)

    def test_python_c_command_substitution_denies(self):
        """Command substitution defeats the inline `-c` AST parse.

        The pre-2026-08-26 guard denied this inside the script-inspection
        function. Removing script inspection dropped it as a side effect, which
        left `python3 -c "$(cat forge.py)"` reaching the interpreter ungated
        while the equivalent literal one-liner still denied.
        """
        for command in (
            'python3 -c "$(cat /tmp/forge.py)"',
            'python3 -c "`cat /tmp/forge.py`"',
            'python3 -c "$(echo cHJpbnQoMSk= | base64 -d)"',
            # Unquoted: tokenizes to [..., '-c', '$', '(', ...], so the operand
            # is a bare '$'. Checking only the operand text misses it, and empty
            # code then parses cleanly. Whitespace-free payloads survive bash
            # word-splitting, which is what a forgery one-liner looks like.
            "python3 -c $(cat /tmp/forge.py)",
            "python3 -c `cat /tmp/forge.py`",
        ):
            with self.subTest(command=command):
                decision, reason = parse_decision(_run_bash(command).stdout)
                self.assertEqual(decision, "deny")
                self.assertIn("command substitution", reason)

    def test_plain_python_c_still_allows(self):
        """The deny above must key on substitution, not on `-c` itself."""
        decision, _ = parse_decision(_run_bash('python3 -c "print(1)"').stdout)
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

    def test_legitimate_lib_consumers_remain_allowed(self):
        for script in (
            "plugin/scripts/install_verified.py",
            "plugin/scripts/health.py",
            "plugin/scripts/verification_gap_check.py",
        ):
            with self.subTest(script=script):
                r = _run_bash(f"python3 {script} --help")
                decision, _ = parse_decision(r.stdout)
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
            "python3 -c \"import os; os.link('doc/harness/goals/current.json', '/tmp/harness-goal-python')\"",
            "python3 -c \"from pathlib import Path; Path('/tmp/harness-goal-pathlib').hardlink_to('doc/harness/goals/current.json')\"",
            "python3 -c \"from pathlib import Path; Path('/tmp/harness-goal-pathlib-old').link_to('doc/harness/goals/current.json')\"",
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
            "python3 -c \"import os; os.unlink('doc/harness/goals/current.json')\"",
            "python3 -c \"import os; os.rename('doc/harness/goals/current.json', '/tmp/harness-goal-renamed')\"",
            "python3 -c \"from pathlib import Path; Path('doc/harness/goals/current.json').unlink()\"",
        ):
            with self.subTest(command=command):
                r = _run_bash(command)
                decision, reason = parse_decision(r.stdout)
                self.assertEqual(decision, "deny")
                self.assertIn("rule=protected-artifact", reason)

    def test_python_goal_writer_and_aliased_path_mutation_deny(self):
        commands = (
            "python3 -c \"from _lib import start_harness_goal; start_harness_goal('.', 'forged')\"",
            "python3 -c \"import _lib; _lib.write_goal_state('.', {})\"",
            "python3 -c \"import harness_server; harness_server.call_tool('goal_start', {'objective':'forged'})\"",
            "python3 -c \"import pathlib; p=pathlib.Path; p('doc/harness/goals/current.json').unlink()\"",
            "python3 -c \"import pathlib; p=pathlib.Path; p('doc/harness/checkpoints/.alias').hardlink_to('doc/harness/goals/current.json')\"",
        )
        for command in commands:
            with self.subTest(command=command):
                r = _run_bash(command)
                decision, reason = parse_decision(r.stdout)
                self.assertEqual(decision, "deny")
                self.assertIn("rule=protected-artifact", reason)

    def test_python_read_only_goal_inspection_allows(self):
        for command in (
            "python3 -c \"open('doc/harness/goals/current.json').read()\"",
            "python3 -c \"from pathlib import Path; Path('doc/harness/goals/current.json').open().read()\"",
            "python3 -c \"from pathlib import Path; Path('doc/harness/goals/current.json').open('r').read()\"",
            "python3 -c \"import io; io.open('doc/harness/goals/current.json').read()\"",
            "python3 -c \"import io as stream; stream.open('doc/harness/goals/current.json', 'r').read()\"",
            "python3 -c \"import os; os.open('doc/harness/goals/current.json', os.O_RDONLY)\"",
            "python3 -c \"import os; os.open('doc/harness/goals/current.json', getattr(os, 'O_'+'RDONLY'))\"",
            "python3 -c \"p='first'; p='second'; print(p)\"",
            "python3 -c \"mode='r'; open('doc/harness/goals/current.json', mode)\"",
            "python3 -c \"mode='w'; mode='r'; open('doc/harness/goals/current.json', mode)\"",
            "python3 -c \"mode='r'; f=lambda x=open('doc/harness/goals/current.json', mode): None\"",
            "python3 -c \"mode='w'; (mode := 'r'); open('doc/harness/goals/current.json', mode)\"",
            "python3 -c \"mode='w'; open((mode := 'r') and 'doc/harness/goals/current.json', mode)\"",
            "python3 -c \"mode='w'; ((mode := 'r') if True else (mode := 'r')); open('doc/harness/goals/current.json', mode)\"",
            "python3 -c $'mode=\"w\"\\nif True:\\n mode=\"r\"\\nopen(\"doc/harness/goals/current.json\", mode)'",
        ):
            with self.subTest(command=command):
                r = _run_bash(command)
                decision, _ = parse_decision(r.stdout)
                self.assertIsNone(decision)

    def test_python_path_open_write_goal_denies(self):
        commands = tuple(
            "python3 -c \"from pathlib import Path; "
            f"Path('doc/harness/goals/current.json').open('{mode}')\""
            for mode in ("w", "a", "x", "r+")
        ) + (
            "python3 -c \"from pathlib import Path; Path('doc/harness/goals/current.json').open(mode='w')\"",
            "python3 -c \"from pathlib import Path; p=Path; p('doc/harness/goals/current.json').open(mode='a')\"",
            "python3 -c \"from pathlib import Path; mode=input(); Path('doc/harness/goals/current.json').open(mode=mode)\"",
            "python3 -c \"open('doc/harness/goals/current.json', 'x')\"",
            "python3 -c \"open('doc/harness/goals/current.json', 'r+')\"",
            "python3 -c \"open('doc/harness/goals/current.json', mode='w')\"",
            "python3 -c \"o=open; o('doc/harness/goals/current.json', mode='a')\"",
            "python3 -c \"from builtins import open as o; o('doc/harness/goals/current.json', 'w')\"",
            "python3 -c \"from io import open as o; o('doc/harness/goals/current.json', mode='a')\"",
            "python3 -c \"import io as stream; stream.open('doc/harness/goals/current.json', 'x')\"",
            "python3 -c \"p='doc/harness/goals/'+'current.json'; open(p,'w').write('{}')\"",
            "python3 -c \"import builtins; getattr(builtins, 'op'+'en')('doc/harness/goals/current.json','w')\"",
            "python3 -c \"import os; os.open('doc/harness/goals/current.json', os.O_WRONLY)\"",
            "python3 -c \"import os; os.truncate('doc/harness/goals/current.json', 0)\"",
            "python3 -c \"import os; os.utime('doc/harness/goals/current.json')\"",
            "python3 -c \"from pathlib import Path; Path('doc/harness/goals/current.json').touch()\"",
            "python3 -c \"import shutil; shutil.move('/tmp/x','doc/harness/goals/current.json')\"",
            "python3 -c \"import shutil; shutil.rmtree('doc/harness/goals')\"",
            "python3 -c \"import os; os.symlink('/tmp/x','doc/harness/goals/current.json')\"",
            "python3 -c \"import os; os.mknod('doc/harness/goals/current.json')\"",
            "python3 -c \"from pathlib import Path; Path('doc/harness/goals/current.json').symlink_to('/tmp/x')\"",
            "python3 -c \"from pathlib import Path; Path('doc','harness','goals','current.json').touch()\"",
            "python3 -c \"import os; p='/'.join(['doc','harness','goals','current.json']); os.utime(p)\"",
            "python3 -c \"from pathlib import Path; a='doc/harness'; b='goals/current.json'; Path(f'{a}/{b}').write_text('{}')\"",
            "python3 -c \"import os; o=os.open; o('doc/harness/goals/current.json', os.O_WRONLY)\"",
            "python3 -c \"from os import open as o; import os; o('doc/harness/goals/current.json', os.O_WRONLY)\"",
            "python3 -c \"import os as operating; o=operating.open; o('doc/harness/goals/current.json', operating.O_WRONLY)\"",
            "python3 -c \"p='ignored'; p='doc/harness/goals/'+'current.json'; open(p,'w')\"",
            "python3 -c \"mode='w'; open('doc/harness/goals/current.json', mode); mode='r'\"",
            "python3 -c \"base='doc/harness/goals/'; p=base+'current.json'; open(p,'w'); base='/tmp/'; p='/tmp/safe'\"",
            "python3 -c $'if True:\\n base=\"doc/harness/goals/\"\\n p=base+\"current.json\"\\n open(p,\"w\")'",
            "python3 -c $'mode=\"r\"\\nfor mode in [\"w\"]:\\n open(\"doc/harness/goals/current.json\", mode)'",
            "python3 -c \"mode='r'; [open('doc/harness/goals/current.json', mode) for mode in ['w']]\"",
            "python3 -c \"mode='r'; tuple(open('doc/harness/goals/current.json', mode) for mode in ['w'])\"",
            "python3 -c \"mode='r'; f=lambda mode: open('doc/harness/goals/current.json', mode); f('w')\"",
            "python3 -c \"mode='w'; f=lambda x=open('doc/harness/goals/current.json', mode): None; mode='r'\"",
            "python3 -c \"mode='r'; (mode := 'w'); open('doc/harness/goals/current.json', mode)\"",
            "python3 -c \"mode='r'; mode += '+'; open('doc/harness/goals/current.json', mode)\"",
            "python3 -c \"mode='r'; mode, = ('w',); open('doc/harness/goals/current.json', mode)\"",
            "python3 -c \"mode='r'; open((mode := 'w') and 'doc/harness/goals/current.json', mode)\"",
            "python3 -c \"mode='r'; ((mode := 'w') if True else (mode := 'r')); open('doc/harness/goals/current.json', mode)\"",
            "python3 -c \"mode='r'; ((mode := 'w') or (mode := 'r')); open('doc/harness/goals/current.json', mode)\"",
            "python3 -c $'mode=\"r\"\\nif True:\\n mode=\"w\"\\nopen(\"doc/harness/goals/current.json\", mode)'",
        )
        for command in commands:
            with self.subTest(command=command):
                r = _run_bash(command)
                decision, reason = parse_decision(r.stdout)
                self.assertEqual(decision, "deny")
                self.assertIn("rule=protected-artifact", reason)

    def test_python_branch_write_source_denies(self):
        for command in (
            "python3 -c $'mode=\"r\"\\nif True:\\n mode=\"w\"\\n"
            "open(\"plugin/scripts/health.py\", mode)'",
            "python3 -c $'mode=\"r\"\\ntry:\\n raise RuntimeError()\\n"
            "except RuntimeError:\\n mode=\"w\"\\n"
            "open(\"plugin/scripts/health.py\", mode)'",
        ):
            with self.subTest(command=command):
                decision, reason = parse_decision(_run_bash(command).stdout)
                self.assertEqual(decision, "deny")
                self.assertIn("rule=source", reason)

    def test_unknown_runtime_with_protected_path_denies(self):
        commands = (
            "node -e \"require('fs').writeFileSync('doc/harness/goals/current.json','{}')\"",
            "node -e \"let p='doc/harness/goals/'+'current.json';require('fs').writeFileSync(p,'{}')\"",
            "p=doc/harness/goals/current.json; node -e \"require('fs').writeFileSync(process.argv[1],'{}')\" \"$p\"",
            "ruby -e \"p='doc/harness/goals/'+'current.json';File.write(p,'{}')\"",
            "node --eval=\"let p='doc/harness/goals/'+'current.json';require('fs').writeFileSync(p,'{}')\"",
            "node -e \"let p='doc/harness/goals/'+'current.json';require('fs').copyFileSync('/tmp/x',p)\"",
            "node -e \"let p='doc/harness/goals/'+'current.json';require('fs/promises').writeFile(p,'{}')\"",
            "node -e \"let p='doc/harness/goals/'+'current.json';require('fs').createWriteStream(p).end('{}')\"",
            "ruby -ep=\"'doc/harness/goals/'+'current.json';File.write(p,'{}')\"",
            "perl -e'$d=\"doc/harness/goals/\";$f=\"current.json\";open(F,\">\",$d.$f)'",
            "perl -we'$d=\"doc/harness/goals/\";$f=\"current.json\";open(F,\">\",$d.$f)'",
            "awk 'BEGIN { print \"{}\" > (\"doc/harness/goals/\" \"current.json\") }'",
            "awk 'BEGIN { system(\"rm doc/harness/goals/current.json\") }'",
            "awk 'BEGIN { system (\"rm doc/harness/goals/current.json\") }'",
            "node -e \"let p='doc/harness/goals/'+'current.json';let f=require('fs');f.readFile('/tmp/x',()=>{});f.linkSync('/tmp/x',p)\"",
            "node -e \"let p='doc/harness/goals/'+'current.json';let f=require('fs');let {linkSync}=f;f.readFile('/tmp/x',()=>{});linkSync('/tmp/x',p)\"",
            "node -e \"let p='doc/harness/goals/'+'current.json';let f=require('fs');f.readFile('/tmp/x',()=>{});f['linkSync']('/tmp/x',p)\"",
            "node -e \"require('fs').writeFileSync('doc/harness/tasks/TASK__remove-duplicate-queue-and-legacy-diagnostics/RECEIPTS.jsonl','{}')\"",
        )
        for command in commands:
            with self.subTest(command=command):
                decision, reason = parse_decision(_run_bash(command).stdout)
                self.assertEqual(decision, "deny")
                self.assertIn("rule=protected-artifact", reason)

    def test_named_readers_do_not_hide_writers(self):
        for command in (
            "git clean -f doc/harness/goals/current.json",
            "pytest --junitxml=doc/harness/goals/current.json tests/test_mcp_bash_guard.py",
            "git diff --output=doc/harness/goals/current.json",
            "sed -n 'w doc/harness/goals/current.json' /dev/null",
            "find /tmp -fprint0 doc/harness/goals/current.json",
            "diff -odoc/harness/goals/current.json /tmp/a /tmp/b",
            "less -odoc/harness/goals/current.json /tmp/a",
            "rg --pre \"sh -c 'printf x > doc/harness/goals/current.json; cat'\" x doc/harness/goals/current.json",
            "sed --in-place 's/a/b/' doc/harness/goals/current.json",
            "sed -n 'wdoc/harness/goals/current.json' /dev/null",
            "sed -n -e '1w doc/harness/goals/current.json' /dev/null",
            "git grep --open-files-in-pager=\"sh -c 'printf x > doc/harness/goals/current.json'\" x",
            "diff --output=doc/harness/goals/current.json /tmp/a /tmp/b",
            "find doc/harness/goals/current.json -delete",
            "find /tmp -maxdepth 0 -ok sh -c 'printf x > doc/harness/goals/current.json' ';'",
            "p=doc/harness/goals/current.json; pytest --junitxml=\"$p\" tests/test_mcp_bash_guard.py",
            "env TARGET=doc/harness/goals/current.json sh -c 'node -e \"require(\\\"fs\\\").writeFileSync(process.env.TARGET,\\\"{}\\\")\"'",
            "pytest --junitxml=\"$(printf 'doc/harness/goals/%s' current.json)\" tests/test_mcp_bash_guard.py",
            "git diff --output=\"$(printf 'doc/harness/goals/%s' current.json)\"",
        ):
            with self.subTest(command=command):
                decision, reason = parse_decision(_run_bash(command).stdout)
                self.assertEqual(decision, "deny")
                self.assertIn("rule=protected-artifact", reason)

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

    def test_noncanonical_reader_denies(self):
        decision, reason = parse_decision(
            _run_bash("/tmp/cat doc/harness/goals/current.json").stdout
        )
        self.assertEqual(decision, "deny")
        self.assertIn("rule=protected-artifact", reason)

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
