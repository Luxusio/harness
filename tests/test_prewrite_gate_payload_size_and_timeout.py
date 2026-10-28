"""Protected-artifact denial survives payload size and a killed gate child.

REQ: doc/harness/REQ__protected-artifact-denial-survives-payload-size-and-hook-timeout.md

- The prewrite gate parses the whole hook payload. ``_lib.read_hook_input``
  caps stdin at 64 KiB by default; a larger Write/Edit/apply_patch payload used
  to be cut mid-JSON, parse to ``{}``, and be allowed, TASK.json and PLAN.md
  included.
- The Codex PreToolUse wrapper runs the gate as a child process. When that
  child times out, exits nonzero with no output, or cannot start, the wrapper
  still denies C-05 protected-artifact targets through prewrite_gate's own
  classifiers and stays fail-open for everything else (C-12).
- ``HARNESS_SKIP_PREWRITE`` and ``HARNESS_DISABLE_SCOPE_LOCK`` are documented as
  applying to every write while set in the runtime's environment.

Every test builds its own tmp harness repo and removes both escape variables
from the environment it runs under.
"""
from __future__ import annotations

import ast
import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "plugin" / "scripts"
GATE = SCRIPTS / "prewrite_gate.py"
WRAPPER = SCRIPTS / "hook_pre_tool_use.py"
TASKS = "doc/harness/tasks"
MANIFEST = "version: 5\nsource_git_roots: []\n"
ESCAPE_VARS = ("HARNESS_SKIP_PREWRITE", "HARNESS_DISABLE_SCOPE_LOCK")
BIG = 70_000
DEFAULT_CAP = 1 << 16
EVERY_WRITE = "applies to every write while set in the runtime's environment"

PROTECTED = (
    f"{TASKS}/TASK__x/TASK.json",
    f"{TASKS}/TASK__x/PLAN.md",
    f"{TASKS}/.active",
    "doc/harness/goals/current.json",
)
# README.md is an ordinary file, src/app.py a gated source file the full gate
# would deny here (an invalid open task exists), plugin/CLAUDE.md a
# workflow-control file the full gate would deny without MAINTENANCE. The
# protected-only fallback allows all three.
UNPROTECTED = ("README.md", "src/app.py", "plugin/CLAUDE.md")


def _clean_env(**extra) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in ESCAPE_VARS}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.update(extra)
    return env


@contextlib.contextmanager
def _escapes_unset():
    saved = {name: os.environ.pop(name) for name in ESCAPE_VARS if name in os.environ}
    try:
        yield
    finally:
        for name in ESCAPE_VARS:
            os.environ.pop(name, None)
        os.environ.update(saved)


def _make_repo(base: Path, name: str = "repo") -> Path:
    repo = base / name
    repo.mkdir()
    subprocess.run(
        ["git", "init", "-q"], cwd=repo, check=True, capture_output=True,
        env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
    )
    (repo / "doc/harness").mkdir(parents=True)
    (repo / "doc/harness/manifest.yaml").write_text(MANIFEST, encoding="utf-8")
    for rel in PROTECTED:
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{}\n", encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src/app.py").write_text("x = 1\n", encoding="utf-8")
    return Path(os.path.realpath(repo))


def _write(repo: Path, rel: str, size: int = BIG, char: str = "a") -> dict:
    return {
        "hook_event_name": "PreToolUse", "cwd": str(repo), "tool_name": "Write",
        "tool_input": {"file_path": str(repo / rel), "content": char * size},
    }


def _patch(repo: Path, rels, size: int = BIG) -> dict:
    lines = ["*** Begin Patch"]
    for rel in rels:
        lines += [f"*** Update File: {repo / rel}", "@@", "-a", "+" + "a" * size]
    lines.append("*** End Patch")
    return {
        "hook_event_name": "PreToolUse", "cwd": str(repo), "tool_name": "apply_patch",
        "tool_input": {"patch": "\n".join(lines) + "\n"},
    }


def _decision(stdout) -> tuple[str | None, str]:
    text = stdout.decode("utf-8") if isinstance(stdout, bytes) else stdout
    if not text.strip():
        return None, ""
    hso = json.loads(text)["hookSpecificOutput"]
    return hso["permissionDecision"], hso["permissionDecisionReason"]


def _run_script(script: Path, payload: dict, repo: Path, *, ascii_json=True, env=None):
    raw = json.dumps(payload, ensure_ascii=ascii_json).encode("utf-8")
    proc = subprocess.run(
        [sys.executable, str(script)], input=raw, capture_output=True,
        cwd=str(repo), env=env or _clean_env(), timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return _decision(proc.stdout), len(raw)


def _load(name: str):
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class _BytesStdin:
    def __init__(self, raw: bytes):
        self.buffer = io.BytesIO(raw)


def _call_wrapper(mod, payload: dict, run=None) -> tuple[int, bytes]:
    """Run ``hook_pre_tool_use.main`` in-process; ``run`` replaces the child."""
    raw = json.dumps(payload).encode("utf-8")
    sink = io.BytesIO()
    stdout = io.TextIOWrapper(sink, encoding="utf-8")
    patches = [mock.patch.object(sys, "stdin", _BytesStdin(raw))]
    if run is not None:
        patches.append(mock.patch.object(mod.subprocess, "run", side_effect=run))
    with contextlib.ExitStack() as stack:
        for patch in patches:
            stack.enter_context(patch)
        stack.enter_context(contextlib.redirect_stdout(stdout))
        rc = mod.main()
        stdout.flush()
    return rc, sink.getvalue()


def _timeout(args, **kwargs):
    raise subprocess.TimeoutExpired(args, kwargs.get("timeout") or 0)


class _TmpRepoCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="prewrite-size-timeout-")
        self.base = Path(os.path.realpath(self._tmp))
        self.repo = _make_repo(self.base)
        stack = contextlib.ExitStack()
        stack.enter_context(_escapes_unset())
        self.addCleanup(stack.close)
        self.addCleanup(shutil.rmtree, self._tmp, True)


class GateReadsWholePayload(_TmpRepoCase):
    """AC-001: >64 KiB payloads reach the classifiers."""

    def test_big_write_to_task_json_and_plan_is_denied_directly(self):
        for rel in (f"{TASKS}/TASK__x/TASK.json", f"{TASKS}/TASK__x/PLAN.md"):
            with self.subTest(rel=rel):
                (decision, reason), size = _run_script(GATE, _write(self.repo, rel), self.repo)
                self.assertGreater(size, DEFAULT_CAP)
                self.assertEqual(decision, "deny")
                self.assertIn("rule=C-05-protected-artifact", reason)
                self.assertIn(f"path={rel}", reason)
                self.assertIn("escape: HARNESS_SKIP_PREWRITE=1 <retry>", reason)

    def test_big_edit_and_multiedit_are_denied_directly(self):
        rel = f"{TASKS}/TASK__x/PLAN.md"
        path = str(self.repo / rel)
        for tool, tool_input in (
            ("Edit", {"file_path": path, "old_string": "{}", "new_string": "b" * BIG}),
            ("MultiEdit", {"file_path": path, "edits": [
                {"old_string": "{}", "new_string": "b" * BIG},
            ]}),
        ):
            with self.subTest(tool=tool):
                payload = {"cwd": str(self.repo), "tool_name": tool, "tool_input": tool_input}
                (decision, reason), size = _run_script(GATE, payload, self.repo)
                self.assertGreater(size, DEFAULT_CAP)
                self.assertEqual(decision, "deny")
                self.assertIn("rule=C-05-protected-artifact", reason)

    def test_multi_mebibyte_payload_is_denied(self):
        payload = _write(self.repo, f"{TASKS}/TASK__x/TASK.json", size=4 << 20)
        (decision, reason), size = _run_script(GATE, payload, self.repo)
        self.assertGreater(size, 4 << 20)
        self.assertEqual(decision, "deny")
        self.assertIn("rule=C-05-protected-artifact", reason)

    def test_non_ascii_payload_over_the_default_char_cap_is_denied(self):
        # Raw UTF-8 JSON: more than 64 Ki characters and far more bytes.
        payload = _write(self.repo, f"{TASKS}/TASK__x/PLAN.md", size=BIG, char="é")
        (decision, reason), size = _run_script(
            GATE, payload, self.repo, ascii_json=False, env=_clean_env(PYTHONUTF8="1"),
        )
        self.assertGreater(size, 2 * DEFAULT_CAP)
        self.assertEqual(decision, "deny")
        self.assertIn("rule=C-05-protected-artifact", reason)

    def test_invalid_utf8_byte_does_not_empty_the_payload(self):
        # Under a strict stdin decoder the old text-mode read raised inside
        # read_hook_input, parsed to {} and allowed. The gate now reads bytes
        # and decodes UTF-8 with surrogateescape whatever the locale; the
        # Codex wrapper and its fallback decode the same bytes the same way.
        # The "strict" subtests fail on the old gate and every wrapper subtest
        # fails on the old wrapper; "utf8-mode" on the gate is a control that
        # already passed, because UTF-8 mode gives stdin surrogateescape.
        rel = f"{TASKS}/TASK__x/TASK.json"
        raw = json.dumps(_write(self.repo, rel, size=10, char="z")).encode("utf-8")
        raw = raw.replace(b"zzzzzzzzzz", b"zz\xffzz", 1)
        envs = {
            "strict": _clean_env(PYTHONIOENCODING="utf-8:strict"),
            "utf8-mode": _clean_env(PYTHONUTF8="1"),
        }
        for script in (GATE, WRAPPER):
            for label, env in envs.items():
                with self.subTest(script=script.name, env=label):
                    proc = subprocess.run(
                        [sys.executable, str(script)], input=raw, capture_output=True,
                        cwd=str(self.repo), env=env, timeout=60,
                    )
                    decision, reason = _decision(proc.stdout)
                    self.assertEqual(decision, "deny")
                    self.assertIn("rule=C-05-protected-artifact", reason)
        gate = _load("prewrite_gate")
        decision, reason = _decision(gate.protected_artifact_decision(raw))
        self.assertEqual(decision, "deny")
        self.assertIn("rule=C-05-protected-artifact", reason)
        wrapper = _load("hook_pre_tool_use")
        sink = io.BytesIO()
        stdout = io.TextIOWrapper(sink, encoding="utf-8")
        with mock.patch.object(sys, "stdin", _BytesStdin(raw)), \
                mock.patch.object(wrapper.subprocess, "run", side_effect=_timeout), \
                contextlib.redirect_stdout(stdout):
            wrapper.main()
            stdout.flush()
        decision, reason = _decision(sink.getvalue())
        self.assertEqual(decision, "deny")
        self.assertIn("rule=C-05-protected-artifact", reason)

    def test_big_write_to_an_ordinary_file_stays_allowed(self):
        (decision, _reason), size = _run_script(GATE, _write(self.repo, "README.md"), self.repo)
        self.assertGreater(size, DEFAULT_CAP)
        self.assertIsNone(decision)


class CodexWrapperPassesWholePayload(_TmpRepoCase):
    """AC-001 through hook_pre_tool_use.py, Write and apply_patch."""

    def test_big_write_through_wrapper_is_denied(self):
        for rel in (f"{TASKS}/TASK__x/TASK.json", f"{TASKS}/TASK__x/PLAN.md"):
            with self.subTest(rel=rel):
                (decision, reason), size = _run_script(WRAPPER, _write(self.repo, rel), self.repo)
                self.assertGreater(size, DEFAULT_CAP)
                self.assertEqual(decision, "deny")
                self.assertIn("rule=C-05-protected-artifact", reason)

    def test_big_apply_patch_through_wrapper_is_denied(self):
        for rel in (f"{TASKS}/TASK__x/TASK.json", f"{TASKS}/TASK__x/PLAN.md"):
            with self.subTest(rel=rel):
                payload = _patch(self.repo, ["README.md", rel])
                (decision, reason), size = _run_script(WRAPPER, payload, self.repo)
                self.assertGreater(size, 2 * BIG)
                self.assertEqual(decision, "deny")
                self.assertIn("rule=C-05-protected-artifact", reason)
                self.assertIn(f"path={rel}", reason)

    def test_big_apply_patch_to_ordinary_file_stays_allowed(self):
        (decision, _reason), _size = _run_script(
            WRAPPER, _patch(self.repo, ["README.md"]), self.repo,
        )
        self.assertIsNone(decision)


class CodexWrapperFallback(_TmpRepoCase):
    """AC-004: a killed or crashed gate child still denies protected targets."""

    def setUp(self):
        super().setUp()
        self.mod = _load("hook_pre_tool_use")

    def _assert_c05(self, out: bytes, rel: str | None = None):
        decision, reason = _decision(out)
        self.assertEqual(decision, "deny")
        self.assertIn("rule=C-05-protected-artifact", reason)
        if rel:
            self.assertIn(f"path={rel}", reason)
        self.assertIn("escape: HARNESS_SKIP_PREWRITE=1 <retry>", reason)

    def test_timeout_denies_every_protected_target(self):
        for rel in PROTECTED:
            with self.subTest(rel=rel):
                rc, out = _call_wrapper(self.mod, _write(self.repo, rel, size=10), run=_timeout)
                self.assertEqual(rc, 0)
                self._assert_c05(out, rel)

    def test_timeout_allows_every_other_target(self):
        for rel in UNPROTECTED:
            with self.subTest(rel=rel):
                rc, out = _call_wrapper(self.mod, _write(self.repo, rel, size=10), run=_timeout)
                self.assertEqual(rc, 0)
                self.assertEqual(out, b"")

    def test_full_gate_would_deny_the_unprotected_non_c05_targets(self):
        # Guards the matrix above: these targets are denied by the full gate for
        # non-C-05 reasons, so an empty fallback answer proves protected-only.
        for rel, rule in (("src/app.py", "no-active-task"),
                          ("plugin/CLAUDE.md", "workflow-control-surface")):
            with self.subTest(rel=rel):
                (decision, reason), _size = _run_script(
                    GATE, _write(self.repo, rel, size=10), self.repo,
                )
                self.assertEqual(decision, "deny")
                self.assertIn(f"rule={rule}", reason)

    def test_fallback_skips_strict_mode_and_plan_first(self):
        strict = _make_repo(self.base, "strict")
        shutil.rmtree(strict / TASKS)
        (strict / "doc/harness/manifest.yaml").write_text(
            MANIFEST + "strict_compliance_requires_delegation: true\n", encoding="utf-8",
        )
        planless = _make_repo(self.base, "planless")
        (planless / TASKS / "TASK__y").mkdir()
        (planless / TASKS / ".active").write_text("TASK__y\n", encoding="utf-8")
        for repo, rule in ((strict, "no-active-task"), (planless, "C-02-plan-first")):
            with self.subTest(rule=rule):
                payload = _write(repo, "src/app.py", size=10)
                (decision, reason), _size = _run_script(GATE, payload, repo)
                self.assertEqual(decision, "deny")
                self.assertIn(f"rule={rule}", reason)
                _rc, out = _call_wrapper(self.mod, payload, run=_timeout)
                self.assertEqual(out, b"")

    def test_timeout_denies_big_apply_patch_with_a_protected_target(self):
        rel = f"{TASKS}/TASK__x/PLAN.md"
        rc, out = _call_wrapper(self.mod, _patch(self.repo, ["README.md", rel]), run=_timeout)
        self.assertEqual(rc, 0)
        self._assert_c05(out, rel)

    def test_nonzero_exit_without_output_denies_protected(self):
        rel = f"{TASKS}/TASK__x/TASK.json"
        for code in (1, -9):
            with self.subTest(code=code):
                def crashed(args, **kwargs):
                    return subprocess.CompletedProcess(args, code, stdout=b"", stderr=b"")
                _rc, out = _call_wrapper(self.mod, _write(self.repo, rel, size=10), run=crashed)
                self._assert_c05(out, rel)
                _rc, out = _call_wrapper(self.mod, _write(self.repo, "README.md", size=10), run=crashed)
                self.assertEqual(out, b"")

    def test_spawn_failure_denies_protected(self):
        def unspawnable(args, **kwargs):
            raise OSError("exec failed")
        rel = f"{TASKS}/TASK__x/TASK.json"
        _rc, out = _call_wrapper(self.mod, _write(self.repo, rel, size=10), run=unspawnable)
        self._assert_c05(out, rel)

    def test_child_output_passes_through_without_fallback(self):
        marker = b'{"hookSpecificOutput": {"permissionDecision": "deny"}}'
        for code in (0, 1):
            with self.subTest(code=code):
                def answered(args, **kwargs):
                    return subprocess.CompletedProcess(args, code, stdout=marker, stderr=b"")
                with mock.patch.object(
                    self.mod, "_protected_artifact_fallback",
                    side_effect=AssertionError("fallback ran after child output"),
                ):
                    _rc, out = _call_wrapper(
                        self.mod, _write(self.repo, "README.md", size=10), run=answered,
                    )
                self.assertEqual(out, marker)

    def test_clean_allow_from_child_does_not_run_fallback(self):
        def allowed(args, **kwargs):
            return subprocess.CompletedProcess(args, 0, stdout=b"", stderr=b"")
        with mock.patch.object(
            self.mod, "_protected_artifact_fallback",
            side_effect=AssertionError("fallback ran after a clean allow"),
        ):
            _rc, out = _call_wrapper(
                self.mod, _write(self.repo, f"{TASKS}/TASK__x/TASK.json", size=10), run=allowed,
            )
        self.assertEqual(out, b"")

    def test_skip_prewrite_is_honored_by_the_fallback(self):
        with mock.patch.dict(os.environ, {"HARNESS_SKIP_PREWRITE": "1"}):
            _rc, out = _call_wrapper(
                self.mod, _write(self.repo, f"{TASKS}/TASK__x/TASK.json", size=10), run=_timeout,
            )
        self.assertEqual(out, b"")

    def test_non_harness_directory_stays_allowed(self):
        plain = self.base / "plain"
        (plain / f"{TASKS}/TASK__x").mkdir(parents=True)
        payload = _write(plain, f"{TASKS}/TASK__x/TASK.json", size=10)
        _rc, out = _call_wrapper(self.mod, payload, run=_timeout)
        self.assertEqual(out, b"")

    def test_fallback_failure_is_fail_open(self):
        broken = mock.Mock()
        broken.protected_artifact_decision.side_effect = RuntimeError("boom")
        for side_effect in (RuntimeError("boom"), SystemExit(0)):
            with self.subTest(side_effect=type(side_effect).__name__):
                broken.protected_artifact_decision.side_effect = side_effect
                with mock.patch.dict(sys.modules, {"prewrite_gate": broken}):
                    rc, out = _call_wrapper(
                        self.mod, _write(self.repo, f"{TASKS}/TASK__x/TASK.json", size=10),
                        run=_timeout,
                    )
                self.assertEqual(rc, 0)
                self.assertEqual(out, b"")

    def test_real_child_timeout_triggers_the_fallback(self):
        with mock.patch.object(self.mod, "CHILD_TIMEOUT_SECONDS", 0.001):
            _rc, out = _call_wrapper(
                self.mod, _write(self.repo, f"{TASKS}/TASK__x/PLAN.md", size=10),
            )
            self._assert_c05(out, f"{TASKS}/TASK__x/PLAN.md")
            _rc, out = _call_wrapper(self.mod, _write(self.repo, "README.md", size=10))
            self.assertEqual(out, b"")

    def test_real_child_crash_triggers_the_fallback(self):
        crashing = self.base / "crashing-scripts"
        crashing.mkdir()
        (crashing / "prewrite_gate.py").write_text("raise SystemExit(3)\n", encoding="utf-8")
        with mock.patch.object(self.mod, "SCRIPTS_DIR", str(crashing)):
            _rc, out = _call_wrapper(
                self.mod, _write(self.repo, f"{TASKS}/TASK__x/TASK.json", size=10),
            )
        self._assert_c05(out, f"{TASKS}/TASK__x/TASK.json")

    def test_wrapper_holds_no_copy_of_the_protected_table(self):
        # Prose may name an artifact inside a longer message; a classifier
        # table would need the bare names as string constants.
        gate = _load("prewrite_gate")
        names = set(gate.PROTECTED_ARTIFACTS) | {".active", ".active_sessions", "goals"}
        source = WRAPPER.read_text(encoding="utf-8")
        constants = {
            node.value for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        self.assertFalse(constants & names, sorted(constants & names))
        self.assertIn("protected_artifact_decision", source)


class ProtectedOnlyDecision(_TmpRepoCase):
    """The public entry the wrapper uses runs only C-05 classification."""

    def setUp(self):
        super().setUp()
        self.gate = _load("prewrite_gate")

    def _decide(self, payload: dict) -> tuple[str | None, str]:
        return _decision(self.gate.protected_artifact_decision(json.dumps(payload)))

    def test_foreign_checkout_protected_target_is_denied(self):
        other = _make_repo(self.base, "other")
        payload = _write(self.repo, "README.md", size=10)
        payload["tool_input"]["file_path"] = str(other / f"{TASKS}/TASK__x/TASK.json")
        decision, reason = self._decide(payload)
        self.assertEqual(decision, "deny")
        self.assertIn("another Harness checkout", reason)
        payload["tool_input"]["file_path"] = str(other / "README.md")
        self.assertEqual(self._decide(payload), (None, ""))


    def test_symlink_escape_is_not_a_protected_denial(self):
        outside = self.base / "outside"
        outside.mkdir()
        (self.repo / "linked").symlink_to(outside, target_is_directory=True)
        payload = _write(self.repo, "linked/notes.md", size=10)
        self.assertEqual(self._decide(payload), (None, ""))
        (decision, reason), _size = _run_script(GATE, payload, self.repo)
        self.assertEqual(decision, "deny")
        self.assertIn("rule=symlink-outside-control", reason)

    def test_invalid_manifest_keeps_c05_and_drops_the_workspace_deny(self):
        # The full gate denies every write under a symlinked manifest; the
        # fallback keeps only the protected-artifact part of that.
        broken = _make_repo(self.base, "broken")
        manifest = broken / "doc/harness/manifest.yaml"
        real = broken / "manifest.real.yaml"
        manifest.rename(real)
        manifest.symlink_to(real)
        ordinary = _write(broken, "README.md", size=10)
        self.assertEqual(self._decide(ordinary), (None, ""))
        (decision, reason), _size = _run_script(GATE, ordinary, broken)
        self.assertEqual(decision, "deny")
        self.assertIn("rule=invalid-harness-workspace", reason)
        protected = _write(broken, f"{TASKS}/TASK__x/TASK.json", size=10)
        decision, reason = self._decide(protected)
        self.assertEqual(decision, "deny")
        self.assertIn("rule=C-05-protected-artifact", reason)

    def test_stdin_is_restored_after_parsing(self):
        before = sys.stdin
        self.gate.protected_artifact_decision(json.dumps(_write(self.repo, "README.md", size=10)))
        self.assertIs(sys.stdin, before)
        self.assertEqual(self.gate.protected_artifact_decision(""), "")
        self.assertEqual(self.gate.protected_artifact_decision("{not json"), "")
        self.assertIs(sys.stdin, before)

    def test_full_decision_is_unchanged_for_protected_targets(self):
        payload = _write(self.repo, f"{TASKS}/TASK__x/TASK.json", size=10)
        full = self.gate._decision(payload)
        protected = self.gate.protected_artifact_decision(json.dumps(payload))
        self.assertEqual(full, protected)


class EnvEscapeWording(unittest.TestCase):
    """AC-005: the escapes apply to every write while set."""

    def _row(self, text: str, var: str) -> str:
        rows = [line for line in text.splitlines() if line.startswith(f"| `{var}`")]
        self.assertEqual(len(rows), 1, var)
        return rows[0]

    def test_plugin_claude_md_table(self):
        text = (REPO_ROOT / "plugin/CLAUDE.md").read_text(encoding="utf-8")
        for var in ESCAPE_VARS:
            row = self._row(text, var)
            self.assertIn(EVERY_WRITE, row)
            self.assertNotRegex(row.lower(), r"one-shot|\bonce\b|one tool call")
        self.assertNotIn("HARNESS_DISABLE_HYGIENE", text)

    def test_auto_maintenance_table(self):
        text = (REPO_ROOT / "doc/harness/patterns/auto-maintenance.md").read_text(encoding="utf-8")
        row = self._row(text, "HARNESS_DISABLE_SCOPE_LOCK")
        self.assertIn(EVERY_WRITE, row)
        self.assertNotRegex(row.lower(), r"one-shot|cleared")
        self.assertNotIn("HARNESS_DISABLE_HYGIENE", text)

    def test_gate_source_has_no_one_shot_claim(self):
        source = GATE.read_text(encoding="utf-8")
        self.assertNotIn("one-shot", source.lower())
        self.assertGreaterEqual(source.count(EVERY_WRITE), 2)

    def test_scope_lock_deny_text(self):
        gate = _load("prewrite_gate")
        with tempfile.TemporaryDirectory() as tmp, _escapes_unset():
            repo = Path(os.path.realpath(tmp))
            task = repo / TASKS / "TASK__scope"
            task.mkdir(parents=True)
            (task / "PROGRESS.md").write_text(
                "allowed_paths:\n  - src/ok.py\nforbidden_paths:\n  - src/billing.py\n",
                encoding="utf-8",
            )
            block, text = gate._handle_scope_lock(
                str(repo / "src/billing.py"), str(task), str(repo), "TASK__scope",
            )
        self.assertTrue(block)
        self.assertIn(f"HARNESS_DISABLE_SCOPE_LOCK=1 ({EVERY_WRITE})", text)
        self.assertNotIn("one-shot", text)
        self.assertTrue(text.endswith("escape: HARNESS_SKIP_PREWRITE=1 <retry>"))


class PatternDocsMatchCode(unittest.TestCase):
    """AC-006: the pattern docs describe the current gate."""

    PREWRITE = REPO_ROOT / "doc/harness/patterns/prewrite-gate.md"
    SCOPE = REPO_ROOT / "doc/harness/patterns/scope-lock.md"

    def test_freshness_current_and_no_exit_code_claims(self):
        for path in (self.PREWRITE, self.SCOPE):
            with self.subTest(doc=path.name):
                text = path.read_text(encoding="utf-8")
                front = text.split("---", 2)[1]
                self.assertRegex(front, r"(?m)^freshness: current$")
                self.assertNotRegex(text, r"(?i)\bexits? 2\b")
                self.assertNotRegex(text.lower(), r"one-shot")
                self.assertNotRegex(text.lower(), r"unlisted[^.\n]*\blogs? (?:a )?warn")

    def test_prewrite_doc_keeps_required_sections(self):
        text = self.PREWRITE.read_text(encoding="utf-8")
        flat = " ".join(text.split())
        self.assertIn("\n## Cross-checkout protection\n", text)
        self.assertIn("\n## Scope lock\n", text)
        self.assertIn("accepts external targets as `outside-repo`", flat)
        self.assertIn("ignore coverage at the path Git sees", flat)
        self.assertNotIn("step b.3", flat)
        self.assertIn("protected_artifact_decision", text)
        self.assertIn("The write proceeds, protected artifacts included.", flat)

    def test_gate_rule_docs_point_at_existing_docs(self):
        gate = _load("prewrite_gate")
        for rule, doc in gate.RULE_DOCS.items():
            self.assertTrue((REPO_ROOT / doc).is_file(), rule)
        self.assertEqual(gate.RULE_DOCS["scope-lock-forbidden"], "doc/harness/patterns/scope-lock.md")


if __name__ == "__main__":
    unittest.main()
