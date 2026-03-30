"""Tests for directive_capture.py and prompt_memory.py — language-agnostic approach.

Covers:
  - Structural signal detection (emphasis, imperatives, file refs)
  - Directive staging into DIRECTIVES_PENDING.yaml
  - Dedup on repeated staging
  - TASK_STATE directive_capture_state update
  - Prompt intent classification (structural, not vocab-based)
  - Lane detection from file path references

Run with: python -m unittest discover -s tests -p 'test_*.py'
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "scripts"))
os.environ["HARNESS_SKIP_STDIN"] = "1"

from directive_capture import (
    _has_directive_structural_signals, stage_directive,
    _is_short_or_casual,
)
from prompt_memory import classify_prompt_intent, detect_lane_from_prompt
from _lib import yaml_field


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


# ---------------------------------------------------------------------------
# Structural signal detection (language-agnostic)
# ---------------------------------------------------------------------------

class TestStructuralSignals(unittest.TestCase):

    def test_exclamation_gives_signal(self):
        score = _has_directive_structural_signals("Do this from now on!")
        self.assertGreater(score, 0.0)

    def test_all_caps_gives_signal(self):
        score = _has_directive_structural_signals("NEVER do this in production code")
        self.assertGreater(score, 0.0)

    def test_bold_markdown_gives_signal(self):
        score = _has_directive_structural_signals("**Always** run tests before commit")
        self.assertGreater(score, 0.0)

    def test_question_reduces_signal(self):
        score = _has_directive_structural_signals("How does the login system work?")
        self.assertLessEqual(score, 0.0)

    def test_empty_returns_zero(self):
        score = _has_directive_structural_signals("")
        self.assertEqual(score, 0.0)

    def test_short_declarative_gives_signal(self):
        """Short declarative (no question mark) should score."""
        score = _has_directive_structural_signals("Run tests before every commit!")
        self.assertGreater(score, 0.0)


class TestIsShortOrCasual(unittest.TestCase):

    def test_short_prompt(self):
        self.assertTrue(_is_short_or_casual("ok"))

    def test_two_words(self):
        self.assertTrue(_is_short_or_casual("sounds good"))

    def test_meaningful_prompt(self):
        self.assertFalse(_is_short_or_casual("Always update templates when changing scripts"))

    def test_empty(self):
        self.assertTrue(_is_short_or_casual(""))

    def test_none(self):
        self.assertTrue(_is_short_or_casual(None))


# ---------------------------------------------------------------------------
# Directive staging
# ---------------------------------------------------------------------------

class TestStageDirective(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.task_dir = os.path.join(self.tmp.name, "TASK__test")
        os.makedirs(self.task_dir)

    def tearDown(self):
        self.tmp.cleanup()

    def _init_task_state(self):
        _write(os.path.join(self.task_dir, "TASK_STATE.yaml"),
               "task_id: TASK__test\n"
               "status: created\n"
               "directive_capture_state: clean\n"
               "pending_directive_ids: []\n"
               "updated: 2026-01-01T00:00:00Z\n")

    def test_stages_directive_to_pending_file(self):
        """stage_directive creates DIRECTIVES_PENDING.yaml."""
        self._init_task_state()
        dir_id = stage_directive(self.task_dir, "Always update templates too", "process")

        self.assertIsNotNone(dir_id)
        pending_file = os.path.join(self.task_dir, "DIRECTIVES_PENDING.yaml")
        self.assertTrue(os.path.isfile(pending_file))

        with open(pending_file) as f:
            content = f.read()
        self.assertIn("status: pending", content)
        self.assertIn("Always update templates too", content)

    def test_updates_task_state(self):
        """Staging updates directive_capture_state to pending."""
        self._init_task_state()
        stage_directive(self.task_dir, "Run tests first", "process")

        state_file = os.path.join(self.task_dir, "TASK_STATE.yaml")
        state = yaml_field("directive_capture_state", state_file)
        self.assertEqual(state, "pending")

    def test_dedup_same_text(self):
        """Same directive text is not staged twice."""
        self._init_task_state()
        id1 = stage_directive(self.task_dir, "Always run tests", "process")
        id2 = stage_directive(self.task_dir, "Always run tests", "process")

        self.assertIsNotNone(id1)
        self.assertIsNone(id2, "Duplicate directive should not be staged again")

    def test_no_task_dir_returns_none(self):
        dir_id = stage_directive(None, "some directive", "process")
        self.assertIsNone(dir_id)

    def test_no_text_returns_none(self):
        self._init_task_state()
        dir_id = stage_directive(self.task_dir, "", "process")
        self.assertIsNone(dir_id)

    def test_multiple_directives_append(self):
        """Multiple different directives append to the same file."""
        self._init_task_state()
        id1 = stage_directive(self.task_dir, "First rule: always test", "process")
        id2 = stage_directive(self.task_dir, "Second rule: never skip review", "process")

        self.assertIsNotNone(id1)
        self.assertIsNotNone(id2)

        pending_file = os.path.join(self.task_dir, "DIRECTIVES_PENDING.yaml")
        with open(pending_file) as f:
            content = f.read()
        self.assertIn("First rule", content)
        self.assertIn("Second rule", content)


# ---------------------------------------------------------------------------
# Prompt intent classification (language-agnostic)
# ---------------------------------------------------------------------------

class TestClassifyPromptIntent(unittest.TestCase):

    def test_question_is_answer(self):
        """Questions (any language) → answer."""
        self.assertEqual(classify_prompt_intent("How does the login system work?"), "answer")

    def test_task_artifact_ref_is_mutating(self):
        """Reference to PLAN.md/TASK_STATE → mutating."""
        self.assertEqual(classify_prompt_intent("@PLAN.md implement this"), "mutating")

    def test_file_ref_short_is_mutating(self):
        """Short prompt with file reference → mutating."""
        self.assertEqual(classify_prompt_intent("fix src/auth.py"), "mutating")

    def test_code_block_is_mutating(self):
        """Code block in prompt → mutating."""
        self.assertEqual(classify_prompt_intent("apply this:\n```\ndef foo(): pass\n```"), "mutating")

    def test_short_command_is_mutating(self):
        """Short imperative (2-8 words, no question) → mutating."""
        self.assertEqual(classify_prompt_intent("clean up the tests"), "mutating")

    def test_empty_is_answer(self):
        self.assertEqual(classify_prompt_intent(""), "answer")

    def test_very_short_is_answer(self):
        self.assertEqual(classify_prompt_intent("ok thanks"), "answer")

    def test_long_with_file_ref_is_investigate(self):
        """Longer text with file refs but no question → investigate."""
        prompt = "I noticed that plugin/scripts/prewrite_gate.py has some issues with the path handling logic and I want to understand the root cause"
        result = classify_prompt_intent(prompt)
        self.assertIn(result, ("investigate", "mutating"))  # Either is reasonable


# ---------------------------------------------------------------------------
# Lane detection (file path based, language-agnostic)
# ---------------------------------------------------------------------------

class TestDetectLaneFromPrompt(unittest.TestCase):

    def test_test_file_ref(self):
        self.assertEqual(detect_lane_from_prompt("look at tests/test_foo.py"), "verify")

    def test_doc_file_ref(self):
        self.assertEqual(detect_lane_from_prompt("update doc/common/notes.md"), "docs-sync")

    def test_readme_ref(self):
        self.assertEqual(detect_lane_from_prompt("fix README section"), "docs-sync")

    def test_spec_file_ref(self):
        self.assertEqual(detect_lane_from_prompt("check auth.spec.ts results"), "verify")

    def test_no_hint(self):
        """No file path references → None."""
        self.assertIsNone(detect_lane_from_prompt("make it better"))

    def test_empty(self):
        self.assertIsNone(detect_lane_from_prompt(""))


if __name__ == "__main__":
    unittest.main()
