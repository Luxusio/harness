"""Regression tests for P1: gate script import safety.

Ensures task_completed_gate, stop_gate, and subagent_stop_gate can all be
imported without NameError/AttributeError, and that key runtime functions
work on minimal or missing task directories.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_DIR = REPO_ROOT / "plugin" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
os.environ["HARNESS_SKIP_STDIN"] = "1"


class GateImportSafetyTests(unittest.TestCase):
    def test_task_completed_gate_imports_without_error(self):
        import importlib
        mod = importlib.import_module("task_completed_gate")
        self.assertIsNotNone(mod)

    def test_stop_gate_imports_without_error(self):
        import importlib
        mod = importlib.import_module("stop_gate")
        self.assertIsNotNone(mod)

    def test_subagent_stop_gate_imports_without_error(self):
        import importlib
        mod = importlib.import_module("subagent_stop_gate")
        self.assertIsNotNone(mod)

    def test_compute_completion_failures_missing_state(self):
        """compute_completion_failures on an empty dir returns a list without crashing."""
        from task_completed_gate import compute_completion_failures  # type: ignore

        with tempfile.TemporaryDirectory() as tmp:
            result = compute_completion_failures(tmp)
        self.assertIsInstance(result, list)

    def test_compute_completion_failures_minimal_state(self):
        """compute_completion_failures with a minimal TASK_STATE.yaml returns a list."""
        from task_completed_gate import compute_completion_failures  # type: ignore

        minimal_state = "\n".join([
            "task_id: TASK__test-minimal",
            "status: implemented",
            "mutates_repo: true",
            "plan_verdict: PASS",
            "runtime_verdict: pending",
            "runtime_verdict_freshness: current",
            "document_verdict: skipped",
            "execution_mode: standard",
            "orchestration_mode: solo",
            "workflow_violations: []",
            "artifact_provenance_required: false",
            "directive_capture_state: clean",
            "complaint_capture_state: clean",
        ]) + "\n"

        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "TASK_STATE.yaml"
            state_file.write_text(minimal_state, encoding="utf-8")
            result = compute_completion_failures(tmp)
        self.assertIsInstance(result, list)

    def test_stop_gate_next_step_returns_string(self):
        """_next_step('created') must return a non-empty string."""
        from stop_gate import _next_step  # type: ignore

        result = _next_step("created")
        self.assertIsInstance(result, str)
        self.assertTrue(len(result) > 0)

    def test_all_gate_scripts_importable_together(self):
        """All three gate modules can be imported simultaneously without conflict."""
        import importlib
        tcg = importlib.import_module("task_completed_gate")
        sg = importlib.import_module("stop_gate")
        ssg = importlib.import_module("subagent_stop_gate")
        self.assertIsNotNone(tcg)
        self.assertIsNotNone(sg)
        self.assertIsNotNone(ssg)


    def test_main_uses_target_not_task_dir_in_merge_call(self):
        """Regression: main() must use 'target' (not undefined 'task_dir') as first arg
        to merge_task_path_fields(). This prevents NameError when git diff returns files.

        P1 regression: task_completed_gate.py line 604 fix.
        """
        source = (REPO_ROOT / "plugin" / "scripts" / "task_completed_gate.py").read_text(encoding="utf-8")
        import ast
        tree = ast.parse(source)

        # Find the main() function body
        main_func = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "main":
                main_func = node
                break
        self.assertIsNotNone(main_func, "main() function not found in task_completed_gate.py")

        # Walk main() and find merge_task_path_fields() calls
        for node in ast.walk(main_func):
            if isinstance(node, ast.Call):
                func = node.func
                func_name = None
                if isinstance(func, ast.Name):
                    func_name = func.id
                elif isinstance(func, ast.Attribute):
                    func_name = func.attr
                if func_name == "merge_task_path_fields" and node.args:
                    first_arg = node.args[0]
                    # First arg must be ast.Name with id='target', not 'task_dir'
                    self.assertIsInstance(
                        first_arg, ast.Name,
                        f"merge_task_path_fields first arg must be a Name node"
                    )
                    self.assertEqual(
                        first_arg.id, "target",
                        f"merge_task_path_fields first arg in main() must be 'target', got '{first_arg.id}'"
                    )


if __name__ == "__main__":
    unittest.main()
