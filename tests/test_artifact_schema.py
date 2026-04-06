import json
import os
import subprocess
import sys
import tempfile
import unittest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "scripts"))
os.environ["HARNESS_SKIP_STDIN"] = "1"

from _lib import (
    ensure_task_scaffold,
    set_task_state_field,
    yaml_field,
    migrate_task_artifacts,
    TASK_STATE_SCHEMA_VERSION,
    CHECKS_SCHEMA_VERSION,
    SESSION_HANDOFF_SCHEMA_VERSION,
)
from handoff_escalation import generate_handoff

HCTL = os.path.join(os.path.dirname(__file__), "..", "plugin", "scripts", "hctl.py")
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


def _run_hctl(*args):
    result = subprocess.run(
        [sys.executable, HCTL] + list(args),
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )
    return result.returncode, result.stdout, result.stderr


class TestTaskStateSchema(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_scaffold_initializes_schema_and_revisions(self):
        task_dir = os.path.join(self.tmp.name, "TASK__schema_scaffold")
        ensure_task_scaffold(task_dir, "TASK__schema_scaffold", request_text="hello")
        state_file = os.path.join(task_dir, "TASK_STATE.yaml")
        self.assertEqual(yaml_field("schema_version", state_file), str(TASK_STATE_SCHEMA_VERSION))
        self.assertEqual(yaml_field("state_revision", state_file), "0")
        self.assertEqual(yaml_field("parent_revision", state_file), "null")

    def test_state_mutation_injects_schema_and_bumps_revision(self):
        task_dir = os.path.join(self.tmp.name, "TASK__legacy")
        os.makedirs(task_dir, exist_ok=True)
        state_file = os.path.join(task_dir, "TASK_STATE.yaml")
        _write(
            state_file,
            "task_id: TASK__legacy\n"
            "status: created\n"
            "updated: 2026-01-01T00:00:00Z\n",
        )
        ok = set_task_state_field(task_dir, "status", "planned")
        self.assertTrue(ok)
        self.assertEqual(yaml_field("schema_version", state_file), str(TASK_STATE_SCHEMA_VERSION))
        self.assertEqual(yaml_field("state_revision", state_file), "1")
        self.assertEqual(yaml_field("parent_revision", state_file), "0")
        self.assertEqual(yaml_field("status", state_file), "planned")


class TestArtifactMigration(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.task_dir = os.path.join(self.tmp.name, "TASK__migrate")
        os.makedirs(self.task_dir, exist_ok=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_migrate_task_artifacts_updates_legacy_files(self):
        _write(
            os.path.join(self.task_dir, "TASK_STATE.yaml"),
            "task_id: TASK__migrate\n"
            "status: planned\n"
            "updated: 2026-01-01T00:00:00Z\n",
        )
        _write(
            os.path.join(self.task_dir, "CHECKS.yaml"),
            "close_gate: standard\n"
            "checks:\n"
            "  - id: AC-001\n"
            "    status: planned\n",
        )
        with open(os.path.join(self.task_dir, "SESSION_HANDOFF.json"), "w", encoding="utf-8") as fh:
            json.dump({"trigger": "runtime_fail", "next_step": "fix it"}, fh)

        summary = migrate_task_artifacts(self.task_dir, write=True)
        self.assertTrue(summary["changed"])
        self.assertEqual(
            yaml_field("schema_version", os.path.join(self.task_dir, "TASK_STATE.yaml")),
            str(TASK_STATE_SCHEMA_VERSION),
        )
        self.assertEqual(
            yaml_field("schema_version", os.path.join(self.task_dir, "CHECKS.yaml")),
            str(CHECKS_SCHEMA_VERSION),
        )
        with open(os.path.join(self.task_dir, "SESSION_HANDOFF.json"), "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        self.assertEqual(payload.get("schema_version"), SESSION_HANDOFF_SCHEMA_VERSION)

    def test_generate_handoff_writes_versioned_payload(self):
        _write(
            os.path.join(self.task_dir, "TASK_STATE.yaml"),
            "task_id: TASK__migrate\n"
            "status: blocked_env\n"
            "plan_verdict: PASS\n"
            "runtime_verdict: FAIL\n"
            "runtime_verdict_fail_count: 1\n"
            'roots_touched: ["app"]\n'
            'touched_paths: ["app/main.py"]\n'
            'verification_targets: ["app/main.py"]\n'
            'blockers: ["missing docker"]\n'
            "updated: 2026-01-01T00:00:00Z\n",
        )
        handoff = generate_handoff(self.task_dir, "runtime_fail")
        self.assertIsInstance(handoff, dict)
        self.assertEqual(handoff.get("schema_version"), SESSION_HANDOFF_SCHEMA_VERSION)
        with open(os.path.join(self.task_dir, "SESSION_HANDOFF.json"), "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        self.assertEqual(payload.get("schema_version"), SESSION_HANDOFF_SCHEMA_VERSION)

    def test_hctl_migrate_json_write(self):
        _write(
            os.path.join(self.task_dir, "TASK_STATE.yaml"),
            "task_id: TASK__migrate\n"
            "status: planned\n"
            "updated: 2026-01-01T00:00:00Z\n",
        )
        _write(
            os.path.join(self.task_dir, "CHECKS.yaml"),
            "close_gate: standard\n"
            "checks:\n"
            "  - id: AC-001\n"
            "    status: planned\n",
        )
        code, out, err = _run_hctl("migrate", "--task-dir", self.task_dir, "--write", "--json")
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertTrue(payload["write"])
        self.assertTrue(payload["changed"])
        artifacts = {item["artifact"]: item for item in payload["artifacts"]}
        self.assertEqual(artifacts["TASK_STATE.yaml"]["schema_version_after"], TASK_STATE_SCHEMA_VERSION)
        self.assertEqual(artifacts["CHECKS.yaml"]["schema_version_after"], CHECKS_SCHEMA_VERSION)


if __name__ == "__main__":
    unittest.main()
