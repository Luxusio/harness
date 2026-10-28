from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "plugin" / "scripts"
_CANONICAL_MODULES = {}


def _load(name: str):
    if name in {"codex_hook_registration", "_lib"} and name in _CANONICAL_MODULES:
        return _CANONICAL_MODULES[name]
    if name in {"codex_hook_registration", "_lib"} and name in sys.modules:
        module = sys.modules[name]
        if Path(getattr(module, "__file__", "")).resolve() == (
            SCRIPTS / f"{name}.py"
        ).resolve():
            _CANONICAL_MODULES[name] = module
            return module
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    if name in {"codex_hook_registration", "_lib"}:
        _CANONICAL_MODULES[name] = module
    return module


class _BytesStdin:
    def __init__(self, text: str):
        self.buffer = io.BytesIO(text.encode("utf-8"))


class TestCodexHookWrappers(unittest.TestCase):
    def test_post_tool_use_routes_native_create_goal_into_harness(self):
        mod = _load("hook_post_tool_use")

        with tempfile.TemporaryDirectory() as repo:
            root = Path(repo)
            (root / ".git").mkdir()
            manifest = root / "doc/harness/manifest.yaml"
            manifest.parent.mkdir(parents=True)
            manifest.write_text("type: cli\n", encoding="utf-8")
            payload = {
                "cwd": repo,
                "tool_name": "functions.create_goal",
                "tool_input": {"objective": "Fix every setup-flow bug"},
                "tool_response": {"status": "active"},
            }
            output = io.StringIO()
            with mock.patch.object(sys, "stdin", _BytesStdin(json.dumps(payload))):
                with contextlib.redirect_stdout(output):
                    self.assertEqual(mod.main(), 0)

        data = json.loads(output.getvalue())
        context = data["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(data["hookSpecificOutput"]["hookEventName"], "PostToolUse")
        self.assertIn("[harness-goal]", context)
        self.assertIn("$harness:run", context)
        self.assertIn("get_goal", context)
        self.assertIn("wbs(action=open, objective=...)", context)
        self.assertIn("one outcome tree", context)
        self.assertIn("Do not create a separate Goal/task planning ledger", context)

    def test_post_tool_use_create_goal_is_silent_outside_harness_repo(self):
        mod = _load("hook_post_tool_use")

        with tempfile.TemporaryDirectory() as repo:
            payload = {
                "cwd": repo,
                "tool_name": "create_goal",
                "tool_response": {"status": "active"},
            }
            output = io.StringIO()
            with mock.patch.object(sys, "stdin", _BytesStdin(json.dumps(payload))):
                with contextlib.redirect_stdout(output):
                    self.assertEqual(mod.main(), 0)

        self.assertEqual(output.getvalue(), "")

    def test_post_tool_create_goal_hint_is_bounded_by_child_timeout(self):
        mod = _load("hook_post_tool_use")
        payload = json.dumps({
            "cwd": str(REPO_ROOT),
            "tool_name": "functions.create_goal",
            "tool_input": {"objective": "Bound the hint"},
            "tool_response": {"status": "active"},
        })
        output = io.StringIO()
        with mock.patch.object(
            mod.subprocess, "run",
            side_effect=subprocess.TimeoutExpired(["goal-hint"], 0.01),
        ) as run, mock.patch.object(
            sys, "stdin", _BytesStdin(payload)
        ), contextlib.redirect_stdout(output):
            self.assertEqual(mod.main(), 0)

        self.assertEqual(output.getvalue(), "")
        self.assertLessEqual(
            run.call_args.kwargs["timeout"], mod.CHILD_TIMEOUT_SECONDS
        )

    def test_post_tool_use_create_goal_is_silent_on_failure(self):
        mod = _load("hook_post_tool_use")

        with tempfile.TemporaryDirectory() as repo:
            root = Path(repo)
            (root / ".git").mkdir()
            manifest = root / "doc/harness/manifest.yaml"
            manifest.parent.mkdir(parents=True)
            manifest.write_text("type: cli\n", encoding="utf-8")
            payload = {
                "cwd": repo,
                "tool_name": "create_goal",
                "tool_input": {"objective": "Will fail"},
                "tool_response": {"status": "failed", "error": "goal already active"},
            }
            output = io.StringIO()
            with mock.patch.object(sys, "stdin", _BytesStdin(json.dumps(payload))):
                with contextlib.redirect_stdout(output):
                    self.assertEqual(mod.main(), 0)

        self.assertEqual(output.getvalue(), "")

    def test_post_tool_use_create_goal_is_silent_when_harness_goal_is_already_linked(self):
        mod = _load("hook_post_tool_use")

        with tempfile.TemporaryDirectory() as repo:
            root = Path(repo)
            (root / ".git").mkdir()
            manifest = root / "doc/harness/manifest.yaml"
            manifest.parent.mkdir(parents=True)
            manifest.write_text("type: cli\n", encoding="utf-8")
            from _lib import native_wbs_id
            from test_harness_mcp_server import HarnessMcpServerTests
            td = root / 'doc/harness/tasks' / ('TASK__' + native_wbs_id('Fix setup routing')[5:])
            HarnessMcpServerTests()._make_task(repo, td.name)
            payload = {
                "cwd": repo,
                "tool_name": "functions.create_goal",
                "tool_input": {"objective": "Fix   setup routing"},
                "tool_response": {"status": "active"},
            }
            output = io.StringIO()
            with mock.patch.object(sys, "stdin", _BytesStdin(json.dumps(payload))):
                with contextlib.redirect_stdout(output):
                    self.assertEqual(mod.main(), 0)

        self.assertEqual(output.getvalue(), "")


    def test_thread_id_selects_active_marker_without_runtime_flag(self):
        lib = _load("_lib")
        thread_id = "thread/id:current"
        with tempfile.TemporaryDirectory() as repo, \
             mock.patch.dict(
                 "os.environ", {"CODEX_THREAD_ID": thread_id}, clear=True
             ), \
             mock.patch.object(lib, "_LAST_HOOK_INPUT", {}):
            task = Path(repo) / "doc/harness/tasks/TASK__thread-marker"
            task.mkdir(parents=True)
            (task / "TASK.json").write_text(json.dumps({
                "run_id": lib.new_uuid7(), "execution_mode": "standard",
                "required_lenses": ["review-code", "qa-cli"],
                "closed": False,
            }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            sid = lib.current_session_id()
            sessions = Path(repo) / "doc/harness/tasks/.active_sessions"
            sessions.mkdir(parents=True)
            (sessions / f"{sid}.json").write_text(json.dumps({
                "session_id": sid, "task_dir": str(task), "task_id": task.name,
                "run_id": lib.read_task_control(task)["run_id"], "updated": lib.now_iso(),
            }) + "\n", encoding="utf-8")

            marker = (
                Path(repo)
                / "doc/harness/tasks/.active_sessions/thread_id_current.json"
            )
            self.assertTrue(marker.is_file())
            self.assertEqual(
                json.loads(marker.read_text())["session_id"],
                "thread_id_current",
            )

    def test_explicit_session_ids_take_precedence_over_thread_id(self):
        lib = _load("_lib")
        with mock.patch.object(lib, "_LAST_HOOK_INPUT", {}):
            with mock.patch.dict(
                "os.environ",
                {
                    "HARNESS_SESSION_ID": "harness-session",
                    "CODEX_SESSION_ID": "codex-session",
                    "CODEX_THREAD_ID": "codex-thread",
                },
                clear=True,
            ):
                self.assertEqual(lib.current_session_id(), "harness-session")

            with mock.patch.dict(
                "os.environ",
                {
                    "CODEX_SESSION_ID": "codex-session",
                    "CODEX_THREAD_ID": "codex-thread",
                },
                clear=True,
            ):
                self.assertEqual(lib.current_session_id(), "codex-session")


    def test_child_dispatching_wrappers_use_payload_cwd(self):
        modules = [
            ("hook_post_tool_use", {"tool_name": "Bash"}),
            ("hook_user_prompt_submit", {}),
        ]
        for name, extra in modules:
            mod = _load(name)
            calls: list[dict] = []

            def fake_run(*args, **kwargs):
                calls.append(kwargs)
                return subprocess.CompletedProcess(args[0], 0, stdout=b"", stderr=b"")

            with tempfile.TemporaryDirectory() as repo:
                payload = dict(extra)
                payload["cwd"] = repo
                with mock.patch("subprocess.run", side_effect=fake_run):
                    with mock.patch.object(sys, "stdin", _BytesStdin(json.dumps(payload))):
                        with contextlib.redirect_stdout(io.StringIO()):
                            self.assertEqual(mod.main(), 0)

                self.assertTrue(calls, name)
                self.assertTrue(all(call.get("cwd") == repo for call in calls), name)

    def test_codex_prompt_wrapper_sets_runtime_for_child(self):
        mod = _load("hook_user_prompt_submit")
        calls: list[dict] = []

        def fake_run(*args, **kwargs):
            calls.append(kwargs)
            return subprocess.CompletedProcess(args[0], 0, stdout=b"", stderr=b"")

        with tempfile.TemporaryDirectory() as repo:
            with mock.patch("subprocess.run", side_effect=fake_run):
                with mock.patch.object(sys, "stdin", _BytesStdin(json.dumps({"cwd": repo}))):
                    with contextlib.redirect_stdout(io.StringIO()):
                        self.assertEqual(mod.main(), 0)

        self.assertEqual(calls[0]["env"]["HARNESS_RUNTIME"], "codex")
        self.assertEqual(calls[0]["timeout"], mod.CHILD_TIMEOUT_SECONDS)

    def test_codex_wrapper_budgets_fit_outer_hook_timeouts(self):
        pre = _load("hook_pre_tool_use")
        self.assertLess(pre.CHILD_TIMEOUT_SECONDS, pre.HOOK_TIMEOUT_SECONDS)
        # The prewrite child got more than the old 1.5 s, and the rest of the
        # outer budget still covers start-up plus the protected-artifact
        # fallback that runs when the child is killed.
        self.assertGreater(pre.CHILD_TIMEOUT_SECONDS, 1.5)
        self.assertGreaterEqual(
            pre.HOOK_TIMEOUT_SECONDS - pre.CHILD_TIMEOUT_SECONDS, 1.5,
        )
        for name in ("hook_post_tool_use", "hook_user_prompt_submit"):
            mod = _load(name)
            self.assertLess(mod.TOTAL_BUDGET_SECONDS, mod.HOOK_TIMEOUT_SECONDS, name)
            self.assertLess(mod.CHILD_TIMEOUT_SECONDS, mod.HOOK_TIMEOUT_SECONDS, name)

    def test_post_tool_spawn_does_no_work(self):
        mod = _load("hook_post_tool_use")
        payload = json.dumps({
            "cwd": str(REPO_ROOT),
            "tool_name": "collaboration.spawn_agent",
            "tool_input": {"task_name": "qa_cli"},
            "tool_response": {"agent_name": "/root/qa_cli"},
        })
        with mock.patch.object(
            mod, "_goal_routing_hint", return_value="",
        ), mock.patch.object(
            mod.subprocess, "run",
            side_effect=AssertionError("spawn hook dispatched a child worker"),
        ), mock.patch.object(
            sys, "stdin", _BytesStdin(payload)
        ), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(mod.main(), 0)

    def test_pre_tool_dispatches_at_most_one_child(self):
        mod = _load("hook_pre_tool_use")
        calls: list[str] = []

        def fake_run(*args, **kwargs):
            calls.append(Path(args[0][1]).name)
            self.assertEqual(kwargs["timeout"], mod.CHILD_TIMEOUT_SECONDS)
            return subprocess.CompletedProcess(args[0], 0, stdout=b"", stderr=b"")

        expected = {
            "Read": [],
            "mcp__chrome-devtools__take_snapshot": [],
            "Write": ["prewrite_gate.py"],
            "Edit": ["prewrite_gate.py"],
            "MultiEdit": ["prewrite_gate.py"],
            "apply_patch": ["prewrite_gate.py"],
            "Bash": [],
            "shell": [],
        }
        with tempfile.TemporaryDirectory() as repo:
            for tool_name, child_names in expected.items():
                calls.clear()
                with mock.patch("subprocess.run", side_effect=fake_run), \
                     mock.patch.object(sys, "stdin", _BytesStdin(json.dumps({"cwd": repo, "tool_name": tool_name}))), \
                     contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(mod.main(), 0)
                self.assertEqual(calls, child_names, tool_name)


    def test_wrapper_constants_match_installed_outer_timeouts(self):
        spec = importlib.util.spec_from_file_location(
            "install_for_hook_budget_test", REPO_ROOT / "install.py"
        )
        install = importlib.util.module_from_spec(spec)
        assert spec.loader
        sys.modules[spec.name] = install
        spec.loader.exec_module(install)
        config = install._codex_hooks_config(REPO_ROOT / "installed")
        self.assertEqual(
            config["hooks"]["PreToolUse"][0]["matcher"],
            "Write|Edit|MultiEdit|apply_patch",
        )
        self.assertEqual(
            config["hooks"]["PostToolUse"][0]["matcher"],
            "Bash|.*create_goal|"
            "wbs|mcp__harness__wbs|mcp__plugin_harness_harness__wbs",
        )
        for event, name in (
            ("PreToolUse", "hook_pre_tool_use"),
            ("PostToolUse", "hook_post_tool_use"),
            ("UserPromptSubmit", "hook_user_prompt_submit"),
        ):
            mod = _load(name)
            outer = config["hooks"][event][0]["hooks"][0]["timeout"]
            self.assertEqual(mod.HOOK_TIMEOUT_SECONDS, outer)
            if event in {"PreToolUse", "PostToolUse"}:
                from codex_hook_registration import AUTHENTICATION_BUDGET_SECONDS, REGISTRATION_BUDGET_SECONDS
                self.assertLessEqual(outer, 10)
                self.assertLess(AUTHENTICATION_BUDGET_SECONDS + REGISTRATION_BUDGET_SECONDS, outer)
            if hasattr(mod, "TOTAL_BUDGET_SECONDS"):
                self.assertLess(mod.TOTAL_BUDGET_SECONDS, outer)
            self.assertLess(mod.CHILD_TIMEOUT_SECONDS, outer)

    def test_codex_prompt_wrapper_injects_public_run_route(self):
        mod = _load("hook_user_prompt_submit")

        def fake_run(*args, **kwargs):
            return subprocess.CompletedProcess(
                args[0], 0, stdout=b"[harness-context] task=TASK__route", stderr=b"",
            )

        with tempfile.TemporaryDirectory() as repo:
            output = io.StringIO()
            with mock.patch("subprocess.run", side_effect=fake_run):
                with mock.patch.object(sys, "stdin", _BytesStdin(json.dumps({"cwd": repo}))):
                    with contextlib.redirect_stdout(output):
                        self.assertEqual(mod.main(), 0)

        context = json.loads(output.getvalue())["hookSpecificOutput"]["additionalContext"]
        self.assertTrue(context.startswith("[harness-route]"))
        self.assertIn("$harness:run", context)
        self.assertIn("before edits", context)
        self.assertIn("read-only", context)

    def test_codex_prompt_wrapper_injects_route_when_repo_is_dormant(self):
        mod = _load("hook_user_prompt_submit")

        def fake_run(*args, **kwargs):
            return subprocess.CompletedProcess(args[0], 0, stdout=b"", stderr=b"")

        with tempfile.TemporaryDirectory() as repo:
            (Path(repo) / ".git").mkdir()
            manifest = Path(repo) / "doc/harness/manifest.yaml"
            manifest.parent.mkdir(parents=True)
            manifest.write_text("type: cli\n", encoding="utf-8")
            child = Path(repo) / "src"
            child.mkdir()
            output = io.StringIO()
            with mock.patch("subprocess.run", side_effect=fake_run):
                with mock.patch.object(sys, "stdin", _BytesStdin(json.dumps({"cwd": str(child)}))):
                    with contextlib.redirect_stdout(output):
                        self.assertEqual(mod.main(), 0)

        context = json.loads(output.getvalue())["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(context, mod.CODEX_ROUTE)

    def test_codex_prompt_wrapper_stays_silent_outside_harness_when_memory_is_silent(self):
        mod = _load("hook_user_prompt_submit")

        def fake_run(*args, **kwargs):
            return subprocess.CompletedProcess(args[0], 0, stdout=b"", stderr=b"")

        output = io.StringIO()
        with mock.patch("subprocess.run", side_effect=fake_run):
            with mock.patch.object(sys, "stdin", _BytesStdin("{}")):
                with contextlib.redirect_stdout(output):
                    self.assertEqual(mod.main(), 0)
        self.assertEqual(output.getvalue(), "")

    def test_codex_prompt_wrapper_does_not_inherit_outer_repo_manifest(self):
        mod = _load("hook_user_prompt_submit")

        def fake_run(*args, **kwargs):
            return subprocess.CompletedProcess(args[0], 0, stdout=b"", stderr=b"")

        with tempfile.TemporaryDirectory() as outer:
            outer_root = Path(outer)
            (outer_root / ".git").mkdir()
            manifest = outer_root / "doc/harness/manifest.yaml"
            manifest.parent.mkdir(parents=True)
            manifest.write_text("type: cli\n", encoding="utf-8")
            inner = outer_root / "plain-project"
            (inner / ".git").mkdir(parents=True)
            output = io.StringIO()
            with mock.patch("subprocess.run", side_effect=fake_run):
                with mock.patch.object(
                    sys, "stdin", _BytesStdin(json.dumps({"cwd": str(inner)})),
                ):
                    with contextlib.redirect_stdout(output):
                        self.assertEqual(mod.main(), 0)

        self.assertEqual(output.getvalue(), "")

    def test_codex_prompt_wrapper_uses_physical_symlinked_cwd(self):
        mod = _load("hook_user_prompt_submit")

        def fake_run(*args, **kwargs):
            return subprocess.CompletedProcess(args[0], 0, stdout=b"", stderr=b"")

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            outer = base / "outer"
            external = base / "external"
            (outer / ".git").mkdir(parents=True)
            manifest = outer / "doc/harness/manifest.yaml"
            manifest.parent.mkdir(parents=True)
            manifest.write_text("type: cli\n", encoding="utf-8")
            (external / ".git").mkdir(parents=True)
            link = outer / "plain-project"
            link.symlink_to(external, target_is_directory=True)
            output = io.StringIO()
            with mock.patch("subprocess.run", side_effect=fake_run):
                with mock.patch.object(
                    sys, "stdin", _BytesStdin(json.dumps({"cwd": str(link)})),
                ):
                    with contextlib.redirect_stdout(output):
                        self.assertEqual(mod.main(), 0)

        self.assertEqual(output.getvalue(), "")

    def test_codex_prompt_wrapper_keeps_route_when_memory_times_out(self):
        mod = _load("hook_user_prompt_submit")

        with tempfile.TemporaryDirectory() as repo:
            (Path(repo) / ".git").mkdir()
            manifest = Path(repo) / "doc/harness/manifest.yaml"
            manifest.parent.mkdir(parents=True)
            manifest.write_text("type: cli\n", encoding="utf-8")
            output = io.StringIO()
            with mock.patch(
                "subprocess.run",
                side_effect=subprocess.TimeoutExpired(["prompt_memory.py"], 6),
            ):
                with mock.patch.object(sys, "stdin", _BytesStdin(json.dumps({"cwd": repo}))):
                    with contextlib.redirect_stdout(output):
                        self.assertEqual(mod.main(), 0)

        context = json.loads(output.getvalue())["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(context, mod.CODEX_ROUTE)

if __name__ == "__main__":
    unittest.main()


def test_installed_matcher_routes_only_bindable_public_wbs_results():
    import re
    spec = importlib.util.spec_from_file_location("install_for_wbs_routing", REPO_ROOT / "install.py")
    install = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = install
    spec.loader.exec_module(install)
    matcher = install._codex_hooks_config(REPO_ROOT / "installed")["hooks"]["PostToolUse"][0]["matcher"]
    mod = _load("codex_hook_registration")
    for name in ("wbs", "mcp__harness__wbs", "mcp__plugin_harness_harness__wbs"):
        assert re.fullmatch(matcher, name)
        for action in ("open", "context", "plan", "check", "next", "dispatch", "verify", "close", "block"):
            body = dict(tool_name=name, tool_input=dict(action=action), tool_response={
                "structuredContent": dict(task_dir="/repo/doc/harness/tasks/TASK__x", task_id="TASK__x", run_id="run"),
            })
            result = mod._task_result(json.dumps(body).encode())
            assert bool(result[0]) == (action in {"open", "context"})
            body["tool_response"]["isError"] = True
            assert mod._task_result(json.dumps(body).encode()) == ("", "", "")
    assert not re.fullmatch(matcher, "mcp__foreign__wbs")


def test_installed_binding_matcher_covers_canonical_tool_allowlist():
    import re
    spec = importlib.util.spec_from_file_location("install_binding_allowlist", REPO_ROOT / "install.py")
    install = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = install
    spec.loader.exec_module(install)
    sys.path.insert(0, str(SCRIPTS))
    from _lib import CODEX_TASK_BINDING_TOOLS
    matcher = install._codex_hooks_config(REPO_ROOT / "installed")["hooks"]["PostToolUse"][0]["matcher"]
    for name in CODEX_TASK_BINDING_TOOLS:
        assert re.fullmatch(matcher, name), name
    for name in ("mcp__foreign__wbs", "mcp__foreign__task_start", "evil_wbs", "wbs_suffix"):
        assert not re.fullmatch(matcher, name), name


def test_malformed_wbs_action_never_crashes_posttool_hook():
    mod = _load("codex_hook_registration")
    for action in ([], {}, None, 1, True):
        data = {"tool_name": "wbs", "arguments": {"action": action},
                "tool_response": {"structuredContent": {"task_dir": "/repo/TASK__x", "task_id": "TASK__x", "run_id": "run"}}}
        payload = json.dumps(data)
        assert mod._task_result(payload.encode()) == ("", "", "")
        result = subprocess.run([sys.executable, str(SCRIPTS / "hook_post_tool_use.py")],
                                input=payload, text=True, capture_output=True, timeout=5)
        assert result.returncode == 0 and not result.stdout and not result.stderr, result.stderr


def test_codex_wrappers_safely_ignore_nonobject_and_malformed_json_payloads(tmp_path):
    for name in ("hook_pre_tool_use", "hook_post_tool_use", "hook_user_prompt_submit", "hook_session_start"):
        for payload in ("[]", "null", "1", '"x"', "false", "{"):
            result = subprocess.run([sys.executable, str(SCRIPTS / (name + ".py"))],
                                    cwd=tmp_path, input=payload, text=True,
                                    capture_output=True, timeout=10)
            assert result.returncode == 0, (name, payload, result.stderr)
            assert not result.stdout and not result.stderr, (name, payload, result.stdout, result.stderr)
            assert not list(tmp_path.iterdir()), (name, payload, "malformed hook changed scratch state")


def test_posttool_binding_discovery_shares_hard_budget_and_preserves_caller_alarm(tmp_path):
    mod = _load("codex_hook_registration")
    payload = json.dumps({"cwd": str(tmp_path), "session_id": "019f825b-f25f-70c3-8ee8-071f79fa1c42"}).encode()
    with mock.patch.object(mod, "_registration_identity", side_effect=lambda _: time.sleep(1)):
        started, status = time.monotonic(), {}
        assert not mod.register_task_result(payload, budget_seconds=0.05, status_out=status)
        assert time.monotonic() - started < 0.5
        assert status["status"] == mod.REGISTRATION_FAILED
        delivered = []
        def caller_alarm(signum, _frame):
            delivered.append(signum)
        previous = signal.signal(signal.SIGALRM, caller_alarm)
        signal.setitimer(signal.ITIMER_REAL, 0.05)
        try:
            assert not mod.register_task_result(payload, budget_seconds=0.5)
            assert delivered == [signal.SIGALRM]
            assert signal.getsignal(signal.SIGALRM) is caller_alarm
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous)


def test_deeply_nested_encoded_arguments_are_rejected_before_binding(tmp_path):
    mod = _load("codex_hook_registration")
    nested = "[" * 10000 + "0" + "]" * 10000
    for alias in ("tool_input", "input", "arguments"):
        body = {"tool_name": "wbs", alias: nested}
        assert mod._task_arguments(body) is None
        assert mod._task_result(json.dumps(body).encode()) == ("", "", "")
        result = subprocess.run([sys.executable, str(SCRIPTS / "hook_post_tool_use.py")],
                                input=json.dumps(body), text=True, cwd=tmp_path,
                                capture_output=True, timeout=3)
        assert result.returncode == 0 and not result.stdout and not result.stderr
    assert not list(tmp_path.iterdir())


def _binding_task(repo: Path, name: str):
    lib = _load("_lib")
    (repo / ".git").mkdir(parents=True, exist_ok=True)
    manifest = repo / "doc/harness/manifest.yaml"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text("version: 5\ntype: library\n")
    task = repo / "doc/harness/tasks" / name
    task.mkdir(parents=True)
    control = {"run_id": lib.new_uuid7(), "execution_mode": "standard",
               "required_lenses": ["review-code", "qa-cli"], "closed": False}
    (task / "TASK.json").write_text(json.dumps(control) + "\n")
    (task / "PLAN.md").write_text("# Plan\n")
    return task, control


def _binding_payload(repo: Path, task: Path, control: dict, sid: str, **extra):
    data = {"cwd": str(repo), "session_id": sid, "tool_name": "wbs",
            "tool_input": {"action": "context", "wbs_id": task.name},
            "tool_response": {"structuredContent": {
                "task_dir": str(task), "task_id": task.name, "run_id": control["run_id"]}}}
    data.update(extra)
    return json.dumps(data).encode()


def test_hook_result_binds_exact_session_and_preserves_legacy_focus(tmp_path, monkeypatch):
    mod, lib = _load("codex_hook_registration"), _load("_lib")
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    repo = tmp_path / "repo"
    task, control = _binding_task(repo, "TASK__bound")
    legacy = repo / "doc/harness/tasks/.active"
    legacy.write_text("unrelated legacy pointer\n")
    sid = "019f825b-f25f-70c3-8ee8-071f79fa1c42"
    status = {}
    assert mod.register_task_result(_binding_payload(repo, task, control, sid), status_out=status), status
    assert lib.resolve_session_task_binding(str(repo), sid)["run_id"] == control["run_id"]
    assert legacy.read_text() == "unrelated legacy pointer\n"
    assert not (repo / "doc/harness/.codex-watcher").exists()
    assert not (task / "RECEIPTS.jsonl").exists()


def test_hook_result_keeps_sessions_separate_and_fences_conflicting_tasks(tmp_path, monkeypatch):
    mod, lib = _load("codex_hook_registration"), _load("_lib")
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    repo = tmp_path / "repo"
    a, ac = _binding_task(repo, "TASK__a")
    b, bc = _binding_task(repo, "TASK__b")
    sa, sb = "019f825b-f25f-70c3-8ee8-071f79fa1c42", "019f834e-1e91-7662-9024-f548103d751e"
    assert mod.register_task_result(_binding_payload(repo, a, ac, sa))
    assert mod.register_task_result(_binding_payload(repo, b, bc, sb))
    assert lib.resolve_session_task_binding(str(repo), sa)["task_dir"] == str(a)
    assert lib.resolve_session_task_binding(str(repo), sb)["task_dir"] == str(b)
    assert not mod.register_task_result(_binding_payload(repo, b, bc, sa))
    assert not lib.resolve_session_task_binding(str(repo), sa)
    assert len(lib.read_active_session_marker(str(repo), sa)["conflicts"]) == 2
    assert lib.resolve_session_task_binding(str(repo), sb)["task_dir"] == str(b)
    bc["closed"] = True
    (b / "TASK.json").write_text(json.dumps(bc) + "\n")
    assert mod.register_task_result(_binding_payload(repo, a, ac, sa))
    assert lib.resolve_session_task_binding(str(repo), sa)["task_dir"] == str(a)


def test_hook_result_rejects_failed_stale_foreign_and_conflicting_identity(tmp_path, monkeypatch):
    mod, lib = _load("codex_hook_registration"), _load("_lib")
    repo = tmp_path / "repo"
    task, control = _binding_task(repo, "TASK__bound")
    sid = "019f825b-f25f-70c3-8ee8-071f79fa1c42"
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    good = json.loads(_binding_payload(repo, task, control, sid))
    variants = []
    for key, value in (("run_id", lib.new_uuid7()), ("task_id", "TASK__wrong"),
                       ("task_dir", str(tmp_path / "other/doc/harness/tasks" / task.name))):
        data = json.loads(json.dumps(good))
        data["tool_response"]["structuredContent"][key] = value
        variants.append(data)
    failed = json.loads(json.dumps(good)); failed["tool_response"]["isError"] = True
    variants.append(failed)
    wrong_tool = json.loads(json.dumps(good)); wrong_tool["tool_name"] = "mcp__foreign__wbs"
    variants.append(wrong_tool)
    wrong_ids = json.loads(json.dumps(good)); wrong_ids["thread_id"] = "019f834e-1e91-7662-9024-f548103d751e"
    variants.append(wrong_ids)
    for data in variants:
        assert not mod.register_task_result(json.dumps(data).encode())
        assert not lib.resolve_session_task_binding(str(repo), sid)
    monkeypatch.setenv("CODEX_THREAD_ID", "019f834e-1e91-7662-9024-f548103d751e")
    assert not mod.register_task_result(json.dumps(good).encode())
    assert not lib.resolve_session_task_binding(str(repo), sid)


def test_spawn_names_never_invoke_a_collector_or_gate(tmp_path):
    mod = _load("hook_pre_tool_use")
    for name in ("reviewer", "qa_cli", "arbitrary helper"):
        payload = json.dumps({"cwd": str(tmp_path), "tool_name": "collaboration.spawn_agent",
                              "tool_input": {"task_name": name}})
        with mock.patch.object(mod.subprocess, "run", side_effect=AssertionError("spawn dispatched work")), \
             mock.patch.object(sys, "stdin", _BytesStdin(payload)), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            assert mod.main() == 0
        assert not output.getvalue()


def test_session_start_only_runs_recovery_and_project_format_from_payload_cwd(tmp_path):
    mod = _load("hook_session_start")
    calls = []
    def run(args, **kwargs):
        calls.append((Path(args[1]).name, kwargs["cwd"]))
        return subprocess.CompletedProcess(args, 0, stdout=b"", stderr=b"")
    with mock.patch.object(mod.subprocess, "run", side_effect=run), \
         mock.patch.object(sys, "stdin", _BytesStdin(json.dumps({"cwd": str(tmp_path)}))):
        assert mod.main() == 0
    assert calls == [("verification_gap_check.py", str(tmp_path)), ("project_format_check.py", str(tmp_path))]


def test_native_session_auth_uses_only_owned_metadata_and_exact_env_identity(tmp_path, monkeypatch):
    from datetime import datetime
    mod, lib = _load("codex_hook_registration"), _load("_lib")
    repo = tmp_path / "repo"
    _binding_task(repo, "TASK__native")
    sid = lib.new_uuid7()
    home = tmp_path / "codex"
    created = datetime.fromtimestamp(lib.uuid7_timestamp_ms(sid) / 1000).astimezone()
    path = home / "sessions" / f"{created:%Y/%m/%d}" / f"rollout-test-{sid}.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"type": "session_meta", "payload": {"id": sid, "cwd": str(repo)}}) + "\n")
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.setenv("CODEX_THREAD_ID", sid)
    assert mod.authenticate_native_session(sid, str(repo)) == str(repo)
    path.write_text(json.dumps({"type": "session_meta", "payload": {"id": "different", "cwd": str(repo)}}) + "\n")
    assert not mod.authenticate_native_session(sid, str(repo))
    path.write_text(json.dumps({"type": "session_meta", "payload": {"id": sid, "cwd": str(repo)}}) + "\n")
    path.chmod(0o666)
    assert not mod.authenticate_native_session(sid, str(repo))
    path.chmod(0o600)
    monkeypatch.delenv("CODEX_THREAD_ID")
    assert not mod.authenticate_native_session(sid, str(repo))
