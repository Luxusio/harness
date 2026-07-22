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

    def test_all_codex_hook_wrappers_restore_registration(self):
        modules = [
            ("hook_session_start", {}, {"retry_seconds": 1.0, "budget_seconds": 1.25}),
            ("hook_pre_tool_use", {"tool_name": "Read"}, {"budget_seconds": 0.5}),
            ("hook_post_tool_use", {"tool_name": "wait_agent"}, {"budget_seconds": 0.4}),
            ("hook_user_prompt_submit", {}, {"budget_seconds": 0.5}),
            ("hook_stop", {}, {"budget_seconds": 0.5}),
        ]
        root_id = "019f834e-1e91-7662-9024-f548103d751e"
        for name, extra, registration_kwargs in modules:
            mod = _load(name)
            with tempfile.TemporaryDirectory() as repo:
                payload = {**extra, "cwd": repo, "session_id": root_id}
                raw = json.dumps(payload)
                with mock.patch.object(mod, "restore_watcher_registration") as restore, \
                     mock.patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, stdout=b"", stderr=b"")), \
                     mock.patch.object(sys, "stdin", _BytesStdin(raw)), \
                     contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(mod.main(), 0)
            restore.assert_called_once_with(raw.encode(), **registration_kwargs)

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
                 mock.patch.object(mod.time, "monotonic", side_effect=[0.0, 0.0, 0.01, 0.02]), \
                 mock.patch.object(mod.time, "sleep"):
                self.assertTrue(mod.restore_watcher_registration(
                    payload, retry_seconds=0.1, ensure_fn=ensure,
                ))
        self.assertEqual(attempts, [(repo, root_id), (repo, root_id)])
        self.assertIn("only future subagent starts", mod.restore_watcher_registration.__doc__)

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
                            payload, budget_seconds=0.5, ensure_fn=slow_ensure,
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
        for name in (
            "hook_pre_tool_use", "hook_post_tool_use", "hook_user_prompt_submit",
        ):
            mod = _load(name)
            self.assertLess(mod.TOTAL_BUDGET_SECONDS, mod.HOOK_TIMEOUT_SECONDS, name)
            self.assertLess(mod.REGISTRATION_BUDGET_SECONDS, mod.TOTAL_BUDGET_SECONDS, name)
            self.assertLess(mod.CHILD_TIMEOUT_SECONDS, mod.HOOK_TIMEOUT_SECONDS, name)

    def test_pre_tool_children_consume_one_shared_deadline(self):
        mod = _load("hook_pre_tool_use")
        calls: list[float] = []

        def fake_run(*args, **kwargs):
            calls.append(kwargs["timeout"])
            return subprocess.CompletedProcess(args[0], 0, stdout=b"", stderr=b"")

        with tempfile.TemporaryDirectory() as repo, \
             mock.patch.object(mod, "restore_watcher_registration"), \
             mock.patch.object(mod.time, "monotonic", side_effect=[0.0, 0.0, 1.5, 3.9]), \
             mock.patch("subprocess.run", side_effect=fake_run), \
             mock.patch.object(sys, "stdin", _BytesStdin(json.dumps({"cwd": repo, "tool_name": "Bash"}))), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(mod.main(), 0)

        self.assertEqual(calls[:2], [mod.CHILD_TIMEOUT_SECONDS] * 2)
        self.assertAlmostEqual(calls[2], 0.25)

    def test_wrapper_constants_match_installed_outer_timeouts(self):
        spec = importlib.util.spec_from_file_location(
            "install_for_hook_budget_test", REPO_ROOT / "install.py"
        )
        install = importlib.util.module_from_spec(spec)
        assert spec.loader
        sys.modules[spec.name] = install
        spec.loader.exec_module(install)
        config = install._codex_hooks_config(REPO_ROOT / "installed")
        for event, name in (
            ("PreToolUse", "hook_pre_tool_use"),
            ("PostToolUse", "hook_post_tool_use"),
            ("UserPromptSubmit", "hook_user_prompt_submit"),
        ):
            mod = _load(name)
            outer = config["hooks"][event][0]["hooks"][0]["timeout"]
            self.assertEqual(mod.HOOK_TIMEOUT_SECONDS, outer)
            self.assertLess(mod.TOTAL_BUDGET_SECONDS, outer)

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

    def test_pre_tool_use_does_not_register_uncorrelatable_codex_agent_id(self):
        mod = _load("hook_pre_tool_use")

        def fake_run(*args, **kwargs):
            return subprocess.CompletedProcess(args[0], 0, stdout=b"", stderr=b"")

        with tempfile.TemporaryDirectory() as repo:
            root = Path(repo)
            (root / ".git").mkdir()
            tasks_dir = root / "doc" / "harness" / "tasks"
            task_dir = tasks_dir / "TASK__codex-subagent"
            task_dir.mkdir(parents=True)
            (tasks_dir / ".active").write_text(str(task_dir), encoding="utf-8")
            payload = {
                "cwd": repo,
                "tool_name": "multi_agent_v1.spawn_agent",
                "tool_call_id": "call-123",
                "tool_input": {"agent_type": "harness:qa-cli"},
            }
            with mock.patch("subprocess.run", side_effect=fake_run):
                with mock.patch.object(sys, "stdin", _BytesStdin(json.dumps(payload))):
                    with contextlib.redirect_stdout(io.StringIO()):
                        self.assertEqual(mod.main(), 0)

            self.assertFalse((task_dir / "SUBAGENT_RECEIPTS.jsonl").exists())
            self.assertFalse((root / "doc/harness/runtime/background.json").exists())

    def test_pre_tool_use_does_not_infer_agent_type_from_message(self):
        mod = _load("hook_post_tool_use")

        def fake_run(*args, **kwargs):
            return subprocess.CompletedProcess(args[0], 0, stdout=b"", stderr=b"")

        with tempfile.TemporaryDirectory() as repo:
            root = Path(repo)
            (root / ".git").mkdir()
            tasks_dir = root / "doc" / "harness" / "tasks"
            task_dir = tasks_dir / "TASK__codex-infer"
            task_dir.mkdir(parents=True)
            (tasks_dir / ".active").write_text(str(task_dir), encoding="utf-8")
            payload = {
                "cwd": repo,
                "tool_name": "spawn_agent",
                "tool_call_id": "call-browser",
                "tool_input": {
                    "agent_type": "default",
                    "message": "You are the qa-browser lens for TASK__codex-infer.",
                },
                "tool_response": {"task_name": "/root/default_worker"},
            }
            with mock.patch("subprocess.run", side_effect=fake_run):
                with mock.patch.object(sys, "stdin", _BytesStdin(json.dumps(payload))):
                    with contextlib.redirect_stdout(io.StringIO()):
                        self.assertEqual(mod.main(), 0)

            receipt = json.loads((task_dir / "SUBAGENT_RECEIPTS.jsonl").read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(receipt["agent_type"], "default")
            self.assertEqual(receipt["lens"], "")

    def test_pre_tool_use_infers_qa_lens_from_structured_task_name(self):
        mod = _load("hook_post_tool_use")

        def fake_run(*args, **kwargs):
            return subprocess.CompletedProcess(args[0], 0, stdout=b"", stderr=b"")

        with tempfile.TemporaryDirectory() as repo:
            root = Path(repo)
            (root / ".git").mkdir()
            tasks_dir = root / "doc" / "harness" / "tasks"
            task_dir = tasks_dir / "TASK__codex-task-name"
            task_dir.mkdir(parents=True)
            (tasks_dir / ".active").write_text(str(task_dir), encoding="utf-8")
            payload = {
                "cwd": repo,
                "tool_name": "collaboration.spawn_agent",
                "tool_call_id": "call-task-name",
                "tool_input": {"task_name": "qa_cli"},
                "tool_response": {"task_name": "/root/qa_cli"},
            }
            with mock.patch("subprocess.run", side_effect=fake_run):
                with mock.patch.object(sys, "stdin", _BytesStdin(json.dumps(payload))):
                    with contextlib.redirect_stdout(io.StringIO()):
                        self.assertEqual(mod.main(), 0)

            receipt = json.loads((task_dir / "SUBAGENT_RECEIPTS.jsonl").read_text().splitlines()[0])
            self.assertEqual(receipt["agent_type"], "qa_cli")
            self.assertEqual(receipt["lens"], "qa-cli")

    def test_post_tool_use_records_codex_wait_agent_completion(self):
        mod = _load("hook_post_tool_use")

        with tempfile.TemporaryDirectory() as repo:
            root = Path(repo)
            (root / ".git").mkdir()
            tasks_dir = root / "doc" / "harness" / "tasks"
            task_dir = tasks_dir / "TASK__codex-complete"
            task_dir.mkdir(parents=True)
            (tasks_dir / ".active").write_text(str(task_dir), encoding="utf-8")
            spawn_payload = {
                "cwd": repo,
                "tool_name": "collaboration.spawn_agent",
                "tool_input": {"task_name": "qa_cli"},
                "tool_response": {"task_name": "/root/qa_cli"},
            }
            with mock.patch.object(sys, "stdin", _BytesStdin(json.dumps(spawn_payload))):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(mod.main(), 0)

            wait_payload = {
                "cwd": repo,
                "tool_name": "collaboration.wait_agent",
                "tool_input": {"target": "/root/qa_cli"},
                "tool_response": {"result": "VERDICT: PASS\n12 tests passed"},
            }
            with mock.patch.object(sys, "stdin", _BytesStdin(json.dumps(wait_payload))):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(mod.main(), 0)

            receipts = [
                json.loads(line)
                for line in (task_dir / "SUBAGENT_RECEIPTS.jsonl").read_text().splitlines()
            ]
            self.assertEqual(receipts[0]["agent_id"], "/root/qa_cli")
            receipt = receipts[-1]
            self.assertEqual(receipt["status"], "completed")
            self.assertEqual(receipt["verdict"], "PASS")
            self.assertEqual(receipt["lens"], "qa-cli")

    def test_post_tool_use_records_targetless_list_agents_completions(self):
        mod = _load("hook_post_tool_use")

        with tempfile.TemporaryDirectory() as repo:
            root = Path(repo)
            (root / ".git").mkdir()
            tasks_dir = root / "doc" / "harness" / "tasks"
            task_dir = tasks_dir / "TASK__codex-list"
            task_dir.mkdir(parents=True)
            (tasks_dir / ".active").write_text(str(task_dir), encoding="utf-8")
            for task_name in ("code_review", "security_review"):
                spawn = {
                    "cwd": repo,
                    "tool_name": "collaboration.spawn_agent",
                    "tool_input": {"task_name": task_name},
                    "tool_response": {"task_name": f"/root/{task_name}"},
                }
                with mock.patch.object(sys, "stdin", _BytesStdin(json.dumps(spawn))):
                    with contextlib.redirect_stdout(io.StringIO()):
                        mod.main()
            listed = {
                "cwd": repo,
                "tool_name": "collaboration.list_agents",
                "tool_input": {},
                "tool_response": {"agents": [
                    {"agent_name": "/root/code_review", "agent_status": {"completed": "VERDICT: PASS\nFINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0\nClean"}},
                    {"agent_name": "/root/security_review", "agent_status": {"completed": "VERDICT: FAIL\nFINDING_COUNTS: FIX_NOW=1 INVESTIGATE=0 OPTIONAL=0\nFinding"}},
                ]},
            }
            with mock.patch.object(sys, "stdin", _BytesStdin(json.dumps(listed))):
                with contextlib.redirect_stdout(io.StringIO()):
                    mod.main()

            receipts = [json.loads(line) for line in (task_dir / "REVIEW_RECEIPTS.jsonl").read_text().splitlines()]
            completions = [item for item in receipts if item["status"] == "completed"]
            self.assertEqual([(item["agent_id"], item["verdict"]) for item in completions], [
                ("/root/code_review", "PASS"),
                ("/root/security_review", "FAIL"),
            ])

    def test_codex_reviewer_lifecycle_uses_separate_receipt_stream(self):
        mod = _load("hook_post_tool_use")

        with tempfile.TemporaryDirectory() as repo:
            root = Path(repo)
            (root / ".git").mkdir()
            tasks_dir = root / "doc" / "harness" / "tasks"
            task_dir = tasks_dir / "TASK__codex-review"
            task_dir.mkdir(parents=True)
            (tasks_dir / ".active").write_text(str(task_dir), encoding="utf-8")
            (task_dir / "TASK_STATE.yaml").write_text(
                "task_id: TASK__codex-review\nstatus: created\nruntime_verdict: pending\n"
                "touched_paths:\n  - src/main.py\nplan_session_state: closed\nclosed_at: null\nupdated: now\n",
                encoding="utf-8",
            )
            source = root / "src/main.py"
            source.parent.mkdir()
            source.write_text("VALUE = 1\n", encoding="utf-8")
            spawn_payload = {
                "cwd": repo,
                "tool_name": "collaboration.spawn_agent",
                "tool_input": {"task_name": "code_review"},
                "tool_response": {"task_name": "/root/code_review"},
            }
            with mock.patch.object(sys, "stdin", _BytesStdin(json.dumps(spawn_payload))):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(mod.main(), 0)
            wait_payload = {
                "cwd": repo,
                "tool_name": "collaboration.wait_agent",
                "tool_input": {"target": "/root/code_review"},
                "tool_response": {"result": "VERDICT: PASS\nFINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0\nNo findings."},
            }
            with mock.patch.object(sys, "stdin", _BytesStdin(json.dumps(wait_payload))):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(mod.main(), 0)

            self.assertTrue((task_dir / "REVIEW_RECEIPTS.jsonl").is_file())
            self.assertFalse((task_dir / "SUBAGENT_RECEIPTS.jsonl").exists())
            receipts = [json.loads(line) for line in (task_dir / "REVIEW_RECEIPTS.jsonl").read_text().splitlines()]
            self.assertEqual(receipts[-1]["lens"], "review-code")
            self.assertEqual(receipts[-1]["verdict"], "PASS")

    def test_codex_reviewer_pass_is_invalidated_when_source_changes_during_review(self):
        mod = _load("hook_post_tool_use")

        with tempfile.TemporaryDirectory() as repo:
            root = Path(repo)
            (root / ".git").mkdir()
            tasks_dir = root / "doc" / "harness" / "tasks"
            task_dir = tasks_dir / "TASK__codex-review-stale"
            task_dir.mkdir(parents=True)
            (tasks_dir / ".active").write_text(str(task_dir), encoding="utf-8")
            (task_dir / "TASK_STATE.yaml").write_text(
                "task_id: TASK__codex-review-stale\nstatus: created\nruntime_verdict: pending\n"
                "touched_paths:\n  - src/main.py\nplan_session_state: closed\nclosed_at: null\nupdated: now\n",
                encoding="utf-8",
            )
            source = root / "src/main.py"
            source.parent.mkdir()
            source.write_text("VALUE = 1\n", encoding="utf-8")
            spawn = {"cwd": repo, "tool_name": "spawn_agent", "tool_input": {"task_name": "code_review"}, "tool_response": {"task_name": "/root/code_review"}}
            with mock.patch.object(sys, "stdin", _BytesStdin(json.dumps(spawn))):
                with contextlib.redirect_stdout(io.StringIO()):
                    mod.main()
            source.write_text("VALUE = 2\n", encoding="utf-8")
            wait = {"cwd": repo, "tool_name": "wait_agent", "tool_input": {"target": "/root/code_review"}, "tool_response": {"result": "VERDICT: PASS\nFINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0"}}
            with mock.patch.object(sys, "stdin", _BytesStdin(json.dumps(wait))):
                with contextlib.redirect_stdout(io.StringIO()):
                    mod.main()

            receipts = [json.loads(line) for line in (task_dir / "REVIEW_RECEIPTS.jsonl").read_text().splitlines()]
            self.assertEqual(receipts[-1]["verdict"], "PENDING")

    def test_post_tool_use_rejects_unmatched_wait_agent_completion(self):
        mod = _load("hook_post_tool_use")

        with tempfile.TemporaryDirectory() as repo:
            root = Path(repo)
            (root / ".git").mkdir()
            tasks_dir = root / "doc" / "harness" / "tasks"
            task_dir = tasks_dir / "TASK__codex-unmatched"
            task_dir.mkdir(parents=True)
            (tasks_dir / ".active").write_text(str(task_dir), encoding="utf-8")
            payload = {
                "cwd": repo,
                "tool_name": "collaboration.wait_agent",
                "tool_input": {"target": "qa_cli"},
                "tool_response": {"result": "VERDICT: PASS\nnot correlated"},
            }
            with mock.patch.object(sys, "stdin", _BytesStdin(json.dumps(payload))):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(mod.main(), 0)

            self.assertFalse((task_dir / "SUBAGENT_RECEIPTS.jsonl").exists())

    def test_pre_tool_use_skips_receipt_when_structured_task_id_mismatches_active_task(self):
        mod = _load("hook_pre_tool_use")

        def fake_run(*args, **kwargs):
            return subprocess.CompletedProcess(args[0], 0, stdout=b"", stderr=b"")

        with tempfile.TemporaryDirectory() as repo:
            root = Path(repo)
            (root / ".git").mkdir()
            tasks_dir = root / "doc" / "harness" / "tasks"
            task_dir = tasks_dir / "TASK__active"
            task_dir.mkdir(parents=True)
            (tasks_dir / ".active").write_text(str(task_dir), encoding="utf-8")
            payload = {
                "cwd": repo,
                "tool_name": "spawn_agent",
                "tool_call_id": "call-other",
                "tool_input": {
                    "task_id": "TASK__other",
                    "agent_type": "harness:qa-cli",
                    "message": "Run QA for TASK__other.",
                },
            }
            with mock.patch("subprocess.run", side_effect=fake_run):
                with mock.patch.object(sys, "stdin", _BytesStdin(json.dumps(payload))):
                    with contextlib.redirect_stdout(io.StringIO()):
                        self.assertEqual(mod.main(), 0)

            self.assertFalse((task_dir / "SUBAGENT_RECEIPTS.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
