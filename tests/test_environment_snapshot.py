import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "plugin" / "scripts"))

from _lib import emit_compact_context
from environment_snapshot import SNAPSHOT_FILENAME, write_environment_snapshot

HCTL = REPO_ROOT / "plugin" / "scripts" / "hctl.py"


class EnvironmentSnapshotTests(unittest.TestCase):
    def _write_task_state(self, task_dir: Path, **fields):
        defaults = {
            "task_id": task_dir.name,
            "status": "planned",
            "lane": "build",
            "risk_level": "medium",
            "qa_required": "true",
            "doc_sync_required": "false",
            "browser_required": "false",
            "parallelism": "1",
            "workflow_locked": "true",
            "maintenance_task": "false",
            "routing_compiled": "true",
            "execution_mode": "standard",
            "orchestration_mode": "solo",
            "planning_mode": "standard",
            "plan_verdict": "pending",
            "runtime_verdict": "pending",
            "updated": "2026-04-01T00:00:00Z",
        }
        defaults.update({k: str(v) for k, v in fields.items()})
        lines = [f"{k}: {v}" for k, v in defaults.items()]
        (task_dir / "TASK_STATE.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def test_hctl_start_writes_environment_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp) / "TASK__snap"
            task_dir.mkdir(parents=True, exist_ok=True)
            self._write_task_state(task_dir, routing_compiled="false", plan_verdict="PASS")
            (task_dir / "REQUEST.md").write_text("Create a dashboard.\n", encoding="utf-8")

            result = subprocess.run(
                [sys.executable, str(HCTL), "start", "--task-dir", str(task_dir)],
                capture_output=True,
                text=True,
                cwd=str(REPO_ROOT),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            snapshot_path = task_dir / SNAPSHOT_FILENAME
            self.assertTrue(snapshot_path.is_file())
            self.assertIn("env_snapshot:", result.stdout)
            content = snapshot_path.read_text(encoding="utf-8")
            self.assertIn("# Environment Snapshot", content)
            self.assertLess(len(content), 2500)

    def test_task_context_prioritizes_snapshot_for_broad_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp) / "TASK__broad"
            task_dir.mkdir(parents=True, exist_ok=True)
            self._write_task_state(task_dir, planning_mode="broad-build", plan_verdict="pending")
            (task_dir / "REQUEST.md").write_text("Build a new admin dashboard.\n", encoding="utf-8")
            write_environment_snapshot(str(task_dir), repo_root=str(REPO_ROOT))

            ctx = emit_compact_context(str(task_dir))
            must_read = ctx.get("must_read", [])
            self.assertIn(f"doc/harness/tasks/{task_dir.name}/REQUEST.md", must_read)
            self.assertIn(f"doc/harness/tasks/{task_dir.name}/{SNAPSHOT_FILENAME}", must_read)
            self.assertIn("ENVIRONMENT_SNAPSHOT.md", ctx.get("next_action", ""))

    def test_task_context_prioritizes_snapshot_for_blocked_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp) / "TASK__blocked"
            task_dir.mkdir(parents=True, exist_ok=True)
            self._write_task_state(task_dir, status="blocked_env", plan_verdict="PASS", runtime_verdict="BLOCKED_ENV")
            (task_dir / "REQUEST.md").write_text("Fix setup issue.\n", encoding="utf-8")
            write_environment_snapshot(str(task_dir), repo_root=str(REPO_ROOT))

            ctx = emit_compact_context(str(task_dir))
            self.assertEqual(ctx.get("review_focus", {}).get("trigger"), "blocked_env")
            self.assertIn(f"doc/harness/tasks/{task_dir.name}/{SNAPSHOT_FILENAME}", ctx.get("must_read", []))
            self.assertIn("ENVIRONMENT_SNAPSHOT.md", ctx.get("next_action", ""))


if __name__ == "__main__":
    unittest.main()
