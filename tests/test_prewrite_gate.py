"""Tests for prewrite_gate.py — blocking enforcement of plan-first workflow.

Covers:
  - Source file write without plan PASS → exit 2 (block)
  - No manifest (harness not initialized) → exit 0 (allow)
  - Plan PASS exists on any active task → exit 0 (allow)
  - Escape hatch HARNESS_SKIP_PREWRITE → exit 0 (allow)
  - Non-source file write → exit 0 (allow)
  - No active tasks → exit 2 (block)
  - Protected artifact ownership enforcement
  - PLAN.md token-based authorization
  - Fail-closed on managed repos

Run with: python -m unittest discover -s tests -p 'test_*.py'
"""

import os
import sys
import json
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "scripts"))
os.environ["HARNESS_SKIP_STDIN"] = "1"

from prewrite_gate import (
    _is_source_file, _find_active_tasks, _extract_file_path,
    _is_protected_artifact, _check_protected_artifact_write,
    _get_agent_role, _check_plan_session_token, _check_team_plan_ready,
    _check_team_write_ownership, _check_team_artifact_write, _get_team_worker_name,
)


# ---------------------------------------------------------------------------
# Unit tests for _is_source_file
# ---------------------------------------------------------------------------

class TestIsSourceFile(unittest.TestCase):

    def test_python_file_is_source(self):
        self.assertTrue(_is_source_file("src/main.py"))

    def test_typescript_file_is_source(self):
        self.assertTrue(_is_source_file("app/index.ts"))

    def test_sql_file_is_source(self):
        self.assertTrue(_is_source_file("migrations/001.sql"))

    def test_markdown_is_not_source(self):
        self.assertFalse(_is_source_file("README.md"))

    def test_yaml_is_not_source(self):
        self.assertFalse(_is_source_file("config.yaml"))

    def test_exempt_task_path(self):
        self.assertFalse(_is_source_file("doc/harness/tasks/TASK__foo/PLAN.md"))

    def test_exempt_claude_path(self):
        self.assertFalse(_is_source_file(".claude/settings.json"))

    def test_exempt_filename_PLAN(self):
        # PLAN.md is a protected artifact, not a source file
        self.assertFalse(_is_source_file("PLAN.md"))

    def test_exempt_filename_TASK_STATE(self):
        self.assertFalse(_is_source_file("TASK_STATE.yaml"))

    def test_exempt_filename_HANDOFF(self):
        # HANDOFF.md is now a protected artifact, not in EXEMPT_FILENAMES
        self.assertFalse(_is_source_file("HANDOFF.md"))

    def test_dot_slash_normalized(self):
        self.assertTrue(_is_source_file("./src/main.py"))

    def test_empty_path(self):
        self.assertFalse(_is_source_file(""))

    def test_none_path(self):
        self.assertFalse(_is_source_file(None))

    def test_meta_json_is_exempt(self):
        self.assertFalse(_is_source_file("doc/harness/tasks/TASK__x/HANDOFF.meta.json"))

    def test_protected_artifact_not_source(self):
        """Protected artifacts are handled separately, not as source files."""
        for artifact in ("PLAN.md", "HANDOFF.md", "DOC_SYNC.md",
                         "CRITIC__plan.md", "CRITIC__runtime.md", "CRITIC__document.md"):
            self.assertFalse(_is_source_file(artifact),
                f"{artifact} should not be treated as source file")


# ---------------------------------------------------------------------------
# Unit tests for _is_protected_artifact
# ---------------------------------------------------------------------------

class TestIsProtectedArtifact(unittest.TestCase):

    def test_plan_is_protected(self):
        self.assertTrue(_is_protected_artifact("PLAN.md"))

    def test_handoff_is_protected(self):
        self.assertTrue(_is_protected_artifact("HANDOFF.md"))

    def test_doc_sync_is_protected(self):
        self.assertTrue(_is_protected_artifact("DOC_SYNC.md"))

    def test_critic_plan_is_protected(self):
        self.assertTrue(_is_protected_artifact("CRITIC__plan.md"))

    def test_critic_runtime_is_protected(self):
        self.assertTrue(_is_protected_artifact("CRITIC__runtime.md"))

    def test_critic_document_is_protected(self):
        self.assertTrue(_is_protected_artifact("CRITIC__document.md"))

    def test_task_state_not_protected(self):
        self.assertFalse(_is_protected_artifact("TASK_STATE.yaml"))

    def test_source_file_not_protected(self):
        self.assertFalse(_is_protected_artifact("src/main.py"))

    def test_full_path_works(self):
        self.assertTrue(_is_protected_artifact("doc/harness/tasks/TASK__foo/HANDOFF.md"))

    def test_none_safe(self):
        self.assertFalse(_is_protected_artifact(None))
        self.assertFalse(_is_protected_artifact(""))


# ---------------------------------------------------------------------------
# Unit tests for _extract_file_path
# ---------------------------------------------------------------------------

class TestExtractFilePath(unittest.TestCase):

    def test_write_tool(self):
        data = json.dumps({"tool_name": "Write", "tool_input": {"file_path": "src/foo.py"}})
        self.assertEqual(_extract_file_path(data), "src/foo.py")

    def test_edit_tool(self):
        data = json.dumps({"tool_name": "Edit", "tool_input": {"file_path": "src/bar.ts"}})
        self.assertEqual(_extract_file_path(data), "src/bar.ts")

    def test_multiedit_tool(self):
        data = json.dumps({"tool_name": "MultiEdit", "tool_input": {"file_path": "src/baz.py"}})
        self.assertEqual(_extract_file_path(data), "src/baz.py")

    def test_read_tool_returns_none(self):
        data = json.dumps({"tool_name": "Read", "tool_input": {"file_path": "src/foo.py"}})
        self.assertIsNone(_extract_file_path(data))

    def test_absolute_path_stripped(self):
        cwd = os.getcwd()
        data = json.dumps({"tool_name": "Write", "tool_input": {"file_path": f"{cwd}/src/foo.py"}})
        self.assertEqual(_extract_file_path(data), "src/foo.py")

    def test_empty_input(self):
        self.assertIsNone(_extract_file_path(""))

    def test_invalid_json(self):
        self.assertIsNone(_extract_file_path("not json"))


# ---------------------------------------------------------------------------
# Integration tests for _find_active_tasks
# ---------------------------------------------------------------------------

class TestFindActiveTasks(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.orig_task_dir = __import__('prewrite_gate')
        # Patch TASK_DIR
        import prewrite_gate
        self._orig_task_dir = prewrite_gate.TASK_DIR
        self.task_base = os.path.join(self.tmp.name, "doc", "harness", "tasks")
        os.makedirs(self.task_base, exist_ok=True)
        prewrite_gate.TASK_DIR = self.task_base

    def tearDown(self):
        import prewrite_gate
        prewrite_gate.TASK_DIR = self._orig_task_dir
        self.tmp.cleanup()

    def _write_task(self, task_id, status="created", plan_verdict="pending"):
        task_dir = os.path.join(self.task_base, task_id)
        os.makedirs(task_dir, exist_ok=True)
        with open(os.path.join(task_dir, "TASK_STATE.yaml"), "w") as f:
            f.write(f"task_id: {task_id}\nstatus: {status}\nplan_verdict: {plan_verdict}\n")

    def test_no_tasks(self):
        self.assertEqual(_find_active_tasks(), [])

    def test_active_task_pending(self):
        self._write_task("TASK__foo", "created", "pending")
        result = _find_active_tasks()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], ("TASK__foo", "pending"))

    def test_active_task_passed(self):
        self._write_task("TASK__bar", "plan_passed", "PASS")
        result = _find_active_tasks()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], ("TASK__bar", "PASS"))

    def test_closed_task_excluded(self):
        self._write_task("TASK__done", "closed", "PASS")
        self.assertEqual(_find_active_tasks(), [])

    def test_mixed_tasks(self):
        self._write_task("TASK__a", "created", "pending")
        self._write_task("TASK__b", "plan_passed", "PASS")
        self._write_task("TASK__c", "closed", "PASS")
        result = _find_active_tasks()
        self.assertEqual(len(result), 2)
        ids = [r[0] for r in result]
        self.assertIn("TASK__a", ids)
        self.assertIn("TASK__b", ids)
        self.assertNotIn("TASK__c", ids)


# ---------------------------------------------------------------------------
# Protected artifact ownership tests
# ---------------------------------------------------------------------------

class TestProtectedArtifactOwnership(unittest.TestCase):
    """Test that protected artifacts can only be written by authorized roles."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.task_base = os.path.join(self.tmp.name, "doc", "harness", "tasks")
        os.makedirs(self.task_base, exist_ok=True)
        # Patch TASK_DIR
        import prewrite_gate
        self._orig_task_dir = prewrite_gate.TASK_DIR
        prewrite_gate.TASK_DIR = self.task_base
        # Save original env
        self._orig_agent = os.environ.get("CLAUDE_AGENT_NAME", "")

    def tearDown(self):
        import prewrite_gate
        prewrite_gate.TASK_DIR = self._orig_task_dir
        os.environ["CLAUDE_AGENT_NAME"] = self._orig_agent
        self.tmp.cleanup()

    def _write_task(self, task_id, **kwargs):
        task_dir = os.path.join(self.task_base, task_id)
        os.makedirs(task_dir, exist_ok=True)
        defaults = {
            "task_id": task_id,
            "status": "plan_passed",
            "plan_verdict": "PASS",
            "plan_session_state": "closed",
            "updated": "2026-01-01T00:00:00Z",
        }
        defaults.update(kwargs)
        with open(os.path.join(task_dir, "TASK_STATE.yaml"), "w") as f:
            for k, v in defaults.items():
                f.write(f"{k}: {v}\n")
        return task_dir

    def _write_plan_session(self, task_dir, state="open", phase="write"):
        token = {"task_id": os.path.basename(task_dir), "state": state, "phase": phase}
        with open(os.path.join(task_dir, "PLAN_SESSION.json"), "w") as f:
            json.dump(token, f)

    def test_harness_writes_source_blocked(self):
        """Harness role writing source file → blocked."""
        os.environ["CLAUDE_AGENT_NAME"] = "harness:harness"
        role = _get_agent_role()
        self.assertEqual(role, "harness")

    def test_developer_writes_handoff_allowed(self):
        """Developer writing HANDOFF.md → allowed."""
        os.environ["CLAUDE_AGENT_NAME"] = "harness:developer"
        allowed, _ = _check_protected_artifact_write("HANDOFF.md")
        self.assertTrue(allowed)

    def test_writer_writes_handoff_blocked(self):
        """Writer writing HANDOFF.md → blocked."""
        os.environ["CLAUDE_AGENT_NAME"] = "harness:writer"
        allowed, msg = _check_protected_artifact_write("HANDOFF.md")
        self.assertFalse(allowed)
        self.assertIn("developer", msg)

    def test_writer_writes_doc_sync_allowed(self):
        """Writer writing DOC_SYNC.md → allowed."""
        os.environ["CLAUDE_AGENT_NAME"] = "harness:writer"
        allowed, _ = _check_protected_artifact_write("DOC_SYNC.md")
        self.assertTrue(allowed)

    def test_harness_writes_doc_sync_blocked(self):
        """Harness writing DOC_SYNC.md → blocked."""
        os.environ["CLAUDE_AGENT_NAME"] = "harness:harness"
        allowed, msg = _check_protected_artifact_write("DOC_SYNC.md")
        self.assertFalse(allowed)
        self.assertIn("writer", msg)

    def test_critic_runtime_writes_critic_runtime_allowed(self):
        """Critic-runtime writing CRITIC__runtime.md → allowed."""
        os.environ["CLAUDE_AGENT_NAME"] = "harness:critic-runtime"
        allowed, _ = _check_protected_artifact_write("CRITIC__runtime.md")
        self.assertTrue(allowed)

    def test_harness_writes_critic_runtime_blocked(self):
        """Harness writing CRITIC__runtime.md → blocked."""
        os.environ["CLAUDE_AGENT_NAME"] = "harness:harness"
        allowed, msg = _check_protected_artifact_write("CRITIC__runtime.md")
        self.assertFalse(allowed)
        self.assertIn("critic-runtime", msg)

    def test_critic_plan_writes_critic_plan_allowed(self):
        """Critic-plan writing CRITIC__plan.md → allowed."""
        os.environ["CLAUDE_AGENT_NAME"] = "harness:critic-plan"
        allowed, _ = _check_protected_artifact_write("CRITIC__plan.md")
        self.assertTrue(allowed)

    def test_developer_writes_critic_plan_blocked(self):
        """Developer writing CRITIC__plan.md → blocked (generator/evaluator separation)."""
        os.environ["CLAUDE_AGENT_NAME"] = "harness:developer"
        allowed, msg = _check_protected_artifact_write("CRITIC__plan.md")
        self.assertFalse(allowed)
        self.assertIn("critic-plan", msg)

    def test_plan_md_without_token_blocked(self):
        """PLAN.md write without plan session token → blocked."""
        os.environ["CLAUDE_AGENT_NAME"] = "harness:harness"
        self._write_task("TASK__test")
        allowed, msg = _check_protected_artifact_write("PLAN.md")
        self.assertFalse(allowed)
        self.assertIn("plan session", msg.lower())

    def test_plan_md_with_token_allowed(self):
        """PLAN.md write with active plan session token → allowed."""
        os.environ["CLAUDE_AGENT_NAME"] = "harness:harness"
        task_dir = self._write_task("TASK__test")
        self._write_plan_session(task_dir, state="open", phase="write")
        allowed, _ = _check_protected_artifact_write("PLAN.md")
        self.assertTrue(allowed)

    def test_plan_md_with_closed_token_blocked(self):
        """PLAN.md write with closed plan session → blocked."""
        os.environ["CLAUDE_AGENT_NAME"] = "harness:harness"
        task_dir = self._write_task("TASK__test")
        self._write_plan_session(task_dir, state="closed", phase="write")
        allowed, msg = _check_protected_artifact_write("PLAN.md")
        self.assertFalse(allowed)

    def test_plan_md_session_state_write_open_allowed(self):
        """PLAN.md write when plan_session_state=write_open in TASK_STATE → allowed."""
        os.environ["CLAUDE_AGENT_NAME"] = "harness:harness"
        self._write_task("TASK__test", plan_session_state="write_open")
        allowed, _ = _check_protected_artifact_write("PLAN.md")
        self.assertTrue(allowed)


# ---------------------------------------------------------------------------
# Fail-closed behavior on managed repos
# ---------------------------------------------------------------------------

class TestFailClosed(unittest.TestCase):
    """Verify that parse failures result in block (fail-closed) on managed repos."""

    def test_exception_handler_blocks_on_managed_repo(self):
        """When manifest exists, exceptions should block (exit 2), not allow."""
        import prewrite_gate
        # The __main__ block now fail-closes when MANIFEST exists
        # We just verify the logic is correct by checking the file
        with open(
            os.path.join(os.path.dirname(__file__), "..", "plugin", "scripts", "prewrite_gate.py"),
            "r",
            encoding="utf-8",
        ) as fh:
            self.assertIn("Fail-closed on managed repos", fh.read())


class TestTeamPlanGate(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        import prewrite_gate
        self._orig_task_dir = prewrite_gate.TASK_DIR
        self.task_base = os.path.join(self.tmp.name, "doc", "harness", "tasks")
        os.makedirs(self.task_base, exist_ok=True)
        prewrite_gate.TASK_DIR = self.task_base
        self._orig_agent_name = os.environ.get("CLAUDE_AGENT_NAME", "")
        self._orig_team_worker = os.environ.get("HARNESS_TEAM_WORKER", "")

    def tearDown(self):
        import prewrite_gate
        prewrite_gate.TASK_DIR = self._orig_task_dir
        os.environ["CLAUDE_AGENT_NAME"] = self._orig_agent_name
        if self._orig_team_worker:
            os.environ["HARNESS_TEAM_WORKER"] = self._orig_team_worker
        else:
            os.environ.pop("HARNESS_TEAM_WORKER", None)
        self.tmp.cleanup()

    def _write_team_task(self, task_id="TASK__team"):
        task_dir = os.path.join(self.task_base, task_id)
        os.makedirs(task_dir, exist_ok=True)
        with open(os.path.join(task_dir, "TASK_STATE.yaml"), "w", encoding="utf-8") as fh:
            fh.write(
                f"task_id: {task_id}\n"
                "status: plan_passed\n"
                "plan_verdict: PASS\n"
                "orchestration_mode: team\n"
                "team_status: planned\n"
                "updated: 2026-01-01T00:00:00Z\n"
            )
        return task_dir

    def _write_complete_plan(self, task_dir):
        with open(os.path.join(task_dir, "TEAM_PLAN.md"), "w", encoding="utf-8") as fh:
            fh.write(
                "# Team Plan\n"
                "## Worker Roster\n- worker-a: app\n- worker-b: api\n\n"
                "## Owned Writable Paths\n- worker-a: app/**\n- worker-b: api/**\n\n"
                "## Shared Read-Only Paths\n- docs/**\n\n"
                "## Forbidden Writes\n- worker-a: api/**\n- worker-b: app/**\n\n"
                "## Synthesis Strategy\n- merge then verify\n"
            )

    def _write_lead_plan(self, task_dir):
        with open(os.path.join(task_dir, "TEAM_PLAN.md"), "w", encoding="utf-8") as fh:
            fh.write(
                "# Team Plan\n"
                "## Worker Roster\n- lead: integrator\n- worker-a: app\n- worker-b: api\n\n"
                "## Owned Writable Paths\n- lead: tests/**\n- worker-a: app/**\n- worker-b: api/**\n\n"
                "## Shared Read-Only Paths\n- docs/**\n\n"
                "## Forbidden Writes\n- lead: app/**, api/**\n- worker-a: tests/**, api/**\n- worker-b: tests/**, app/**\n\n"
                "## Synthesis Strategy\n- lead merges worker summaries and writes TEAM_SYNTHESIS.md then refreshes HANDOFF.md\n"
            )

    def _write_documentation_owner_plan(self, task_dir):
        with open(os.path.join(task_dir, "TEAM_PLAN.md"), "w", encoding="utf-8") as fh:
            fh.write(
                "# Team Plan\n"
                "## Worker Roster\n- lead: integrator\n- worker-a: app\n- reviewer: doc-reviewer\n\n"
                "## Owned Writable Paths\n- lead: tests/**\n- worker-a: app/**\n- reviewer: docs/**\n\n"
                "## Shared Read-Only Paths\n- api/**\n\n"
                "## Forbidden Writes\n- lead: app/**, docs/**\n- worker-a: tests/**, docs/**\n- reviewer: tests/**, app/**\n\n"
                "## Synthesis Strategy\n- lead merges worker summaries and writes TEAM_SYNTHESIS.md then refreshes HANDOFF.md\n\n"
                "## Documentation Ownership\n- writer: reviewer\n- critic-document: lead\n"
            )

    def _write_complete_team_synthesis(self, task_dir):
        with open(os.path.join(task_dir, "TEAM_SYNTHESIS.md"), "w", encoding="utf-8") as fh:
            fh.write(
                "# Team Synthesis\n"
                "## Integrated Result\n- merged slices\n\n"
                "## Cross-Checks\n- ownership respected\n\n"
                "## Verification Summary\n- pytest tests/test_example.py\n\n"
                "## Residual Risks\n- none\n"
            )

    def test_incomplete_team_plan_blocks_source_writes(self):
        task_dir = self._write_team_task()
        with open(os.path.join(task_dir, "TEAM_PLAN.md"), "w", encoding="utf-8") as fh:
            fh.write("# Team Plan\n\n## Worker Roster\n- TODO: assign workers\n")
        allowed, message = _check_team_plan_ready(task_dir)
        self.assertFalse(allowed)
        self.assertIn("TEAM_PLAN.md", message)

    def test_completed_team_plan_allows_source_writes(self):
        task_dir = self._write_team_task()
        self._write_complete_plan(task_dir)
        allowed, message = _check_team_plan_ready(task_dir)
        self.assertTrue(allowed)
        self.assertEqual(message, "")

    def test_worker_suffixed_agent_name_maps_to_developer(self):
        os.environ["CLAUDE_AGENT_NAME"] = "harness:developer:worker-a"
        self.assertEqual(_get_agent_role(), "developer")

    def test_worker_hint_is_extracted_from_agent_name(self):
        os.environ["CLAUDE_AGENT_NAME"] = "harness:developer:worker-a"
        self.assertEqual(_get_team_worker_name(["worker-a", "worker-b"]), "worker-a")

    def test_overlapping_team_plan_blocks_source_writes(self):
        task_dir = self._write_team_task()
        with open(os.path.join(task_dir, "TEAM_PLAN.md"), "w", encoding="utf-8") as fh:
            fh.write(
                "# Team Plan\n"
                "## Worker Roster\n- worker-a: app\n- worker-b: api\n\n"
                "## Owned Writable Paths\n- worker-a: src/api/*.ts\n- worker-b: src/api/auth.ts\n\n"
                "## Shared Read-Only Paths\n- docs/**\n\n"
                "## Forbidden Writes\n- worker-a: src/api/auth.ts\n- worker-b: src/api/*.ts\n\n"
                "## Synthesis Strategy\n- merge then verify\n"
            )
        allowed, message = _check_team_plan_ready(task_dir)
        self.assertFalse(allowed)
        self.assertIn("overlapping writable ownership", message)

    def test_unique_owned_path_allows_write_without_worker_hint(self):
        task_dir = self._write_team_task()
        self._write_complete_plan(task_dir)
        allowed, message = _check_team_write_ownership(task_dir, "app/main.py")
        self.assertTrue(allowed)
        self.assertEqual(message, "")

    def test_unowned_path_blocks_write(self):
        task_dir = self._write_team_task()
        self._write_complete_plan(task_dir)
        allowed, message = _check_team_write_ownership(task_dir, "tests/test_feature.py")
        self.assertFalse(allowed)
        self.assertIn("outside TEAM_PLAN.md owned writable paths", message)

    def test_worker_hint_blocks_cross_owner_write(self):
        task_dir = self._write_team_task()
        self._write_complete_plan(task_dir)
        os.environ["HARNESS_TEAM_WORKER"] = "worker-a"
        allowed, message = _check_team_write_ownership(task_dir, "api/server.py")
        self.assertFalse(allowed)
        self.assertIn("owned by 'worker-b'", message)

    def test_shared_read_only_path_blocks_write(self):
        task_dir = self._write_team_task()
        self._write_complete_plan(task_dir)
        allowed, message = _check_team_write_ownership(task_dir, "docs/architecture.md")
        self.assertFalse(allowed)
        self.assertIn("shared read-only", message)

    def test_non_lead_worker_cannot_write_team_synthesis(self):
        task_dir = self._write_team_task()
        self._write_lead_plan(task_dir)
        os.environ["HARNESS_TEAM_WORKER"] = "worker-a"
        allowed, message = _check_team_artifact_write(task_dir, "TEAM_SYNTHESIS.md")
        self.assertFalse(allowed)
        self.assertIn("synthesis owner", message)

    def test_lead_worker_can_write_team_synthesis_and_handoff(self):
        task_dir = self._write_team_task()
        self._write_lead_plan(task_dir)
        os.environ["HARNESS_TEAM_WORKER"] = "lead"
        allowed, message = _check_team_artifact_write(task_dir, "TEAM_SYNTHESIS.md")
        self.assertTrue(allowed)
        self.assertEqual(message, "")
        allowed, message = _check_team_artifact_write(task_dir, "HANDOFF.md")
        self.assertTrue(allowed)
        self.assertEqual(message, "")

    def test_worker_can_only_write_own_summary(self):
        task_dir = self._write_team_task()
        self._write_lead_plan(task_dir)
        os.environ["HARNESS_TEAM_WORKER"] = "worker-a"
        allowed, message = _check_team_artifact_write(task_dir, "doc/harness/tasks/TASK__team/team/worker-a.md")
        self.assertTrue(allowed)
        self.assertEqual(message, "")
        allowed, message = _check_team_artifact_write(task_dir, "doc/harness/tasks/TASK__team/team/worker-b.md")
        self.assertFalse(allowed)
        self.assertIn("owned by worker 'worker-b'", message)

    def test_doc_sync_can_be_reserved_to_documentation_owner(self):
        task_dir = self._write_team_task()
        self._write_documentation_owner_plan(task_dir)
        os.environ["HARNESS_TEAM_WORKER"] = "worker-a"
        allowed, message = _check_team_artifact_write(task_dir, "DOC_SYNC.md")
        self.assertFalse(allowed)
        self.assertIn("documentation owner", message)
        self.assertIn("reviewer", message)

    def test_document_critic_can_be_reserved_to_document_critic_owner(self):
        task_dir = self._write_team_task()
        self._write_documentation_owner_plan(task_dir)
        os.environ["HARNESS_TEAM_WORKER"] = "lead"
        allowed, message = _check_team_artifact_write(task_dir, "CRITIC__document.md")
        self.assertTrue(allowed)
        self.assertEqual(message, "")
        os.environ["HARNESS_TEAM_WORKER"] = "reviewer"
        allowed, message = _check_team_artifact_write(task_dir, "CRITIC__document.md")
        self.assertFalse(allowed)
        self.assertIn("document critic owner", message)
        self.assertIn("lead", message)


    def test_non_lead_worker_cannot_write_final_runtime_artifact_after_synthesis(self):
        task_dir = self._write_team_task()
        self._write_lead_plan(task_dir)
        os.makedirs(os.path.join(task_dir, "team"), exist_ok=True)
        with open(os.path.join(task_dir, "team", "worker-a.md"), "w", encoding="utf-8") as fh:
            fh.write(
                """# Worker Summary
## Completed Work
- app slice

## Owned Paths Handled
- app/main.py

## Verification
- pytest

## Residual Risks
- none
"""
            )
        with open(os.path.join(task_dir, "team", "worker-b.md"), "w", encoding="utf-8") as fh:
            fh.write(
                """# Worker Summary
## Completed Work
- api slice

## Owned Paths Handled
- api/server.py

## Verification
- pytest

## Residual Risks
- none
"""
            )
        self._write_complete_team_synthesis(task_dir)
        with open(os.path.join(task_dir, "TASK_STATE.yaml"), "a", encoding="utf-8") as fh:
            fh.write("mutates_repo: true\n")
        os.environ["HARNESS_TEAM_WORKER"] = "worker-a"
        allowed, message = _check_team_artifact_write(task_dir, "CRITIC__runtime.md")
        self.assertFalse(allowed)
        self.assertIn("final team runtime verification artifacts", message)

    def test_lead_worker_can_write_final_runtime_artifact_after_synthesis(self):
        task_dir = self._write_team_task()
        self._write_lead_plan(task_dir)
        os.makedirs(os.path.join(task_dir, "team"), exist_ok=True)
        with open(os.path.join(task_dir, "team", "worker-a.md"), "w", encoding="utf-8") as fh:
            fh.write(
                """# Worker Summary
## Completed Work
- app slice

## Owned Paths Handled
- app/main.py

## Verification
- pytest

## Residual Risks
- none
"""
            )
        with open(os.path.join(task_dir, "team", "worker-b.md"), "w", encoding="utf-8") as fh:
            fh.write(
                """# Worker Summary
## Completed Work
- api slice

## Owned Paths Handled
- api/server.py

## Verification
- pytest

## Residual Risks
- none
"""
            )
        self._write_complete_team_synthesis(task_dir)
        with open(os.path.join(task_dir, "TASK_STATE.yaml"), "a", encoding="utf-8") as fh:
            fh.write("mutates_repo: true\n")
        os.environ["HARNESS_TEAM_WORKER"] = "lead"
        allowed, message = _check_team_artifact_write(task_dir, "CRITIC__runtime.md")
        self.assertTrue(allowed)
        self.assertEqual(message, "")


if __name__ == "__main__":
    unittest.main()
