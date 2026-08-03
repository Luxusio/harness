"""Tests for the plugin-local harness MCP server."""

import importlib.util
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
SERVER_PATH = REPO_ROOT / "plugin" / "mcp" / "harness_server.py"


spec = importlib.util.spec_from_file_location("harness_server", SERVER_PATH)
assert spec and spec.loader
harness_server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness_server)
import _lib as harness_lib  # type: ignore


EXPECTED_TOOLS = {
    "goal_start",
    "goal_context",
    "goal_add_task",
    "goal_next_task",
    "goal_finish",
    "task_start",
    "task_context",
    "task_verify",
    "task_close",
    "task_blocked",
    "write_plan",
}


class HarnessMcpServerTests(unittest.TestCase):
    def _run_git(self, cwd: str, *args: str) -> None:
        subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        )

    def _make_task(self, base_dir: str, task_id: str) -> str:
        task_dir = Path(base_dir) / "doc" / "harness" / "tasks" / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        (task_dir / "TASK_STATE.yaml").write_text(
            "\n".join(
                [
                    f"task_id: {task_id}",
                    "status: created",
                    "runtime_verdict: pending",
                    "touched_paths: []",
                    "plan_session_state: closed",
                    "closed_at: null",
                    "updated: 2026-01-01T00:00:00Z",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        (task_dir / "PLAN.md").write_text("# Plan\n\nSmall plan.\n", encoding="utf-8")
        return str(task_dir)

    def _call_in_repo(self, repo_root: str, name: str, args: dict) -> dict:
        with mock.patch.object(harness_server, "find_repo_root", return_value=repo_root):
            return harness_server.call_tool(name, args)

    def _write_subagent_receipt(
        self,
        task_dir: str,
        *,
        agent_id: str = "agent-1",
        agent_type: str = "harness:qa-cli",
        source: str = "subagent_start_hook",
    ) -> None:
        receipt = {
            "receipt_id": f"subagent-{agent_id}",
            "ts": "2026-01-01T00:00:01Z",
            "kind": "subagent",
            "source": source,
            "status": "completed",
            "task_id": Path(task_dir).name,
            "agent_id": agent_id,
            "agent_type": agent_type,
            "lens": "qa-cli",
            "verdict": "PASS",
            "summary": "VERDICT: PASS",
            "transcript_path": "",
            "transcript_sha256": "",
            "prompt_hash": "",
            "head_sha": harness_server._git_head_for_receipt(task_dir),
            "diff_fingerprint": harness_lib.review_diff_fingerprint(task_dir),
        }
        (Path(task_dir) / "SUBAGENT_RECEIPTS.jsonl").write_text(
            json.dumps(receipt, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def test_server_info_is_harness(self):
        self.assertEqual(harness_server.SERVER_INFO["name"], "harness")
        self.assertEqual(harness_server.SERVER_INFO["title"], "harness Control Plane")

    def test_tool_registry_matches_expected_tool_surface(self):
        tools = {tool["name"] for tool in harness_server.list_tools()}
        self.assertEqual(tools, EXPECTED_TOOLS)

    def test_each_tool_has_description_and_schema(self):
        for tool in harness_server.list_tools():
            self.assertIn("name", tool)
            self.assertIn("description", tool)
            self.assertIn("inputSchema", tool)
            self.assertTrue(tool["description"], f"{tool['name']} missing description")

    def test_start_only_receipt_does_not_produce_runtime_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__start-only")
            receipt = {
                "kind": "subagent",
                "status": "started",
                "task_id": "TASK__start-only",
                "agent_id": "qa-1",
                "agent_type": "harness:qa-cli",
                "lens": "qa-cli",
                "verdict": "",
                "ts": "2099-01-01T00:00:00Z",
            }
            (Path(task_dir) / "SUBAGENT_RECEIPTS.jsonl").write_text(
                json.dumps(receipt) + "\n", encoding="utf-8"
            )
            self.assertEqual(harness_server.receipt_runtime_verdict(task_dir), "PENDING")

    def test_completed_qa_fail_controls_runtime_verdict(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__qa-fail")
            harness_server.record_subagent_receipt(
                task_dir,
                {
                    "agent_id": "qa-1",
                    "agent_type": "harness:qa-cli",
                    "status": "completed",
                    "verdict": "FAIL",
                    "summary": "VERDICT: FAIL",
                },
            )
            self.assertEqual(harness_server.receipt_runtime_verdict(task_dir), "FAIL")

    def test_new_qa_start_invalidates_older_completed_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__qa-restarted")
            self._write_subagent_receipt(task_dir, agent_id="qa-old")
            harness_server.record_subagent_receipt(
                task_dir,
                {
                    "agent_id": "qa-new",
                    "agent_type": "harness:qa-cli",
                    "status": "started",
                    "summary": "rerun started",
                },
            )
            self.assertEqual(harness_server.receipt_runtime_verdict(task_dir), "PENDING")

    def test_unknown_tool_returns_error_payload(self):
        result = harness_server.call_tool("does_not_exist", {})
        self.assertTrue(result.get("isError"))
        self.assertIn("Unknown tool", result["structuredContent"]["error"])

    def test_git_binding_error_returns_structured_recovery_details(self):
        original = harness_server.TOOLS["task_context"]["handler"]

        def fail(_args):
            raise harness_lib.GitBindingError(
                "REGISTERED_WORKTREE_BINDING_MISMATCH",
                "registered source failed validation",
                path="services/front",
                invariant="admin_gitdir_backreference",
                next_action="Repair the worktree and retry.",
            )

        harness_server.TOOLS["task_context"]["handler"] = fail
        try:
            result = harness_server.call_tool("task_context", {"task_id": "TASK__x"})
        finally:
            harness_server.TOOLS["task_context"]["handler"] = original

        self.assertTrue(result["isError"])
        self.assertEqual(
            result["structuredContent"]["error_code"],
            "REGISTERED_WORKTREE_BINDING_MISMATCH",
        )
        self.assertEqual(result["structuredContent"]["path"], "services/front")
        self.assertEqual(
            result["structuredContent"]["invariant"], "admin_gitdir_backreference"
        )

    def test_goal_tools_manage_active_goal_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            original_find_repo_root = harness_server.find_repo_root
            harness_server.find_repo_root = lambda *a, **kw: tmp
            try:
                start = harness_server.call_tool(
                    "goal_start",
                    {
                        "objective": "Fix every bug on the login page",
                        "source": {"runtime": "codex"},
                    },
                )
                self.assertNotIn("isError", start)
                goal = start["structuredContent"]["goal"]
                self.assertEqual(goal["status"], "active")
                self.assertNotIn("strategy", goal)

                add = harness_server.call_tool(
                    "goal_add_task",
                    {
                        "task_id": "TASK__login-bugs",
                        "title": "Audit and fix login bugs",
                        "status": "closed",
                    },
                )
                self.assertNotIn("isError", add)

                task_dir = Path(tmp) / "doc/harness/tasks/TASK__login-bugs"
                task_dir.mkdir(parents=True, exist_ok=True)
                (task_dir / "TASK_STATE.yaml").write_text(
                    "task_id: TASK__login-bugs\nstatus: closed\n"
                    "runtime_verdict: PASS\ntouched_paths: []\n"
                    "closed_at: 2026-01-01T00:00:02Z\n",
                    encoding="utf-8",
                )
                self._write_subagent_receipt(str(task_dir))
                harness_server.write_task_close_attestation(
                    str(task_dir),
                    harness_server.read_state(str(task_dir)),
                    head_sha="a" * 40,
                    receipt_fingerprint=harness_server.receipt_stream_fingerprint(str(task_dir)),
                )

                nxt = harness_server.call_tool("goal_next_task", {})
                self.assertIsNone(nxt["structuredContent"]["task"])

                finish = harness_server.call_tool("goal_finish", {"status": "complete"})
                self.assertEqual(finish["structuredContent"]["goal"]["status"], "complete")
                self.assertTrue((Path(tmp) / "doc" / "harness" / "goals" / "current.json").is_file())
            finally:
                harness_server.find_repo_root = original_find_repo_root

    def test_goal_completion_rejects_unfinished_child_and_restart_reactivates(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(self._make_task(tmp, "TASK__goal-child"))
            self._call_in_repo(tmp, "goal_start", {
                "objective": "finish safely", "goal_id": "GOAL__finish-safely",
            })
            self._call_in_repo(tmp, "goal_add_task", {
                "task_id": "TASK__goal-child", "status": "active",
            })

            blocked = self._call_in_repo(tmp, "goal_finish", {"status": "complete"})
            self.assertTrue(blocked.get("isError"))
            self.assertIn("TASK__goal-child", blocked["structuredContent"]["error"])

            state = task_dir / "TASK_STATE.yaml"
            state.write_text(
                state.read_text(encoding="utf-8")
                .replace("status: created", "status: closed")
                .replace("runtime_verdict: pending", "runtime_verdict: PASS"),
                encoding="utf-8",
            )
            missing_receipt = self._call_in_repo(tmp, "goal_finish", {"status": "complete"})
            self.assertTrue(missing_receipt.get("isError"))
            self._write_subagent_receipt(str(task_dir))
            state.write_text(
                state.read_text(encoding="utf-8")
                .replace("closed_at: null", "closed_at: 2026-01-01T00:00:02Z"),
                encoding="utf-8",
            )
            harness_server.write_task_close_attestation(
                str(task_dir),
                harness_server.read_state(str(task_dir)),
                head_sha="a" * 40,
                receipt_fingerprint=harness_server.receipt_stream_fingerprint(str(task_dir)),
            )
            self._call_in_repo(tmp, "goal_add_task", {
                "task_id": "TASK__goal-child", "status": "closed",
            })
            finished = self._call_in_repo(tmp, "goal_finish", {"status": "complete"})
            self.assertEqual(finished["structuredContent"]["goal"]["status"], "complete")

            restarted = self._call_in_repo(tmp, "goal_start", {
                "objective": "finish safely", "goal_id": "GOAL__finish-safely",
            })
            goal = restarted["structuredContent"]["goal"]
            self.assertEqual(goal["status"], "active")
            self.assertNotIn("finished_at", goal)

    def test_goal_completion_rejects_hand_labeled_closed_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(self._make_task(tmp, "TASK__closed-child"))
            self._call_in_repo(tmp, "goal_start", {"objective": "finish multiple children"})
            self._call_in_repo(tmp, "goal_add_task", {
                "task_id": "TASK__closed-child", "status": "closed",
            })
            state = task_dir / "TASK_STATE.yaml"
            state.write_text(
                state.read_text(encoding="utf-8")
                .replace("status: created", "status: closed")
                .replace("runtime_verdict: pending", "runtime_verdict: PASS")
                .replace("closed_at: null", "closed_at: 2026-01-01T00:00:01Z"),
                encoding="utf-8",
            )

            finished = self._call_in_repo(tmp, "goal_finish", {"status": "complete"})

            self.assertTrue(finished.get("isError"))
            self.assertIn("TASK__closed-child", finished["structuredContent"]["error"])

    def test_goal_completion_requires_at_least_one_child(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._call_in_repo(tmp, "goal_start", {"objective": "empty goal"})
            result = self._call_in_repo(tmp, "goal_finish", {"status": "complete"})
            self.assertTrue(result.get("isError"))
            self.assertIn("no child tasks", result["structuredContent"]["error"])

    def test_terminal_goal_rejects_child_mutation_and_repeat_finish_until_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._call_in_repo(tmp, "goal_start", {
                "objective": "terminal guard", "goal_id": "GOAL__terminal-guard",
            })
            blocked = self._call_in_repo(tmp, "goal_finish", {"status": "blocked"})
            self.assertEqual(blocked["structuredContent"]["goal"]["status"], "blocked")

            add = self._call_in_repo(tmp, "goal_add_task", {
                "task_id": "TASK__must-restart", "status": "queued",
            })
            self.assertTrue(add.get("isError"))
            self.assertIn("call goal_start explicitly", add["structuredContent"]["error"])

            finish = self._call_in_repo(tmp, "goal_finish", {"status": "blocked"})
            self.assertTrue(finish.get("isError"))
            self.assertIn("call goal_start explicitly", finish["structuredContent"]["error"])

            restarted = self._call_in_repo(tmp, "goal_start", {
                "objective": "terminal guard", "goal_id": "GOAL__terminal-guard",
            })
            self.assertEqual(restarted["structuredContent"]["goal"]["status"], "active")
            add_after_restart = self._call_in_repo(tmp, "goal_add_task", {
                "task_id": "TASK__must-restart", "status": "queued",
            })
            self.assertNotIn("isError", add_after_restart)

    def test_goal_start_rejects_prefixed_traversal_before_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._call_in_repo(
                tmp,
                "goal_start",
                {"objective": "safe objective", "goal_id": "GOAL__/../../../escaped"},
            )
            self.assertTrue(result.get("isError"))
            self.assertIn("goal_id", result["structuredContent"]["error"])
            self.assertFalse((Path(tmp) / "escaped.json").exists())

    def test_goal_start_rejects_symlinked_storage_roots(self):
        for symlink_parent in (False, True):
            with self.subTest(symlink_parent=symlink_parent), tempfile.TemporaryDirectory() as tmp:
                outside = Path(tmp) / "outside-goals"
                outside.mkdir()
                if symlink_parent:
                    (Path(tmp) / "doc").symlink_to(outside, target_is_directory=True)
                else:
                    harness_dir = Path(tmp) / "doc" / "harness"
                    harness_dir.mkdir(parents=True)
                    (harness_dir / "goals").symlink_to(outside, target_is_directory=True)
                result = self._call_in_repo(
                    tmp,
                    "goal_start",
                    {"objective": "safe objective", "goal_id": "GOAL__safe"},
                )
                self.assertTrue(result.get("isError"))
                self.assertEqual(result["structuredContent"]["field"], "goal_storage_root")
                self.assertEqual(result["structuredContent"]["rejected_value"], repr("doc/harness/goals"))
                self.assertIn("non-symlink", result["structuredContent"]["expected"])
                self.assertIn("Restore", result["structuredContent"]["next_action"])
                self.assertFalse((outside / "GOAL__safe.json").exists())
                self.assertFalse((outside / "current.json").exists())

    def test_goal_state_replaces_leaf_symlinks_without_touching_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            goals = Path(tmp) / "doc" / "harness" / "goals"
            goals.mkdir(parents=True)
            sentinel = Path(tmp) / "goal-sentinel"
            sentinel.write_text("keep", encoding="utf-8")
            for name in ("GOAL__safe.json", "GOAL__safe.json.tmp", "current.json"):
                (goals / name).symlink_to(sentinel)
            result = self._call_in_repo(
                tmp,
                "goal_start",
                {"objective": "safe objective", "goal_id": "GOAL__safe"},
            )
            self.assertNotIn("isError", result)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")
            self.assertFalse((goals / "GOAL__safe.json").is_symlink())
            self.assertFalse((goals / "current.json").is_symlink())

    def test_goal_readers_ignore_valid_json_leaf_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            goals = Path(tmp) / "doc" / "harness" / "goals"
            goals.mkdir(parents=True)
            external = Path(tmp) / "external-goal.json"
            external.write_text(
                json.dumps({
                    "goal_id": "GOAL__safe",
                    "objective": "SENSITIVE_VALUE",
                    "status": "active",
                    "source": {"secret": "SENSITIVE_VALUE"},
                    "tasks": [{"task_id": "TASK__foreign"}],
                }),
                encoding="utf-8",
            )

            (goals / "current.json").symlink_to(external)
            context = self._call_in_repo(tmp, "goal_context", {})
            self.assertNotIn("SENSITIVE_VALUE", json.dumps(context))

            (goals / "current.json").unlink()
            (goals / "GOAL__safe.json").symlink_to(external)
            started = self._call_in_repo(
                tmp,
                "goal_start",
                {"objective": "safe objective", "goal_id": "GOAL__safe"},
            )
            self.assertNotIn("isError", started)
            self.assertNotIn("SENSITIVE_VALUE", json.dumps(started))
            self.assertFalse((goals / "GOAL__safe.json").is_symlink())

    def test_goal_add_task_rejects_outside_task_dir_before_persisting(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._call_in_repo(tmp, "goal_start", {"objective": "safe objective"})
            current = Path(tmp) / "doc" / "harness" / "goals" / "current.json"
            before = current.read_bytes()
            result = self._call_in_repo(
                tmp,
                "goal_add_task",
                {"task_id": "TASK__safe", "task_dir": str(Path(tmp) / "outside")},
            )
            self.assertTrue(result.get("isError"))
            self.assertEqual(current.read_bytes(), before)

    def test_task_start_rejects_traversal_and_outside_paths_without_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            git_dir = Path(tmp) / ".git"
            git_dir.mkdir()
            index_lock = git_dir / "index.lock"
            index_lock.write_bytes(b"")
            escaped = Path(tmp).parent / "escaped-task"
            outside = Path(tmp) / "outside"
            for args in (
                {"task_id": "TASK__/../../../escaped-task"},
                {"task_dir": str(outside)},
                {"task_dir": "doc/harness/tasks/../tasks/TASK__safe"},
                {"task_id": "TASK__safe\n"},
            ):
                result = self._call_in_repo(tmp, "task_start", args)
                self.assertTrue(result.get("isError"), args)
                self.assertIn("canonical", result["structuredContent"]["error"])
                self.assertIn("next_action", result["structuredContent"])
            self.assertFalse(escaped.exists())
            self.assertFalse(outside.exists())
            self.assertTrue(index_lock.exists(), "invalid selectors must not clean repository locks")

    def test_task_start_blocks_unborn_git_repo_without_orphan_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init", "-q"], cwd=tmp, check=True)
            result = self._call_in_repo(
                tmp, "task_start", {"task_id": "TASK__unborn-baseline"},
            )
            self.assertTrue(result.get("isError"))
            self.assertIn("baseline capture unavailable", result["structuredContent"]["error"])
            state = Path(tmp) / "doc/harness/tasks/TASK__unborn-baseline/TASK_STATE.yaml"
            self.assertFalse(state.exists())

    def test_task_start_never_removes_repository_index_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._run_git(tmp, "init", "-q")
            self._run_git(tmp, "config", "user.email", "a@b")
            self._run_git(tmp, "config", "user.name", "a")
            (Path(tmp) / "README.md").write_text("# repo\n", encoding="utf-8")
            self._run_git(tmp, "add", "README.md")
            self._run_git(tmp, "commit", "-qm", "init")
            lock = Path(tmp) / ".git/index.lock"
            lock.write_bytes(b"")

            result = self._call_in_repo(
                tmp, "task_start", {"task_id": "TASK__preserve-index-lock"},
            )

            self.assertNotIn("isError", result)
            self.assertTrue(lock.exists())

    def test_task_start_fails_closed_for_invalid_resumed_baseline(self):
        cases = ("missing", "corrupt", "symlink", "mismatched-root")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                self._run_git(tmp, "init", "-q")
                self._run_git(tmp, "config", "user.email", "a@b")
                self._run_git(tmp, "config", "user.name", "a")
                (Path(tmp) / "README.md").write_text("# repo\n", encoding="utf-8")
                self._run_git(tmp, "add", "README.md")
                self._run_git(tmp, "commit", "-qm", "init")
                task_dir = Path(tmp) / f"doc/harness/tasks/TASK__invalid-{case}"
                lib_globals = harness_server.ensure_task_scaffold.__globals__
                lib_globals["ensure_task_scaffold"](
                    str(task_dir), f"TASK__invalid-{case}"
                )
                baseline = task_dir / "TASK_BASELINE.json"
                if case == "missing":
                    baseline.unlink()
                elif case == "corrupt":
                    baseline.write_text("{not json", encoding="utf-8")
                elif case == "symlink":
                    outside = Path(tmp) / "outside-baseline.json"
                    outside.write_bytes(baseline.read_bytes())
                    baseline.unlink()
                    baseline.symlink_to(outside)
                else:
                    data = json.loads(baseline.read_text(encoding="utf-8"))
                    data["repo_root"] = str(Path(tmp) / "other")
                    baseline.write_text(json.dumps(data), encoding="utf-8")

                result = self._call_in_repo(
                    tmp,
                    "task_start",
                    {"task_id": f"TASK__invalid-{case}"},
                )

                self.assertTrue(result.get("isError"))
                self.assertNotEqual(
                    result["structuredContent"].get("start_status"),
                    "ready_with_warnings",
                )
                self.assertFalse((task_dir.parent / ".active").exists())
                self.assertFalse((task_dir.parent / ".active_sessions").exists())

    def test_task_start_rejects_mismatched_existing_state_before_side_effects(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(self._make_task(tmp, "TASK__expected"))
            state = task_dir / "TASK_STATE.yaml"
            state.write_text(
                state.read_text(encoding="utf-8").replace(
                    "task_id: TASK__expected", "task_id: TASK__other"
                ),
                encoding="utf-8",
            )
            git_dir = Path(tmp) / ".git"
            git_dir.mkdir()
            lock = git_dir / "index.lock"
            lock.write_bytes(b"")
            before = {path.name: path.read_bytes() for path in task_dir.iterdir() if path.is_file()}

            result = self._call_in_repo(tmp, "task_start", {"task_id": "TASK__expected"})

            self.assertTrue(result.get("isError"))
            self.assertIn("does not match", result["structuredContent"]["error"])
            after = {path.name: path.read_bytes() for path in task_dir.iterdir() if path.is_file()}
            self.assertEqual(after, before)
            self.assertTrue(lock.exists())
            self.assertFalse((task_dir.parent / ".active").exists())
            self.assertFalse((task_dir.parent / ".active_sessions").exists())

    def test_task_selectors_accept_canonical_paths_and_reject_mismatch_or_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(self._make_task(tmp, "TASK__safe"))
            relative = task.relative_to(tmp).as_posix()
            self.assertEqual(
                harness_server.canonical_task_dir(task_dir=relative, repo_root=tmp),
                str(task),
            )
            self.assertEqual(
                harness_server.canonical_task_dir(
                    task_id="safe", task_dir=str(task), repo_root=tmp
                ),
                str(task),
            )
            with self.assertRaisesRegex(ValueError, "disagree"):
                harness_server.canonical_task_dir(
                    task_id="TASK__other", task_dir=str(task), repo_root=tmp
                )
            outside = Path(tmp) / "outside-target"
            outside.mkdir()
            alias = task.parent / "TASK__alias"
            alias.symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "real directory"):
                harness_server.canonical_task_dir(task_dir=str(alias), repo_root=tmp)
            for tool_args in (
                ("task_start", {"task_id": "TASK__alias"}),
                ("write_plan", {"task_id": "TASK__alias", "plan": "# escaped\n"}),
                ("task_blocked", {
                    "task_id": "TASK__alias",
                    "blocked_reason": "x",
                    "unblock_condition": "y",
                }),
            ):
                result = self._call_in_repo(tmp, tool_args[0], tool_args[1])
                self.assertTrue(result.get("isError"), tool_args[0])
                self.assertEqual(result["structuredContent"]["field"], "task_id")
                self.assertEqual(result["structuredContent"]["rejected_value"], repr("TASK__alias"))
                self.assertIn("TASK__", result["structuredContent"]["expected"])
                self.assertIn("next_action", result["structuredContent"])
            self.assertEqual(list(outside.iterdir()), [])

        with tempfile.TemporaryDirectory() as tmp:
            harness_dir = Path(tmp) / "doc" / "harness"
            harness_dir.mkdir(parents=True)
            outside = Path(tmp) / "outside-task-root"
            outside.mkdir()
            (harness_dir / "tasks").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "task root"):
                harness_server.canonical_task_dir(task_id="TASK__safe", repo_root=tmp)

        with tempfile.TemporaryDirectory() as tmp:
            outside = Path(tmp) / "outside-doc"
            outside.mkdir()
            (Path(tmp) / "doc").symlink_to(outside, target_is_directory=True)
            result = self._call_in_repo(tmp, "task_start", {"task_id": "TASK__escape"})
            self.assertTrue(result.get("isError"))
            self.assertFalse((outside / "harness" / "tasks" / "TASK__escape").exists())

    def test_stdio_transport_accepts_content_length_frames(self):
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-11-25"},
        }
        body = json.dumps(request).encode()
        stdin = io.TextIOWrapper(
            io.BytesIO(b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body),
            encoding="utf-8",
        )
        stdout_bytes = io.BytesIO()
        stdout = io.TextIOWrapper(stdout_bytes, encoding="utf-8")

        server = harness_server.McpServer()
        with (
            mock.patch.dict(harness_server.os.environ, {"HARNESS_RUNTIME": ""}),
            mock.patch.object(harness_server.sys, "stdin", stdin),
            mock.patch.object(harness_server.sys, "stdout", stdout),
        ):
            server.handle_request(server._read())
            stdout.flush()
            server.close()

        raw = stdout_bytes.getvalue()
        self.assertTrue(raw.startswith(b"Content-Length: "), raw)
        response_body = raw.split(b"\r\n\r\n", 1)[1]
        response = json.loads(response_body.decode())
        self.assertEqual(response["id"], 1)
        self.assertEqual(response["result"]["serverInfo"]["name"], "harness")

    def test_initialize_instructions_match_current_codex_mcp_contract(self):
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-11-25", "clientInfo": {"name": "codex-cli"}},
        }
        stdin = io.TextIOWrapper(io.BytesIO(json.dumps(request).encode() + b"\n"), encoding="utf-8")
        stdout_bytes = io.BytesIO()
        stdout = io.TextIOWrapper(stdout_bytes, encoding="utf-8")

        server = harness_server.McpServer()
        with (
            mock.patch.dict(harness_server.os.environ, {"HARNESS_RUNTIME": ""}),
            mock.patch.object(harness_server.sys, "stdin", stdin),
            mock.patch.object(harness_server.sys, "stdout", stdout),
        ):
            server.handle_request(server._read())
            stdout.flush()

        response = json.loads(stdout_bytes.getvalue().decode())
        server.close()
        instructions = response["result"]["instructions"]
        self.assertIn("Goal-first control plane", instructions)
        self.assertIn("goal_start", instructions)
        self.assertIn("plain repo-mutating request", instructions)
        self.assertIn("hooks do not create tasks automatically", instructions)
        self.assertIn("bare tool names", instructions)
        self.assertIn("Codex callers should use these bare tool names directly", instructions)
        self.assertIn("get_goal", instructions)
        self.assertIn("write_plan", instructions)
        self.assertNotIn("write_plan_artifact", instructions)
        self.assertNotIn("write_handoff", instructions)
        self.assertNotIn("write_doc_sync", instructions)
        self.assertNotIn("write_req_doc", instructions)
        self.assertNotIn("record_attempt", instructions)
        self.assertNotIn("7 tools", instructions)
        self.assertIn("mcp__plugin_harness_harness__", instructions)
        self.assertIn("do not use Claude display prefixes", instructions)
        self.assertNotIn("write_critic_runtime", instructions)

    def test_codex_initialize_hosts_and_closes_watcher_manager(self):
        manager = mock.Mock()
        manager.start.return_value = manager
        server = harness_server.McpServer()
        with (
            mock.patch.object(harness_server, "_WatcherManager", return_value=manager) as factory,
            mock.patch.object(harness_server, "find_repo_root", return_value="/trusted/repo"),
            mock.patch.object(server, "_reply"),
        ):
            server.handle_request({
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"clientInfo": {"name": "codex-cli"}},
            })
            server.close()
        factory.assert_called_once_with("/trusted/repo")
        manager.start.assert_called_once_with()
        manager.stop.assert_called_once_with()

    def test_watcher_manager_failure_does_not_break_codex_initialize(self):
        server = harness_server.McpServer()
        with (
            mock.patch.object(harness_server, "_WatcherManager", side_effect=RuntimeError("boom")),
            mock.patch.object(server, "_reply") as reply,
        ):
            server.handle_request({
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"clientInfo": {"name": "codex-cli"}},
            })
        self.assertIsNone(server.watcher_manager)
        reply.assert_called_once()

    def test_initialize_instructions_match_current_claude_mcp_contract(self):
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-11-25", "clientInfo": {"name": "claude-code"}},
        }
        stdin = io.TextIOWrapper(io.BytesIO(json.dumps(request).encode() + b"\n"), encoding="utf-8")
        stdout_bytes = io.BytesIO()
        stdout = io.TextIOWrapper(stdout_bytes, encoding="utf-8")

        server = harness_server.McpServer()
        with (
            mock.patch.dict(harness_server.os.environ, {"HARNESS_RUNTIME": ""}),
            mock.patch.object(harness_server.sys, "stdin", stdin),
            mock.patch.object(harness_server.sys, "stdout", stdout),
        ):
            server.handle_request(server._read())
            stdout.flush()

        response = json.loads(stdout_bytes.getvalue().decode())
        instructions = response["result"]["instructions"]
        self.assertIn("Goal-first control plane", instructions)
        self.assertIn("goal_context", instructions)
        self.assertIn("plain repo-mutating request", instructions)
        self.assertIn("Protocol tool names are bare", instructions)
        self.assertIn("Claude Code may display callable tools with a runtime prefix", instructions)
        self.assertNotIn("7 tools", instructions)
        self.assertNotIn("write_critic_runtime", instructions)

    def test_stdio_transport_accepts_lowercase_content_length_with_extra_headers(self):
        request = {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "ping",
            "params": {},
        }
        body = json.dumps(request).encode()
        frame = (
            b"content-length: "
            + str(len(body)).encode()
            + b"\r\nx-test-header: ignored\r\n\r\n"
            + body
        )
        stdin = io.TextIOWrapper(io.BytesIO(frame), encoding="utf-8")
        stdout_bytes = io.BytesIO()
        stdout = io.TextIOWrapper(stdout_bytes, encoding="utf-8")

        server = harness_server.McpServer()
        with mock.patch.object(harness_server.sys, "stdin", stdin), mock.patch.object(harness_server.sys, "stdout", stdout):
            server.handle_request(server._read())
            stdout.flush()

        raw = stdout_bytes.getvalue()
        self.assertTrue(raw.startswith(b"Content-Length: "), raw)
        response = json.loads(raw.split(b"\r\n\r\n", 1)[1].decode())
        self.assertEqual(response, {"jsonrpc": "2.0", "id": 7, "result": {}})

    def test_stdio_transport_reads_multiple_content_length_frames_from_one_stream(self):
        initialize = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-11-25"},
        }
        tools_list = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {},
        }

        def frame(payload: dict) -> bytes:
            body = json.dumps(payload).encode()
            return b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body

        stdin = io.TextIOWrapper(io.BytesIO(frame(initialize) + frame(tools_list)), encoding="utf-8")
        stdout_bytes = io.BytesIO()
        stdout = io.TextIOWrapper(stdout_bytes, encoding="utf-8")

        server = harness_server.McpServer()
        with mock.patch.object(harness_server.sys, "stdin", stdin), mock.patch.object(harness_server.sys, "stdout", stdout):
            first = server._read()
            second = server._read()
            server.handle_request(first)
            server.handle_request(second)
            stdout.flush()

        raw = stdout_bytes.getvalue()
        parts = raw.split(b"Content-Length: ")
        self.assertEqual(len(parts), 3, raw)
        responses = []
        for part in parts[1:]:
            _, body = part.split(b"\r\n\r\n", 1)
            responses.append(json.loads(body.decode()))
        self.assertEqual([response["id"] for response in responses], [1, 2])
        tool_names = {tool["name"] for tool in responses[1]["result"]["tools"]}
        self.assertEqual(tool_names, EXPECTED_TOOLS)

    def test_stdio_transport_keeps_json_line_responses_for_json_line_requests(self):
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-11-25"},
        }
        stdin = io.TextIOWrapper(io.BytesIO(json.dumps(request).encode() + b"\n"), encoding="utf-8")
        stdout_bytes = io.BytesIO()
        stdout = io.TextIOWrapper(stdout_bytes, encoding="utf-8")

        server = harness_server.McpServer()
        with mock.patch.object(harness_server.sys, "stdin", stdin), mock.patch.object(harness_server.sys, "stdout", stdout):
            server.handle_request(server._read())
            stdout.flush()

        raw = stdout_bytes.getvalue()
        self.assertFalse(raw.startswith(b"Content-Length:"), raw)
        response = json.loads(raw.decode())
        self.assertEqual(response["id"], 1)
        self.assertEqual(response["result"]["serverInfo"]["name"], "harness")

    def test_stdio_transport_returns_none_for_header_without_content_length(self):
        stdin = io.TextIOWrapper(io.BytesIO(b"X-Test: ignored\r\n\r\n{}"), encoding="utf-8")

        server = harness_server.McpServer()
        with mock.patch.object(harness_server.sys, "stdin", stdin):
            self.assertIsNone(server._read())
        self.assertTrue(server.framed_stdio)

    def test_initialized_notification_sets_state_without_response(self):
        stdout_bytes = io.BytesIO()
        stdout = io.TextIOWrapper(stdout_bytes, encoding="utf-8")
        server = harness_server.McpServer()

        with mock.patch.object(harness_server.sys, "stdout", stdout):
            server.handle_request({"jsonrpc": "2.0", "method": "notifications/initialized"})
            stdout.flush()

        self.assertTrue(server.initialized)
        self.assertEqual(stdout_bytes.getvalue(), b"")

    def test_tools_call_requires_string_name(self):
        stdout_bytes = io.BytesIO()
        stdout = io.TextIOWrapper(stdout_bytes, encoding="utf-8")
        server = harness_server.McpServer()
        server.framed_stdio = False

        with mock.patch.object(harness_server.sys, "stdout", stdout):
            server.handle_request({"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {"name": 123}})
            stdout.flush()

        response = json.loads(stdout_bytes.getvalue().decode())
        self.assertEqual(response["id"], 9)
        self.assertEqual(response["error"]["code"], -32602)
        self.assertIn("Tool name must be a string", response["error"]["message"])

    def test_unknown_method_returns_jsonrpc_method_not_found(self):
        stdout_bytes = io.BytesIO()
        stdout = io.TextIOWrapper(stdout_bytes, encoding="utf-8")
        server = harness_server.McpServer()
        server.framed_stdio = False

        with mock.patch.object(harness_server.sys, "stdout", stdout):
            server.handle_request({"jsonrpc": "2.0", "id": 10, "method": "unknown/method"})
            stdout.flush()

        response = json.loads(stdout_bytes.getvalue().decode())
        self.assertEqual(response["id"], 10)
        self.assertEqual(response["error"]["code"], -32601)
        self.assertIn("Method not found", response["error"]["message"])

    def test_task_context_returns_structured_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__mcp")
            original_ctd = harness_server.canonical_task_dir
            harness_server.canonical_task_dir = lambda task_id=None, **kw: task_dir
            try:
                result = harness_server.call_tool("task_context", {"task_id": "TASK__mcp"})
            finally:
                harness_server.canonical_task_dir = original_ctd
            self.assertNotIn("isError", result)
            structured = result["structuredContent"]
            self.assertEqual(structured["task_context"]["task_id"], "TASK__mcp")

    def test_critic_tools_are_not_exposed(self):
        for tool in ("write_critic_document", "write_critic_qa", "write_critic_ux"):
            result = harness_server.call_tool(
                tool,
                {
                    "task_id": "TASK__removed",
                    "verdict": "PASS",
                    "summary": "self-authored pass",
                    "transcript": "not a receipt",
                },
            )
            self.assertTrue(result.get("isError"), tool)
            self.assertIn("Unknown tool", result["structuredContent"]["error"], tool)

    def test_task_verify_reconcile_skips_without_subagent_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__qapromote")
            (Path(task_dir) / "CHECKS.yaml").write_text(
                '- id: AC-001\n  title: "one"\n  status: open\n  kind: functional\n'
                '  last_updated: 2026-01-01T00:00:00Z\n  evidence: ""\n'
                '- id: AC-002\n  title: "two"\n  status: open\n  kind: functional\n'
                '  last_updated: 2026-01-01T00:00:00Z\n  evidence: ""\n',
                encoding="utf-8",
            )
            original_ctd = harness_server.canonical_task_dir
            harness_server.canonical_task_dir = lambda task_id=None, **kw: task_dir
            try:
                result = harness_server.call_tool(
                    "task_verify",
                    {"task_id": "TASK__qapromote", "reconcile_acs": True},
                )
            finally:
                harness_server.canonical_task_dir = original_ctd
            body = (Path(task_dir) / "CHECKS.yaml").read_text(encoding="utf-8")
        self.assertNotIn("isError", result)
        self.assertEqual(result["structuredContent"]["ac_reconcile"]["promoted_acs"], [])
        self.assertIn("QA completion", result["structuredContent"]["ac_reconcile"]["reason"])
        self.assertEqual(body.count("status: open"), 2)

    def test_task_verify_reconcile_promotes_open_acs_from_subagent_start_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__qareconcile")
            (Path(task_dir) / "CHECKS.yaml").write_text(
                '- id: AC-001\n  title: "one"\n  status: open\n  kind: functional\n'
                '  last_updated: 2026-01-01T00:00:00Z\n  evidence: ""\n'
                '- id: AC-002\n  title: "two"\n  status: open\n  kind: functional\n'
                '  last_updated: 2026-01-01T00:00:00Z\n  evidence: ""\n',
                encoding="utf-8",
            )
            self._write_subagent_receipt(task_dir)
            original_ctd = harness_server.canonical_task_dir
            harness_server.canonical_task_dir = lambda task_id=None, **kw: task_dir
            try:
                verify = harness_server.call_tool(
                    "task_verify",
                    {"task_id": "TASK__qareconcile", "reconcile_acs": True},
                )
            finally:
                harness_server.canonical_task_dir = original_ctd
            body = (Path(task_dir) / "CHECKS.yaml").read_text(encoding="utf-8")
        self.assertNotIn("isError", verify)
        self.assertEqual(verify["structuredContent"]["ac_reconcile"]["promoted_acs"], ["AC-001", "AC-002"])
        self.assertEqual(body.count("status: passed"), 2)
        self.assertIn("evidence: SUBAGENT_RECEIPTS.jsonl task_verify PASS", body)

    def test_task_verify_reconcile_skips_failed_deferred_and_without_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__qaskip")
            (Path(task_dir) / "CHECKS.yaml").write_text(
                '- id: AC-001\n  title: "open"\n  status: open\n  kind: functional\n'
                '  last_updated: 2026-01-01T00:00:00Z\n  evidence: ""\n'
                '- id: AC-002\n  title: "failed"\n  status: failed\n  kind: functional\n'
                '  last_updated: 2026-01-01T00:00:00Z\n  evidence: ""\n'
                '- id: AC-003\n  title: "deferred"\n  status: deferred\n  kind: functional\n'
                '  last_updated: 2026-01-01T00:00:00Z\n  evidence: ""\n',
                encoding="utf-8",
            )
            original_ctd = harness_server.canonical_task_dir
            harness_server.canonical_task_dir = lambda task_id=None, **kw: task_dir
            try:
                verify = harness_server.call_tool(
                    "task_verify",
                    {"task_id": "TASK__qaskip", "reconcile_acs": True},
                )
            finally:
                harness_server.canonical_task_dir = original_ctd
            body = (Path(task_dir) / "CHECKS.yaml").read_text(encoding="utf-8")
        self.assertEqual(verify["structuredContent"]["ac_reconcile"]["promoted_acs"], [])
        self.assertIn("QA completion", verify["structuredContent"]["ac_reconcile"]["reason"])
        self.assertIn("status: open", body)
        self.assertIn("status: failed", body)
        self.assertIn("status: deferred", body)

    def test_task_verify_reconcile_promotes_plan_writer_indented_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__qaindent")
            (Path(task_dir) / "CHECKS.yaml").write_text(
                "version: 1\nchecks:\n"
                "  - id: AC-001\n"
                "    description: one\n"
                "    status: open\n"
                "    evidence: []\n",
                encoding="utf-8",
            )
            self._write_subagent_receipt(task_dir)
            original_ctd = harness_server.canonical_task_dir
            harness_server.canonical_task_dir = lambda task_id=None, **kw: task_dir
            try:
                verify = harness_server.call_tool(
                    "task_verify",
                    {"task_id": "TASK__qaindent", "reconcile_acs": True},
                )
            finally:
                harness_server.canonical_task_dir = original_ctd
            body = (Path(task_dir) / "CHECKS.yaml").read_text(encoding="utf-8")
        self.assertEqual(verify["structuredContent"]["ac_reconcile"]["promoted_acs"], ["AC-001"])
        self.assertIn("status: passed", body)
        self.assertRegex(body, r"(?m)^    last_updated: 2026-")
        self.assertNotIn("P26-", body)

    def test_record_ac_evidence_is_not_exposed(self):
        result = harness_server.call_tool(
            "record_ac_evidence",
            {
                "task_id": "TASK__removed",
                "ac_id": "AC-001",
                "evidence": "self-authored claim",
            },
        )
        self.assertTrue(result.get("isError"))
        self.assertIn("Unknown tool", result["structuredContent"]["error"])

    def test_record_subagent_receipt_is_not_exposed_and_task_verify_surfaces_hook_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__subagentreceipt")
            self._write_subagent_receipt(
                task_dir,
                agent_id="agent-123",
                agent_type="harness:qa-cli",
            )
            original_ctd = harness_server.canonical_task_dir
            harness_server.canonical_task_dir = lambda task_id=None, **kw: task_dir
            try:
                result = harness_server.call_tool(
                    "record_subagent_receipt",
                    {
                        "task_id": "TASK__subagentreceipt",
                        "source": "spawn_agent",
                        "agent_id": "agent-123",
                        "agent_type": "harness:qa-cli",
                        "verdict": "PASS",
                        "summary": "qa-cli passed focused checks",
                    },
                )
                verify = harness_server.call_tool(
                    "task_verify",
                    {"task_id": "TASK__subagentreceipt"},
                )
                receipt_path = Path(task_dir) / "SUBAGENT_RECEIPTS.jsonl"
                receipt_exists = receipt_path.is_file()
                receipt = json.loads(receipt_path.read_text(encoding="utf-8").splitlines()[0]) if receipt_exists else {}
            finally:
                harness_server.canonical_task_dir = original_ctd
        self.assertTrue(result.get("isError"))
        self.assertIn("Unknown tool", result["structuredContent"]["error"])
        self.assertTrue(receipt_exists)
        self.assertEqual(receipt["agent_id"], "agent-123")
        self.assertEqual(receipt["lens"], "qa-cli")
        self.assertEqual(verify["structuredContent"]["subagent_receipts"]["count"], 1)
        self.assertEqual(verify["structuredContent"]["subagent_receipts"]["by_lens"]["qa-cli"], 1)
        self.assertEqual(verify["structuredContent"]["subagent_receipts"]["by_agent_type"]["harness:qa-cli"], 1)
        self.assertEqual(verify["structuredContent"]["subagent_receipts"]["by_source"]["subagent_start_hook"], 1)

    def test_micro_execution_mode_allows_no_plan_but_still_requires_verify(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp) / "doc" / "harness" / "tasks" / "TASK__micro"
            original_ctd = harness_server.canonical_task_dir
            original_root = harness_server.find_repo_root
            harness_server.canonical_task_dir = lambda task_id=None, slug=None, repo_root=None, **kw: str(task_dir)
            harness_server.find_repo_root = lambda *a, **kw: tmp
            try:
                start = harness_server.call_tool(
                    "task_start",
                    {"task_id": "TASK__micro", "execution_mode": "micro"},
                )
                close = harness_server.call_tool("task_close", {"task_id": "TASK__micro"})
            finally:
                harness_server.canonical_task_dir = original_ctd
                harness_server.find_repo_root = original_root
        ctx = start["structuredContent"]["task_context"]
        self.assertTrue(ctx["source_write_allowed"])
        self.assertEqual(ctx["routing"]["execution_mode"], "micro")
        self.assertNotIn("PLAN.md", ctx["missing_for_close"])
        self.assertIn("subagent", ctx["next_action"])
        self.assertTrue(close.get("isError"))
        self.assertIn("completed QA verdict: qa-cli", close["structuredContent"]["missing_for_close"])

    def test_task_blocked_records_pause_state_and_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__blocked")
            original_ctd = harness_server.canonical_task_dir
            original_root = harness_server.find_repo_root
            harness_server.canonical_task_dir = lambda task_id=None, **kw: task_dir
            harness_server.find_repo_root = lambda *a, **kw: tmp
            try:
                result = harness_server.call_tool(
                    "task_blocked",
                    {
                        "task_id": "TASK__blocked",
                        "blocked_reason": "CI service is unavailable on this host.",
                        "unblock_condition": "Run CI where the service exists.",
                    },
                )
            finally:
                harness_server.canonical_task_dir = original_ctd
                harness_server.find_repo_root = original_root
            self.assertNotIn("isError", result)
            self.assertEqual(result["structuredContent"]["status"], "blocked")
            body = (Path(task_dir) / "BLOCKED.md").read_text(encoding="utf-8")
            self.assertIn("CI service is unavailable", body)
            state = (Path(task_dir) / "TASK_STATE.yaml").read_text(encoding="utf-8")
            self.assertIn("status: blocked", state)
            self.assertIn("runtime_verdict: BLOCKED_ENV", state)

    def test_task_writers_replace_leaf_symlinks_without_touching_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp) / "doc" / "harness" / "tasks" / "TASK__leaf-safe"
            task_dir.mkdir(parents=True)
            sentinel = Path(tmp) / "task-sentinel"
            sentinel.write_text("keep", encoding="utf-8")
            request = Path(tmp) / "request.txt"
            request.write_text("request body", encoding="utf-8")
            (task_dir / "REQUEST.md").symlink_to(sentinel)
            (task_dir / "BLOCKED.md").symlink_to(sentinel)
            active = task_dir.parent / ".active"
            active.symlink_to(sentinel)

            start = self._call_in_repo(
                tmp,
                "task_start",
                {"task_id": "TASK__leaf-safe", "request_file": str(request)},
            )
            self.assertNotIn("isError", start)
            blocked = self._call_in_repo(
                tmp,
                "task_blocked",
                {
                    "task_id": "TASK__leaf-safe",
                    "blocked_reason": "environment",
                    "unblock_condition": "restore environment",
                },
            )
            self.assertNotIn("isError", blocked)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")
            self.assertFalse((task_dir / "REQUEST.md").is_symlink())
            self.assertFalse((task_dir / "BLOCKED.md").is_symlink())
            self.assertFalse(active.exists(), "task_blocked clears the safely replaced active marker")

    def test_task_blocked_missing_task_leaves_no_orphan_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp) / "doc" / "harness" / "tasks" / "TASK__typo"
            result = self._call_in_repo(
                tmp,
                "task_blocked",
                {
                    "task_id": "TASK__typo",
                    "blocked_reason": "missing",
                    "unblock_condition": "start it first",
                },
            )
            self.assertTrue(result.get("isError"))
            self.assertIn("task_start", result["structuredContent"].get("next_action", ""))
            self.assertFalse(task_dir.exists())

    def test_task_blocked_rejects_symlinked_state_without_touching_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(self._make_task(tmp, "TASK__unsafe-state"))
            state = task_dir / "TASK_STATE.yaml"
            external = Path(tmp) / "external-state.yaml"
            external.write_text(state.read_text(encoding="utf-8"), encoding="utf-8")
            state.unlink()
            state.symlink_to(external)
            before = external.read_bytes()
            result = self._call_in_repo(
                tmp,
                "task_blocked",
                {
                    "task_id": "TASK__unsafe-state",
                    "blocked_reason": "environment",
                    "unblock_condition": "restore environment",
                },
            )
            self.assertTrue(result.get("isError"))
            self.assertEqual(external.read_bytes(), before)
            self.assertTrue(state.is_symlink())

    def test_task_mutators_reject_mismatched_state_before_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(self._make_task(tmp, "TASK__expected"))
            state = task_dir / "TASK_STATE.yaml"
            state.write_text(
                state.read_text(encoding="utf-8").replace(
                    "task_id: TASK__expected", "task_id: TASK__other"
                ),
                encoding="utf-8",
            )
            before = {path.name: path.read_bytes() for path in task_dir.iterdir() if path.is_file()}

            plan = self._call_in_repo(
                tmp,
                "write_plan",
                {"task_id": "TASK__expected", "plan": "# Replacement\n"},
            )
            blocked = self._call_in_repo(
                tmp,
                "task_blocked",
                {
                    "task_id": "TASK__expected",
                    "blocked_reason": "environment",
                    "unblock_condition": "restore environment",
                },
            )
            self.assertTrue(plan.get("isError"))
            self.assertTrue(blocked.get("isError"))
            self.assertIn("invalid TASK_STATE.yaml", plan["structuredContent"]["error"])
            self.assertIn("invalid TASK_STATE.yaml", blocked["structuredContent"]["error"])
            after = {path.name: path.read_bytes() for path in task_dir.iterdir() if path.is_file()}
            self.assertEqual(after, before)
            self.assertFalse((task_dir / "BLOCKED.md").exists())

    def test_task_start_explicitly_resumes_blocked_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__resume-blocked")
            state_path = Path(task_dir) / "TASK_STATE.yaml"
            state = state_path.read_text(encoding="utf-8")
            state_path.write_text(
                state.replace("status: created", "status: blocked").replace(
                    "runtime_verdict: pending", "runtime_verdict: BLOCKED_ENV"
                ),
                encoding="utf-8",
            )
            (Path(task_dir) / "BLOCKED.md").write_text("# BLOCKED\n", encoding="utf-8")
            original_ctd = harness_server.canonical_task_dir
            original_root = harness_server.find_repo_root
            harness_server.canonical_task_dir = lambda task_id=None, slug=None, repo_root=None, **kw: task_dir
            harness_server.find_repo_root = lambda *a, **kw: tmp
            try:
                result = harness_server.call_tool(
                    "task_start", {"task_id": "TASK__resume-blocked"}
                )
            finally:
                harness_server.canonical_task_dir = original_ctd
                harness_server.find_repo_root = original_root

            self.assertNotIn("isError", result)
            context = result["structuredContent"]["task_context"]
            self.assertEqual(context["status"], "created")
            self.assertEqual(context["runtime_verdict"], "PENDING")
            self.assertFalse(result["structuredContent"]["task_created"])
            self.assertTrue(result["structuredContent"]["resumed"])
            self.assertFalse((Path(task_dir) / "BLOCKED.md").exists())

    def test_task_start_reopens_closed_task_and_clears_close_attestation(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__resume-closed")
            state_path = Path(task_dir) / "TASK_STATE.yaml"
            state_path.write_text(
                state_path.read_text(encoding="utf-8")
                .replace("status: created", "status: closed")
                .replace("runtime_verdict: pending", "runtime_verdict: PASS")
                .replace("closed_at: null", "closed_at: 2026-01-01T00:00:01Z"),
                encoding="utf-8",
            )
            close_receipt = Path(task_dir) / "TASK_CLOSE_RECEIPT.json"
            close_receipt.write_text("{}\n", encoding="utf-8")
            original_ctd = harness_server.canonical_task_dir
            original_root = harness_server.find_repo_root
            harness_server.canonical_task_dir = lambda task_id=None, slug=None, repo_root=None, **kw: task_dir
            harness_server.find_repo_root = lambda *a, **kw: tmp
            try:
                result = harness_server.call_tool(
                    "task_start", {"task_id": "TASK__resume-closed"}
                )
            finally:
                harness_server.canonical_task_dir = original_ctd
                harness_server.find_repo_root = original_root

            self.assertNotIn("isError", result)
            context = result["structuredContent"]["task_context"]
            self.assertEqual(context["status"], "created")
            self.assertEqual(context["runtime_verdict"], "PENDING")
            self.assertFalse(close_receipt.exists())

    def test_task_start_returns_ready_with_warnings_after_committed_scaffold(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._run_git(tmp, "init", "-q")
            self._run_git(tmp, "config", "user.email", "a@b")
            self._run_git(tmp, "config", "user.name", "a")
            (Path(tmp) / "README.md").write_text("# repo\n", encoding="utf-8")
            self._run_git(tmp, "add", "README.md")
            self._run_git(tmp, "commit", "-qm", "init")
            task_dir = Path(tmp) / "doc/harness/tasks/TASK__context-warning"

            with (
                mock.patch.object(
                    harness_server, "canonical_task_dir", return_value=str(task_dir)
                ),
                mock.patch.object(harness_server, "find_repo_root", return_value=tmp),
                mock.patch.object(
                    harness_server,
                    "emit_compact_context",
                    side_effect=RuntimeError("context scan exceeded request budget"),
                ),
                mock.patch.object(harness_server, "_env_snapshot", return_value=""),
            ):
                result = harness_server.handle_task_start(
                    {"task_id": "TASK__context-warning"}
                )

            self.assertNotIn("isError", result)
            payload = result["structuredContent"]
            self.assertEqual(payload["start_status"], "ready_with_warnings")
            self.assertTrue(payload["task_created"])
            self.assertFalse(payload["resumed"])
            self.assertIsInstance(payload["task_context"], dict)
            self.assertFalse(payload["task_context"]["context_complete"])
            self.assertFalse(payload["task_context"]["source_write_allowed"])
            self.assertIn("Do not call task_start again", payload["next_action"])
            self.assertEqual(payload["warnings"][0]["code"], "TASK_CONTEXT_DEFERRED")
            self.assertTrue((task_dir / "TASK_BASELINE.json").is_file())
            self.assertTrue((task_dir / "TASK_STATE.yaml").is_file())

    def test_task_start_defers_error_shaped_compact_context_after_committed_scaffold(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._run_git(tmp, "init", "-q")
            self._run_git(tmp, "config", "user.email", "a@b")
            self._run_git(tmp, "config", "user.name", "a")
            (Path(tmp) / "README.md").write_text("# repo\n", encoding="utf-8")
            self._run_git(tmp, "add", "README.md")
            self._run_git(tmp, "commit", "-qm", "init")
            task_dir = Path(tmp) / "doc/harness/tasks/TASK__context-error-result"

            with (
                mock.patch.object(
                    harness_server, "canonical_task_dir", return_value=str(task_dir)
                ),
                mock.patch.object(harness_server, "find_repo_root", return_value=tmp),
                mock.patch.object(
                    harness_server,
                    "emit_compact_context",
                    return_value={"error": "context scan unavailable"},
                ),
                mock.patch.object(harness_server, "_env_snapshot", return_value=""),
            ):
                result = harness_server.handle_task_start(
                    {"task_id": "TASK__context-error-result"}
                )

            self.assertNotIn("isError", result)
            payload = result["structuredContent"]
            self.assertEqual(payload["start_status"], "ready_with_warnings")
            self.assertEqual(payload["warnings"][0]["code"], "TASK_CONTEXT_DEFERRED")
            self.assertIn("context scan unavailable", payload["warnings"][0]["detail"])
            self.assertFalse(payload["task_context"]["source_write_allowed"])

    def test_resumed_task_context_warning_reports_ready_without_new_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._run_git(tmp, "init", "-q")
            self._run_git(tmp, "config", "user.email", "a@b")
            self._run_git(tmp, "config", "user.name", "a")
            (Path(tmp) / "README.md").write_text("# repo\n", encoding="utf-8")
            self._run_git(tmp, "add", "README.md")
            self._run_git(tmp, "commit", "-qm", "init")
            task_dir = Path(tmp) / "doc/harness/tasks/TASK__resumed-warning"
            harness_server.ensure_task_scaffold(
                str(task_dir), "TASK__resumed-warning"
            )

            with (
                mock.patch.object(
                    harness_server, "canonical_task_dir", return_value=str(task_dir)
                ),
                mock.patch.object(harness_server, "find_repo_root", return_value=tmp),
                mock.patch.object(
                    harness_server,
                    "emit_compact_context",
                    side_effect=RuntimeError("context delayed"),
                ),
                mock.patch.object(harness_server, "_env_snapshot", return_value=""),
            ):
                result = harness_server.handle_task_start(
                    {"task_id": "TASK__resumed-warning"}
                )

            payload = result["structuredContent"]
            self.assertEqual(payload["start_status"], "ready_with_warnings")
            self.assertFalse(payload["task_created"])
            self.assertTrue(payload["resumed"])
            self.assertEqual(
                payload["warnings"][0]["message"],
                "Task ready; full routing context was deferred to keep task_start responsive.",
            )

    def test_task_start_reuses_changed_path_snapshot_for_compact_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._run_git(tmp, "init", "-q")
            self._run_git(tmp, "config", "user.email", "a@b")
            self._run_git(tmp, "config", "user.name", "a")
            (Path(tmp) / "README.md").write_text("# repo\n", encoding="utf-8")
            self._run_git(tmp, "add", "README.md")
            self._run_git(tmp, "commit", "-qm", "init")
            task_dir = Path(tmp) / "doc/harness/tasks/TASK__cached-start"
            lib_globals = harness_server.ensure_task_scaffold.__globals__
            original_scan = lib_globals["_uncached_git_changed_paths"]
            original_run = lib_globals["subprocess"].run
            scan_calls = 0
            committed_diff_calls = 0
            baseline_commit_calls = 0
            baseline_ancestor_calls = 0

            def counted_scan(repo_root):
                nonlocal scan_calls
                scan_calls += 1
                return original_scan(repo_root)

            def counted_run(command, *args, **kwargs):
                nonlocal committed_diff_calls, baseline_commit_calls, baseline_ancestor_calls
                if (
                    command[:4] == ["git", "diff", "--name-only", "-z"]
                    and "HEAD" in command
                    and "--no-renames" in command
                ):
                    committed_diff_calls += 1
                if (
                    command[:4] == ["git", "rev-parse", "--verify", "--end-of-options"]
                    and command[-1].endswith("^{commit}")
                ):
                    baseline_commit_calls += 1
                if command[:3] == ["git", "merge-base", "--is-ancestor"]:
                    baseline_ancestor_calls += 1
                return original_run(command, *args, **kwargs)

            with (
                mock.patch.object(
                    harness_server, "canonical_task_dir", return_value=str(task_dir)
                ),
                mock.patch.object(harness_server, "find_repo_root", return_value=tmp),
                mock.patch.object(harness_server, "_env_snapshot", return_value=""),
                mock.patch.dict(
                    lib_globals, {"_uncached_git_changed_paths": counted_scan}
                ),
                mock.patch.object(lib_globals["subprocess"], "run", side_effect=counted_run),
            ):
                result = harness_server.handle_task_start(
                    {"task_id": "TASK__cached-start"}
                )

            self.assertNotIn("isError", result)
            self.assertEqual(result["structuredContent"]["start_status"], "ready")
            self.assertEqual(scan_calls, 1)
            self.assertEqual(committed_diff_calls, 1)
            self.assertEqual(baseline_commit_calls, 1)
            self.assertEqual(baseline_ancestor_calls, 1)

    def test_task_start_does_not_defer_mandatory_binding_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp) / "doc/harness/tasks/TASK__binding-failure"
            task_dir.mkdir(parents=True)
            (task_dir / "TASK_STATE.yaml").write_text(
                "task_id: TASK__binding-failure\n"
                "status: created\n"
                "runtime_verdict: pending\n"
                "touched_paths: []\n"
                "plan_session_state: closed\n"
                "closed_at: null\n"
                "updated: 2026-08-03T00:00:00Z\n",
                encoding="utf-8",
            )
            binding_error = harness_server.GitBindingError(
                "REGISTERED_WORKTREE_BINDING_CHANGED",
                "binding changed",
                path="services/front",
                invariant="source_snapshot_binding",
                next_action="retry",
            )
            with (
                mock.patch.object(
                    harness_server, "canonical_task_dir", return_value=str(task_dir)
                ),
                mock.patch.object(harness_server, "find_repo_root", return_value=tmp),
                mock.patch.object(harness_server, "ensure_task_scaffold"),
                mock.patch.object(
                    harness_server, "emit_compact_context", side_effect=binding_error
                ),
            ):
                with self.assertRaises(harness_server.GitBindingError):
                    harness_server.handle_task_start(
                        {"task_id": "TASK__binding-failure"}
                    )

    def test_task_start_does_not_defer_committed_diff_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp) / "doc/harness/tasks/TASK__diff-failure"
            task_dir.mkdir(parents=True)
            (task_dir / "TASK_STATE.yaml").write_text(
                "task_id: TASK__diff-failure\nstatus: created\n"
                "runtime_verdict: pending\ntouched_paths: []\n"
                "plan_session_state: closed\nclosed_at: null\n"
                "updated: 2026-08-03T00:00:00Z\n",
                encoding="utf-8",
            )
            with (
                mock.patch.object(
                    harness_server, "canonical_task_dir", return_value=str(task_dir)
                ),
                mock.patch.object(harness_server, "find_repo_root", return_value=tmp),
                mock.patch.object(
                    harness_server, "ensure_task_scaffold",
                    return_value={"created": [str(task_dir / "TASK_STATE.yaml")]},
                ),
                mock.patch.object(
                    harness_server,
                    "emit_compact_context",
                    side_effect=RuntimeError(
                        "task baseline Git diff unavailable: committed path diff timed out"
                    ),
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "Git diff unavailable"):
                    harness_server.handle_task_start(
                        {"task_id": "TASK__diff-failure"}
                    )

    def test_new_task_start_rolls_back_after_final_authority_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._run_git(tmp, "init", "-q")
            self._run_git(tmp, "config", "user.email", "a@b")
            self._run_git(tmp, "config", "user.name", "a")
            (Path(tmp) / "README.md").write_text("# repo\n", encoding="utf-8")
            self._run_git(tmp, "add", "README.md")
            self._run_git(tmp, "commit", "-qm", "init")
            task_dir = Path(tmp) / "doc/harness/tasks/TASK__final-authority"
            binding_error = harness_server.GitBindingError(
                "REGISTERED_WORKTREE_BINDING_CHANGED",
                "binding changed after marker",
                path="services/front",
                invariant="request_source_snapshot_binding",
                next_action="retry",
            )
            with (
                mock.patch.object(
                    harness_server, "canonical_task_dir", return_value=str(task_dir)
                ),
                mock.patch.object(harness_server, "find_repo_root", return_value=tmp),
                mock.patch.object(harness_server, "_env_snapshot", return_value=""),
                mock.patch.object(
                    harness_server,
                    "revalidate_request_source_authorities",
                    side_effect=binding_error,
                ),
            ):
                with self.assertRaises(harness_server.GitBindingError):
                    harness_server.handle_task_start(
                        {"task_id": "TASK__final-authority"}
                    )

            self.assertFalse((task_dir / "TASK_BASELINE.json").exists())
            self.assertFalse((task_dir / "TASK_STATE.yaml").exists())
            self.assertFalse((Path(tmp) / "doc/harness/tasks/.active").exists())
            sessions = Path(tmp) / "doc/harness/tasks/.active_sessions"
            self.assertFalse(sessions.exists() and any(sessions.iterdir()))

    def test_failed_terminal_resume_restores_state_and_artifacts(self):
        for terminal_status in ("closed", "blocked"):
            with self.subTest(status=terminal_status), tempfile.TemporaryDirectory() as tmp:
                self._run_git(tmp, "init", "-q")
                self._run_git(tmp, "config", "user.email", "a@b")
                self._run_git(tmp, "config", "user.name", "a")
                (Path(tmp) / "README.md").write_text("# repo\n", encoding="utf-8")
                self._run_git(tmp, "add", "README.md")
                self._run_git(tmp, "commit", "-qm", "init")
                task_dir = Path(tmp) / f"doc/harness/tasks/TASK__resume-{terminal_status}"
                lib_globals = harness_server.ensure_task_scaffold.__globals__
                lib_globals["ensure_task_scaffold"](
                    str(task_dir), f"TASK__resume-{terminal_status}", repo_root=tmp
                )
                state = harness_server.read_state(str(task_dir))
                state["status"] = terminal_status
                state["runtime_verdict"] = "PASS" if terminal_status == "closed" else "BLOCKED_ENV"
                state["closed_at"] = "2026-08-03T00:00:00Z" if terminal_status == "closed" else None
                harness_server.write_state(str(task_dir), state)
                artifact = (
                    task_dir / "TASK_CLOSE_RECEIPT.json"
                    if terminal_status == "closed"
                    else task_dir / "BLOCKED.md"
                )
                artifact.write_text("preserve\n", encoding="utf-8")
                binding_error = harness_server.GitBindingError(
                    "REGISTERED_WORKTREE_BINDING_CHANGED", "late failure",
                    path="services/front", invariant="request_source_snapshot_binding",
                    next_action="retry",
                )
                with (
                    mock.patch.object(
                        harness_server, "canonical_task_dir", return_value=str(task_dir)
                    ),
                    mock.patch.object(harness_server, "find_repo_root", return_value=tmp),
                    mock.patch.object(
                        harness_server, "revalidate_request_source_authorities",
                        side_effect=binding_error,
                    ),
                ):
                    with self.assertRaises(harness_server.GitBindingError):
                        harness_server.handle_task_start(
                            {"task_id": f"TASK__resume-{terminal_status}"}
                        )

                restored = harness_server.read_state(str(task_dir))
                self.assertEqual(restored["status"], terminal_status)
                self.assertEqual(restored["runtime_verdict"], state["runtime_verdict"])
                self.assertEqual(restored["closed_at"], state["closed_at"])
                self.assertTrue(artifact.exists())

    def test_invalid_execution_mode_does_not_create_or_reopen_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            new_task = Path(tmp) / "doc/harness/tasks/TASK__invalid-mode-new"
            with (
                mock.patch.object(
                    harness_server, "canonical_task_dir", return_value=str(new_task)
                ),
                mock.patch.object(harness_server, "find_repo_root", return_value=tmp),
            ):
                with self.assertRaisesRegex(ValueError, "execution_mode"):
                    harness_server.handle_task_start({
                        "task_id": "TASK__invalid-mode-new",
                        "execution_mode": "bogus",
                    })
            self.assertFalse((new_task / "TASK_STATE.yaml").exists())

            terminal_task = Path(tmp) / "doc/harness/tasks/TASK__invalid-mode-closed"
            terminal_task.mkdir(parents=True)
            original = {
                "task_id": "TASK__invalid-mode-closed",
                "status": "closed",
                "runtime_verdict": "PASS",
                "touched_paths": [],
                "plan_session_state": "closed",
                "closed_at": "2026-08-03T00:00:00Z",
                "updated": "2026-08-03T00:00:00Z",
            }
            harness_server.write_state(str(terminal_task), original)
            with (
                mock.patch.object(
                    harness_server, "canonical_task_dir", return_value=str(terminal_task)
                ),
                mock.patch.object(harness_server, "find_repo_root", return_value=tmp),
            ):
                with self.assertRaisesRegex(ValueError, "execution_mode"):
                    harness_server.handle_task_start({
                        "task_id": "TASK__invalid-mode-closed",
                        "execution_mode": "bogus",
                    })
            self.assertEqual(
                harness_server.read_state(str(terminal_task))["status"], "closed"
            )

    def test_new_task_rolls_back_when_active_marker_write_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._run_git(tmp, "init", "-q")
            self._run_git(tmp, "config", "user.email", "a@b")
            self._run_git(tmp, "config", "user.name", "a")
            (Path(tmp) / "README.md").write_text("# repo\n", encoding="utf-8")
            self._run_git(tmp, "add", "README.md")
            self._run_git(tmp, "commit", "-qm", "init")
            task_dir = Path(tmp) / "doc/harness/tasks/TASK__marker-failure"
            with (
                mock.patch.object(
                    harness_server, "canonical_task_dir", return_value=str(task_dir)
                ),
                mock.patch.object(harness_server, "find_repo_root", return_value=tmp),
                mock.patch.object(
                    harness_server, "write_active_marker",
                    side_effect=OSError("marker unavailable"),
                ),
            ):
                with self.assertRaisesRegex(OSError, "marker unavailable"):
                    harness_server.handle_task_start(
                        {"task_id": "TASK__marker-failure"}
                    )
            self.assertFalse((task_dir / "TASK_BASELINE.json").exists())
            self.assertFalse((task_dir / "TASK_STATE.yaml").exists())

    def test_task_context_returns_warning_when_dirty_evidence_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._run_git(tmp, "init", "-q")
            self._run_git(tmp, "config", "user.email", "a@b")
            self._run_git(tmp, "config", "user.name", "a")
            (Path(tmp) / "README.md").write_text("# repo\n", encoding="utf-8")
            self._run_git(tmp, "add", "README.md")
            self._run_git(tmp, "commit", "-qm", "init")
            task_dir = Path(tmp) / "doc/harness/tasks/TASK__dirty-warning"
            lib_globals = harness_server.ensure_task_scaffold.__globals__

            with (
                mock.patch.object(
                    harness_server, "canonical_task_dir", return_value=str(task_dir)
                ),
                mock.patch.object(harness_server, "find_repo_root", return_value=tmp),
                mock.patch.object(harness_server, "_env_snapshot", return_value=""),
            ):
                start = harness_server.handle_task_start(
                    {"task_id": "TASK__dirty-warning"}
                )
                self.assertNotIn("isError", start)
                with mock.patch.dict(
                    lib_globals,
                    {
                        "_uncached_git_changed_paths": mock.Mock(
                            side_effect=RuntimeError(
                                "Git changed-path snapshot unavailable: "
                                "working tree diff timed out after 3.0s in " + tmp
                            )
                        )
                    },
                ):
                    result = harness_server.handle_task_context(
                        {"task_id": "TASK__dirty-warning"}
                    )

            self.assertNotIn("isError", result)
            warnings = result["structuredContent"]["git_snapshot_warnings"]
            self.assertEqual(warnings[0]["code"], "GIT_DIRTY_SNAPSHOT_SKIPPED")
            self.assertEqual(warnings[0]["root"], str(Path(tmp).resolve()))

    def test_write_plan_writes_plan_meta_checks_and_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__planmcp")
            result = self._call_in_repo(
                tmp,
                "write_plan",
                {
                    "task_dir": task_dir,
                    "plan": "# MCP Plan\n",
                    "checks": "- id: AC-001\n  title: x\n  status: open\n",
                    "audit": "| 1 | p | d | c | p | r | - |\n",
                    "meta": {"routing": "light"},
                },
            )
            self.assertNotIn("isError", result)
            self.assertEqual(
                result["structuredContent"]["written"],
                ["PLAN.md", "PLAN.meta.json", "CHECKS.yaml", "AUDIT_TRAIL.md"],
            )
            bytes_written = result["structuredContent"]["bytes_written"]
            self.assertGreater(bytes_written["PLAN.md"], 0)
            self.assertGreater(bytes_written["PLAN.meta.json"], 0)
            self.assertGreater(bytes_written["CHECKS.yaml"], 0)
            self.assertGreater(bytes_written["AUDIT_TRAIL.md"], 0)
            self.assertEqual((Path(task_dir) / "PLAN.md").read_text(encoding="utf-8"), "# MCP Plan\n")
            meta = json.loads((Path(task_dir) / "PLAN.meta.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["author_role"], "plan-skill")
            self.assertEqual(meta["plan_meta"]["routing"], "light")
            self.assertIn("AC-001", (Path(task_dir) / "CHECKS.yaml").read_text(encoding="utf-8"))
            self.assertIn("| 1 |", (Path(task_dir) / "AUDIT_TRAIL.md").read_text(encoding="utf-8"))

    def test_write_plan_rejects_empty_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__emptyplan")
            result = self._call_in_repo(
                tmp,
                "write_plan",
                {"task_dir": task_dir, "plan": " \n\t"},
            )
            self.assertTrue(result.get("isError"))
            self.assertIn("empty PLAN.md", result["structuredContent"]["error"])

    def test_write_plan_rejects_empty_optional_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__emptychecks")
            before = {p.name: p.read_bytes() for p in Path(task_dir).iterdir() if p.is_file()}
            result = self._call_in_repo(
                tmp,
                "write_plan",
                {"task_dir": task_dir, "plan": "# Plan\n", "checks": " \n\t"},
            )
            self.assertTrue(result.get("isError"))
            self.assertIn("empty CHECKS.yaml", result["structuredContent"]["error"])
            after = {p.name: p.read_bytes() for p in Path(task_dir).iterdir() if p.is_file()}
            self.assertEqual(after, before)

    def test_write_plan_rejects_empty_optional_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__emptyaudit")
            result = self._call_in_repo(
                tmp,
                "write_plan",
                {"task_dir": task_dir, "plan": "# Plan\n", "audit": "\n"},
            )
            self.assertTrue(result.get("isError"))
            self.assertIn("empty AUDIT_TRAIL.md", result["structuredContent"]["error"])

    def test_write_plan_rejects_invalid_audit_before_any_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__invalidaudit")
            before = {p.name: p.read_bytes() for p in Path(task_dir).iterdir() if p.is_file()}
            result = self._call_in_repo(
                tmp,
                "write_plan",
                {"task_dir": task_dir, "plan": "# Replacement\n", "audit": "not a table row\n"},
            )
            self.assertTrue(result.get("isError"))
            self.assertIn("invalid AUDIT_TRAIL.md", result["structuredContent"]["error"])
            self.assertIn("full Markdown table", result["structuredContent"]["next_action"])
            self.assertIn("| 1 | phase | decision |", result["structuredContent"]["example"])
            after = {p.name: p.read_bytes() for p in Path(task_dir).iterdir() if p.is_file()}
            self.assertEqual(after, before)

    def test_write_plan_accepts_full_markdown_audit_table_and_normalizes_header(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__friendlyaudit")
            full_table = "\n".join(
                [
                    "# Audit Trail",
                    "",
                    "| # | phase | decision | classification | principle | rationale | rejected_option |",
                    "|---|---|---|---|---|---|---|",
                    "| 1 | plan | accept natural Markdown | Mechanical | P5 | friendly input | row-only input |",
                    "",
                ]
            )

            result = self._call_in_repo(
                tmp,
                "write_plan",
                {"task_dir": task_dir, "plan": "# Plan\n", "audit": full_table},
            )

            self.assertNotIn("isError", result)
            body = (Path(task_dir) / "AUDIT_TRAIL.md").read_text(encoding="utf-8")
            self.assertEqual(body.count("# Audit Trail"), 0)
            self.assertEqual(
                body.count(
                    "| # | phase | decision | classification | principle | rationale | rejected_option |"
                ),
                1,
            )
            self.assertEqual(body.count("|---|---|---|---|---|---|---|"), 1)
            self.assertIn("| 1 | plan | accept natural Markdown |", body)

    def test_write_plan_accepts_unspaced_canonical_audit_header(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__unspacedaudit")
            audit = "\n".join(
                [
                    "# Audit Trail",
                    "|#|phase|decision|classification|principle|rationale|rejected_option|",
                    "|---|---|---|---|---|---|---|",
                    "|1|plan|friendly input|Mechanical|P5|less friction|-|",
                ]
            )

            result = self._call_in_repo(
                tmp,
                "write_plan",
                {"task_dir": task_dir, "plan": "# Plan\n", "audit": audit},
            )

            self.assertNotIn("isError", result)
            body = (Path(task_dir) / "AUDIT_TRAIL.md").read_text(encoding="utf-8")
            self.assertEqual(body.count("# Audit Trail"), 0)
            self.assertIn("|1|plan|friendly input|", body)

    def test_write_plan_rejects_noncanonical_hash_header_without_dropping_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__wrongauditheader")
            before = {
                p.name: p.read_bytes() for p in Path(task_dir).iterdir() if p.is_file()
            }
            result = self._call_in_repo(
                tmp,
                "write_plan",
                {
                    "task_dir": task_dir,
                    "plan": "# Replacement\n",
                    "audit": "| # | garbage | x |\n| 1 | phase | decision |\n",
                },
            )

            self.assertTrue(result.get("isError"))
            self.assertIn("audit header columns", result["structuredContent"]["next_action"])
            after = {
                p.name: p.read_bytes() for p in Path(task_dir).iterdir() if p.is_file()
            }
            self.assertEqual(after, before)

    def test_write_plan_rejects_audit_header_without_data_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__headeronlyaudit")
            before = {p.name: p.read_bytes() for p in Path(task_dir).iterdir() if p.is_file()}
            result = self._call_in_repo(
                tmp,
                "write_plan",
                {
                    "task_dir": task_dir,
                    "plan": "# Replacement\n",
                    "audit": (
                        "# Audit Trail\n\n"
                        "| # | phase | decision | classification | principle | rationale | rejected_option |\n"
                        "|---|---|---|---|---|---|---|\n"
                    ),
                },
            )
            self.assertTrue(result.get("isError"))
            self.assertIn("at least one audit data row", result["structuredContent"]["next_action"])
            after = {p.name: p.read_bytes() for p in Path(task_dir).iterdir() if p.is_file()}
            self.assertEqual(after, before)

    def test_write_plan_rejects_blank_or_separator_only_audit_rows_atomically(self):
        invalid_values = (
            "| | |",
            "|---|---|",
            "# Audit Trail\n| | |",
        )
        for index, audit_value in enumerate(invalid_values):
            with self.subTest(audit=audit_value), tempfile.TemporaryDirectory() as tmp:
                task_dir = self._make_task(tmp, f"TASK__incompleteaudit{index}")
                before = {
                    p.name: p.read_bytes()
                    for p in Path(task_dir).iterdir()
                    if p.is_file()
                }
                result = self._call_in_repo(
                    tmp,
                    "write_plan",
                    {
                        "task_dir": task_dir,
                        "plan": "# Replacement\n",
                        "audit": audit_value,
                    },
                )
                self.assertTrue(result.get("isError"))
                self.assertIn("non-empty cells", result["structuredContent"]["next_action"])
                after = {
                    p.name: p.read_bytes()
                    for p in Path(task_dir).iterdir()
                    if p.is_file()
                }
                self.assertEqual(after, before)

    def test_write_plan_rejects_audit_leaf_symlink_without_copying_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(self._make_task(tmp, "TASK__auditsymlink"))
            sentinel = Path(tmp) / "audit-sentinel"
            sentinel.write_text("TOP_SECRET_SENTINEL", encoding="utf-8")
            audit = task_dir / "AUDIT_TRAIL.md"
            audit.symlink_to(sentinel)
            plan_before = (task_dir / "PLAN.md").read_bytes()
            result = self._call_in_repo(
                tmp,
                "write_plan",
                {
                    "task_dir": str(task_dir),
                    "plan": "# Replacement\n",
                    "audit": "| 1 | phase | decision |\n",
                },
            )
            self.assertTrue(result.get("isError"))
            self.assertIn("unsafe AUDIT_TRAIL.md", result["structuredContent"]["error"])
            self.assertEqual((task_dir / "PLAN.md").read_bytes(), plan_before)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "TOP_SECRET_SENTINEL")
            self.assertTrue(audit.is_symlink())

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO artifact regression requires POSIX")
    def test_write_plan_rejects_audit_fifo_without_blocking(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(self._make_task(tmp, "TASK__auditfifo"))
            audit = task_dir / "AUDIT_TRAIL.md"
            os.mkfifo(audit)
            result = self._call_in_repo(
                tmp,
                "write_plan",
                {
                    "task_dir": str(task_dir),
                    "plan": "# Replacement\n",
                    "audit": "| 1 | phase | decision |\n",
                },
            )
            self.assertTrue(result.get("isError"))
            self.assertIn("unsafe AUDIT_TRAIL.md", result["structuredContent"]["error"])

    def test_write_plan_appends_audit_header_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__auditmcp")
            for row in ("| 1 | p | d | c | p | r | - |\n", "| 2 | p | d2 | c | p | r | - |\n"):
                result = self._call_in_repo(
                    tmp,
                    "write_plan",
                    {"task_dir": task_dir, "plan": "# Plan\n", "audit": row},
                )
                self.assertNotIn("isError", result)
            body = (Path(task_dir) / "AUDIT_TRAIL.md").read_text(encoding="utf-8")
            self.assertEqual(body.count("| # | phase | decision | classification | principle | rationale | rejected_option |"), 1)
            self.assertIn("| 1 |", body)
            self.assertIn("| 2 |", body)

    def test_write_plan_rejects_malformed_checks_before_any_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__malformedchecks")
            before = {p.name: p.read_bytes() for p in Path(task_dir).iterdir() if p.is_file()}
            result = self._call_in_repo(
                tmp,
                "write_plan",
                {
                    "task_dir": task_dir,
                    "plan": "# Replacement\n",
                    "checks": "- id: AC-001\n  title: missing status\n",
                },
            )
            self.assertTrue(result.get("isError"))
            self.assertIn("invalid CHECKS.yaml", result["structuredContent"]["error"])
            after = {p.name: p.read_bytes() for p in Path(task_dir).iterdir() if p.is_file()}
            self.assertEqual(after, before)

    def test_present_invalid_checks_are_not_absent_or_auto_promoted(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__invalidchecks")
            checks = Path(task_dir) / "CHECKS.yaml"
            checks.write_text("- id: AC-001\n  status: mystery\n", encoding="utf-8")
            self._write_subagent_receipt(task_dir)
            self.assertEqual(harness_server._checks_gate_status(task_dir)[0], "invalid")
            before = checks.read_bytes()
            self.assertEqual(harness_server._auto_promote_open_acs(task_dir, "PASS"), [])
            self.assertEqual(checks.read_bytes(), before)
            original_ctd = harness_server.canonical_task_dir
            harness_server.canonical_task_dir = lambda task_id=None, **kw: task_dir
            try:
                verify = harness_server.call_tool(
                    "task_verify", {"task_id": "TASK__invalidchecks", "reconcile_acs": True}
                )
            finally:
                harness_server.canonical_task_dir = original_ctd
            self.assertIn("present but invalid", verify["structuredContent"]["ac_reconcile"]["reason"])

    def test_checks_gate_rejects_symlink_and_non_regular_ledger_leaves(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__invalidchecksleaf")
            checks = Path(task_dir) / "CHECKS.yaml"
            external = Path(tmp) / "external-checks.yaml"
            external.write_text("- id: AC-001\n  status: passed\n", encoding="utf-8")

            checks.symlink_to(external)
            self.assertEqual(harness_server._checks_gate_status(task_dir)[0], "invalid")
            self.assertEqual(harness_server._auto_promote_open_acs(task_dir, "PASS"), [])
            self.assertEqual(external.read_text(encoding="utf-8"), "- id: AC-001\n  status: passed\n")

            checks.unlink()
            checks.mkdir()
            self.assertEqual(harness_server._checks_gate_status(task_dir)[0], "invalid")

            if hasattr(os, "mkfifo"):
                checks.rmdir()
                os.mkfifo(checks)
                self.assertEqual(harness_server._checks_gate_status(task_dir)[0], "invalid")
                self.assertEqual(harness_server._auto_promote_open_acs(task_dir, "PASS"), [])

    def test_checks_parser_rejects_empty_duplicate_missing_and_invalid_fields(self):
        invalid_ledgers = (
            " \n",
            "- id: \n  status: open\n",
            "- id: AC-001\n  status: open\n- id: AC-001\n  status: passed\n",
            "- id: AC-001\n  title: no status\n",
            "- id: AC-001\n  status: mystery\n",
            "not-a-checks-ledger\n",
            "- id: AC-001\n  status: open\nunindented garbage\n",
            "- id: AC-001\n  status: open\n  - stray list item\n",
            "- id: AC-001\n  status: open\n  status: passed\n",
            "- id: AC-001\n  status: passed\n  - id: AC-002\n    status: passed\n",
            '- id: "AC-001\n  status: open\n',
            '- id: AC-001\n  status: "open\n',
        )
        for ledger in invalid_ledgers:
            with self.subTest(ledger=ledger):
                with self.assertRaises(ValueError):
                    harness_server._parse_checks_text(ledger)

    def test_malformed_quoted_checks_never_promote_or_pass_gate(self):
        malformed = (
            '- id: "AC-001\n  status: open\n',
            '- id: AC-001\n  status: "open\n',
        )
        for ledger in malformed:
            with self.subTest(ledger=ledger), tempfile.TemporaryDirectory() as tmp:
                task_dir = self._make_task(tmp, "TASK__malformed-quotes")
                checks = Path(task_dir) / "CHECKS.yaml"
                checks.write_text(ledger, encoding="utf-8")
                before = checks.read_bytes()
                self.assertEqual(harness_server._checks_gate_status(task_dir)[0], "invalid")
                self.assertEqual(harness_server._auto_promote_open_acs(task_dir, "PASS"), [])
                self.assertEqual(checks.read_bytes(), before)

    def test_checks_parser_preserves_supported_flat_and_wrapped_shapes(self):
        flat = '- id: AC-001\n  title: "flat"\n  status: open\n  extra: kept\n'
        wrapped = (
            "version: 1\nchecks:\n"
            "  - id: AC-002\n    description: wrapped\n    status: implemented_candidate\n"
        )
        self.assertEqual(harness_server._parse_checks_text(flat)[0]["title"], "flat")
        self.assertEqual(
            harness_server._parse_checks_text(wrapped)[0]["status"],
            "implemented_candidate",
        )
        for legacy_wrapper in ("acs", "acceptance"):
            with self.subTest(legacy_wrapper=legacy_wrapper):
                legacy = (
                    f"task_id: TASK__legacy\n{legacy_wrapper}:\n"
                    "  - id: AC-LEGACY\n"
                    "    description: repository-proven wrapper\n"
                    "    status: open\n"
                )
                self.assertEqual(
                    harness_server._parse_checks_text(legacy)[0]["id"],
                    "AC-LEGACY",
                )
        nested_unknown = (
            "task_id: TASK__compat\n"
            "metadata:\n"
            "  source: legacy\n"
            "checks:\n"
            "  - id: AC-003\n"
            "    status: open\n"
            "    files:\n"
            "      - plugin/scripts/_lib.py\n"
            "    checks:\n"
            "      note: nested metadata\n"
            "    evidence_log:\n"
            "      - id: evidence-row\n"
            "        path: tests/test_harness_mcp_server.py\n"
        )
        self.assertEqual(harness_server._parse_checks_text(nested_unknown)[0]["id"], "AC-003")

        trailing_metadata = (
            "version: 1\n"
            "checks:\n"
            "  - id: AC-TRAILING\n"
            "    status: open\n"
            "metadata:\n"
            "  source: legacy\n"
            "trailer: kept\n"
        )
        self.assertEqual(
            harness_server._parse_checks_text(trailing_metadata)[0]["id"],
            "AC-TRAILING",
        )
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__trailing-metadata")
            checks = Path(task_dir) / "CHECKS.yaml"
            checks.write_text(trailing_metadata, encoding="utf-8")
            self.assertEqual(
                harness_server._auto_promote_open_acs(task_dir, "trailing compatibility"),
                ["AC-TRAILING"],
            )
            result_text = checks.read_text(encoding="utf-8")
            self.assertLess(result_text.index("checks:"), result_text.index("- id: AC-TRAILING"))
            self.assertLess(result_text.index("- id: AC-TRAILING"), result_text.index("metadata:"))
            self.assertIn("  source: legacy", result_text)
            self.assertIn("trailer: kept", result_text)
            self.assertEqual(harness_server._parse_checks_text(result_text)[0]["status"], "passed")

        for wrapper, item_indent, field_indent in (("", "", "  "), ("checks:\n", "  ", "    ")):
            with self.subTest(quoted_wrapper=bool(wrapper)), tempfile.TemporaryDirectory() as tmp:
                task_dir = self._make_task(tmp, "TASK__quoted-promotion")
                checks = Path(task_dir) / "CHECKS.yaml"
                checks.write_text(
                    wrapper
                    + f'{item_indent}- id: "AC-QUOTED"\n'
                    + f'{field_indent}status: "open"\n',
                    encoding="utf-8",
                )
                self.assertEqual(
                    harness_server._auto_promote_open_acs(task_dir, "quoted compatibility"),
                    ["AC-QUOTED"],
                )
                self.assertEqual(harness_server._parse_checks_yaml(task_dir)[0]["status"], "passed")

        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__nested-promotion")
            checks = Path(task_dir) / "CHECKS.yaml"
            checks.write_text(nested_unknown, encoding="utf-8")
            promoted = harness_server._auto_promote_open_acs(task_dir, "nested compatibility")
            result_text = checks.read_text(encoding="utf-8")
            reparsed = harness_server._parse_checks_text(result_text)
            self.assertEqual(promoted, ["AC-003"])
            self.assertEqual([(item["id"], item["status"]) for item in reparsed], [("AC-003", "passed")])
            self.assertIn("- id: evidence-row", result_text)

        nested_collision = (
            "- id: AC-004\n"
            "  metadata:\n"
            "    status: open\n"
            "    last_updated: nested\n"
            "    evidence: nested\n"
            "  status: failed\n"
            "  last_updated: direct\n"
            "  evidence: direct\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__nested-collision")
            checks = Path(task_dir) / "CHECKS.yaml"
            checks.write_text(nested_collision, encoding="utf-8")
            before = checks.read_bytes()
            self.assertEqual(harness_server._auto_promote_open_acs(task_dir, "must not promote"), [])
            self.assertEqual(checks.read_bytes(), before)


class HarnessMcpServerPR2CloseGate(unittest.TestCase):
    """AC-001..AC-006: CHECKS gate + runtime-stale gate in task_close / task_verify."""

    def _prepare_task(self, base: str, task_id: str, *, checks_yaml: str | None,
                      write_receipt: bool = True, write_handoff: bool = True,
                      touched_paths: list[str] | None = None,
                      handoff_body: str | None = None) -> str:
        repo = Path(base)
        git_dir = repo / ".git"
        if git_dir.is_dir() and not (git_dir / "HEAD").exists():
            git_dir.rmdir()
        if not git_dir.exists():
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "mcp@test"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "MCP Test"], cwd=repo, check=True)
        (repo / ".gitignore").write_text("TASK__*/\n", encoding="utf-8")
        for rel in (
            "plugin/skills/run/self-improvement.md",
            "plugin/scripts/_lib.py",
            "plugin/scripts/health.py",
            "plugin/CLAUDE.md",
            "README.md",
        ):
            p = Path(base) / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            if not p.exists():
                p.write_text("# fixture\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        staged = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=repo)
        if staged.returncode != 0:
            subprocess.run(["git", "commit", "-qm", "fixture baseline"], cwd=repo, check=True)
        head_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, check=True,
            capture_output=True, text=True,
        ).stdout.strip()
        task_dir = Path(base) / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        tp = touched_paths or []
        tp_yaml = "[]" if not tp else "\n" + "\n".join(f"  - {p}" for p in tp)
        (task_dir / "TASK_STATE.yaml").write_text(
            f"task_id: {task_id}\n"
            f"status: created\n"
            f"runtime_verdict: pending\n"
            f"touched_paths: {tp_yaml}\n"
            f"plan_session_state: closed\n"
            f"closed_at: null\n"
            f"updated: 2026-04-19T15:00:00Z\n",
            encoding="utf-8",
        )
        (task_dir / "PLAN.md").write_text("# plan\n", encoding="utf-8")
        (task_dir / "TASK_BASELINE.json").write_text(
            json.dumps({
                "version": 1, "repo_root": str(repo),
                "head_sha": head_sha, "dirty_paths": {},
            }) + "\n",
            encoding="utf-8",
        )
        if write_handoff:
            default_handoff = "# handoff\n\n## Commit-backed Learnings\n\nStatus: none\n"
            body = handoff_body or default_handoff
            if "Self-Healing Candidates" not in body:
                body = body.rstrip() + "\n\n## Self-Healing Candidates\n\nStatus: none\n"
            (task_dir / "HANDOFF.md").write_text(
                body,
                encoding="utf-8",
            )
        if write_receipt:
            review_types = {
                "review-code": "harness:code-reviewer",
                "review-security": "harness:security-reviewer",
            }
            for lens in harness_server.required_review_lenses(task_dir):
                for status, verdict in (("started", ""), ("completed", "PASS")):
                    harness_server.record_subagent_receipt(task_dir, {
                        "source": "subagent_start_hook" if status == "started" else "subagent_stop_hook",
                        "status": status,
                        "agent_id": f"{lens}-{task_id}",
                        "agent_type": review_types[lens],
                        "verdict": verdict,
                        "summary": (
                            "VERDICT: PASS\n"
                            "FINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0"
                            if verdict else "review started"
                        ),
                    })
            for status, verdict in (("started", ""), ("completed", "PASS")):
                harness_server.record_subagent_receipt(task_dir, {
                    "source": "subagent_start_hook" if status == "started" else "subagent_stop_hook",
                    "status": status,
                    "agent_id": f"agent-{task_id}",
                    "agent_type": "harness:qa-cli",
                    "verdict": verdict,
                    "summary": f"VERDICT: {verdict}" if verdict else "qa started",
                })
        if checks_yaml is not None:
            (task_dir / "CHECKS.yaml").write_text(checks_yaml, encoding="utf-8")
        return str(task_dir)

    def _patch(self, task_dir: str):
        """Patch canonical_task_dir + sync_from_git_diff to isolate from git state."""
        self._orig_ctd = harness_server.canonical_task_dir
        self._orig_sync = harness_server.sync_from_git_diff
        harness_server.canonical_task_dir = lambda task_id=None, **kw: task_dir
        harness_server.sync_from_git_diff = lambda td: []

    def _unpatch(self):
        harness_server.canonical_task_dir = self._orig_ctd
        harness_server.sync_from_git_diff = self._orig_sync

    def _patch_repo_root_for_context(self, repo_root: str):
        self._orig_context_find_repo_root = harness_server.emit_compact_context.__globals__["find_repo_root"]
        self._orig_context_git_changed_paths = harness_server.emit_compact_context.__globals__["_git_changed_paths"]
        harness_server.emit_compact_context.__globals__["find_repo_root"] = (
            lambda *args, **kw: repo_root
        )

    def _set_context_git_changed_paths(self, paths: list[str]):
        harness_server.emit_compact_context.__globals__["_git_changed_paths"] = (
            lambda repo_root, *args, **kwargs: (
                {path: "sha256:test" for path in paths}
                if kwargs.get("with_fingerprints")
                else set(paths)
            )
        )

    def _unpatch_repo_root_for_context(self):
        harness_server.emit_compact_context.__globals__["find_repo_root"] = (
            self._orig_context_find_repo_root
        )
        harness_server.emit_compact_context.__globals__["_git_changed_paths"] = (
            self._orig_context_git_changed_paths
        )

    def test_context_surfaces_feedback_ids_without_handoff_close_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp,
                "TASK__feedback-next-action-ids",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n',
            )
            Path(td, "USER_FEEDBACK.jsonl").write_text(
                json.dumps({"id": "ufe-needed", "prompt_excerpt": "remember this"}) + "\n",
                encoding="utf-8",
            )
            self._patch(td)
            try:
                result = harness_server.call_tool(
                    "task_context", {"task_id": "TASK__feedback-next-action-ids"}
                )
            finally:
                self._unpatch()
        ctx = result["structuredContent"]["task_context"]
        self.assertEqual(ctx["unresolved_feedback_ids"], ["ufe-needed"])
        self.assertNotIn("User feedback disposition", ctx["missing_for_close"])
        self.assertNotIn("ufe-needed", ctx["next_action"])

    def test_conversation_open_item_blocks_close(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp,
                "TASK__conversation-open",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n',
            )
            Path(td, "CONVERSATION.md").write_text(
                "# Conversation\n\n"
                "<!-- harness:conversation-log v1 -->\n\n"
                "## 2026-06-23T00:00:00Z - User\n"
                "사용자가 새 요구사항을 말했다.\n"
                "<!-- item: type=requirement status=open key=reader-back-stack -->\n",
                encoding="utf-8",
            )
            self._patch(td)
            try:
                result = harness_server.call_tool(
                    "task_close", {"task_id": "TASK__conversation-open"}
                )
            finally:
                self._unpatch()
        self.assertTrue(result.get("isError"))
        ctx = result["structuredContent"]["task_context"]
        self.assertIn("CONVERSATION.md open items", ctx["missing_for_close"])
        self.assertEqual(ctx["conversation_open_items"][0]["key"], "reader-back-stack")
        self.assertIn("CONVERSATION.md open item markers", ctx["next_action"])

    def test_conversation_captured_item_does_not_block_close(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp,
                "TASK__conversation-captured",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n',
            )
            Path(td, "CONVERSATION.md").write_text(
                "# Conversation\n\n"
                "<!-- harness:conversation-log v1 -->\n\n"
                "<!-- item: type=requirement status=captured key=reader-back-stack ref=doc/ui/REQ__reader.md -->\n",
                encoding="utf-8",
            )
            self._patch(td)
            try:
                result = harness_server.call_tool(
                    "task_close", {"task_id": "TASK__conversation-captured"}
                )
            finally:
                self._unpatch()
        self.assertNotIn("isError", result)
        self.assertTrue(result["structuredContent"]["closed"])

    # ---- AC-001: failed AC blocks close ----
    def test_close_rejects_failed_ac(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp, "TASK__pr2-001",
                checks_yaml=(
                    '- id: AC-001\n  title: "done"\n  status: passed\n  kind: functional\n'
                    '- id: AC-002\n  title: "not done"\n  status: failed\n  kind: functional\n'
                ),
            )
            self._patch(td)
            try:
                result = harness_server.call_tool("task_close", {"task_id": "TASK__pr2-001"})
            finally:
                self._unpatch()
        self.assertTrue(result.get("isError"))
        err = result["structuredContent"]
        self.assertIn("CHECKS gate", err["error"])
        blockers = err["blocking_acs"]
        self.assertEqual(len(blockers), 1)
        self.assertEqual(blockers[0]["id"], "AC-002")
        self.assertEqual(blockers[0]["status"], "failed")

    def test_close_rejects_open_ac(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp, "TASK__pr2-001b",
                checks_yaml=(
                    '- id: AC-001\n  title: "ac1"\n  status: open\n  kind: functional\n'
                ),
            )
            self._patch(td)
            try:
                result = harness_server.call_tool("task_close", {"task_id": "TASK__pr2-001b"})
            finally:
                self._unpatch()
        self.assertTrue(result.get("isError"))
        self.assertEqual(result["structuredContent"]["blocking_acs"][0]["status"], "open")

    # ---- AC-002: all-passed closes cleanly ----
    def test_close_passes_with_all_acs_terminal(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp, "TASK__pr2-002",
                checks_yaml=(
                    '- id: AC-001\n  title: "x"\n  status: passed\n  kind: functional\n'
                    '- id: AC-002\n  title: "y"\n  status: deferred\n  kind: functional\n'
                ),
            )
            self._patch(td)
            try:
                result = harness_server.call_tool("task_close", {"task_id": "TASK__pr2-002"})
            finally:
                self._unpatch()
        self.assertNotIn("isError", result)
        self.assertTrue(result["structuredContent"]["closed"])

    def test_close_uses_git_diff_fallback_for_missing_req_when_touched_paths_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".git").mkdir(exist_ok=True)
            (Path(tmp) / "doc/harness").mkdir(parents=True, exist_ok=True)
            (Path(tmp) / "doc/harness/manifest.yaml").write_text(
                "type: browser\nqa:\n  browser_qa_supported: true\n",
                encoding="utf-8",
            )
            td = self._prepare_task(
                tmp,
                "TASK__req-git-fallback",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n',
                touched_paths=[],
            )
            self._patch(td)
            self._patch_repo_root_for_context(tmp)
            self._set_context_git_changed_paths(["src/mobile/Reader.tsx"])
            try:
                result = harness_server.call_tool(
                    "task_close", {"task_id": "TASK__req-git-fallback"}
                )
            finally:
                self._unpatch_repo_root_for_context()
                self._unpatch()
        self.assertTrue(result.get("isError"))
        self.assertIn(
            "REQ durable doc for UI observable behavior",
            result["structuredContent"]["missing_for_close"],
        )

    def test_close_requires_req_for_user_feedback_observable_behavior(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".git").mkdir(exist_ok=True)
            (Path(tmp) / "doc/harness").mkdir(parents=True, exist_ok=True)
            (Path(tmp) / "doc/harness/manifest.yaml").write_text("type: browser\n", encoding="utf-8")
            td = self._prepare_task(
                tmp,
                "TASK__req-feedback",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n',
                touched_paths=[],
            )
            (Path(td) / "USER_FEEDBACK.md").write_text(
                "Native Android APK/emulator back-stack behavior for the reader "
                "must be verified; browser mobile is not enough.\n",
                encoding="utf-8",
            )
            self._patch(td)
            self._patch_repo_root_for_context(tmp)
            self._set_context_git_changed_paths([])
            try:
                result = harness_server.call_tool("task_close", {"task_id": "TASK__req-feedback"})
            finally:
                self._unpatch_repo_root_for_context()
                self._unpatch()
        self.assertTrue(result.get("isError"))
        self.assertIn(
            "REQ durable doc for observable behavior or user feedback",
            result["structuredContent"]["missing_for_close"],
        )

    def test_close_uses_subagent_receipt_not_ux_critic_file_for_cli_surface(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".git").mkdir(exist_ok=True)
            (Path(tmp) / "doc/harness").mkdir(parents=True, exist_ok=True)
            (Path(tmp) / "doc/harness/manifest.yaml").write_text(
                "type: cli\nqa:\n  ux_review_supported: true\n",
                encoding="utf-8",
            )
            source = Path(tmp) / "src/cli/main.py"
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text("print('hi')\n", encoding="utf-8")
            td = self._prepare_task(
                tmp, "TASK__ux-cli-required",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n',
                touched_paths=["src/cli/main.py"],
            )
            self._patch(td)
            self._patch_repo_root_for_context(tmp)
            try:
                result = harness_server.call_tool(
                    "task_close", {"task_id": "TASK__ux-cli-required"}
                )
            finally:
                self._unpatch_repo_root_for_context()
                self._unpatch()
        self.assertNotIn("isError", result)
        self.assertTrue(result["structuredContent"]["closed"])

    def test_close_accepts_required_ux_cli_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".git").mkdir(exist_ok=True)
            (Path(tmp) / "doc/harness").mkdir(parents=True, exist_ok=True)
            (Path(tmp) / "doc/harness/manifest.yaml").write_text(
                "type: cli\nqa:\n  ux_review_supported: true\n",
                encoding="utf-8",
            )
            source = Path(tmp) / "src/cli/main.py"
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text("print('hi')\n", encoding="utf-8")
            td = self._prepare_task(
                tmp, "TASK__ux-cli-pass",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n',
                touched_paths=["src/cli/main.py"],
            )
            self._patch(td)
            self._patch_repo_root_for_context(tmp)
            try:
                result = harness_server.call_tool("task_close", {"task_id": "TASK__ux-cli-pass"})
            finally:
                self._unpatch_repo_root_for_context()
                self._unpatch()
        self.assertNotIn("isError", result)
        self.assertTrue(result["structuredContent"]["closed"])

    def test_close_ignores_absent_or_stale_ux_critic_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".git").mkdir(exist_ok=True)
            (Path(tmp) / "doc/harness").mkdir(parents=True, exist_ok=True)
            (Path(tmp) / "doc/harness/manifest.yaml").write_text(
                "type: cli\nqa:\n  ux_review_supported: true\n",
                encoding="utf-8",
            )
            source = Path(tmp) / "src/cli/main.py"
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text("print('hi')\n", encoding="utf-8")
            td = self._prepare_task(
                tmp, "TASK__ux-cli-stale",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n',
                touched_paths=["src/cli/main.py"],
            )
            critic = Path(td) / "CRITIC__ux.md"
            critic.write_text("stale legacy critic\n", encoding="utf-8")
            future = os.path.getmtime(Path(td) / "SUBAGENT_RECEIPTS.jsonl") + 10
            os.utime(critic, (future, future))
            self._patch(td)
            self._patch_repo_root_for_context(tmp)
            try:
                result = harness_server.call_tool("task_close", {"task_id": "TASK__ux-cli-stale"})
            finally:
                self._unpatch_repo_root_for_context()
                self._unpatch()
        self.assertNotIn("isError", result)
        self.assertTrue(result["structuredContent"]["closed"])

    def test_close_does_not_require_ux_for_non_applicable_library_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".git").mkdir(exist_ok=True)
            (Path(tmp) / "doc/harness").mkdir(parents=True, exist_ok=True)
            (Path(tmp) / "doc/harness/manifest.yaml").write_text(
                "type: library\nqa:\n  ux_review_supported: false\n",
                encoding="utf-8",
            )
            td = self._prepare_task(
                tmp, "TASK__ux-not-applicable",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n',
                touched_paths=["plugin/scripts/_lib.py"],
            )
            self._patch(td)
            self._patch_repo_root_for_context(tmp)
            try:
                result = harness_server.call_tool(
                    "task_close", {"task_id": "TASK__ux-not-applicable"}
                )
            finally:
                self._unpatch_repo_root_for_context()
                self._unpatch()
        self.assertNotIn("isError", result)
        self.assertTrue(result["structuredContent"]["closed"])

    # ---- AC-003: missing CHECKS.yaml warn-passes + logs ----
    def test_close_blocks_present_structurally_invalid_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp,
                "TASK__invalid-checks-close",
                checks_yaml="- id: AC-001\n  status: passed\nunindented garbage\n",
            )
            self._patch(td)
            try:
                result = harness_server.call_tool(
                    "task_close", {"task_id": "TASK__invalid-checks-close"}
                )
            finally:
                self._unpatch()
        self.assertTrue(result.get("isError"))
        self.assertIn("invalid", result["structuredContent"]["error"])

    def test_close_blocks_if_checks_become_invalid_during_final_reread(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp,
                "TASK__checks-toctou",
                checks_yaml="- id: AC-001\n  status: passed\n",
            )
            self._patch(td)
            try:
                with mock.patch.object(
                    harness_server,
                    "_checks_gate_status",
                    side_effect=[("ok", []), ("invalid", [])],
                ):
                    result = harness_server.call_tool(
                        "task_close", {"task_id": "TASK__checks-toctou"}
                    )
            finally:
                self._unpatch()
            state = Path(td, "TASK_STATE.yaml").read_text(encoding="utf-8")
        self.assertTrue(result.get("isError"))
        self.assertTrue(result["structuredContent"]["checks_invalid"])
        self.assertNotIn("status: closed", state)

    def test_close_warn_passes_without_checks_yaml(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(tmp, "TASK__pr2-003", checks_yaml=None)
            self._patch(td)
            try:
                result = harness_server.call_tool("task_close", {"task_id": "TASK__pr2-003"})
            finally:
                self._unpatch()
        self.assertNotIn("isError", result)
        self.assertTrue(result["structuredContent"]["closed"])

    # ---- AC-004: completed QA must be newer than touched source ----
    def test_close_rejects_receipt_when_touched_path_is_newer(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp, "TASK__pr2-004",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n  kind: functional\n',
                touched_paths=["plugin/scripts/health.py"],
            )
            receipt_path = Path(td) / "SUBAGENT_RECEIPTS.jsonl"
            receipts = [json.loads(line) for line in receipt_path.read_text(encoding="utf-8").splitlines()]
            receipts[-1]["ts"] = "2000-01-01T00:00:01Z"
            receipt_path.write_text(
                "".join(json.dumps(item) + "\n" for item in receipts), encoding="utf-8"
            )
            self._patch(td)
            try:
                result = harness_server.call_tool("task_close", {"task_id": "TASK__pr2-004"})
            finally:
                self._unpatch()
        self.assertTrue(result.get("isError"))
        self.assertIn("stale", result["content"][0]["text"])

    # ---- AC-006: task_verify derives PASS from subagent receipt ----
    def test_verify_reports_receipt_pass_without_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp, "TASK__pr2-006",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n  kind: functional\n',
                touched_paths=["plugin/scripts/health.py"],
            )
            self._patch(td)
            try:
                result = harness_server.call_tool("task_verify", {"task_id": "TASK__pr2-006"})
            finally:
                self._unpatch()
            # Read state while tempdir still exists
            state = (Path(td) / "TASK_STATE.yaml").read_text(encoding="utf-8")
        s = result["structuredContent"]
        self.assertFalse(s["stale"])
        self.assertEqual(s["stale_path"], "")
        self.assertEqual(s["runtime_verdict"], "PASS")
        self.assertIn("runtime_verdict: PASS", state)

    def test_stale_skip_list_ignores_pyc(self):
        """Stale check must not trip on Python cache files."""
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp, "TASK__pr2-006b",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n  kind: functional\n',
                touched_paths=["plugin/scripts/__pycache__/health.cpython-311.pyc"],
            )
            self._patch(td)
            try:
                result = harness_server.call_tool("task_close", {"task_id": "TASK__pr2-006b"})
            finally:
                self._unpatch()
        # pyc skip path — should close cleanly (not stale)
        self.assertNotIn("isError", result,
                         f"__pycache__ pyc path should be skipped, not treated as stale: {result}")

    def test_stale_check_ignores_task_artifacts_after_qa(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp, "TASK__pr2-artifact",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n  kind: functional\n',
                touched_paths=["doc/harness/tasks/TASK__pr2-artifact/HANDOFF.md"],
            )
            handoff = Path(td) / "HANDOFF.md"
            handoff.write_text(
                "# handoff after qa\n\n"
                "## Commit-backed Learnings\n\n"
                "Status: none\n\n"
                "## Self-Healing Candidates\n\n"
                "Status: none\n",
                encoding="utf-8",
            )
            self._patch(td)
            try:
                result = harness_server.call_tool("task_close", {"task_id": "TASK__pr2-artifact"})
            finally:
                self._unpatch()
        self.assertNotIn("isError", result)

    def test_stale_check_ignores_deleted_touched_path(self):
        """Deleted files in touched_paths must not stale a fresh QA verdict forever."""
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp, "TASK__pr2-006c",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n  kind: functional\n',
                touched_paths=["plugin/scripts/deleted_install_helper.py"],
            )
            self._patch(td)
            try:
                result = harness_server.call_tool("task_close", {"task_id": "TASK__pr2-006c"})
            finally:
                self._unpatch()
        self.assertNotIn("isError", result,
                         f"deleted touched path should not be permanently stale: {result}")

    def test_close_refreshes_snapshot_and_blocks_if_final_gate_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp, "TASK__close-race",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n  kind: functional\n',
            )
            initial = {"missing_for_close": [], "next_action": "close"}
            changed = {"missing_for_close": ["fresh review receipt"], "next_action": "verify"}
            with (
                mock.patch.object(harness_server, "canonical_task_dir", return_value=td),
                mock.patch.object(harness_server, "sync_from_git_diff", return_value=[]),
                mock.patch.object(harness_server, "emit_compact_context", side_effect=[initial, changed]) as emit,
                mock.patch.object(harness_server, "_runtime_is_stale", side_effect=[(False, ""), (False, "")]),
                mock.patch.object(harness_server, "_checks_gate_status", side_effect=[("passed", []), ("passed", [])]),
                mock.patch.object(harness_server, "refresh_review_snapshot") as refresh,
            ):
                result = harness_server.handle_task_close({"task_id": "TASK__close-race"})

        self.assertTrue(result.get("isError"))
        self.assertIn("final freshness changed", result["content"][0]["text"])
        self.assertEqual(emit.call_count, 2)
        self.assertEqual(refresh.call_count, 2)

    def test_close_marks_active_goal_child_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp,
                "TASK__goal-close-sync",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n  kind: functional\n',
            )
            repo = Path(tmp)
            canonical = repo / "doc/harness/tasks/TASK__goal-close-sync"
            canonical.parent.mkdir(parents=True)
            Path(td).rename(canonical)
            td = str(canonical)
            harness_server.start_harness_goal(
                tmp, "close child", goal_id="GOAL__close-child",
            )
            harness_server.add_goal_task(
                tmp, "TASK__goal-close-sync", status="active",
            )
            clean = {"missing_for_close": [], "next_action": "close"}
            with (
                mock.patch.object(harness_server, "canonical_task_dir", return_value=td),
                mock.patch.object(harness_server, "find_repo_root", return_value=tmp),
                mock.patch.object(harness_server, "sync_from_git_diff", return_value=[]),
                mock.patch.object(harness_server, "emit_compact_context", return_value=clean),
                mock.patch.object(harness_server, "_runtime_is_stale", return_value=(False, "")),
                mock.patch.object(harness_server, "_checks_gate_status", return_value=("passed", [])),
                mock.patch.object(harness_server, "_git_head_for_receipt", return_value="a" * 40),
                mock.patch.object(harness_server, "_workspace_changed_path_fingerprints", return_value={}),
            ):
                result = harness_server.handle_task_close({"task_id": "TASK__goal-close-sync"})

            self.assertNotIn("isError", result)
            current = json.loads(
                (repo / "doc/harness/goals/current.json").read_text(encoding="utf-8")
            )
            self.assertEqual(current["tasks"][0]["status"], "closed")
            self.assertTrue((canonical / "TASK_CLOSE_RECEIPT.json").is_file())

    def test_goal_completion_preserves_closed_child_after_later_child_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            tasks_root = repo / "doc/harness/tasks"
            tasks_root.mkdir(parents=True)
            task_dirs = {}
            for task_id in ("TASK__first-child", "TASK__second-child"):
                prepared = Path(self._prepare_task(
                    tmp,
                    task_id,
                    checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n  kind: functional\n',
                ))
                canonical = tasks_root / task_id
                prepared.rename(canonical)
                task_dirs[task_id] = str(canonical)

            harness_server.start_harness_goal(tmp, "two children", goal_id="GOAL__two-children")
            for task_id in task_dirs:
                harness_server.add_goal_task(tmp, task_id, status="active")

            clean = {"missing_for_close": [], "next_action": "close"}
            for index, (task_id, task_dir) in enumerate(task_dirs.items()):
                with (
                    mock.patch.object(harness_server, "canonical_task_dir", return_value=task_dir),
                    mock.patch.object(harness_server, "find_repo_root", return_value=tmp),
                    mock.patch.object(harness_server, "sync_from_git_diff", return_value=[]),
                    mock.patch.object(harness_server, "emit_compact_context", return_value=clean),
                    mock.patch.object(harness_server, "_runtime_is_stale", return_value=(False, "")),
                    mock.patch.object(harness_server, "_checks_gate_status", return_value=("passed", [])),
                    mock.patch.object(harness_server, "_git_head_for_receipt", return_value="a" * 40),
                    mock.patch.object(harness_server, "_workspace_changed_path_fingerprints", return_value={}),
                ):
                    closed = harness_server.handle_task_close({"task_id": task_id})
                self.assertNotIn("isError", closed)
                if index == 0:
                    (repo / "later-child.py").write_text("changed later\n", encoding="utf-8")

            finished = harness_server.finish_harness_goal(tmp, status="complete")
            self.assertEqual(finished["status"], "complete")

    def test_close_blocks_when_initial_git_head_is_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp, "TASK__head-unavailable",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n  kind: functional\n',
            )
            with (
                mock.patch.object(harness_server, "canonical_task_dir", return_value=td),
                mock.patch.object(harness_server, "sync_from_git_diff", return_value=[]),
                mock.patch.object(harness_server, "_git_head_for_receipt", return_value=""),
            ):
                result = harness_server.handle_task_close({"task_id": "TASK__head-unavailable"})

        self.assertTrue(result.get("isError"))
        self.assertIn("Git HEAD unavailable", result["content"][0]["text"])

    def test_blocked_close_preserves_dirty_snapshot_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp, "TASK__blocked-dirty-warning",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n',
            )
            warning_globals = harness_server.git_snapshot_warnings.__globals__

            def degraded_sync(_task_dir):
                warning_globals["_record_dirty_snapshot_warning"](
                    tmp,
                    "Git changed-path snapshot unavailable: root dirty scan "
                    "budget exhausted before staged diff",
                )
                return []

            with (
                mock.patch.object(harness_server, "canonical_task_dir", return_value=td),
                mock.patch.object(
                    harness_server, "sync_from_git_diff", side_effect=degraded_sync
                ),
                mock.patch.object(harness_server, "_git_head_for_receipt", return_value=""),
            ):
                result = harness_server.handle_task_close(
                    {"task_id": "TASK__blocked-dirty-warning"}
                )

        self.assertTrue(result.get("isError"))
        warnings = result["structuredContent"]["git_snapshot_warnings"]
        self.assertEqual(warnings[0]["code"], "GIT_DIRTY_SNAPSHOT_SKIPPED")

    def test_close_blocks_when_initial_git_snapshot_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp, "TASK__initial-git-failure",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n  kind: functional\n',
            )
            with (
                mock.patch.object(harness_server, "canonical_task_dir", return_value=td),
                mock.patch.object(harness_server, "sync_from_git_diff", return_value=[]),
                mock.patch.object(
                    harness_server, "_workspace_changed_path_fingerprints",
                    side_effect=RuntimeError("snapshot unavailable"),
                ),
            ):
                result = harness_server.handle_task_close({"task_id": "TASK__initial-git-failure"})

        self.assertTrue(result.get("isError"))
        self.assertIn("Git changed-path snapshot unavailable", result["content"][0]["text"])

    def test_close_revalidates_authority_before_state_publication(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp, "TASK__close-authority",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n',
            )
            binding_error = harness_server.GitBindingError(
                "REGISTERED_WORKTREE_BINDING_CHANGED", "late close retarget",
                path="services/front", invariant="request_source_snapshot_binding",
                next_action="retry",
            )
            self._patch(td)
            try:
                with mock.patch.object(
                    harness_server, "revalidate_request_source_authorities",
                    side_effect=binding_error,
                ):
                    result = harness_server.call_tool(
                        "task_close", {"task_id": "TASK__close-authority"}
                    )
            finally:
                self._unpatch()

        self.assertTrue(result.get("isError"))
        self.assertEqual(
            result["structuredContent"]["code"],
            "REGISTERED_WORKTREE_BINDING_CHANGED",
        )

    def test_close_rolls_back_when_authority_changes_during_publication(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp, "TASK__close-publication-authority",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n',
            )
            before = harness_server.read_state(td)
            binding_error = harness_server.GitBindingError(
                "REGISTERED_WORKTREE_BINDING_CHANGED", "retarget during close write",
                path="services/front", invariant="request_source_snapshot_binding",
                next_action="retry",
            )
            self._patch(td)
            try:
                with mock.patch.object(
                    harness_server,
                    "revalidate_request_source_authorities",
                    side_effect=[None, binding_error],
                ):
                    result = harness_server.call_tool(
                        "task_close",
                        {"task_id": "TASK__close-publication-authority"},
                    )
            finally:
                self._unpatch()

            self.assertTrue(result.get("isError"))
            restored = harness_server.read_state(td)
            self.assertEqual(restored["status"], before["status"])
            self.assertEqual(restored["runtime_verdict"], before["runtime_verdict"])
            self.assertFalse(Path(td, "TASK_CLOSE_RECEIPT.json").exists())

    def test_close_blocks_when_final_git_head_is_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp, "TASK__final-head-unavailable",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n  kind: functional\n',
            )
            with (
                mock.patch.object(harness_server, "canonical_task_dir", return_value=td),
                mock.patch.object(harness_server, "sync_from_git_diff", return_value=[]),
                mock.patch.object(harness_server, "_git_head_for_receipt", side_effect=["a" * 40, ""]),
            ):
                result = harness_server.handle_task_close({"task_id": "TASK__final-head-unavailable"})

        self.assertTrue(result.get("isError"))
        self.assertIn("final freshness changed", result["content"][0]["text"])

    def test_close_blocks_when_final_git_snapshot_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp, "TASK__final-git-failure",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n  kind: functional\n',
            )
            with (
                mock.patch.object(harness_server, "canonical_task_dir", return_value=td),
                mock.patch.object(harness_server, "sync_from_git_diff", return_value=[]),
                mock.patch.object(
                    harness_server, "_workspace_changed_path_fingerprints",
                    side_effect=[set(), RuntimeError("snapshot unavailable")],
                ),
            ):
                result = harness_server.handle_task_close({"task_id": "TASK__final-git-failure"})

        self.assertTrue(result.get("isError"))
        self.assertIn("final Git changed-path snapshot unavailable", result["content"][0]["text"])

    def test_close_blocks_when_changed_path_fingerprint_map_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp, "TASK__snapshot-map-race",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n  kind: functional\n',
            )
            with (
                mock.patch.object(harness_server, "canonical_task_dir", return_value=td),
                mock.patch.object(harness_server, "sync_from_git_diff", return_value=[]),
                mock.patch.object(
                    harness_server, "_workspace_changed_path_fingerprints",
                    side_effect=[{"src/a.py": "sha256:old"}, {"src/a.py": "sha256:new"}],
                ),
            ):
                result = harness_server.handle_task_close({"task_id": "TASK__snapshot-map-race"})

        self.assertTrue(result.get("isError"))
        self.assertTrue(result["structuredContent"]["snapshot_changed"])
        self.assertIn("final freshness changed", result["content"][0]["text"])

    def test_handlers_compute_git_path_snapshot_once_per_phase(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp, "TASK__snapshot-count",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n  kind: functional\n',
            )
            globals_ = harness_server.emit_compact_context.__globals__
            original = globals_["_uncached_git_changed_paths"]
            calls = 0

            def counted(repo_root):
                nonlocal calls
                calls += 1
                return original(repo_root)

            with (
                mock.patch.object(harness_server, "canonical_task_dir", return_value=td),
                mock.patch.dict(globals_, {"_uncached_git_changed_paths": counted}),
            ):
                context = harness_server.handle_task_context({"task_id": "TASK__snapshot-count"})
                self.assertNotIn("isError", context)
                self.assertEqual(calls, 1)

                calls = 0
                verified = harness_server.handle_task_verify({"task_id": "TASK__snapshot-count"})
                self.assertNotIn("isError", verified)
                self.assertEqual(calls, 1)

                calls = 0
                closed = harness_server.handle_task_close({"task_id": "TASK__snapshot-count"})
                self.assertNotIn("isError", closed)
                self.assertEqual(calls, 3)

    def test_close_real_source_change_during_refresh_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp, "TASK__source-race",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n  kind: functional\n',
                touched_paths=["plugin/scripts/health.py"],
            )
            source = Path(tmp) / "plugin/scripts/health.py"
            original_refresh = harness_server.refresh_review_snapshot

            def mutate_then_refresh():
                source.write_text("# changed during close\n", encoding="utf-8")
                original_refresh()

            with (
                mock.patch.object(harness_server, "canonical_task_dir", return_value=td),
                mock.patch.object(harness_server, "sync_from_git_diff", return_value=[]),
                mock.patch.object(harness_server, "refresh_review_snapshot", side_effect=mutate_then_refresh),
            ):
                result = harness_server.handle_task_close({"task_id": "TASK__source-race"})

        self.assertTrue(result.get("isError"))
        self.assertIn("final freshness changed", result["content"][0]["text"])

    def test_close_new_untracked_source_during_refresh_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp, "TASK__untracked-race",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n  kind: functional\n',
            )
            source = Path(tmp) / "plugin/scripts/new_during_close.py"
            original_refresh = harness_server.refresh_review_snapshot

            def create_then_refresh():
                source.write_text("VALUE = 1\n", encoding="utf-8")
                original_refresh()

            with (
                mock.patch.object(harness_server, "canonical_task_dir", return_value=td),
                mock.patch.object(harness_server, "sync_from_git_diff", return_value=[]),
                mock.patch.object(harness_server, "refresh_review_snapshot", side_effect=create_then_refresh),
            ):
                result = harness_server.handle_task_close({"task_id": "TASK__untracked-race"})

        self.assertTrue(result.get("isError"))
        self.assertIn("final freshness changed", result["content"][0]["text"])

    def test_close_head_change_during_refresh_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp, "TASK__head-race",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n  kind: functional\n',
            )
            readme = Path(tmp) / "README.md"
            original_refresh = harness_server.refresh_review_snapshot
            mutated = False

            def commit_then_refresh():
                nonlocal mutated
                if not mutated:
                    readme.write_text("# committed during close\n", encoding="utf-8")
                    subprocess.run(["git", "add", "README.md"], cwd=tmp, check=True)
                    subprocess.run(["git", "commit", "-qm", "race commit"], cwd=tmp, check=True)
                    mutated = True
                original_refresh()

            with (
                mock.patch.object(harness_server, "canonical_task_dir", return_value=td),
                mock.patch.object(harness_server, "sync_from_git_diff", return_value=[]),
                mock.patch.object(harness_server, "refresh_review_snapshot", side_effect=commit_then_refresh),
            ):
                result = harness_server.handle_task_close({"task_id": "TASK__head-race"})

        self.assertTrue(result.get("isError"))
        self.assertIn("final freshness changed", result["content"][0]["text"])

    def test_close_head_change_during_final_context_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp, "TASK__late-head-race",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n  kind: functional\n',
            )
            readme = Path(tmp) / "README.md"
            original_emit = harness_server.emit_compact_context
            calls = 0

            def emit_then_commit(task_dir):
                nonlocal calls
                calls += 1
                result = original_emit(task_dir)
                if calls == 2:
                    readme.write_text("# committed during final context\n", encoding="utf-8")
                    subprocess.run(["git", "add", "README.md"], cwd=tmp, check=True)
                    subprocess.run(["git", "commit", "-qm", "late race commit"], cwd=tmp, check=True)
                return result

            with (
                mock.patch.object(harness_server, "canonical_task_dir", return_value=td),
                mock.patch.object(harness_server, "sync_from_git_diff", return_value=[]),
                mock.patch.object(harness_server, "emit_compact_context", side_effect=emit_then_commit),
            ):
                result = harness_server.handle_task_close({"task_id": "TASK__late-head-race"})

        self.assertEqual(calls, 2)
        self.assertTrue(result.get("isError"))
        self.assertIn("final freshness changed", result["content"][0]["text"])

    def test_close_uncommitted_change_during_final_context_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp, "TASK__late-source-race",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n  kind: functional\n',
            )
            source = Path(tmp) / "plugin/scripts/health.py"
            original_emit = harness_server.emit_compact_context
            calls = 0

            def emit_then_mutate(task_dir):
                nonlocal calls
                calls += 1
                result = original_emit(task_dir)
                if calls == 2:
                    source.write_text("# uncommitted during final context\n", encoding="utf-8")
                return result

            with (
                mock.patch.object(harness_server, "canonical_task_dir", return_value=td),
                mock.patch.object(harness_server, "sync_from_git_diff", return_value=[]),
                mock.patch.object(harness_server, "emit_compact_context", side_effect=emit_then_mutate),
            ):
                result = harness_server.handle_task_close({"task_id": "TASK__late-source-race"})

        self.assertEqual(calls, 2)
        self.assertTrue(result.get("isError"))
        self.assertTrue(result["structuredContent"]["snapshot_changed"])
        self.assertIn("final freshness changed", result["content"][0]["text"])

    def test_close_live_receipt_change_during_refresh_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp, "TASK__receipt-race",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n  kind: functional\n',
            )
            receipts = Path(td) / "SUBAGENT_RECEIPTS.jsonl"
            original_refresh = harness_server.refresh_review_snapshot

            def remove_receipts_then_refresh():
                receipts.write_text("", encoding="utf-8")
                original_refresh()

            with (
                mock.patch.object(harness_server, "canonical_task_dir", return_value=td),
                mock.patch.object(harness_server, "sync_from_git_diff", return_value=[]),
                mock.patch.object(harness_server, "refresh_review_snapshot", side_effect=remove_receipts_then_refresh),
            ):
                result = harness_server.handle_task_close({"task_id": "TASK__receipt-race"})

        self.assertTrue(result.get("isError"))
        self.assertIn("final freshness changed", result["content"][0]["text"])

    def test_close_receipt_change_during_final_context_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp, "TASK__late-receipt-race",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n  kind: functional\n',
            )
            receipts = Path(td) / "SUBAGENT_RECEIPTS.jsonl"
            original_emit = harness_server.emit_compact_context
            calls = 0

            def emit_then_remove_receipts(task_dir):
                nonlocal calls
                calls += 1
                result = original_emit(task_dir)
                if calls == 2:
                    receipts.write_text("", encoding="utf-8")
                return result

            with (
                mock.patch.object(harness_server, "canonical_task_dir", return_value=td),
                mock.patch.object(harness_server, "sync_from_git_diff", return_value=[]),
                mock.patch.object(harness_server, "emit_compact_context", side_effect=emit_then_remove_receipts),
            ):
                result = harness_server.handle_task_close({"task_id": "TASK__late-receipt-race"})

        self.assertEqual(calls, 2)
        self.assertTrue(result.get("isError"))
        self.assertTrue(result["structuredContent"]["receipt_stream_changed"])
        self.assertIn("final freshness changed", result["content"][0]["text"])


class HarnessTouchedPathSubmoduleTests(unittest.TestCase):
    def _git(self, cwd: str, *args: str):
        return subprocess.run(
            ["git", *args], cwd=cwd, check=True,
            capture_output=True, text=True,
        )

    def test_sync_from_git_diff_keeps_paths_committed_after_task_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            self._git(tmp, "init", "-q")
            self._git(tmp, "config", "user.email", "t@example.com")
            self._git(tmp, "config", "user.name", "T")
            (repo / ".gitignore").write_text("doc/harness/tasks/\n", encoding="utf-8")
            source = repo / "plugin/file.py"
            source.parent.mkdir(parents=True)
            source.write_text("v1\n", encoding="utf-8")
            self._git(tmp, "add", ".gitignore", "plugin/file.py")
            self._git(tmp, "commit", "-qm", "baseline")
            baseline = self._git(tmp, "rev-parse", "HEAD").stdout.strip()

            task_dir = repo / "doc/harness/tasks/TASK__committed-path"
            task_dir.mkdir(parents=True)
            (task_dir / "TASK_STATE.yaml").write_text(
                "task_id: TASK__committed-path\nstatus: created\n"
                "runtime_verdict: pending\ntouched_paths: []\n",
                encoding="utf-8",
            )
            (task_dir / "TASK_BASELINE.json").write_text(
                json.dumps({
                    "version": 1, "repo_root": str(repo),
                    "head_sha": baseline, "dirty_paths": {},
                }),
                encoding="utf-8",
            )

            source.write_text("v2\n", encoding="utf-8")
            self._git(tmp, "add", "plugin/file.py")
            self._git(tmp, "commit", "-qm", "task change")

            touched = harness_server.sync_from_git_diff(str(task_dir))
            self.assertEqual(touched, ["plugin/file.py"])
            state = harness_server.read_state(str(task_dir))
            self.assertEqual(state["touched_paths"], ["plugin/file.py"])

    def test_sync_from_git_diff_includes_initialized_submodule_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            sub_src = Path(tmp) / "sub-src"
            parent = Path(tmp) / "parent"
            sub_src.mkdir()
            parent.mkdir()
            self._git(str(sub_src), "init", "-q")
            self._git(str(sub_src), "config", "user.email", "t@example.com")
            self._git(str(sub_src), "config", "user.name", "T")
            (sub_src / "api.py").write_text("v1\n", encoding="utf-8")
            self._git(str(sub_src), "add", "api.py")
            self._git(str(sub_src), "commit", "-qm", "init sub")

            self._git(str(parent), "init", "-q")
            self._git(str(parent), "config", "user.email", "t@example.com")
            self._git(str(parent), "config", "user.name", "T")
            self._git(
                str(parent), "-c", "protocol.file.allow=always",
                "submodule", "add", "-q", str(sub_src), "services/api space",
            )
            self._git(str(parent), "commit", "-qm", "add submodule")

            task_dir = parent / "doc" / "harness" / "tasks" / "TASK__submodule"
            task_dir.mkdir(parents=True)
            (task_dir / "TASK_STATE.yaml").write_text(
                "task_id: TASK__submodule\n"
                "status: created\n"
                "runtime_verdict: pending\n"
                "touched_paths: []\n"
                "plan_session_state: closed\n"
                "closed_at: null\n"
                "updated: 2026-01-01T00:00:00Z\n",
                encoding="utf-8",
            )
            (task_dir / "TASK_BASELINE.json").write_text(
                json.dumps({
                    "version": 1,
                    "repo_root": str(parent),
                    "head_sha": self._git(
                        str(parent), "rev-parse", "HEAD",
                    ).stdout.strip(),
                    "dirty_paths": {},
                }),
                encoding="utf-8",
            )
            (parent / "services" / "api space" / "api.py").write_text("v2\n", encoding="utf-8")

            touched = harness_server.sync_from_git_diff(str(task_dir))
            self.assertIn("services/api space/api.py", touched)

    def test_close_blocks_clean_submodule_checkout_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            sub_src = Path(tmp) / "sub-src"
            parent = Path(tmp) / "parent"
            sub_src.mkdir()
            parent.mkdir()
            self._git(str(sub_src), "init", "-q")
            self._git(str(sub_src), "config", "user.email", "t@example.com")
            self._git(str(sub_src), "config", "user.name", "T")
            (sub_src / "api.py").write_text("v1\n", encoding="utf-8")
            self._git(str(sub_src), "add", "api.py")
            self._git(str(sub_src), "commit", "-qm", "v1")
            first = self._git(str(sub_src), "rev-parse", "HEAD").stdout.strip()
            (sub_src / "api.py").write_text("v2\n", encoding="utf-8")
            self._git(str(sub_src), "commit", "-qam", "v2")
            second = self._git(str(sub_src), "rev-parse", "HEAD").stdout.strip()
            self._git(str(sub_src), "checkout", "-q", first)

            self._git(str(parent), "init", "-q")
            self._git(str(parent), "config", "user.email", "t@example.com")
            self._git(str(parent), "config", "user.name", "T")
            self._git(
                str(parent), "-c", "protocol.file.allow=always",
                "submodule", "add", "-q", str(sub_src), "services/api space",
            )
            self._git(str(parent), "commit", "-qm", "add submodule")

            gate = HarnessMcpServerPR2CloseGate()
            td = gate._prepare_task(
                str(parent), "TASK__submodule-head-race",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n  kind: functional\n',
            )
            initial = {"missing_for_close": [], "next_action": "close"}
            calls = 0

            def emit_then_checkout(task_dir):
                nonlocal calls
                calls += 1
                if calls == 2:
                    self._git(str(parent / "services/api space"), "checkout", "-q", second)
                return initial

            with (
                mock.patch.object(harness_server, "canonical_task_dir", return_value=td),
                mock.patch.object(harness_server, "sync_from_git_diff", return_value=[]),
                mock.patch.object(harness_server, "emit_compact_context", side_effect=emit_then_checkout),
                mock.patch.object(harness_server, "_runtime_is_stale", side_effect=[(False, ""), (False, "")]),
                mock.patch.object(harness_server, "_checks_gate_status", side_effect=[("passed", []), ("passed", [])]),
            ):
                result = harness_server.handle_task_close({"task_id": "TASK__submodule-head-race"})

            self.assertTrue(result.get("isError"))
            self.assertTrue(result["structuredContent"]["snapshot_changed"])

    def test_close_blocks_staged_gitlink_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            sub_src = Path(tmp) / "sub-src"
            parent = Path(tmp) / "parent"
            sub_src.mkdir()
            parent.mkdir()
            self._git(str(sub_src), "init", "-q")
            self._git(str(sub_src), "config", "user.email", "t@example.com")
            self._git(str(sub_src), "config", "user.name", "T")
            (sub_src / "api.py").write_text("v1\n", encoding="utf-8")
            self._git(str(sub_src), "add", "api.py")
            self._git(str(sub_src), "commit", "-qm", "v1")
            first = self._git(str(sub_src), "rev-parse", "HEAD").stdout.strip()
            (sub_src / "api.py").write_text("v2\n", encoding="utf-8")
            self._git(str(sub_src), "commit", "-qam", "v2")
            second = self._git(str(sub_src), "rev-parse", "HEAD").stdout.strip()
            self._git(str(sub_src), "checkout", "-q", first)

            self._git(str(parent), "init", "-q")
            self._git(str(parent), "config", "user.email", "t@example.com")
            self._git(str(parent), "config", "user.name", "T")
            self._git(
                str(parent), "-c", "protocol.file.allow=always",
                "submodule", "add", "-q", str(sub_src), "services/api space",
            )
            self._git(str(parent), "commit", "-qm", "add submodule")

            gate = HarnessMcpServerPR2CloseGate()
            td = gate._prepare_task(
                str(parent), "TASK__submodule-index-race",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n  kind: functional\n',
            )
            initial = {"missing_for_close": [], "next_action": "close"}
            calls = 0

            def emit_then_stage_gitlink(task_dir):
                nonlocal calls
                calls += 1
                if calls == 2:
                    self._git(
                        str(parent), "update-index", "--cacheinfo",
                        f"160000,{second},services/api space",
                    )
                return initial

            with (
                mock.patch.object(harness_server, "canonical_task_dir", return_value=td),
                mock.patch.object(harness_server, "sync_from_git_diff", return_value=[]),
                mock.patch.object(harness_server, "emit_compact_context", side_effect=emit_then_stage_gitlink),
                mock.patch.object(harness_server, "_runtime_is_stale", side_effect=[(False, ""), (False, "")]),
                mock.patch.object(harness_server, "_checks_gate_status", side_effect=[("passed", []), ("passed", [])]),
            ):
                result = harness_server.handle_task_close({"task_id": "TASK__submodule-index-race"})

            self.assertTrue(result.get("isError"))
            self.assertTrue(result["structuredContent"]["snapshot_changed"])

    def test_close_blocks_uninitialized_gitlink_index_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            sub_src = Path(tmp) / "sub-src"
            parent = Path(tmp) / "parent"
            sub_src.mkdir()
            parent.mkdir()
            for repo in (sub_src, parent):
                self._git(str(repo), "init", "-q")
                self._git(str(repo), "config", "user.email", "t@example.com")
                self._git(str(repo), "config", "user.name", "T")
            (sub_src / "api.py").write_text("v1\n", encoding="utf-8")
            self._git(str(sub_src), "add", "api.py")
            self._git(str(sub_src), "commit", "-qm", "v1")
            first = self._git(str(sub_src), "rev-parse", "HEAD").stdout.strip()
            (sub_src / "api.py").write_text("v2\n", encoding="utf-8")
            self._git(str(sub_src), "commit", "-qam", "v2")
            second = self._git(str(sub_src), "rev-parse", "HEAD").stdout.strip()
            self._git(
                str(parent), "update-index", "--add", "--cacheinfo",
                f"160000,{first},ghost-sub",
            )
            self._git(str(parent), "commit", "-qm", "add uninitialized gitlink")

            gate = HarnessMcpServerPR2CloseGate()
            td = gate._prepare_task(
                str(parent), "TASK__uninitialized-gitlink-race",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n  kind: functional\n',
            )
            initial = {"missing_for_close": [], "next_action": "close"}
            calls = 0

            def emit_then_stage_gitlink(task_dir):
                nonlocal calls
                calls += 1
                if calls == 2:
                    self._git(
                        str(parent), "update-index", "--add", "--cacheinfo",
                        f"160000,{second},ghost-sub",
                    )
                return initial

            with (
                mock.patch.object(harness_server, "canonical_task_dir", return_value=td),
                mock.patch.object(harness_server, "sync_from_git_diff", return_value=[]),
                mock.patch.object(harness_server, "emit_compact_context", side_effect=emit_then_stage_gitlink),
                mock.patch.object(harness_server, "_runtime_is_stale", side_effect=[(False, ""), (False, "")]),
                mock.patch.object(harness_server, "_checks_gate_status", side_effect=[("passed", []), ("passed", [])]),
            ):
                result = harness_server.handle_task_close(
                    {"task_id": "TASK__uninitialized-gitlink-race"},
                )

            self.assertTrue(result.get("isError"))
            self.assertTrue(result["structuredContent"]["snapshot_changed"])


if __name__ == "__main__":
    unittest.main()
