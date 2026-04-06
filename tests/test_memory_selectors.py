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

    def test_select_relevant_notes_respects_scan_roots(self):
        with tempfile.TemporaryDirectory() as tmp:
            prev = os.getcwd()
            os.chdir(tmp)
            try:
                os.makedirs("doc/common", exist_ok=True)
                os.makedirs("doc/api", exist_ok=True)
                os.makedirs("doc/ui", exist_ok=True)
                os.makedirs("doc/harness", exist_ok=True)
                with open("doc/harness/manifest.yaml", "w", encoding="utf-8") as f:
                    f.write("registered_roots:\n  - common\n  - api\n  - ui\n")

                with open("doc/api/handler.md", "w", encoding="utf-8") as f:
                    f.write(
                        "# API note\n"
                        "summary: api guidance\n"
                        "freshness: current\n\n"
                        "handler regression fix\n"
                    )
                with open("doc/ui/handler.md", "w", encoding="utf-8") as f:
                    f.write(
                        "# UI note\n"
                        "summary: ui guidance\n"
                        "freshness: current\n\n"
                        "handler regression fix\n"
                    )

                original_listdir = memory_selectors.os.listdir

                def guarded_listdir(path):
                    normalized = os.path.normpath(path)
                    if normalized.endswith(os.path.normpath("doc/ui")):
                        raise AssertionError("unrelated root should not be scanned")
                    return original_listdir(path)

                with mock.patch.object(memory_selectors.os, "listdir", side_effect=guarded_listdir):
                    notes = memory_selectors.select_relevant_notes(
                        "fix the handler regression",
                        query_context={
                            "active_roots": ["common", "api"],
                            "scan_roots": ["common", "api"],
                        },
                    )

                self.assertTrue(notes)
                self.assertEqual(notes[0][0], "doc/api/handler.md")
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
                    f.write(
                        "task_id: TASK__active\n"
                        "status: planned\n"
                        "lane: build\n"
                        "verification_targets: [\"doc/api/service.md\"]\n"
                        "touched_paths: []\n"
                    )
                with open("doc/api/handler.md", "w", encoding="utf-8") as f:
                    f.write(
                        "# API handler\n"
                        "summary: API handler regression guidance\n"
                        "freshness: current\n\n"
                        "handler regression fix\n"
                    )
                with open("doc/ui/handler.md", "w", encoding="utf-8") as f:
                    f.write(
                        "# UI handler\n"
                        "summary: UI handler regression guidance\n"
                        "freshness: current\n\n"
                        "handler regression fix\n"
                    )

                active_task = os.path.join(tmp, "doc", "harness", "tasks", "TASK__active")
                with mock.patch.object(
                    prompt_memory,
                    "TASK_DIR",
                    os.path.join(tmp, "doc", "harness", "tasks"),
                ), mock.patch.object(prompt_memory, "_get_active_task_dir", return_value=active_task):
                    parts = prompt_memory.gather_context("fix the handler regression")

                note_parts = [part for part in parts if part.startswith("note:")]
                self.assertTrue(note_parts, parts)
                self.assertIn("[api] API handler regression guidance", note_parts[0])
            finally:
                os.chdir(prev)

    def test_gather_context_only_constrains_scan_roots_when_root_hint_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            prev = os.getcwd()
            os.chdir(tmp)
            try:
                os.makedirs("doc/harness", exist_ok=True)
                with open("doc/harness/manifest.yaml", "w", encoding="utf-8") as f:
                    f.write("registered_roots:\n  - common\n  - api\n  - ui\n")

                captured = {}

                def fake_select(prompt, query_context=None, notes_dir=None):
                    captured["query_context"] = dict(query_context or {})
                    return []

                with mock.patch.object(prompt_memory, "_get_active_task_dir", return_value=None), mock.patch.object(
                    prompt_memory,
                    "select_prompt_notes",
                    side_effect=fake_select,
                ):
                    prompt_memory.gather_context("why does the handler regression happen?")

                self.assertIn("active_roots", captured["query_context"])
                self.assertNotIn("scan_roots", captured["query_context"])
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
                    f.write(
                        "task_id: TASK__active\n"
                        "status: planned\n"
                        "lane: build\n"
                        "verification_targets: []\n"
                        "touched_paths: []\n"
                    )
                with open("doc/common/build-note.md", "w", encoding="utf-8") as f:
                    f.write(
                        "# Build note\n"
                        "summary: build-lane guidance\n"
                        "lane: build\n"
                        "freshness: current\n\n"
                        "handler regression fix\n"
                    )
                with open("doc/common/verify-note.md", "w", encoding="utf-8") as f:
                    f.write(
                        "# Verify note\n"
                        "summary: verify-lane guidance\n"
                        "lane: verify\n"
                        "freshness: current\n\n"
                        "handler regression fix\n"
                    )

                active_task = os.path.join(tmp, "doc", "harness", "tasks", "TASK__active")
                with mock.patch.object(
                    prompt_memory,
                    "TASK_DIR",
                    os.path.join(tmp, "doc", "harness", "tasks"),
                ), mock.patch.object(prompt_memory, "_get_active_task_dir", return_value=active_task):
                    parts = prompt_memory.gather_context("fix the handler regression")

                note_parts = [part for part in parts if part.startswith("note:")]
                self.assertTrue(note_parts, parts)
                self.assertIn("build-lane guidance", note_parts[0])
                self.assertNotIn("verify-lane guidance", note_parts[0])
            finally:
                os.chdir(prev)


class TestPromptNoteOrdering(unittest.TestCase):
    def test_select_prompt_notes_prefers_req_note_when_scores_tie(self):
        with tempfile.TemporaryDirectory() as tmp:
            prev = os.getcwd()
            os.chdir(tmp)
            try:
                os.makedirs("doc/common", exist_ok=True)
                os.makedirs("doc/harness", exist_ok=True)
                with open("doc/harness/manifest.yaml", "w", encoding="utf-8") as f:
                    f.write(
                        "registered_roots:\n"
                        "  - common\n"
                    )

                shared_body = "protected artifact writes should use CLI tool\n"
                with open("doc/common/REQ__policy.md", "w", encoding="utf-8") as f:
                    f.write(
                        "# REQ policy\n"
                        "summary: requirement note\n"
                        "freshness: current\n\n"
                        + shared_body
                    )
                with open("doc/common/OBS__policy.md", "w", encoding="utf-8") as f:
                    f.write(
                        "# OBS policy\n"
                        "summary: observation note\n"
                        "freshness: current\n\n"
                        + shared_body
                    )

                notes = memory_selectors.select_prompt_notes(
                    "protected artifact writes should use CLI tool",
                    query_context={"active_roots": ["common"]},
                )

                self.assertEqual(notes[0][0], "doc/common/REQ__policy.md")
                self.assertEqual(notes[1][0], "doc/common/OBS__policy.md")
            finally:
                os.chdir(prev)


class TestPromptNoteBundle(unittest.TestCase):
    def test_select_prompt_notes_prefers_complementary_second_note(self):
        with tempfile.TemporaryDirectory() as tmp:
            prev = os.getcwd()
            os.chdir(tmp)
            try:
                os.makedirs("doc/common", exist_ok=True)
                os.makedirs("doc/api", exist_ok=True)
                os.makedirs("doc/ui", exist_ok=True)
                os.makedirs("doc/harness", exist_ok=True)
                with open("doc/harness/manifest.yaml", "w", encoding="utf-8") as f:
                    f.write(
                        "registered_roots:\n"
                        "  - common\n"
                        "  - api\n"
                        "  - ui\n"
                    )

                with open("doc/api/login.md", "w", encoding="utf-8") as f:
                    f.write(
                        "# API login\n"
                        "summary: API login contract\n"
                        "freshness: current\n"
                        "path_scope: [src/api/login.py]\n\n"
                        "login validation error fix\n"
                    )
                with open("doc/common/login.md", "w", encoding="utf-8") as f:
                    f.write(
                        "# Common login\n"
                        "summary: shared login rollback guard\n"
                        "freshness: current\n\n"
                        "login validation rollback guard\n"
                    )
                with open("doc/ui/login.md", "w", encoding="utf-8") as f:
                    f.write(
                        "# UI login\n"
                        "summary: ui login styling\n"
                        "freshness: current\n\n"
                        "login button styling only\n"
                    )

                notes = memory_selectors.select_prompt_notes(
                    "fix login validation in src/api/login.py",
                    query_context={
                        "active_roots": ["common", "api"],
                        "current_lane": "build",
                    },
                )

                self.assertEqual(len(notes), 2)
                self.assertEqual(notes[0][0], "doc/api/login.md")
                self.assertEqual(notes[1][0], "doc/common/login.md")
            finally:
                os.chdir(prev)


class TestPromptMemoryNoteInjection(unittest.TestCase):
    def test_gather_context_injects_primary_and_check_note(self):
        with mock.patch.object(
            prompt_memory,
            "_get_active_task_dir",
            return_value=None,
        ), mock.patch.object(
            prompt_memory,
            "_get_task_required_hint",
            return_value="",
        ), mock.patch.object(
            prompt_memory,
            "select_prompt_notes",
            return_value=[
                ("doc/api/login.md", 0.91, "API login contract", "current", "api"),
                ("doc/common/login.md", 0.74, "shared login rollback guard", "current", "common"),
            ],
        ):
            parts = prompt_memory.gather_context("fix login validation")

        note_parts = [part for part in parts if part.startswith("note")]
        self.assertEqual(note_parts[:2], [
            "note:[api] API login contract",
            "note[check]:shared login rollback guard",
        ])


if __name__ == "__main__":
    unittest.main()
