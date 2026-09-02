"""`doc/harness/qa/QA_KNOWLEDGE.yaml` must stay loadable and mapping-shaped.

Every `qa-*` agent is told to read this file first and to append discoveries to
it, so an append in the wrong shape is a normal, expected event — and its blast
radius is the whole file, not the new entry. On 2026-09-02 a QA lens appended a
top-level sequence item to this mapping document; parsing it then raised and
every accumulated section became unreadable at once, including the note that
existed to stop the next session re-diagnosing a known-expected warning.

Four consecutive review rounds and a 1010-test suite missed it for one reason:
nothing parsed the file. This test is that parse.

PyYAML is not a dependency of this repo (see
`test_no_toplevel_third_party_imports`), so the parser is imported inside the
tests and the checks degrade to a structural scan when it is absent.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KNOWLEDGE = ROOT / "doc" / "harness" / "qa" / "QA_KNOWLEDGE.yaml"

TOP_LEVEL_SECTIONS = (
    "services",
    "selectors",
    "test_data",
    "known_issues",
    "patterns",
    "qa_notes",
)


def _yaml():
    try:
        import yaml  # type: ignore
    except ImportError:
        return None
    return yaml


class QaKnowledgeShapeTests(unittest.TestCase):
    def setUp(self):
        self.text = KNOWLEDGE.read_text(encoding="utf-8")

    def test_no_top_level_sequence_item(self):
        """The break was a `- ` at column 0 in a mapping document.

        Checked without a parser so the guard holds on a bare interpreter.
        """
        offenders = [
            (n, line)
            for n, line in enumerate(self.text.splitlines(), 1)
            if re.match(r"^- ", line)
        ]
        self.assertEqual(offenders, [], offenders)

    def test_parses_and_keeps_its_sections(self):
        yaml = _yaml()
        if yaml is None:
            self.skipTest("PyYAML absent")
        data = yaml.safe_load(self.text)
        self.assertIsInstance(data, dict)
        for section in TOP_LEVEL_SECTIONS:
            self.assertIn(section, data)

    def test_every_qa_note_is_a_named_mapping(self):
        """Appends belong under `qa_notes` as `<slug>: {discovered, notes}`."""
        yaml = _yaml()
        if yaml is None:
            self.skipTest("PyYAML absent")
        notes = yaml.safe_load(self.text)["qa_notes"]
        self.assertIsInstance(notes, dict)
        self.assertTrue(notes)
        for name, entry in notes.items():
            self.assertIsInstance(name, str, name)
            self.assertIsInstance(entry, dict, name)
            self.assertIn("notes", entry, name)


if __name__ == "__main__":
    unittest.main()
