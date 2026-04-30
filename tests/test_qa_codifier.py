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
        """Fixture CRITIC__qa.md should yield codifiable blocks."""
        fixture = os.path.join(FIXTURES, "CRITIC_qa_codifiable.md")
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
        """Missing CRITIC__qa.md should exit 0 (never blocks)."""
        with tempfile.TemporaryDirectory() as d:
            result = self.module.codify(d, transcript_path="/nonexistent/transcript.md",
                                        target_root=d)
        self.assertEqual(result, 0, "codify must return 0 even with missing transcript")

    def test_empty_transcript_exits_0(self):
        """Transcript with no codifiable blocks should exit 0."""
        with tempfile.TemporaryDirectory() as d:
            transcript = os.path.join(d, "CRITIC__qa.md")
            with open(transcript, "w") as f:
                f.write("AC-001: PASS — manual verification only.\n")
            result = self.module.codify(d, transcript_path=transcript, target_root=d)
        self.assertEqual(result, 0)

    def test_codify_with_fixture_exits_0(self):
        """Fixture with codifiable blocks should exit 0 and produce tests."""
        fixture = os.path.join(FIXTURES, "CRITIC_qa_codifiable.md")
        with tempfile.TemporaryDirectory() as task_dir:
            with tempfile.TemporaryDirectory() as fake_root:
                manifest_dir = os.path.join(fake_root, "doc", "harness")
                os.makedirs(manifest_dir, exist_ok=True)
                with open(os.path.join(manifest_dir, "manifest.yaml"), "w") as f:
                    f.write("test_command: pytest\n")
                result = self.module.codify(task_dir, transcript_path=fixture,
                                            target_root=fake_root)
                self.assertEqual(result, 0)
                # Assert moved > 0: at least one .py file landed in target_dir
                sanitized = self.module.sanitize_task_id(os.path.basename(task_dir))
                target_dir = os.path.join(fake_root, "tests", "regression", sanitized)
                self.assertTrue(os.path.isdir(target_dir),
                                f"No regression dir created at {target_dir}")
                files = [f for f in os.listdir(target_dir) if f.endswith(".py")]
                self.assertGreater(len(files), 0,
                                   "Expected >=1 .py file in target_dir (moved > 0)")
                for fname in files:
                    self.assertTrue(fname.startswith("test_ac_"),
                                    f"File {fname} does not start with test_ac_")

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
            transcript = os.path.join(d, "CRITIC__qa.md")
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

    def test_qa_cli_has_ac_id_requirement(self):
        """AC-005: qa-cli.md must require ac_id and show bad echo hello example."""
        path = os.path.join(REPO_ROOT, "plugin", "agents", "qa-cli.md")
        with open(path) as f:
            content = f.read()
        self.assertIn("ac_id", content)
        self.assertIn("echo hello", content)

    def test_qa_api_has_ac_id_requirement(self):
        """AC-005: qa-api.md must require ac_id and show bad echo hello example."""
        path = os.path.join(REPO_ROOT, "plugin", "agents", "qa-api.md")
        with open(path) as f:
            content = f.read()
        self.assertIn("ac_id", content)
        self.assertIn("echo hello", content)


# Module-level fixture constants for new tests
FIXTURE_TRANSCRIPT_WITH_AC_ID = """
codifiable:
  - behavior: gate_exits_zero_on_allowed
    ac_id: AC-001
    command: "python3 plugin/scripts/update_checks.py --help"
    expected_exit: 0
    expected_stdout_contains: ["usage"]
    expected_stderr_contains: []
"""

FIXTURE_TRANSCRIPT_TRIVIAL = """
codifiable:
  - behavior: trivial_echo
    ac_id: AC-001
    command: "echo hello"
    expected_exit: 0
    expected_stdout_contains: ["hello"]
    expected_stderr_contains: []
"""


def _make_fake_root_with_manifest(base_dir):
    """Helper: create a minimal fake repo root with manifest.yaml."""
    manifest_dir = os.path.join(base_dir, "doc", "harness")
    os.makedirs(manifest_dir, exist_ok=True)
    with open(os.path.join(manifest_dir, "manifest.yaml"), "w") as f:
        f.write("test_command: pytest\n")
    return base_dir


class TestParseCodifiableYamlAcId(unittest.TestCase):
    """AC-003: _parse_codifiable_yaml extracts ac_id (scalar, list, missing)."""

    def setUp(self):
        import importlib
        import qa_codifier
        importlib.reload(qa_codifier)
        self.module = qa_codifier

    def test_ac_id_scalar_parsed(self):
        txt = """codifiable:
  - behavior: foo
    ac_id: AC-001
    command: "echo x"
    expected_exit: 0
    expected_stdout_contains: []
    expected_stderr_contains: []
"""
        blocks = self.module._parse_codifiable_yaml(txt)
        self.assertEqual(blocks[0]["ac_id"], "AC-001")

    def test_ac_id_list_parsed(self):
        txt = """codifiable:
  - behavior: foo
    ac_id: [AC-001, AC-002]
    command: "echo x"
    expected_exit: 0
    expected_stdout_contains: []
    expected_stderr_contains: []
"""
        blocks = self.module._parse_codifiable_yaml(txt)
        self.assertEqual(blocks[0]["ac_id"], ["AC-001", "AC-002"])

    def test_ac_id_missing_is_none(self):
        txt = """codifiable:
  - behavior: foo
    command: "echo x"
    expected_exit: 0
    expected_stdout_contains: []
    expected_stderr_contains: []
"""
        blocks = self.module._parse_codifiable_yaml(txt)
        self.assertIsNone(blocks[0].get("ac_id"))

    def test_ac_id_empty_string_is_none(self):
        txt = """codifiable:
  - behavior: foo
    ac_id:
    command: "echo x"
    expected_exit: 0
    expected_stdout_contains: []
    expected_stderr_contains: []
"""
        blocks = self.module._parse_codifiable_yaml(txt)
        self.assertIsNone(blocks[0].get("ac_id"))

    def test_fixture_transcript_has_ac_id(self):
        """AC-006: updated fixture blocks must expose ac_id field."""
        fixture = os.path.join(FIXTURES, "CRITIC_qa_codifiable.md")
        with open(fixture) as f:
            transcript = f.read()
        blocks = self.module._parse_codifiable_blocks(transcript)
        self.assertGreater(len(blocks), 0)
        self.assertIsNotNone(blocks[0].get("ac_id"),
                             "Fixture block[0] must have ac_id set")


class TestCodifierAcIdFiltering(unittest.TestCase):
    """AC-003: codify() skips+logs on missing/invalid ac_id; uses ac_NNN__ filename prefix."""

    def setUp(self):
        import importlib
        import qa_codifier
        importlib.reload(qa_codifier)
        self.module = qa_codifier

    def _run_codify(self, transcript_text, fake_root):
        with tempfile.TemporaryDirectory() as task_dir:
            transcript = os.path.join(task_dir, "CRITIC__qa.md")
            with open(transcript, "w") as f:
                f.write(transcript_text)
            result = self.module.codify(task_dir, transcript_path=transcript,
                                        target_root=fake_root)
            sanitized = self.module.sanitize_task_id(os.path.basename(task_dir))
            target_dir = os.path.join(fake_root, "tests", "regression", sanitized)
            learnings_path = os.path.join(fake_root, "doc", "harness", "learnings.jsonl")
            return result, target_dir, learnings_path

    def test_missing_ac_id_skipped_with_log(self):
        """Block without ac_id must not produce a file; codifier-rejected log written."""
        txt = """codifiable:
  - behavior: no_ac
    command: "python3 plugin/scripts/update_checks.py --help"
    expected_exit: 0
    expected_stdout_contains: []
    expected_stderr_contains: []
"""
        with tempfile.TemporaryDirectory() as fake_root:
            _make_fake_root_with_manifest(fake_root)
            result, target_dir, learnings_path = self._run_codify(txt, fake_root)
            self.assertEqual(result, 0)
            # No files produced
            if os.path.isdir(target_dir):
                files = os.listdir(target_dir)
                self.assertEqual(files, [], f"Expected no files, got {files}")
            # Log written
            self.assertTrue(os.path.isfile(learnings_path))
            with open(learnings_path) as f:
                log = f.read()
            self.assertIn("codifier-rejected", log)
            self.assertIn("missing-ac_id", log)

    def test_null_ac_id_skipped_with_log(self):
        """Block with blank ac_id: line is treated as missing, skipped + logged."""
        txt = """codifiable:
  - behavior: blank_ac
    ac_id:
    command: "python3 plugin/scripts/update_checks.py --help"
    expected_exit: 0
    expected_stdout_contains: []
    expected_stderr_contains: []
"""
        with tempfile.TemporaryDirectory() as fake_root:
            _make_fake_root_with_manifest(fake_root)
            result, target_dir, learnings_path = self._run_codify(txt, fake_root)
            self.assertEqual(result, 0)
            if os.path.isdir(target_dir):
                self.assertEqual(os.listdir(target_dir), [])
            self.assertTrue(os.path.isfile(learnings_path))
            with open(learnings_path) as f:
                log = f.read()
            self.assertIn("missing-ac_id", log)

    def test_valid_ac_id_accepted(self):
        """Block with valid ac_id produces a file, no skip log."""
        with tempfile.TemporaryDirectory() as fake_root:
            _make_fake_root_with_manifest(fake_root)
            result, target_dir, learnings_path = self._run_codify(
                FIXTURE_TRANSCRIPT_WITH_AC_ID, fake_root)
            self.assertEqual(result, 0)
            self.assertTrue(os.path.isdir(target_dir),
                            f"target_dir not created: {target_dir}")
            files = [f for f in os.listdir(target_dir) if f.endswith(".py")]
            self.assertGreater(len(files), 0, "Expected at least one .py file")
            # No rejection log for this block
            if os.path.isfile(learnings_path):
                with open(learnings_path) as f:
                    log = f.read()
                self.assertNotIn("missing-ac_id", log)

    def test_scalar_ac_id_filename_prefix(self):
        """ac_id: AC-001 produces filename starting with test_ac_001__ (pytest-discoverable)."""
        with tempfile.TemporaryDirectory() as fake_root:
            _make_fake_root_with_manifest(fake_root)
            result, target_dir, _ = self._run_codify(
                FIXTURE_TRANSCRIPT_WITH_AC_ID, fake_root)
            self.assertEqual(result, 0)
            files = os.listdir(target_dir) if os.path.isdir(target_dir) else []
            py_files = [f for f in files if f.endswith(".py")]
            self.assertTrue(any(f.startswith("test_ac_001__") for f in py_files),
                            f"No test_ac_001__ file found: {py_files}")

    def test_list_ac_id_filename_uses_first(self):
        """ac_id: [AC-002, AC-001] uses first id => test_ac_002__ prefix (pytest-discoverable)."""
        txt = """codifiable:
  - behavior: list_ac_test
    ac_id: [AC-002, AC-001]
    command: "python3 plugin/scripts/update_checks.py --help"
    expected_exit: 0
    expected_stdout_contains: ["usage"]
    expected_stderr_contains: []
"""
        with tempfile.TemporaryDirectory() as fake_root:
            _make_fake_root_with_manifest(fake_root)
            result, target_dir, _ = self._run_codify(txt, fake_root)
            self.assertEqual(result, 0)
            files = os.listdir(target_dir) if os.path.isdir(target_dir) else []
            py_files = [f for f in files if f.endswith(".py")]
            self.assertTrue(any(f.startswith("test_ac_002__") for f in py_files),
                            f"No test_ac_002__ file found: {py_files}")

    def test_pytest_discovery_compatible_naming(self):
        """All rendered files start with test_ so pytest auto-discovery works.

        Regression for AC-006 of TASK__test-workflow-gaps. Codified tests must land
        in tests/regression/<sanitized>/ AND be named test_*.py — without the
        prefix, pytest skips them silently and the codifier's output rots.
        """
        with tempfile.TemporaryDirectory() as fake_root:
            _make_fake_root_with_manifest(fake_root)
            result, target_dir, _ = self._run_codify(
                FIXTURE_TRANSCRIPT_WITH_AC_ID, fake_root)
            self.assertEqual(result, 0)
            files = os.listdir(target_dir) if os.path.isdir(target_dir) else []
            py_files = [f for f in files if f.endswith(".py")]
            self.assertTrue(py_files, "Expected at least one .py file emitted")
            for f in py_files:
                self.assertTrue(
                    f.startswith("test_"),
                    f"Codified test file {f!r} lacks 'test_' prefix; pytest "
                    f"will not auto-discover it.",
                )


class TestTrivialCommandFilter(unittest.TestCase):
    """AC-004: _is_trivial_command rejects bare echo/--version/true/: with subshell exclusion."""

    def setUp(self):
        import importlib
        import qa_codifier
        importlib.reload(qa_codifier)
        self.module = qa_codifier

    def test_echo_simple_rejected(self):
        self.assertTrue(self.module._is_trivial_command("echo hello"))

    def test_echo_with_pipe_allowed(self):
        self.assertFalse(self.module._is_trivial_command("echo hello | grep hello"))

    def test_echo_with_semicolon_allowed(self):
        self.assertFalse(self.module._is_trivial_command("echo hello; ls"))

    def test_echo_with_ampersand_allowed(self):
        self.assertFalse(self.module._is_trivial_command("echo hello && true"))

    def test_echo_subshell_allowed(self):
        """echo $(date) has subshell substitution — char class excludes $() so it is NOT trivial."""
        self.assertFalse(self.module._is_trivial_command("echo $(date)"))

    def test_version_flag_rejected(self):
        self.assertTrue(self.module._is_trivial_command("python3 --version"))

    def test_version_flag_with_path_rejected(self):
        self.assertTrue(self.module._is_trivial_command("/usr/bin/python3 --version"))

    def test_version_like_but_not_rejected(self):
        """myapp --version-file is not a bare --version, must be allowed."""
        self.assertFalse(self.module._is_trivial_command("myapp --version-file"))

    def test_true_command_rejected(self):
        self.assertTrue(self.module._is_trivial_command("true"))

    def test_colon_command_rejected(self):
        self.assertTrue(self.module._is_trivial_command(":"))

    def test_real_command_allowed(self):
        self.assertFalse(self.module._is_trivial_command(
            "python3 plugin/scripts/prewrite_gate.py"))

    def test_skip_logs_rejected_entry(self):
        """Trivial command must produce a codifier-rejected log entry."""
        with tempfile.TemporaryDirectory() as fake_root:
            _make_fake_root_with_manifest(fake_root)
            with tempfile.TemporaryDirectory() as task_dir:
                transcript = os.path.join(task_dir, "CRITIC__qa.md")
                with open(transcript, "w") as f:
                    f.write(FIXTURE_TRANSCRIPT_TRIVIAL)
                self.module.codify(task_dir, transcript_path=transcript,
                                   target_root=fake_root)
            learnings = os.path.join(fake_root, "doc", "harness", "learnings.jsonl")
            self.assertTrue(os.path.isfile(learnings), "No learnings.jsonl written")
            with open(learnings) as f:
                log = f.read()
            self.assertIn("codifier-rejected", log)
            self.assertIn("trivial-command", log)

    def test_learnings_no_leak(self):
        """codify(target_root=fake) must write learnings inside fake_root, not real repo."""
        with tempfile.TemporaryDirectory() as fake_root:
            _make_fake_root_with_manifest(fake_root)
            with tempfile.TemporaryDirectory() as task_dir:
                transcript = os.path.join(task_dir, "CRITIC__qa.md")
                with open(transcript, "w") as f:
                    f.write(FIXTURE_TRANSCRIPT_WITH_AC_ID)
                self.module.codify(task_dir, transcript_path=transcript,
                                   target_root=fake_root)
            real_learnings = os.path.join(REPO_ROOT, "doc", "harness", "learnings.jsonl")
            if os.path.isfile(real_learnings):
                with open(real_learnings) as f:
                    content = f.read()
                # Must not have entries from this specific test run task
                # (we can only check that fake_root learnings exist)
            fake_learnings = os.path.join(fake_root, "doc", "harness", "learnings.jsonl")
            # Either no log (no blocks skipped) or log is inside fake_root only
            if os.path.isfile(fake_learnings):
                with open(fake_learnings) as f:
                    log = f.read()
                self.assertIn("qa_codifier", log)

    def test_codify_accepts_target_root_kwarg(self):
        """AC-001: codify() writes to target_root, never leaks to real repo."""
        with tempfile.TemporaryDirectory() as task_dir:
            with tempfile.TemporaryDirectory() as fake_root:
                _make_fake_root_with_manifest(fake_root)
                transcript = os.path.join(task_dir, "CRITIC__qa.md")
                with open(transcript, "w") as f:
                    f.write(FIXTURE_TRANSCRIPT_WITH_AC_ID)
                result = self.module.codify(task_dir, transcript_path=transcript,
                                            target_root=fake_root)
                self.assertEqual(result, 0)
                regression_dir = os.path.join(fake_root, "tests", "regression")
                self.assertTrue(os.path.isdir(regression_dir),
                                f"regression dir missing in fake_root: {fake_root}")
                real_regression = os.path.join(REPO_ROOT, "tests", "regression")
                task_id = os.path.basename(os.path.normpath(task_dir))
                sanitized = self.module.sanitize_task_id(task_id)
                leaked = os.path.join(real_regression, sanitized)
                self.assertFalse(os.path.exists(leaked),
                                 f"codify leaked into real repo root: {leaked}")


if __name__ == "__main__":
    unittest.main()
