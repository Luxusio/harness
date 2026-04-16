import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HCTL = REPO_ROOT / "plugin" / "scripts" / "hctl.py"


def _run_hctl(*args, cwd=None):
    result = subprocess.run(
        [sys.executable, str(HCTL)] + list(args),
        capture_output=True,
        text=True,
        cwd=cwd or str(REPO_ROOT),
    )
    return result.returncode, result.stdout, result.stderr


def _make_task(base_dir: str, task_id: str):
    task_dir = Path(base_dir) / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "TASK_STATE.yaml").write_text(
        "\n".join(
            [
                f"task_id: {task_id}",
                "status: planned",
                "lane: refactor",
                "risk_tags: []",
                "browser_required: false",
                "runtime_verdict_fail_count: 0",
                "qa_required: true",
                "doc_sync_required: true",
                "parallelism: 1",
                "workflow_locked: true",
                "maintenance_task: false",
                "routing_compiled: false",
                "routing_source: pending",
                "risk_level: pending",
                "execution_mode: standard",
                "orchestration_mode: solo",
                "plan_verdict: PASS",
                "runtime_verdict: pending",
                "updated: 2026-01-01T00:00:00Z",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (task_dir / "PLAN.md").write_text("# Plan\n\nSmall plan.\n", encoding="utf-8")
    (task_dir / "CHECKS.yaml").write_text(
        "\n".join(
            [
                "checks:",
                *sum(
                    [
                        [
                            f"  - id: AC-{i:03d}",
                            "    status: failed" if i % 2 else "    status: pending",
                            "    title: This is an intentionally long acceptance title that should not be dumped in full into the compact runtime context JSON output",
                        ]
                        for i in range(1, 8)
                    ],
                    [],
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return str(task_dir)


class PromptBudgetTests(unittest.TestCase):
    def test_control_docs_stay_small(self):
        # harness orchestrator agent removed — the doc set that must stay
        # budget-compliant is the surviving control surface only.
        caps = {
            "plugin/CLAUDE.md": 8000,
            "plugin/skills/plan/SKILL.md": 8000,
            "plugin/agents/critic-runtime.md": 6000,
            "plugin/agents/critic-plan.md": 5000,
        }
        for rel_path, cap in caps.items():
            path = REPO_ROOT / rel_path
            if not path.exists():
                continue
            size = path.stat().st_size
            self.assertLessEqual(size, cap, f"{rel_path} should stay under {cap} bytes")

    def test_context_with_many_checks_stays_brief(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = _make_task(tmp, "TASK__budget")
            code, _, err = _run_hctl("start", "--task-dir", task_dir)
            self.assertEqual(code, 0, err)
            code, out, err = _run_hctl("context", "--task-dir", task_dir, "--json")
            self.assertEqual(code, 0, err)
            ctx = json.loads(out)
            self.assertLess(len(out), 2200, "compact context should stay small even with many checks")
            self.assertRegex(ctx["context_revision"], r"^[0-9a-f]{12}$")
            self.assertEqual(
                set(ctx["team"].keys()),
                {"provider", "status", "size", "reason", "fallback_used"},
                "non-team compact context should only surface the minimal team summary",
            )
            self.assertLessEqual(len(ctx["must_read"]), 4)
            self.assertLessEqual(len(ctx["checks"]["top_open_titles"]), 2)
            forbidden = {
                "plugin/CLAUDE.md",
                "plugin/scripts/hctl.py",
                "doc/harness/manifest.yaml",
            }
            self.assertTrue(forbidden.isdisjoint(set(ctx["must_read"])))


if __name__ == "__main__":
    unittest.main()
