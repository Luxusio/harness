"""Tests for hook payload parsing correctness — Phase 0 §5.1.

Covers:
  - TaskCompleted reads task_id from JSON stdin correctly
  - SubagentStop reads task_id + agent_name correctly
  - env fallback (HARNESS_TASK_ID, CLAUDE_AGENT_NAME) is preserved
  - hook_json_get(input_str, field) wrapper has unambiguous arg order

Run with: python -m unittest discover -s tests -p 'test_*.py'
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "scripts"))
os.environ["HARNESS_SKIP_STDIN"] = "1"

from _lib import json_field, hook_json_get


# ---------------------------------------------------------------------------
# json_field arg-order contract
# ---------------------------------------------------------------------------

class TestJsonFieldArgOrder(unittest.TestCase):
    """json_field(field, input_str) — field first, input_str second."""

    def test_correct_order_returns_value(self):
        payload = '{"task_id": "TASK__foo"}'
        self.assertEqual(json_field("task_id", payload), "TASK__foo")

    def test_swapped_order_returns_empty(self):
        """The existing bug: json_field(data, "field") swaps args.
        json_field treats the JSON string as the field name and "task_id" as
        the input to parse — json.loads("task_id") fails, regex also fails → ''.
        This test documents the bug so WS-1 can confirm the fix.
        """
        payload = '{"task_id": "TASK__foo"}'
        result = json_field(payload, "task_id")
        self.assertEqual(result, "",
            "Swapped args must return empty — confirms the pre-WS-1 bug")

    def test_multiple_fields(self):
        payload = '{"agent_name": "developer", "task_id": "TASK__bar"}'
        self.assertEqual(json_field("agent_name", payload), "developer")
        self.assertEqual(json_field("task_id", payload), "TASK__bar")

    def test_missing_field_returns_empty(self):
        payload = '{"other": "value"}'
        self.assertEqual(json_field("task_id", payload), "")

    def test_empty_input_returns_empty(self):
        self.assertEqual(json_field("task_id", ""), "")

    def test_invalid_json_regex_fallback(self):
        # Partial JSON that the regex can match
        payload = '"task_id": "TASK__regex"'
        result = json_field("task_id", payload)
        self.assertEqual(result, "TASK__regex")


# ---------------------------------------------------------------------------
# hook_json_get wrapper (added in WS-1)
# ---------------------------------------------------------------------------

class TestHookJsonGet(unittest.TestCase):
    """hook_json_get(input_str, field) — explicit semantics, input first."""

    def test_basic_task_id(self):
        payload = '{"task_id": "TASK__baz"}'
        self.assertEqual(hook_json_get(payload, "task_id"), "TASK__baz")

    def test_agent_name(self):
        payload = '{"agent_name": "critic-runtime", "task_id": "TASK__qux"}'
        self.assertEqual(hook_json_get(payload, "agent_name"), "critic-runtime")
        self.assertEqual(hook_json_get(payload, "task_id"), "TASK__qux")

    def test_empty_input_returns_empty(self):
        self.assertEqual(hook_json_get("", "task_id"), "")

    def test_missing_field_returns_empty(self):
        self.assertEqual(hook_json_get('{"x": "y"}', "task_id"), "")

    def test_all_agent_names(self):
        for name in ("developer", "writer", "critic-plan", "critic-runtime", "critic-document"):
            payload = f'{{"agent_name": "{name}", "task_id": "TASK__t"}}'
            self.assertEqual(hook_json_get(payload, "agent_name"), name)

    def test_does_not_raise_on_garbage_input(self):
        """Must never raise — any input returns str."""
        for bad in ("", "null", "[]", "{}", "not json at all", None):
            try:
                result = hook_json_get(bad or "", "task_id")
                self.assertIsInstance(result, str)
            except Exception as e:
                self.fail(f"hook_json_get raised {e} on input {bad!r}")


# ---------------------------------------------------------------------------
# Environment fallback
# ---------------------------------------------------------------------------

class TestEnvFallback(unittest.TestCase):
    """HARNESS_TASK_ID and CLAUDE_AGENT_NAME env vars provide fallback."""

    def setUp(self):
        self._orig_task_id = os.environ.get("HARNESS_TASK_ID")
        self._orig_agent = os.environ.get("CLAUDE_AGENT_NAME")

    def tearDown(self):
        if self._orig_task_id is None:
            os.environ.pop("HARNESS_TASK_ID", None)
        else:
            os.environ["HARNESS_TASK_ID"] = self._orig_task_id
        if self._orig_agent is None:
            os.environ.pop("CLAUDE_AGENT_NAME", None)
        else:
            os.environ["CLAUDE_AGENT_NAME"] = self._orig_agent

    def test_task_id_env_fallback(self):
        """hook_json_get returns '' for missing field; caller uses env as fallback."""
        os.environ["HARNESS_TASK_ID"] = "TASK__env_fallback"
        empty_json = '{"other": "value"}'
        task_id = hook_json_get(empty_json, "task_id") or os.environ.get("HARNESS_TASK_ID", "")
        self.assertEqual(task_id, "TASK__env_fallback")

    def test_agent_name_env_fallback(self):
        """CLAUDE_AGENT_NAME used when JSON has no agent_name."""
        os.environ["CLAUDE_AGENT_NAME"] = "critic-plan"
        empty_json = '{"task_id": "TASK__foo"}'
        agent = (hook_json_get(empty_json, "agent_name")
                 or hook_json_get(empty_json, "agent")
                 or os.environ.get("CLAUDE_AGENT_NAME", "unknown"))
        self.assertEqual(agent, "critic-plan")

    def test_json_value_takes_priority_over_env(self):
        """JSON value takes priority over env fallback."""
        os.environ["HARNESS_TASK_ID"] = "TASK__env_value"
        payload = '{"task_id": "TASK__json_value"}'
        task_id = hook_json_get(payload, "task_id") or os.environ.get("HARNESS_TASK_ID", "")
        self.assertEqual(task_id, "TASK__json_value")


if __name__ == "__main__":
    unittest.main()
