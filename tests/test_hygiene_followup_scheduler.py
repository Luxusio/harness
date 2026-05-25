import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "plugin" / "scripts" / "hygiene_followup.py"

spec = importlib.util.spec_from_file_location("hygiene_followup", SCRIPT)
assert spec and spec.loader
hygiene_followup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hygiene_followup)


class HygieneFollowupSchedulerTests(unittest.TestCase):
    def _write_pending(self, root: Path, count: int = 2) -> None:
        hb = root / "doc" / "harness"
        hb.mkdir(parents=True, exist_ok=True)
        items = []
        for idx in range(count):
            items.append(
                {
                    "path": f"doc/changes/example-{idx}.md",
                    "kind": "review",
                    "signals": {
                        "reference_count": 0,
                        "freshness": "suspect" if idx == 0 else "current",
                    },
                    "added_at": "2026-05-25T00:00:00Z",
                }
            )
        (hb / ".hygiene-pending.json").write_text(json.dumps(items), encoding="utf-8")

    def test_create_followup_task_from_pending_hygiene(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_pending(root)

            result = hygiene_followup.create_followup(str(root))

            task_dir = root / "doc" / "harness" / "tasks" / "TASK__hygiene-review-pending-docs"
            request = (task_dir / "REQUEST.md").read_text(encoding="utf-8")
            plan = (task_dir / "PLAN.md").read_text(encoding="utf-8")
            checks = (task_dir / "CHECKS.yaml").read_text(encoding="utf-8")

        self.assertEqual(result["action"], "run_followup")
        self.assertTrue(result["auto_run"])
        self.assertEqual(result["pending_count"], 2)
        self.assertIn("Do not combine this cleanup with unrelated feature work", request)
        self.assertIn("standalone cleanup task", plan)
        self.assertIn("AC-001", checks)

    def test_create_followup_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_pending(root)

            first = hygiene_followup.create_followup(str(root))
            task_dir = root / "doc" / "harness" / "tasks" / "TASK__hygiene-review-pending-docs"
            plan_path = task_dir / "PLAN.md"
            plan_path.write_text("custom plan remains\n", encoding="utf-8")
            second = hygiene_followup.create_followup(str(root))

            plan = plan_path.read_text(encoding="utf-8")

        self.assertEqual(first["action"], "run_followup")
        self.assertEqual(second["action"], "run_followup")
        self.assertEqual(plan, "custom plan remains\n")

    def test_no_pending_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = hygiene_followup.create_followup(tmp)

        self.assertEqual(result["action"], "none")
        self.assertEqual(result["pending_count"], 0)


if __name__ == "__main__":
    unittest.main()
