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

    def test_ordinary_script_execution_allows(self):
        """The defect this replaced: a literal path that does not resolve."""
        for command in (
            "python3 scripts/gen.py",
            "python3 manage.py migrate",
            "python3 tools/build_docs.py --check",
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
