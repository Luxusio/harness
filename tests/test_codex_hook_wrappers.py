from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
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
    def test_session_start_runs_children_from_payload_cwd(self):
        mod = _load("hook_session_start")
        calls: list[dict] = []

        def fake_run(*args, **kwargs):
            kwargs["cmd"] = args[0]
            calls.append(kwargs)
            return subprocess.CompletedProcess(args[0], 0, stdout=b"", stderr=b"")

        with tempfile.TemporaryDirectory() as repo:
            payload = json.dumps({"cwd": repo})
            with mock.patch("subprocess.run", side_effect=fake_run):
                with mock.patch.object(sys, "stdin", _BytesStdin(payload)):
                    with contextlib.redirect_stdout(io.StringIO()):
                        self.assertEqual(mod.main(), 0)

        self.assertTrue(calls)
        self.assertTrue(all(call.get("cwd") == repo for call in calls))
        child_names = [Path(call["cmd"][1]).name for call in calls]
        self.assertNotIn("hygiene_scan.py", child_names)
        self.assertNotIn("inject_checkpoint.py", child_names)
        self.assertNotIn("contract_lint.py", child_names)

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

    def test_pre_tool_use_records_codex_subagent_start_receipt(self):
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

            receipt_path = task_dir / "SUBAGENT_RECEIPTS.jsonl"
            self.assertTrue(receipt_path.is_file())
            receipt = json.loads(receipt_path.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(receipt["source"], "subagent_start_hook")
            self.assertEqual(receipt["status"], "started")
            self.assertEqual(receipt["agent_id"], "call-123")
            self.assertEqual(receipt["agent_type"], "harness:qa-cli")
            self.assertEqual(receipt["lens"], "qa-cli")

    def test_pre_tool_use_infers_harness_agent_type_from_message(self):
        mod = _load("hook_pre_tool_use")

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
            }
            with mock.patch("subprocess.run", side_effect=fake_run):
                with mock.patch.object(sys, "stdin", _BytesStdin(json.dumps(payload))):
                    with contextlib.redirect_stdout(io.StringIO()):
                        self.assertEqual(mod.main(), 0)

            receipt = json.loads((task_dir / "SUBAGENT_RECEIPTS.jsonl").read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(receipt["agent_type"], "harness:qa-browser")
            self.assertEqual(receipt["lens"], "qa-browser")


if __name__ == "__main__":
    unittest.main()
