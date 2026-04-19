"""AC-004: plugin/hooks/hooks.json registers both gates with `|| true` preserved.
"""
from __future__ import annotations

import json
import os
import unittest


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOKS = os.path.join(REPO_ROOT, "plugin", "hooks", "hooks.json")


class TestHooksJson(unittest.TestCase):
    def setUp(self):
        with open(HOOKS, encoding="utf-8") as f:
            self.data = json.load(f)

    def test_pretooluse_has_both_gates(self):
        entries = self.data["hooks"]["PreToolUse"]
        commands = []
        for entry in entries:
            for h in entry.get("hooks", []):
                commands.append((entry.get("matcher"), h["command"]))
        prewrite = [c for m, c in commands if "prewrite_gate.py" in c]
        bash_guard = [c for m, c in commands if "mcp_bash_guard.py" in c]
        self.assertEqual(len(prewrite), 1, f"expected one prewrite hook, got {prewrite}")
        self.assertEqual(len(bash_guard), 1, f"expected one bash_guard hook, got {bash_guard}")

    def test_user_prompt_submit_registers_prompt_memory(self):
        """AC-007: UserPromptSubmit entry for prompt_memory with fail-safe."""
        entries = self.data["hooks"].get("UserPromptSubmit", [])
        commands = []
        for entry in entries:
            for h in entry.get("hooks", []):
                commands.append(h["command"])
        pm = [c for c in commands if "prompt_memory.py" in c]
        self.assertEqual(len(pm), 1, f"expected one prompt_memory hook, got {pm}")
        self.assertTrue(pm[0].rstrip().endswith("|| true"),
                        f"prompt_memory must fail-safe with `|| true`: {pm[0]!r}")

    def test_post_tool_use_registers_tool_routing(self):
        """PR4 AC-005: PostToolUse Bash matcher invokes tool_routing with `|| true`."""
        entries = self.data["hooks"].get("PostToolUse", [])
        bash_entries = [e for e in entries if e.get("matcher") == "Bash"]
        self.assertTrue(bash_entries, "no PostToolUse Bash entry")
        commands = []
        for entry in bash_entries:
            for h in entry.get("hooks", []):
                commands.append(h["command"])
        tr = [c for c in commands if "tool_routing.py" in c]
        self.assertEqual(len(tr), 1, f"expected one tool_routing hook, got {tr}")
        self.assertTrue(tr[0].rstrip().endswith("|| true"),
                        f"tool_routing must fail-safe with `|| true`: {tr[0]!r}")

    def test_bash_guard_matcher_is_bash(self):
        entries = self.data["hooks"]["PreToolUse"]
        bash_entries = [e for e in entries
                        if any("mcp_bash_guard.py" in h["command"] for h in e.get("hooks", []))]
        self.assertEqual(len(bash_entries), 1)
        self.assertEqual(bash_entries[0].get("matcher"), "Bash")

    def test_both_gate_commands_have_fail_safe(self):
        """C-12: every hook must end with `|| true` (fail-safe)."""
        entries = self.data["hooks"]["PreToolUse"]
        for entry in entries:
            for h in entry.get("hooks", []):
                cmd = h["command"]
                # Only gate scripts (prewrite, bash_guard) need fail-safe.
                if "prewrite_gate.py" in cmd or "mcp_bash_guard.py" in cmd:
                    self.assertTrue(cmd.rstrip().endswith("|| true"),
                                    f"missing `|| true`: {cmd!r}")

    def test_timeouts_are_bounded(self):
        """C-12 convention: every hook timeout ≤ 10s."""
        for event, entries in self.data["hooks"].items():
            for entry in entries:
                for h in entry.get("hooks", []):
                    timeout = h.get("timeout", 5)
                    self.assertLessEqual(timeout, 10,
                                         f"{event} timeout > 10s: {h}")


if __name__ == "__main__":
    unittest.main()
