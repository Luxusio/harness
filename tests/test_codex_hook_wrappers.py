from __future__ import annotations

import contextlib
import importlib.util
import io
import json
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


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    if name == "hook_post_tool_use":
        module.RECEIPT_EVENT_MODE = "sync"
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
        self.assertIn("goal_start", context)
        self.assertIn("task_start", context)
        self.assertIn("goal_add_task", context)

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
            goals = root / "doc/harness/goals"
            goals.mkdir(parents=True)
            (goals / "current.json").write_text(json.dumps({
                "goal_id": "GOAL__linked",
                "objective": "Fix setup routing",
                "status": "active",
                "tasks": [],
            }), encoding="utf-8")
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

    def test_session_start_runs_children_from_payload_cwd(self):
        mod = _load("hook_session_start")
        calls: list[dict] = []
        root_id = "019f834e-1e91-7662-9024-f548103d751e"

        def fake_run(*args, **kwargs):
            kwargs["cmd"] = args[0]
            calls.append(kwargs)
            return subprocess.CompletedProcess(args[0], 0, stdout=b"", stderr=b"")

        with tempfile.TemporaryDirectory() as repo:
            payload = json.dumps({"cwd": repo, "session_id": root_id})
            with mock.patch.dict("os.environ", {}, clear=True), \
                 mock.patch.object(mod, "restore_watcher_registration") as restore, \
                 mock.patch("subprocess.run", side_effect=fake_run):
                with mock.patch.object(sys, "stdin", _BytesStdin(payload)):
                    with contextlib.redirect_stdout(io.StringIO()):
                        self.assertEqual(mod.main(), 0)

        self.assertTrue(calls)
        self.assertTrue(all(call.get("cwd") == repo for call in calls))
        child_names = [Path(call["cmd"][1]).name for call in calls]
        restore.assert_called_once_with(
            payload.encode(), retry_seconds=1.0, budget_seconds=1.25,
        )
        self.assertNotIn("codex_lifecycle_watcher.py", child_names)
        self.assertNotIn("hygiene_scan.py", child_names)
        self.assertNotIn("inject_checkpoint.py", child_names)
        self.assertNotIn("contract_lint.py", child_names)

    def test_session_start_rejects_mismatched_payload_and_environment_identity(self):
        mod = _load("codex_hook_registration")
        payload_id = "019f834e-1e91-7662-9024-f548103d751e"
        env_id = "019f825b-f25f-70c3-8ee8-071f79fa1c42"
        with tempfile.TemporaryDirectory() as repo:
            payload = json.dumps({"cwd": repo, "session_id": payload_id}).encode()
            with mock.patch.dict("os.environ", {"CODEX_THREAD_ID": env_id}, clear=True), \
                 mock.patch.object(mod, "ensure") as ensure:
                self.assertFalse(mod.restore_watcher_registration(payload, ensure_fn=ensure))

        ensure.assert_not_called()

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
            lib.write_active_marker(repo, str(task))

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

    def test_only_session_start_wrapper_restores_registration(self):
        root_id = "019f834e-1e91-7662-9024-f548103d751e"
        mod = _load("hook_session_start")
        with tempfile.TemporaryDirectory() as repo:
            raw = json.dumps({"cwd": repo, "session_id": root_id})
            with mock.patch.object(mod, "restore_watcher_registration") as restore, \
                 mock.patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, stdout=b"", stderr=b"")), \
                 mock.patch.object(sys, "stdin", _BytesStdin(raw)), \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(mod.main(), 0)
        restore.assert_called_once_with(raw.encode(), retry_seconds=1.0, budget_seconds=1.25)

        for name in ("hook_post_tool_use", "hook_user_prompt_submit", "hook_stop"):
            self.assertFalse(hasattr(_load(name), "restore_watcher_registration"), name)

    def test_registration_helper_retries_and_late_recovery_is_future_only(self):
        mod = _load("codex_hook_registration")
        root_id = "019f834e-1e91-7662-9024-f548103d751e"
        attempts = []
        with tempfile.TemporaryDirectory() as repo:
            (Path(repo) / ".git").mkdir()
            manifest = Path(repo) / "doc/harness/manifest.yaml"
            manifest.parent.mkdir(parents=True)
            manifest.write_text("type: cli\n", encoding="utf-8")
            payload = json.dumps({"cwd": repo, "session_id": root_id}).encode()

            def ensure(repo_root, thread_id):
                attempts.append((repo_root, thread_id))
                return len(attempts) == 2

            with mock.patch.dict("os.environ", {}, clear=True), \
                 mock.patch.object(
                     mod.time, "monotonic",
                     side_effect=[index / 100 for index in range(50)],
                 ), \
                 mock.patch.object(mod.time, "sleep"):
                self.assertTrue(mod.restore_watcher_registration(
                    payload, retry_seconds=0.1, ensure_fn=ensure,
                ))
        self.assertEqual(attempts, [(repo, root_id), (repo, root_id)])
        self.assertIn("only future subagent starts", mod.restore_watcher_registration.__doc__)

    def test_pre_spawn_registration_binds_default_task_to_root_thread(self):
        mod = _load("codex_hook_registration")
        lib = _load("_lib")
        root_id = "019f834e-1e91-7662-9024-f548103d751e"
        with tempfile.TemporaryDirectory() as repo:
            task = Path(repo) / "doc/harness/tasks/TASK__root-binding"
            task.mkdir(parents=True)
            lib.ensure_task_scaffold(
                str(task), "TASK__root-binding", repo_root=repo
            )
            lib.write_active_marker(repo, str(task), session_id="default")

            self.assertTrue(mod._bind_active_task_to_root_session(repo, root_id))

            marker = (
                Path(repo)
                / "doc/harness/tasks/.active_sessions"
                / f"{root_id}.json"
            )
            payload = json.loads(marker.read_text(encoding="utf-8"))
            self.assertEqual(payload["session_id"], root_id)
            self.assertEqual(payload["task_id"], "TASK__root-binding")
            self.assertEqual(
                payload["task_run_id"], lib.read_task_control(str(task))["task_run_id"]
            )

    def test_registration_binds_before_rollout_discovery(self):
        mod = _load("codex_hook_registration")
        root_id = "019f834e-1e91-7662-9024-f548103d751e"
        order: list[str] = []
        with tempfile.TemporaryDirectory() as repo:
            (Path(repo) / ".git").mkdir()
            manifest = Path(repo) / "doc/harness/manifest.yaml"
            manifest.parent.mkdir(parents=True)
            manifest.write_text("type: cli\n", encoding="utf-8")
            payload = json.dumps({"cwd": repo, "session_id": root_id}).encode()

            with mock.patch.dict("os.environ", {}, clear=True):
                self.assertTrue(mod.restore_watcher_registration(
                    payload,
                    ensure_fn=lambda _root, _thread: order.append("ensure") or True,
                    bind_fn=lambda _root, _thread: order.append("bind") or True,
                ))

        self.assertEqual(order, ["bind", "ensure"])

    def test_registration_helper_noops_without_harness_manifest(self):
        mod = _load("codex_hook_registration")
        root_id = "019f834e-1e91-7662-9024-f548103d751e"
        with tempfile.TemporaryDirectory() as repo:
            payload = json.dumps({"cwd": repo, "session_id": root_id}).encode()
            ensure = mock.Mock(return_value=True)
            with mock.patch.dict("os.environ", {}, clear=True):
                self.assertFalse(mod.restore_watcher_registration(payload, ensure_fn=ensure))
        ensure.assert_not_called()

    def test_registration_helper_does_not_inherit_outer_repo_manifest(self):
        mod = _load("codex_hook_registration")
        root_id = "019f834e-1e91-7662-9024-f548103d751e"
        with tempfile.TemporaryDirectory() as outer:
            outer_root = Path(outer)
            (outer_root / ".git").mkdir()
            manifest = outer_root / "doc/harness/manifest.yaml"
            manifest.parent.mkdir(parents=True)
            manifest.write_text("type: cli\n", encoding="utf-8")
            inner = outer_root / "plain-project"
            (inner / ".git").mkdir(parents=True)
            payload = json.dumps({"cwd": str(inner), "session_id": root_id}).encode()
            ensure = mock.Mock(return_value=True)
            with mock.patch.dict("os.environ", {}, clear=True):
                self.assertFalse(mod.restore_watcher_registration(payload, ensure_fn=ensure))
        ensure.assert_not_called()

    def test_registration_helper_uses_physical_symlinked_cwd(self):
        mod = _load("codex_hook_registration")
        root_id = "019f834e-1e91-7662-9024-f548103d751e"
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
            payload = json.dumps({"cwd": str(link), "session_id": root_id}).encode()
            ensure = mock.Mock(return_value=True)
            with mock.patch.dict("os.environ", {}, clear=True):
                self.assertFalse(mod.restore_watcher_registration(payload, ensure_fn=ensure))
        ensure.assert_not_called()

    def test_registration_attempt_has_a_hard_wall_clock_deadline(self):
        mod = _load("codex_hook_registration")

        def slow_ensure(*_args, **_kwargs):
            time.sleep(1.0)
            return True

        started = time.monotonic()
        with mock.patch.object(mod, "ensure", side_effect=slow_ensure):
            restored = mod._ensure_with_deadline(
                "/tmp", "019f834e-1e91-7662-9024-f548103d751e", started + 0.05,
            )
        self.assertFalse(restored)
        self.assertLess(time.monotonic() - started, 0.5)

    def test_registration_root_resolution_uses_the_same_hard_deadline(self):
        mod = _load("codex_hook_registration")
        root_id = "019f834e-1e91-7662-9024-f548103d751e"
        with tempfile.TemporaryDirectory() as repo:
            payload = json.dumps({
                "cwd": repo,
                "session_id": root_id,
            }).encode()

            def slow_root(_cwd):
                time.sleep(1.0)
                return repo

            started = time.monotonic()
            with mock.patch.object(mod, "find_harness_root", side_effect=slow_root):
                restored = mod.restore_watcher_registration(
                    payload, budget_seconds=0.05
                )
        self.assertFalse(restored)
        self.assertLess(time.monotonic() - started, 0.5)

    def test_registration_preserves_a_shorter_existing_alarm(self):
        mod = _load("codex_hook_registration")
        delivered: list[int] = []

        def previous_handler(signum, _frame):
            delivered.append(signum)

        def slow_ensure(*_args, **_kwargs):
            time.sleep(1.0)
            return True

        original_handler = signal.signal(signal.SIGALRM, previous_handler)
        signal.setitimer(signal.ITIMER_REAL, 0.05)
        try:
            with mock.patch.object(mod, "ensure", side_effect=slow_ensure):
                restored = mod._ensure_with_deadline(
                    "/tmp", "019f834e-1e91-7662-9024-f548103d751e",
                    time.monotonic() + 0.5,
                )
            self.assertFalse(restored)
            self.assertEqual(delivered, [signal.SIGALRM])
            self.assertIs(signal.getsignal(signal.SIGALRM), previous_handler)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0.0)
            signal.signal(signal.SIGALRM, original_handler)

    def test_registration_does_not_swallow_a_restored_alarm_exception(self):
        mod = _load("codex_hook_registration")

        class CallerDeadline(Exception):
            pass

        def previous_handler(_signum, _frame):
            raise CallerDeadline("caller deadline")

        def slow_ensure(*_args, **_kwargs):
            time.sleep(1.0)
            return True

        original_ensure = mod.ensure
        original_handler = signal.signal(signal.SIGALRM, previous_handler)
        signal.setitimer(signal.ITIMER_REAL, 0.05)
        try:
            mod.ensure = slow_ensure
            with tempfile.TemporaryDirectory() as repo:
                (Path(repo) / ".git").mkdir()
                manifest = Path(repo) / "doc/harness/manifest.yaml"
                manifest.parent.mkdir(parents=True)
                manifest.write_text("type: cli\n", encoding="utf-8")
                payload = json.dumps({
                    "cwd": repo,
                    "session_id": "019f834e-1e91-7662-9024-f548103d751e",
                }).encode()
                with mock.patch.dict("os.environ", {}, clear=True):
                    with self.assertRaisesRegex(CallerDeadline, "caller deadline"):
                        mod.restore_watcher_registration(
                            payload,
                            budget_seconds=0.5,
                            ensure_fn=slow_ensure,
                            bind_fn=lambda _root, _thread: True,
                        )
        finally:
            mod.ensure = original_ensure
            signal.setitimer(signal.ITIMER_REAL, 0.0)
            signal.signal(signal.SIGALRM, original_handler)

    def test_pre_post_prompt_and_stop_wrappers_use_payload_cwd(self):
        modules = [
            ("hook_pre_tool_use", {"tool_name": "Bash"}),
            ("hook_post_tool_use", {"tool_name": "Bash"}),
            ("hook_user_prompt_submit", {}),
            ("hook_stop", {}),
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
        self.assertLess(pre.REGISTRATION_BUDGET_SECONDS, pre.HOOK_TIMEOUT_SECONDS)
        self.assertLess(pre.CHILD_TIMEOUT_SECONDS, pre.HOOK_TIMEOUT_SECONDS)
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
            "Bash": ["mcp_bash_guard.py"],
            "shell": ["mcp_bash_guard.py"],
        }
        with tempfile.TemporaryDirectory() as repo:
            for tool_name, child_names in expected.items():
                calls.clear()
                with mock.patch("subprocess.run", side_effect=fake_run), \
                     mock.patch.object(sys, "stdin", _BytesStdin(json.dumps({"cwd": repo, "tool_name": tool_name}))), \
                     contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(mod.main(), 0)
                self.assertEqual(calls, child_names, tool_name)

    def test_pre_tool_registration_runs_only_for_spawn(self):
        mod = _load("hook_pre_tool_use")
        with tempfile.TemporaryDirectory() as repo:
            for tool_name in ("Read", "Write", "Bash"):
                raw = json.dumps({"cwd": repo, "tool_name": tool_name})
                with mock.patch.object(mod, "restore_watcher_registration") as restore, \
                     mock.patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, stdout=b"", stderr=b"")), \
                     mock.patch.object(sys, "stdin", _BytesStdin(raw)), \
                     contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(mod.main(), 0)
                restore.assert_not_called()
            raw = json.dumps({"cwd": repo, "tool_name": "collaboration.spawn_agent"})
            with mock.patch.object(mod, "restore_watcher_registration") as restore, \
                 mock.patch("subprocess.run") as run_child, \
                 mock.patch.object(sys, "stdin", _BytesStdin(raw)), \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(mod.main(), 0)
            restore.assert_called_once_with(raw.encode(), budget_seconds=0.5)
            run_child.assert_not_called()

            for tool_name in ("multi_agent_v1__spawn_agent", "functions.spawn_agent"):
                raw = json.dumps({"cwd": repo, "tool_name": tool_name})
                with mock.patch.object(mod, "restore_watcher_registration") as restore, \
                     mock.patch("subprocess.run") as run_child, \
                     mock.patch.object(sys, "stdin", _BytesStdin(raw)), \
                     contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(mod.main(), 0)
                restore.assert_not_called()
                run_child.assert_not_called()

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
            "Write|Edit|MultiEdit|Bash|apply_patch|shell|collaboration\\.spawn_agent",
        )
        self.assertEqual(
            config["hooks"]["PostToolUse"][0]["matcher"],
            "Bash|.*create_goal",
        )
        for event, name in (
            ("PreToolUse", "hook_pre_tool_use"),
            ("PostToolUse", "hook_post_tool_use"),
            ("UserPromptSubmit", "hook_user_prompt_submit"),
        ):
            mod = _load(name)
            outer = config["hooks"][event][0]["hooks"][0]["timeout"]
            self.assertEqual(mod.HOOK_TIMEOUT_SECONDS, outer)
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
