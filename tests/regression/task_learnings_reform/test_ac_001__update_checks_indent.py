"""Regression test for TASK__learnings-system-reform-and-yaml-bug-fix AC-001.

Bug: update_checks.py _set_field append branch hardcoded 2-space indent for
missing fields, mismatching the 4-space indent of existing AC item fields.
Symptom: when promoting an AC whose CHECKS.yaml lacks `last_updated`, the
appended field landed at list-item indent instead of nested-field indent,
producing invalid YAML. _parse_checks_yaml in harness_server.py then returned
None, which caused _checks_gate_status to report "absent" and silently
bypass the close-gate AC validation.

This test:
1. Writes a CHECKS.yaml whose AC block lacks `last_updated`.
2. Invokes update_check() to promote status.
3. Asserts the resulting file is valid YAML.
4. Asserts `last_updated` lands at the same indent as existing fields.
"""
import os
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(REPO_ROOT, "plugin", "scripts"))


def _make_fake_repo(root: str) -> str:
    os.makedirs(os.path.join(root, "doc", "harness"), exist_ok=True)
    with open(os.path.join(root, "doc", "harness", "manifest.yaml"), "w") as f:
        f.write("name: fake\ntype: library\n")
    task_dir = os.path.join(root, "doc", "harness", "tasks", "TASK__fake")
    os.makedirs(task_dir, exist_ok=True)
    return task_dir


def _fresh_module():
    import importlib
    import update_checks
    importlib.reload(update_checks)
    return update_checks


class TestUpdateChecksIndent(unittest.TestCase):
    def test_missing_last_updated_appended_at_correct_indent(self):
        """Promoting a status when last_updated is missing must produce valid YAML."""
        with tempfile.TemporaryDirectory() as root:
            task_dir = _make_fake_repo(root)
            checks_path = os.path.join(task_dir, "CHECKS.yaml")

            # AC block with the schema we actually emit from write_plan_artifact.py:
            # no last_updated field, list-item at 2-space indent, fields at 4-space.
            with open(checks_path, "w") as f:
                f.write(
                    "checks:\n"
                    "  - id: AC-001\n"
                    '    title: "first"\n'
                    "    kind: doc\n"
                    "    status: open\n"
                    '    evidence: ""\n'
                    "    reopen_count: 0\n"
                    "  - id: AC-002\n"
                    '    title: "second"\n'
                    "    kind: doc\n"
                    "    status: open\n"
                    '    evidence: ""\n'
                    "    reopen_count: 0\n"
                )

            uc = _fresh_module()
            uc.update_check(
                checks_path=checks_path,
                ac_id="AC-001",
                status="implemented_candidate",
                evidence="bench",
                note=None,
                root_cause=None,
                test_evidence=None,
                no_test_required=None,
            )

            with open(checks_path) as f:
                content = f.read()

            # 1. last_updated must exist and NOT be at 2-space indent.
            self.assertIn("last_updated", content,
                          "last_updated should have been appended")
            self.assertNotIn("\n  last_updated:", content,
                             "last_updated must NOT be at 2-space indent — that mixes "
                             "list-item and map-key levels and breaks YAML parsing")

            # 2. last_updated must be at 4-space indent (matches existing fields).
            self.assertIn("\n    last_updated:", content,
                          "last_updated should land at 4-space indent matching "
                          "sibling field lines")

            # 3. The file must still be parseable. Try yaml if available; fall back
            #    to a structural check that AC-002 still appears as a list item.
            try:
                import yaml  # noqa: F401
                parsed = yaml.safe_load(content)
                self.assertIsInstance(parsed, dict)
                self.assertIn("checks", parsed)
                self.assertEqual(len(parsed["checks"]), 2)
                ac1 = parsed["checks"][0]
                self.assertEqual(ac1["id"], "AC-001")
                self.assertEqual(ac1["status"], "implemented_candidate")
                self.assertIn("last_updated", ac1)
            except ImportError:
                # PyYAML unavailable — structural fallback.
                self.assertIn("- id: AC-002", content)


if __name__ == "__main__":
    unittest.main()
