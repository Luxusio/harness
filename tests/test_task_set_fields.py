"""Tests for task_set_fields — MCP tool and hctl CLI subcommand.

Covers:
  AC-001: task_set_fields sets allowed fields and bumps state_revision
  AC-002: Protected fields are rejected; other fields still apply
  AC-003: hctl set-fields CLI works identically
  AC-004: state_revision increments on every successful call
  AC-005: Single field, multi-field, blocked+allowed mix, missing task_dir,
          revision bump, bool/str coercion
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "scripts"))
os.environ["HARNESS_SKIP_STDIN"] = "1"

HCTL = os.path.join(os.path.dirname(__file__), "..", "plugin", "scripts", "hctl.py")
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

from _lib import set_task_state_field, yaml_field, write_task_state_content


# ------------------------------------------------------------------ helpers

def _make_task(base_dir, task_id, extra_fields=None):
    task_dir = os.path.join(base_dir, task_id)
    os.makedirs(task_dir, exist_ok=True)
    fields = {
        "task_id": task_id,
        "schema_version": "1",
        "state_revision": "5",
        "parent_revision": "4",
        "status": "created",
        "lane": "unknown",
        "maintenance_task": "false",
        "mutates_repo": "false",
        "doc_sync_required": "false",
        "qa_required": "false",
        "browser_required": "false",
        "risk_level": "medium",
        "parallelism": "1",
        "plan_verdict": "pending",
        "runtime_verdict": "pending",
        "updated": "2026-01-01T00:00:00Z",
    }
    if extra_fields:
        fields.update(extra_fields)
    lines = "\n".join(f"{k}: {v}" for k, v in fields.items()) + "\n"
    state_file = os.path.join(task_dir, "TASK_STATE.yaml")
    with open(state_file, "w") as f:
        f.write(lines)
    return task_dir


def _run_hctl(args, cwd=None):
    result = subprocess.run(
        [sys.executable, HCTL] + args,
        capture_output=True, text=True,
        cwd=cwd or REPO_ROOT
    )
    return result


def _mcp_call(func_name, **kwargs):
    """Call a handler function from harness_server directly."""
    sys.path.insert(0, os.path.join(REPO_ROOT, "plugin", "mcp"))
    import harness_server
    handler = None
    for tool in harness_server.TOOL_DEFS:
        if tool["name"] == func_name:
            handler = tool["handler"]
            break
    if handler is None:
        raise ValueError(f"tool {func_name!r} not found in TOOL_DEFS")
    return handler(kwargs)


# ------------------------------------------------------------------ tests

class TestTaskSetFieldsMCP(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    # AC-001: sets allowed field
    def test_set_single_allowed_field(self):
        task_dir = _make_task(self.tmp, "TASK__test-sf-001")
        result = _mcp_call("task_set_fields", task_dir=task_dir, fields={"maintenance_task": True})
        data = result["structuredContent"]
        self.assertIn("maintenance_task", data["updated"])
        self.assertEqual(data["updated"]["maintenance_task"], True)
        self.assertEqual(data["rejected"], {})
        # Verify on disk
        val = yaml_field("maintenance_task", os.path.join(task_dir, "TASK_STATE.yaml"))
        self.assertEqual(str(val).lower(), "true")

    # AC-001: sets lane field
    def test_set_lane_field(self):
        task_dir = _make_task(self.tmp, "TASK__test-sf-002")
        result = _mcp_call("task_set_fields", task_dir=task_dir, fields={"lane": "build"})
        data = result["structuredContent"]
        self.assertIn("lane", data["updated"])
        self.assertEqual(yaml_field("lane", os.path.join(task_dir, "TASK_STATE.yaml")), "build")

    # AC-001 + AC-004: state_revision bumps
    def test_state_revision_bumps(self):
        task_dir = _make_task(self.tmp, "TASK__test-sf-003")
        state_file = os.path.join(task_dir, "TASK_STATE.yaml")
        before = int(yaml_field("state_revision", state_file) or 0)
        _mcp_call("task_set_fields", task_dir=task_dir, fields={"maintenance_task": True})
        after = int(yaml_field("state_revision", state_file) or 0)
        self.assertGreater(after, before)

    # AC-004: each call bumps revision
    def test_each_call_bumps_revision(self):
        task_dir = _make_task(self.tmp, "TASK__test-sf-004")
        state_file = os.path.join(task_dir, "TASK_STATE.yaml")
        r0 = int(yaml_field("state_revision", state_file) or 0)
        _mcp_call("task_set_fields", task_dir=task_dir, fields={"lane": "build"})
        r1 = int(yaml_field("state_revision", state_file) or 0)
        _mcp_call("task_set_fields", task_dir=task_dir, fields={"lane": "docs-sync"})
        r2 = int(yaml_field("state_revision", state_file) or 0)
        self.assertGreater(r1, r0)
        self.assertGreater(r2, r1)

    # AC-002: protected field is rejected
    def test_protected_field_rejected(self):
        task_dir = _make_task(self.tmp, "TASK__test-sf-005")
        result = _mcp_call("task_set_fields", task_dir=task_dir, fields={"plan_verdict": "PASS"})
        data = result["structuredContent"]
        self.assertIn("plan_verdict", data["rejected"])
        # Verify plan_verdict not changed on disk
        val = yaml_field("plan_verdict", os.path.join(task_dir, "TASK_STATE.yaml"))
        self.assertEqual(val, "pending")

    # AC-002: mixed allowed+blocked — allowed fields still apply
    def test_mixed_allowed_and_blocked(self):
        task_dir = _make_task(self.tmp, "TASK__test-sf-006")
        result = _mcp_call(
            "task_set_fields",
            task_dir=task_dir,
            fields={"maintenance_task": True, "plan_verdict": "PASS", "status": "closed"}
        )
        data = result["structuredContent"]
        self.assertIn("maintenance_task", data["updated"])
        self.assertIn("plan_verdict", data["rejected"])
        self.assertIn("status", data["rejected"])
        # maintenance_task was set
        val = yaml_field("maintenance_task", os.path.join(task_dir, "TASK_STATE.yaml"))
        self.assertEqual(str(val).lower(), "true")

    # AC-002: agent_run_* prefix is rejected
    def test_agent_run_prefix_rejected(self):
        task_dir = _make_task(self.tmp, "TASK__test-sf-007")
        result = _mcp_call("task_set_fields", task_dir=task_dir, fields={"agent_run_developer_count": 5})
        data = result["structuredContent"]
        self.assertIn("agent_run_developer_count", data["rejected"])

    # AC-002: state_revision is rejected
    def test_internal_field_rejected(self):
        task_dir = _make_task(self.tmp, "TASK__test-sf-008")
        result = _mcp_call("task_set_fields", task_dir=task_dir, fields={"state_revision": 999})
        data = result["structuredContent"]
        self.assertIn("state_revision", data["rejected"])
        # Verify not overwritten
        val = int(yaml_field("state_revision", os.path.join(task_dir, "TASK_STATE.yaml")) or 0)
        self.assertNotEqual(val, 999)

    # AC-005: bool string coercion
    def test_bool_string_coercion(self):
        task_dir = _make_task(self.tmp, "TASK__test-sf-009")
        result = _mcp_call("task_set_fields", task_dir=task_dir, fields={"maintenance_task": "true"})
        data = result["structuredContent"]
        self.assertIn("maintenance_task", data["updated"])
        self.assertEqual(data["updated"]["maintenance_task"], True)

    def test_bool_false_string_coercion(self):
        task_dir = _make_task(self.tmp, "TASK__test-sf-010", {"maintenance_task": "true"})
        _mcp_call("task_set_fields", task_dir=task_dir, fields={"maintenance_task": "false"})
        val = yaml_field("maintenance_task", os.path.join(task_dir, "TASK_STATE.yaml"))
        self.assertEqual(str(val).lower(), "false")

    # AC-005: multi-field set
    def test_multi_field_set(self):
        task_dir = _make_task(self.tmp, "TASK__test-sf-011")
        result = _mcp_call(
            "task_set_fields",
            task_dir=task_dir,
            fields={"maintenance_task": True, "lane": "build", "doc_sync_required": True, "qa_required": False}
        )
        data = result["structuredContent"]
        self.assertEqual(len(data["updated"]), 4)
        self.assertEqual(data["rejected"], {})

    # AC-005: missing task_dir returns error
    def test_missing_task_dir(self):
        result = _mcp_call("task_set_fields", task_dir="/nonexistent/path/TASK__x", fields={"maintenance_task": True})
        # Should be an error or all fields rejected
        data = result["structuredContent"]
        self.assertTrue(
            data.get("isError") or
            (data.get("rejected") and not data.get("updated")),
            f"Expected error for missing task_dir, got: {data}"
        )

    # AC-005: empty fields raises error
    def test_empty_fields_raises(self):
        task_dir = _make_task(self.tmp, "TASK__test-sf-012")
        with self.assertRaises(ValueError):
            _mcp_call("task_set_fields", task_dir=task_dir, fields={})

    # AC-005: unknown field rejected
    def test_unknown_field_rejected(self):
        task_dir = _make_task(self.tmp, "TASK__test-sf-013")
        result = _mcp_call("task_set_fields", task_dir=task_dir, fields={"nonexistent_field": "value"})
        data = result["structuredContent"]
        self.assertIn("nonexistent_field", data["rejected"])


class TestTaskSetFieldsCLI(unittest.TestCase):
    """AC-003: hctl set-fields CLI subcommand."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_cli_set_single_field(self):
        task_dir = _make_task(self.tmp, "TASK__test-cli-001")
        r = _run_hctl(["set-fields", "--task-dir", task_dir, "--field", "maintenance_task=true"])
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        val = yaml_field("maintenance_task", os.path.join(task_dir, "TASK_STATE.yaml"))
        self.assertEqual(str(val).lower(), "true")

    def test_cli_set_multiple_fields(self):
        task_dir = _make_task(self.tmp, "TASK__test-cli-002")
        r = _run_hctl([
            "set-fields", "--task-dir", task_dir,
            "--field", "maintenance_task=true",
            "--field", "lane=build"
        ])
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertEqual(yaml_field("lane", os.path.join(task_dir, "TASK_STATE.yaml")), "build")

    def test_cli_blocked_field_exits_nonzero_if_only_field(self):
        task_dir = _make_task(self.tmp, "TASK__test-cli-003")
        r = _run_hctl(["set-fields", "--task-dir", task_dir, "--field", "plan_verdict=PASS"])
        # All fields rejected → error exit
        self.assertNotEqual(r.returncode, 0)

    def test_cli_missing_field_flag(self):
        task_dir = _make_task(self.tmp, "TASK__test-cli-004")
        r = _run_hctl(["set-fields", "--task-dir", task_dir])
        self.assertNotEqual(r.returncode, 0)

    def test_cli_revision_bumps(self):
        task_dir = _make_task(self.tmp, "TASK__test-cli-005")
        state_file = os.path.join(task_dir, "TASK_STATE.yaml")
        before = int(yaml_field("state_revision", state_file) or 0)
        _run_hctl(["set-fields", "--task-dir", task_dir, "--field", "lane=docs-sync"])
        after = int(yaml_field("state_revision", state_file) or 0)
        self.assertGreater(after, before)


if __name__ == "__main__":
    unittest.main()
