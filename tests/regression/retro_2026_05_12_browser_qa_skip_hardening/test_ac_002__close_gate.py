"""AC-002 — emit_compact_context appends qa-browser evidence to missing_for_close.

Scenarios:
  1. browser_qa_supported=true + frontend in touched + no qa-browser section → blocked.
  2. browser_qa_supported=true + no frontend in touched → silent.
  3. browser_qa_supported=false (or absent) + frontend in touched → silent (opt-out).
  4. browser_qa_supported=true + frontend in touched + qa-browser section present → silent.
  5. browser_qa_supported=true + frontend in touched + no CRITIC at all → silent
     (runtime_verdict PASS separately gates this; we don't double-gate).
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "plugin" / "scripts"))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


lib = _load("_lib_ac002", REPO / "plugin" / "scripts" / "_lib.py")


def _write(p, body):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(body)


def _state_yaml(touched):
    paths_yaml = "\n".join(f"  - {p}" for p in touched) if touched else " []"
    if touched:
        return (
            "task_id: TASK__demo\n"
            "status: implementing\n"
            "runtime_verdict: PASS\n"
            "touched_paths:\n"
            + paths_yaml + "\n"
            "plan_session_state: closed\n"
            "closed_at: null\n"
            "updated: 2026-05-12T00:00:00Z\n"
        )
    return (
        "task_id: TASK__demo\n"
        "status: implementing\n"
        "runtime_verdict: PASS\n"
        "touched_paths: []\n"
        "plan_session_state: closed\n"
        "closed_at: null\n"
        "updated: 2026-05-12T00:00:00Z\n"
    )


class TestCloseGateBrowserQA(unittest.TestCase):
    def setUp(self):
        self.td_obj = tempfile.TemporaryDirectory()
        self.repo = self.td_obj.name
        self.task_dir = os.path.join(self.repo, "doc", "harness", "tasks", "TASK__demo")
        os.makedirs(self.task_dir, exist_ok=True)
        # PLAN.md + HANDOFF.md so the other gates pass
        _write(os.path.join(self.task_dir, "PLAN.md"), "# PLAN")
        _write(os.path.join(self.task_dir, "HANDOFF.md"), "# HANDOFF")

    def tearDown(self):
        self.td_obj.cleanup()

    def _set_manifest(self, browser_supported: bool):
        _write(os.path.join(self.repo, "doc", "harness", "manifest.yaml"),
               "name: demo\n"
               "type: library\n"
               "qa:\n"
               f"  browser_qa_supported: {str(browser_supported).lower()}\n")

    def _state(self, touched):
        _write(os.path.join(self.task_dir, "TASK_STATE.yaml"), _state_yaml(touched))

    def _critic(self, body):
        _write(os.path.join(self.task_dir, "CRITIC__qa.md"), body)

    def _run(self):
        with mock.patch.object(lib, "find_repo_root", return_value=self.repo):
            return lib.emit_compact_context(self.task_dir)

    def test_blocks_when_browser_supported_frontend_diff_no_qa_browser_section(self):
        self._set_manifest(True)
        self._state(["src/app.tsx"])
        self._critic("# CRITIC — qa\n\n## qa-api verdict: PASS\n")
        ctx = self._run()
        self.assertIn("qa-browser evidence in CRITIC__qa.md", ctx["missing_for_close"])

    def test_passes_when_browser_supported_no_frontend_diff(self):
        self._set_manifest(True)
        self._state(["plugin/scripts/foo.py"])
        self._critic("# CRITIC — qa\n\n## qa-api verdict: PASS\n")
        ctx = self._run()
        self.assertNotIn("qa-browser evidence in CRITIC__qa.md", ctx["missing_for_close"])

    def test_passes_when_browser_not_supported(self):
        self._set_manifest(False)
        self._state(["src/app.tsx"])
        self._critic("# CRITIC — qa\n\n## qa-api verdict: PASS\n")
        ctx = self._run()
        self.assertNotIn("qa-browser evidence in CRITIC__qa.md", ctx["missing_for_close"])

    def test_passes_when_qa_browser_section_present(self):
        self._set_manifest(True)
        self._state(["src/app.tsx"])
        self._critic("# CRITIC — qa\n\n## qa-api verdict: PASS\n## qa-browser verdict: PASS\n")
        ctx = self._run()
        self.assertNotIn("qa-browser evidence in CRITIC__qa.md", ctx["missing_for_close"])

    def test_silent_when_critic_absent(self):
        # No CRITIC__qa.md at all → runtime_verdict gate handles this;
        # AC-002 stays quiet to avoid double-gating the same condition.
        self._set_manifest(True)
        self._state(["src/app.tsx"])
        # do NOT write CRITIC__qa.md
        ctx = self._run()
        self.assertNotIn("qa-browser evidence in CRITIC__qa.md", ctx["missing_for_close"])


class TestHelpers(unittest.TestCase):
    def test_frontend_touched_extensions(self):
        for p in ("a.tsx", "a.jsx", "a.vue", "a.svelte", "a.html", "a.css", "a.scss"):
            self.assertTrue(lib._frontend_touched([p]), f"{p} should match")

    def test_frontend_touched_path_fragments(self):
        for p in ("src/components/Foo.ts", "app/pages/index.ts",
                  "lib/views/main.go", "service/routes/api.py"):
            self.assertTrue(lib._frontend_touched([p]), f"{p} should match")

    def test_frontend_touched_negative(self):
        for p in ("plugin/scripts/foo.py", "docs/README.md", "build.gradle"):
            self.assertFalse(lib._frontend_touched([p]), f"{p} should NOT match")

    def test_has_qa_browser_section_anchored(self):
        with tempfile.TemporaryDirectory() as td:
            _write(os.path.join(td, "CRITIC__qa.md"),
                   "# CRITIC\n\nnote about qa-browser flow\n## qa-api verdict: PASS\n")
            self.assertFalse(lib._has_qa_browser_section(td))
            _write(os.path.join(td, "CRITIC__qa.md"),
                   "# CRITIC\n\n## qa-browser verdict: PASS\n")
            self.assertTrue(lib._has_qa_browser_section(td))


if __name__ == "__main__":
    unittest.main()
