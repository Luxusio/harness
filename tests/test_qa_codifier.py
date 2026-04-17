"""Tests for qa_codifier.py (AC-013..AC-014)."""
import json
import os
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(REPO_ROOT, "tests", "fixtures", "gstack_adoption")

sys.path.insert(0, os.path.join(REPO_ROOT, "plugin", "scripts"))


class TestSanitizeTaskId(unittest.TestCase):
    """Task-id sanitizer: TASK__add-feature -> task_add_feature."""

    def setUp(self):
        import importlib
        import qa_codifier
        importlib.reload(qa_codifier)
        self.module = qa_codifier

    def test_task_prefix_lowercased(self):
        self.assertEqual(self.module.sanitize_task_id("TASK__add-feature"), "task_add_feature")

    def test_dashes_to_underscores(self):
        self.assertEqual(self.module.sanitize_task_id("my-task-name"), "my_task_name")

    def test_dots_to_underscores(self):
        self.assertEqual(self.module.sanitize_task_id("task.v1"), "task_v1")

    def test_collapse_consecutive_underscores(self):
        result = self.module.sanitize_task_id("TASK__foo--bar")
        self.assertNotIn("__", result, "Consecutive underscores should be collapsed")

    def test_strip_leading_trailing_underscores(self):
        result = self.module.sanitize_task_id("_TASK__test_")
        self.assertFalse(result.startswith("_"), "Should not start with _")
        self.assertFalse(result.endswith("_"), "Should not end with _")


class TestParseCodifiableBlocks(unittest.TestCase):
    """Parser extracts codifiable: YAML blocks from transcript."""

    def setUp(self):
        import importlib
        import qa_codifier
        importlib.reload(qa_codifier)
        self.module = qa_codifier

    def test_parses_single_block(self):
        """Should parse one codifiable block."""
        transcript = """
Some QA text here.

codifiable:
  - behavior: gate_exits_zero
    command: "echo hello"
    expected_exit: 0
    expected_stdout_contains: ["hello"]
    expected_stderr_contains: []

More text.
"""
        blocks = self.module._parse_codifiable_blocks(transcript)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["behavior"], "gate_exits_zero")
        self.assertEqual(blocks[0]["command"], "echo hello")
        self.assertEqual(blocks[0]["expected_exit"], 0)
        self.assertEqual(blocks[0]["expected_stdout_contains"], ["hello"])

    def test_parses_multiple_blocks(self):
        """Should parse multiple codifiable blocks."""
        transcript = """
codifiable:
  - behavior: test_one
    command: "echo one"
    expected_exit: 0
    expected_stdout_contains: ["one"]
    expected_stderr_contains: []

codifiable:
  - behavior: test_two
    command: "echo two"
    expected_exit: 0
    expected_stdout_contains: ["two"]
    expected_stderr_contains: []
"""
        blocks = self.module._parse_codifiable_blocks(transcript)
        self.assertEqual(len(blocks), 2)

    def test_no_blocks_returns_empty(self):
        """Transcript without codifiable blocks should return empty list."""
        transcript = "AC-001: PASS — verified manually\nNo codifiable scenarios.\n"
        blocks = self.module._parse_codifiable_blocks(transcript)
        self.assertEqual(blocks, [])

    def test_fixture_transcript_parsed(self):
        """Fixture CRITIC__runtime.md should yield codifiable blocks."""
        fixture = os.path.join(FIXTURES, "CRITIC_runtime_codifiable.md")
        with open(fixture) as f:
            transcript = f.read()
        blocks = self.module._parse_codifiable_blocks(transcript)
        self.assertGreater(len(blocks), 0, "Fixture should have codifiable blocks")


class TestRenderPytest(unittest.TestCase):
    """Rendered pytest code should be valid Python."""

    def setUp(self):
        import importlib
        import qa_codifier
        importlib.reload(qa_codifier)
        self.module = qa_codifier

    def test_renders_valid_python(self):
        """Rendered pytest test should compile without errors."""
        code = self.module._render_pytest(
            "my_test", "echo hello", 0, ["hello"], []
        )
        compile(code, "<test>", "exec")  # Should not raise

    def test_renders_function_name(self):
        code = self.module._render_pytest("my_test", "echo hello", 0, [], [])
        self.assertIn("def test_my_test()", code)

    def test_renders_stdout_assert(self):
        code = self.module._render_pytest("t", "cmd", 0, ["expected_str"], [])
        self.assertIn("expected_str", code)
        self.assertIn("r.stdout", code)


class TestCodifierPipeline(unittest.TestCase):
    """AC-013..AC-014: full codifier pipeline."""

    def setUp(self):
        import importlib
        import qa_codifier
        importlib.reload(qa_codifier)
        self.module = qa_codifier

    def test_no_transcript_exits_0(self):
        """Missing CRITIC__runtime.md should exit 0 (never blocks)."""
        with tempfile.TemporaryDirectory() as d:
            result = self.module.codify(d, transcript_path="/nonexistent/transcript.md")
        self.assertEqual(result, 0, "codify must return 0 even with missing transcript")

    def test_empty_transcript_exits_0(self):
        """Transcript with no codifiable blocks should exit 0."""
        with tempfile.TemporaryDirectory() as d:
            transcript = os.path.join(d, "CRITIC__runtime.md")
            with open(transcript, "w") as f:
                f.write("AC-001: PASS — manual verification only.\n")
            result = self.module.codify(d, transcript_path=transcript)
        self.assertEqual(result, 0)

    def test_codify_with_fixture_exits_0(self):
        """Fixture with codifiable blocks should exit 0 and produce tests."""
        fixture = os.path.join(FIXTURES, "CRITIC_runtime_codifiable.md")
        with tempfile.TemporaryDirectory() as task_dir:
            result = self.module.codify(task_dir, transcript_path=fixture)
        self.assertEqual(result, 0)

    def test_collision_appends_suffix(self):
        """Behavior name collision should append _2, _3."""
        with tempfile.TemporaryDirectory() as d:
            target_dir = os.path.join(d, "tests", "regression", "task_test")
            os.makedirs(target_dir, exist_ok=True)
            # Pre-create the target file
            with open(os.path.join(target_dir, "my_behavior.py"), "w") as f:
                f.write("# existing\n")
            result = self.module._unique_behavior_name(target_dir, "my_behavior", "py")
            self.assertEqual(result, "my_behavior_2")

    def test_compile_check_valid_python(self):
        """Valid Python should pass compile check."""
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
            f.write("import subprocess\ndef test_x():\n    pass\n")
            path = f.name
        try:
            ok = self.module._compile_check_python(path)
            self.assertTrue(ok)
        finally:
            os.unlink(path)

    def test_compile_check_invalid_python(self):
        """Invalid Python should fail compile check."""
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
            f.write("def broken(\n    pass\n")  # syntax error
            path = f.name
        try:
            ok = self.module._compile_check_python(path)
            self.assertFalse(ok)
        finally:
            os.unlink(path)

    def test_codifier_never_crashes_on_garbage(self):
        """Codifier should return 0 even on garbage transcript."""
        with tempfile.TemporaryDirectory() as d:
            transcript = os.path.join(d, "CRITIC__runtime.md")
            with open(transcript, "w") as f:
                f.write("codifiable:\n  - behavior: !!garbage yaml\n    command: [not: valid\n")
            result = self.module.codify(d, transcript_path=transcript)
        self.assertEqual(result, 0, "codify must never crash")


class TestCodifiableContractInAgentDocs(unittest.TestCase):
    """AC-012: codifiable block contract documented in agent files."""

    def test_qa_cli_has_codifiable_section(self):
        path = os.path.join(REPO_ROOT, "plugin", "agents", "qa-cli.md")
        with open(path) as f:
            content = f.read()
        self.assertIn("Codifiable block contract", content)
        self.assertIn("expected_exit", content)

    def test_qa_api_has_codifiable_section(self):
        path = os.path.join(REPO_ROOT, "plugin", "agents", "qa-api.md")
        with open(path) as f:
            content = f.read()
        self.assertIn("Codifiable block contract", content)
        self.assertIn("expected_stdout_contains", content)

    def test_qa_browser_has_v1_opt_out(self):
        path = os.path.join(REPO_ROOT, "plugin", "agents", "qa-browser.md")
        with open(path) as f:
            content = f.read()
        self.assertIn("Codifiable block contract", content)
        self.assertIn("v1", content.lower())


if __name__ == "__main__":
    unittest.main()
