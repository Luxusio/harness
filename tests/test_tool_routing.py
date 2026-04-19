"""AC-003 + AC-004: tool_routing.py PostToolUse hint on known bash failures.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
ROUTING = REPO_ROOT / "plugin" / "scripts" / "tool_routing.py"


def _invoke(payload: dict, *, cwd: str | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_ROOT"] = str(REPO_ROOT / "plugin")
    return subprocess.run(
        [sys.executable, str(ROUTING)],
        input=json.dumps(payload),
        capture_output=True, text=True,
        cwd=cwd or str(REPO_ROOT),
        env=env, timeout=5,
    )


def _mk_manifest(base: Path, test_cmd: str) -> None:
    doc_h = base / "doc" / "harness"
    doc_h.mkdir(parents=True, exist_ok=True)
    (doc_h / "manifest.yaml").write_text(
        f'test_command: "{test_cmd}"\n', encoding="utf-8")


class TestToolRoutingHints(unittest.TestCase):

    def test_command_not_found_pytest_suggests_test_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / ".git").mkdir()
            _mk_manifest(base, "python3 -m pytest")
            r = _invoke({
                "tool_name": "Bash",
                "tool_response": {"stderr": "bash: pytest: command not found"},
            }, cwd=str(base))
        self.assertEqual(r.returncode, 0)
        self.assertIn("[harness-hint]", r.stdout)
        self.assertIn("pytest", r.stdout)
        self.assertIn("python3 -m pytest", r.stdout)

    def test_command_not_found_npm_suggests_test_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / ".git").mkdir()
            _mk_manifest(base, "bun test")
            r = _invoke({
                "tool_name": "Bash",
                "tool_response": {"stderr": "bash: npm: command not found"},
            }, cwd=str(base))
        self.assertIn("[harness-hint]", r.stdout)
        self.assertIn("bun test", r.stdout)

    def test_no_such_file_on_plugin_scripts_suggests_neighbor(self):
        r = _invoke({
            "tool_name": "Bash",
            "tool_response": {
                "stderr": "python3: can't open file '/project/x/plugin/scripts/notthere.py': "
                          "[Errno 2] No such file or directory: 'plugin/scripts/notthere.py'",
            },
        })
        self.assertIn("[harness-hint]", r.stdout)
        # Suggests existing real neighbors. Prefix-match ranks `note_freshness.py`
        # first (shares "not" prefix with "notthere.py"); others appear alphabetical.
        self.assertIn(".py", r.stdout)
        self.assertTrue(
            any(s in r.stdout for s in (
                "note_freshness.py", "prompt_memory.py", "prewrite_gate.py",
                "golden_replay.py", "_lib.py", "audit.py",
            )),
            f"no neighbor suggested: {r.stdout!r}",
        )

    def test_silent_on_non_matching_output(self):
        r = _invoke({
            "tool_name": "Bash",
            "tool_response": {"stdout": "all good", "stderr": ""},
        })
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")

    def test_silent_on_non_bash_tool(self):
        r = _invoke({
            "tool_name": "Write",
            "tool_response": {"stderr": "command not found: pytest"},
        })
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")

    def test_silent_on_malformed_stdin(self):
        env = os.environ.copy()
        env["CLAUDE_PLUGIN_ROOT"] = str(REPO_ROOT / "plugin")
        r = subprocess.run(
            [sys.executable, str(ROUTING)],
            input="not json {{{",
            capture_output=True, text=True, cwd=str(REPO_ROOT), env=env, timeout=5,
        )
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")


if __name__ == "__main__":
    unittest.main()
