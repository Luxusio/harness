"""Tests for capability_probe.py — delegation capability detection.

Covers:
  - Manifest explicit override (available/unavailable)
  - Agent name presence signal
  - CI environment signals → unavailable
  - Default → unknown

Run with: python -m unittest discover -s tests -p 'test_*.py'
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "scripts"))
os.environ["HARNESS_SKIP_STDIN"] = "1"

from capability_probe import probe_delegation_capability, update_task_capability
from _lib import yaml_field
import _lib


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


class TestProbeCapability(unittest.TestCase):

    def setUp(self):
        self._orig_env = {}
        for key in ("CLAUDE_AGENT_NAME", "CI", "GITHUB_ACTIONS", "GITLAB_CI",
                     "JENKINS_URL", "CIRCLECI", "BUILDKITE", "CONTINUOUS_INTEGRATION"):
            self._orig_env[key] = os.environ.pop(key, None)
        self._orig_manifest = _lib.MANIFEST

    def tearDown(self):
        for key, val in self._orig_env.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val
        _lib.MANIFEST = self._orig_manifest

    def test_agent_name_with_colon_returns_available(self):
        """Running as harness:developer → available."""
        os.environ["CLAUDE_AGENT_NAME"] = "harness:developer"
        result = probe_delegation_capability()
        self.assertEqual(result, "available")

    def test_ci_environment_returns_unavailable(self):
        """CI=true → unavailable."""
        os.environ["CI"] = "true"
        result = probe_delegation_capability()
        self.assertEqual(result, "unavailable")

    def test_github_actions_returns_unavailable(self):
        """GITHUB_ACTIONS=true → unavailable."""
        os.environ["GITHUB_ACTIONS"] = "true"
        result = probe_delegation_capability()
        self.assertEqual(result, "unavailable")

    def test_no_signals_returns_unknown(self):
        """No signals → unknown."""
        result = probe_delegation_capability()
        self.assertEqual(result, "unknown")

    def test_manifest_override_available(self):
        """manifest capabilities.delegation_mode=available overrides everything."""
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        tmp.write("capabilities:\n  delegation_mode: available\n")
        tmp.close()
        _lib.MANIFEST = tmp.name
        try:
            result = probe_delegation_capability()
            self.assertEqual(result, "available")
        finally:
            os.unlink(tmp.name)

    def test_manifest_override_unavailable(self):
        """manifest capabilities.delegation_mode=unavailable overrides agent name."""
        os.environ["CLAUDE_AGENT_NAME"] = "harness:developer"
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        tmp.write("capabilities:\n  delegation_mode: unavailable\n")
        tmp.close()
        _lib.MANIFEST = tmp.name
        try:
            result = probe_delegation_capability()
            self.assertEqual(result, "unavailable")
        finally:
            os.unlink(tmp.name)


class TestUpdateTaskCapability(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.task_dir = os.path.join(self.tmp.name, "TASK__test")
        os.makedirs(self.task_dir)

    def tearDown(self):
        self.tmp.cleanup()

    def test_updates_capability_field(self):
        _write(os.path.join(self.task_dir, "TASK_STATE.yaml"),
               "task_id: TASK__test\ncapability_delegation: unknown\n")
        update_task_capability(self.task_dir, "available")
        state_file = os.path.join(self.task_dir, "TASK_STATE.yaml")
        val = yaml_field("capability_delegation", state_file)
        self.assertEqual(val, "available")

    def test_adds_field_if_missing(self):
        _write(os.path.join(self.task_dir, "TASK_STATE.yaml"),
               "task_id: TASK__test\nstatus: created\n")
        update_task_capability(self.task_dir, "unavailable")
        state_file = os.path.join(self.task_dir, "TASK_STATE.yaml")
        val = yaml_field("capability_delegation", state_file)
        self.assertEqual(val, "unavailable")

    def test_missing_state_file_no_crash(self):
        """Missing TASK_STATE.yaml does not crash."""
        empty_dir = os.path.join(self.tmp.name, "empty")
        os.makedirs(empty_dir)
        result = update_task_capability(empty_dir, "available")
        self.assertEqual(result, "available")


if __name__ == "__main__":
    unittest.main()
