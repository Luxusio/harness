"""Tests for the test-evidence gate added to update_checks.py.

Covers AC-001..AC-005 of TASK__test-workflow-gaps:
- AC-001: kind=feature blocked without --test-evidence
- AC-002: kind=functional blocked; missing kind defaults to unknown and SKIPS
- AC-003: kind in {bugfix, doc, verification} skip the new gate
- AC-004: --test-evidence path validation (existence, symlink, traversal)
- AC-005: --no-test-required bypass with non-empty reason; cap at 400 chars; logged
"""
import json
import os
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "plugin", "scripts"))


def _make_fake_repo(root: str) -> str:
    """Create a minimal repo layout (manifest + task dir) inside `root`.

    Returns the task_dir path. CHECKS.yaml is left for individual tests to populate.
    """
    os.makedirs(os.path.join(root, "doc", "harness"), exist_ok=True)
    with open(os.path.join(root, "doc", "harness", "manifest.yaml"), "w") as f:
        f.write("name: fake\ntype: library\n")
    task_dir = os.path.join(root, "doc", "harness", "tasks", "TASK__fake")
    os.makedirs(task_dir, exist_ok=True)
    return task_dir


def _write_checks(task_dir: str, *acs: str) -> str:
    """Write CHECKS.yaml with the supplied AC blocks. Returns the file path."""
    path = os.path.join(task_dir, "CHECKS.yaml")
    with open(path, "w") as f:
        f.write("\n".join(acs) + "\n")
    return path


def _ac_block(ac_id: str, kind: str | None = "feature", status: str = "open",
              evidence: str = "") -> str:
    """Build one CHECKS.yaml AC block. `kind=None` omits the field entirely."""
    lines = [
        f"- id: {ac_id}",
        f'  title: "test ac"',
        f"  status: {status}",
    ]
    if kind is not None:
        lines.append(f"  kind: {kind}")
    lines.extend([
        "  owner: developer",
        "  completeness: 7",
        '  root_cause: ""',
        "  reopen_count: 0",
        "  last_updated: 2026-04-30T00:00:00Z",
        f'  evidence: "{evidence}"',
        '  note: ""',
    ])
    return "\n".join(lines)


def _fresh_module():
    import importlib
    import update_checks
    importlib.reload(update_checks)
    return update_checks


class TestFeatureGate(unittest.TestCase):
    """AC-001: kind=feature blocked without --test-evidence."""

    def test_feature_kind_blocked_without_evidence(self):
        with tempfile.TemporaryDirectory() as root:
            task_dir = _make_fake_repo(root)
            checks = _write_checks(task_dir, _ac_block("AC-001", kind="feature"))
            uc = _fresh_module()
            with self.assertRaises(ValueError) as cm:
                uc.update_check(checks, "AC-001", "implemented_candidate")
            msg = str(cm.exception)
            self.assertIn("test evidence", msg.lower())
            self.assertIn("--test-evidence", msg)
            self.assertIn("--no-test-required", msg)
            self.assertIn("AC-001", msg)
            self.assertIn("feature", msg)

    def test_feature_passes_with_evidence(self):
        with tempfile.TemporaryDirectory() as root:
            task_dir = _make_fake_repo(root)
            checks = _write_checks(task_dir, _ac_block("AC-001", kind="feature"))
            # Create a real test file the evidence flag can point at.
            test_path = os.path.join(root, "tests", "regression", "test_thing.py")
            os.makedirs(os.path.dirname(test_path), exist_ok=True)
            with open(test_path, "w") as f:
                f.write("def test_x(): pass\n")
            uc = _fresh_module()
            result = uc.update_check(
                checks, "AC-001", "implemented_candidate",
                test_evidence="tests/regression/test_thing.py",
            )
            self.assertEqual(result["status"], "implemented_candidate")
            with open(checks) as f:
                body = f.read()
            self.assertIn("evidence: tests/regression/test_thing.py", body)


class TestFunctionalAndMissingKind(unittest.TestCase):
    """AC-002: kind=functional gated; missing kind defaults to 'unknown' and SKIPS."""

    def test_functional_kind_blocked_without_evidence(self):
        with tempfile.TemporaryDirectory() as root:
            task_dir = _make_fake_repo(root)
            checks = _write_checks(task_dir, _ac_block("AC-001", kind="functional"))
            uc = _fresh_module()
            with self.assertRaises(ValueError) as cm:
                uc.update_check(checks, "AC-001", "implemented_candidate")
            self.assertIn("functional", str(cm.exception))

    def test_missing_kind_defaults_unknown_and_skips_gate(self):
        with tempfile.TemporaryDirectory() as root:
            task_dir = _make_fake_repo(root)
            checks = _write_checks(task_dir, _ac_block("AC-001", kind=None))
            uc = _fresh_module()
            # Should NOT raise — missing kind => unknown => skip the test-evidence gate.
            result = uc.update_check(checks, "AC-001", "implemented_candidate")
            self.assertEqual(result["status"], "implemented_candidate")


class TestSkipAllowlist(unittest.TestCase):
    """AC-003: kind in {bugfix, doc, verification} skip the test-evidence gate."""

    def test_doc_kind_skips_gate(self):
        with tempfile.TemporaryDirectory() as root:
            task_dir = _make_fake_repo(root)
            checks = _write_checks(task_dir, _ac_block("AC-001", kind="doc"))
            uc = _fresh_module()
            result = uc.update_check(checks, "AC-001", "implemented_candidate")
            self.assertEqual(result["status"], "implemented_candidate")

    def test_verification_kind_skips_gate(self):
        with tempfile.TemporaryDirectory() as root:
            task_dir = _make_fake_repo(root)
            checks = _write_checks(task_dir, _ac_block("AC-001", kind="verification"))
            uc = _fresh_module()
            result = uc.update_check(checks, "AC-001", "implemented_candidate")
            self.assertEqual(result["status"], "implemented_candidate")

    def test_bugfix_remains_under_iron_law_only(self):
        """bugfix is gated by --root-cause, NOT by the new test-evidence rule."""
        with tempfile.TemporaryDirectory() as root:
            task_dir = _make_fake_repo(root)
            checks = _write_checks(task_dir, _ac_block("AC-001", kind="bugfix"))
            uc = _fresh_module()
            with self.assertRaises(ValueError) as cm:
                uc.update_check(checks, "AC-001", "implemented_candidate")
            # Should mention root_cause, NOT test-evidence
            msg = str(cm.exception)
            self.assertIn("root_cause", msg)
            self.assertNotIn("--test-evidence", msg)


class TestEvidencePathValidation(unittest.TestCase):
    """AC-004: realpath inside repo_root + reject symlinks + reject missing files."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.task_dir = _make_fake_repo(self.root)
        self.checks = _write_checks(self.task_dir, _ac_block("AC-001", kind="feature"))
        self.uc = _fresh_module()

    def tearDown(self):
        self.tmp.cleanup()

    def test_nonexistent_path_rejected(self):
        with self.assertRaises(ValueError) as cm:
            self.uc.update_check(
                self.checks, "AC-001", "implemented_candidate",
                test_evidence="tests/does/not/exist.py",
            )
        self.assertIn("does not exist", str(cm.exception))

    def test_symlink_rejected(self):
        target = os.path.join(self.root, "tests", "real_test.py")
        link = os.path.join(self.root, "tests", "linked_test.py")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w") as f:
            f.write("def test_x(): pass\n")
        os.symlink(target, link)
        with self.assertRaises(ValueError) as cm:
            self.uc.update_check(
                self.checks, "AC-001", "implemented_candidate",
                test_evidence="tests/linked_test.py",
            )
        self.assertIn("symlink", str(cm.exception).lower())

    def test_path_outside_repo_root_rejected(self):
        # Create a real file outside repo_root, then attempt absolute reference.
        with tempfile.NamedTemporaryFile(delete=False, suffix=".py") as f:
            f.write(b"def test_x(): pass\n")
            outside = f.name
        try:
            with self.assertRaises(ValueError) as cm:
                self.uc.update_check(
                    self.checks, "AC-001", "implemented_candidate",
                    test_evidence=outside,
                )
            self.assertIn("outside", str(cm.exception).lower())
        finally:
            os.unlink(outside)

    def test_absolute_path_inside_repo_accepted(self):
        target = os.path.join(self.root, "tests", "test_thing.py")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w") as f:
            f.write("def test_x(): pass\n")
        result = self.uc.update_check(
            self.checks, "AC-001", "implemented_candidate",
            test_evidence=target,
        )
        self.assertEqual(result["status"], "implemented_candidate")


class TestNoTestRequiredBypass(unittest.TestCase):
    """AC-005: --no-test-required bypasses gate; logs to learnings.jsonl; capped 400."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.task_dir = _make_fake_repo(self.root)
        self.checks = _write_checks(self.task_dir, _ac_block("AC-001", kind="feature"))
        self.uc = _fresh_module()
        self.learnings = os.path.join(self.root, "doc", "harness", "learnings.jsonl")

    def tearDown(self):
        self.tmp.cleanup()

    def test_bypass_with_reason_succeeds(self):
        result = self.uc.update_check(
            self.checks, "AC-001", "implemented_candidate",
            no_test_required="config-only AC, no behavior to test",
        )
        self.assertEqual(result["status"], "implemented_candidate")

    def test_bypass_logs_to_learnings(self):
        self.uc.update_check(
            self.checks, "AC-001", "implemented_candidate",
            no_test_required="config-only AC",
        )
        self.assertTrue(os.path.isfile(self.learnings),
                        "learnings.jsonl was not created")
        with open(self.learnings) as f:
            entries = [json.loads(ln) for ln in f if ln.strip()]
        bypass = [e for e in entries if e.get("type") == "test-evidence-bypass"]
        self.assertEqual(len(bypass), 1)
        self.assertEqual(bypass[0]["ac"], "AC-001")
        self.assertEqual(bypass[0]["reason"], "config-only AC")
        self.assertEqual(bypass[0]["source"], "update_checks")

    def test_bypass_writes_BYPASS_marker_into_evidence_field(self):
        self.uc.update_check(
            self.checks, "AC-001", "implemented_candidate",
            no_test_required="config-only AC",
        )
        with open(self.checks) as f:
            body = f.read()
        self.assertIn("BYPASS: config-only AC", body)

    def test_empty_reason_rejected(self):
        with self.assertRaises(ValueError) as cm:
            self.uc.update_check(
                self.checks, "AC-001", "implemented_candidate",
                no_test_required="",
            )
        self.assertIn("non-empty reason", str(cm.exception))

    def test_whitespace_only_reason_rejected(self):
        with self.assertRaises(ValueError) as cm:
            self.uc.update_check(
                self.checks, "AC-001", "implemented_candidate",
                no_test_required="   \t  ",
            )
        self.assertIn("non-empty reason", str(cm.exception))

    def test_reason_over_400_chars_rejected(self):
        with self.assertRaises(ValueError) as cm:
            self.uc.update_check(
                self.checks, "AC-001", "implemented_candidate",
                no_test_required="x" * 401,
            )
        self.assertIn("400 char cap", str(cm.exception))

    def test_reason_exactly_400_chars_accepted(self):
        result = self.uc.update_check(
            self.checks, "AC-001", "implemented_candidate",
            no_test_required="x" * 400,
        )
        self.assertEqual(result["status"], "implemented_candidate")


class TestSuggestion(unittest.TestCase):
    """The error message includes 'Suggested:' when exactly one matching test exists."""

    def test_suggestion_appears_when_test_file_matches_ac_id(self):
        with tempfile.TemporaryDirectory() as root:
            task_dir = _make_fake_repo(root)
            checks = _write_checks(task_dir, _ac_block("AC-007", kind="feature"))
            # One matching test file under tests/.
            match = os.path.join(root, "tests", "regression", "task_x", "test_ac_007__behavior.py")
            os.makedirs(os.path.dirname(match), exist_ok=True)
            with open(match, "w") as f:
                f.write("def test_x(): pass\n")
            uc = _fresh_module()
            with self.assertRaises(ValueError) as cm:
                uc.update_check(checks, "AC-007", "implemented_candidate")
            self.assertIn("Suggested:", str(cm.exception))
            self.assertIn("test_ac_007__behavior.py", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
