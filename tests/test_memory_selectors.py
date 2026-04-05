#!/usr/bin/env python3
import os
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "scripts"))
os.environ["HARNESS_SKIP_STDIN"] = "1"
os.environ["HARNESS_SKIP_PREREAD"] = "1"

import memory_selectors
import prompt_memory


class TestMemorySelectors(unittest.TestCase):
    def test_inline_header_freshness_and_summary_are_respected(self):
        with tempfile.TemporaryDirectory() as tmp:
            prev = os.getcwd()
            os.chdir(tmp)
            try:
                os.makedirs("doc/common", exist_ok=True)
                os.makedirs("doc/harness", exist_ok=True)
                with open("doc/harness/manifest.yaml", "w", encoding="utf-8") as f:
                    f.write("registered_roots:\n  - common\n")

                with open("doc/common/stale.md", "w", encoding="utf-8") as f:
                    f.write(textwrap.dedent("""\
                        # stale note
                        summary: stale summary should not win
                        freshness: stale
                        invalidated_by_paths:
                          - src/api.py

                        stale api foo
                    """))
                with open("doc/common/current.md", "w", encoding="utf-8") as f:
                    f.write(textwrap.dedent("""\
                        # current note
                        summary: current summary should win
                        freshness: current

                        api foo
                    """))

                notes = memory_selectors.select_relevant_notes("fix src/api.py foo")
                self.assertGreaterEqual(len(notes), 2)
                self.assertEqual(notes[0][0], "doc/common/current.md")
                self.assertEqual(notes[0][2], "current summary should win")
                self.assertEqual(notes[0][3], "current")
                self.assertEqual(notes[1][3], "stale")
            finally:
                os.chdir(prev)


class TestPromptMemoryRootAndLaneInference(unittest.TestCase):
    def test_gather_context_prefers_task_root_over_unrelated_registered_roots(self):
        with tempfile.TemporaryDirectory() as tmp:
            prev = os.getcwd()
            os.chdir(tmp)
            try:
                os.makedirs("doc/harness/tasks/TASK__active", exist_ok=True)
                os.makedirs("doc/harness", exist_ok=True)
                os.makedirs("doc/api", exist_ok=True)
                os.makedirs("doc/ui", exist_ok=True)
                with open("doc/harness/manifest.yaml", "w", encoding="utf-8") as f:
                    f.write("registered_roots:\n  - common\n  - api\n  - ui\n")
                with open("doc/harness/tasks/TASK__active/TASK_STATE.yaml", "w", encoding="utf-8") as f:
                    f.write("task_id: TASK__active\nstatus: planned\nlane: build\nverification_targets: [\"doc/api/service.md\"]\ntouched_paths: []\n")
                with open("doc/api/handler.md", "w", encoding="utf-8") as f:
                    f.write("# API handler\nsummary: API handler regression guidance\nfreshness: current\n\nhandler regression fix\n")
                with open("doc/ui/handler.md", "w", encoding="utf-8") as f:
                    f.write("# UI handler\nsummary: UI handler regression guidance\nfreshness: current\n\nhandler regression fix\n")

                active_task = os.path.join(tmp, "doc", "harness", "tasks", "TASK__active")
                with mock.patch.object(prompt_memory, "TASK_DIR", os.path.join(tmp, "doc", "harness", "tasks")),                      mock.patch.object(prompt_memory, "_get_active_task_dir", return_value=active_task):
                    parts = prompt_memory.gather_context("fix the handler regression")

                note_parts = [part for part in parts if part.startswith("note:")]
                self.assertTrue(note_parts, parts)
                self.assertIn("[api] API handler regression guidance", note_parts[0])
            finally:
                os.chdir(prev)

    def test_gather_context_prefers_active_task_lane_when_prompt_has_no_lane_hint(self):
        with tempfile.TemporaryDirectory() as tmp:
            prev = os.getcwd()
            os.chdir(tmp)
            try:
                os.makedirs("doc/harness/tasks/TASK__active", exist_ok=True)
                os.makedirs("doc/harness", exist_ok=True)
                os.makedirs("doc/common", exist_ok=True)
                with open("doc/harness/manifest.yaml", "w", encoding="utf-8") as f:
                    f.write("registered_roots:\n  - common\n")
                with open("doc/harness/tasks/TASK__active/TASK_STATE.yaml", "w", encoding="utf-8") as f:
                    f.write("task_id: TASK__active\nstatus: planned\nlane: build\nverification_targets: []\ntouched_paths: []\n")
                with open("doc/common/build-note.md", "w", encoding="utf-8") as f:
                    f.write("# Build note\nsummary: build-lane guidance\nlane: build\nfreshness: current\n\nhandler regression fix\n")
                with open("doc/common/verify-note.md", "w", encoding="utf-8") as f:
                    f.write("# Verify note\nsummary: verify-lane guidance\nlane: verify\nfreshness: current\n\nhandler regression fix\n")

                active_task = os.path.join(tmp, "doc", "harness", "tasks", "TASK__active")
                with mock.patch.object(prompt_memory, "TASK_DIR", os.path.join(tmp, "doc", "harness", "tasks")),                      mock.patch.object(prompt_memory, "_get_active_task_dir", return_value=active_task):
                    parts = prompt_memory.gather_context("fix the handler regression")

                note_parts = [part for part in parts if part.startswith("note:")]
                self.assertTrue(note_parts, parts)
                self.assertIn("build-lane guidance", note_parts[0])
                self.assertNotIn("verify-lane guidance", note_parts[0])
            finally:
                os.chdir(prev)


if __name__ == "__main__":
    unittest.main()
