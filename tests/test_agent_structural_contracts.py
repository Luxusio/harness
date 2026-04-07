"""Regression tests for P3: writer.md contains doc registry navigation directives.

Ensures plugin/agents/writer.md references doc/CLAUDE.md and has proper
durable-note location guidance, and that plugin/CLAUDE.md has a Doc delegation
section that references the doc registry.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_DIR = REPO_ROOT / "plugin" / "scripts"
WRITER_MD = REPO_ROOT / "plugin" / "agents" / "writer.md"
PLUGIN_CLAUDE_MD = REPO_ROOT / "plugin" / "CLAUDE.md"
sys.path.insert(0, str(SCRIPT_DIR))
os.environ["HARNESS_SKIP_STDIN"] = "1"


class WriterMdStructuralContractTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.writer_content = WRITER_MD.read_text(encoding="utf-8")
        cls.plugin_claude_content = PLUGIN_CLAUDE_MD.read_text(encoding="utf-8")

    def test_writer_md_contains_doc_registry_reference(self):
        """writer.md must mention doc/CLAUDE.md as the doc registry."""
        self.assertIn(
            "doc/CLAUDE.md",
            self.writer_content,
            msg=f"{WRITER_MD} does not contain 'doc/CLAUDE.md'",
        )

    def test_writer_md_has_durable_note_location_section(self):
        """writer.md must have a 'Durable note location' section."""
        self.assertIn(
            "Durable note location",
            self.writer_content,
            msg=f"{WRITER_MD} does not contain 'Durable note location' section",
        )

    def test_writer_md_doc_root_guidance_not_task_dir(self):
        """writer.md must explicitly warn against writing durable notes to the task dir."""
        self.assertIn(
            "Never write",
            self.writer_content,
            msg=(
                f"{WRITER_MD} does not contain 'Never write' guidance "
                "discouraging durable notes in the task dir"
            ),
        )

    def test_plugin_claude_md_has_doc_delegation_section(self):
        """plugin/CLAUDE.md must have a 'Doc delegation' section."""
        self.assertIn(
            "Doc delegation",
            self.plugin_claude_content,
            msg=f"{PLUGIN_CLAUDE_MD} does not contain 'Doc delegation' section",
        )

    def test_plugin_claude_md_doc_delegation_references_doc_registry(self):
        """The Doc delegation section in plugin/CLAUDE.md must reference doc/CLAUDE.md."""
        # Find the Doc delegation section and check that doc/CLAUDE.md appears
        # within a reasonable window after it.
        idx = self.plugin_claude_content.find("Doc delegation")
        self.assertNotEqual(idx, -1, msg=f"{PLUGIN_CLAUDE_MD} lacks 'Doc delegation' section")
        # Look within 500 chars after the section header for the registry reference
        window = self.plugin_claude_content[idx: idx + 500]
        self.assertIn(
            "doc/CLAUDE.md",
            window,
            msg=(
                f"'doc/CLAUDE.md' not found within 500 chars after 'Doc delegation' "
                f"in {PLUGIN_CLAUDE_MD}. Window: {window!r}"
            ),
        )


if __name__ == "__main__":
    unittest.main()
