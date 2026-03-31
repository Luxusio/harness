"""Tests for WS-3: Observability activation policy.

Covers:
  - observability_ready=false → always inactive
  - ready=true + performance overlay → active
  - ready=true + runtime_fail_count=2 → active
  - ready=true + intermittent/latency context → active
  - library/cli kind → inactive
  - ready=true but normal small bugfix context → inactive
  - Various project kinds (web, api, fullstack, worker)
  - Edge cases: None values, empty strings

Run with: python -m unittest discover -s tests -p 'test_*.py'
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "scripts"))
os.environ["HARNESS_SKIP_STDIN"] = "1"

from _lib import should_activate_observability


class TestShouldActivateObservability(unittest.TestCase):
    """Test the pure policy function directly."""

    # --- Readiness gate ---

    def test_not_ready_always_inactive(self):
        """observability_ready=false → always inactive regardless of signals."""
        active, reason = should_activate_observability(
            manifest_ready=False,
            project_kind="fullstack_web",
            review_overlays=["performance"],
            runtime_fail_count=5,
            context_text="intermittent flaky error",
        )
        self.assertFalse(active)
        self.assertIn("observability_ready", reason)

    # --- Project kind gate ---

    def test_library_kind_inactive(self):
        """library kind → inactive even with all signals."""
        active, reason = should_activate_observability(
            manifest_ready=True,
            project_kind="library",
            review_overlays=["performance"],
            runtime_fail_count=3,
        )
        self.assertFalse(active)
        self.assertIn("not suitable", reason)

    def test_cli_kind_inactive(self):
        """cli kind → inactive."""
        active, reason = should_activate_observability(
            manifest_ready=True,
            project_kind="cli",
            review_overlays=["performance"],
            runtime_fail_count=3,
        )
        self.assertFalse(active)
        self.assertIn("not suitable", reason)

    # --- Suitable kinds ---

    def test_web_kind_with_signal_active(self):
        active, _ = should_activate_observability(
            manifest_ready=True,
            project_kind="web",
            review_overlays=["performance"],
            runtime_fail_count=0,
        )
        self.assertTrue(active)

    def test_api_kind_with_signal_active(self):
        active, _ = should_activate_observability(
            manifest_ready=True,
            project_kind="api",
            review_overlays=["performance"],
            runtime_fail_count=0,
        )
        self.assertTrue(active)

    def test_fullstack_kind_with_signal_active(self):
        active, _ = should_activate_observability(
            manifest_ready=True,
            project_kind="fullstack",
            review_overlays=["performance"],
            runtime_fail_count=0,
        )
        self.assertTrue(active)

    def test_fullstack_web_kind_with_signal_active(self):
        active, _ = should_activate_observability(
            manifest_ready=True,
            project_kind="fullstack_web",
            review_overlays=["performance"],
            runtime_fail_count=0,
        )
        self.assertTrue(active)

    def test_worker_kind_with_signal_active(self):
        active, _ = should_activate_observability(
            manifest_ready=True,
            project_kind="worker",
            review_overlays=["performance"],
            runtime_fail_count=0,
        )
        self.assertTrue(active)

    def test_web_frontend_kind_with_signal_active(self):
        active, _ = should_activate_observability(
            manifest_ready=True,
            project_kind="web-frontend",
            review_overlays=["performance"],
            runtime_fail_count=0,
        )
        self.assertTrue(active)

    # --- Performance overlay signal ---

    def test_performance_overlay_triggers_activation(self):
        active, reason = should_activate_observability(
            manifest_ready=True,
            project_kind="fullstack_web",
            review_overlays=["performance"],
            runtime_fail_count=0,
        )
        self.assertTrue(active)
        self.assertIn("performance overlay", reason)

    def test_non_performance_overlay_no_activation(self):
        """security overlay alone doesn't trigger observability."""
        active, reason = should_activate_observability(
            manifest_ready=True,
            project_kind="fullstack_web",
            review_overlays=["security"],
            runtime_fail_count=0,
        )
        self.assertFalse(active)
        self.assertIn("no activation signal", reason)

    # --- Runtime fail count signal ---

    def test_fail_count_2_triggers_activation(self):
        active, reason = should_activate_observability(
            manifest_ready=True,
            project_kind="api",
            review_overlays=[],
            runtime_fail_count=2,
        )
        self.assertTrue(active)
        self.assertIn("runtime_verdict_fail_count=2", reason)

    def test_fail_count_1_no_activation(self):
        active, _ = should_activate_observability(
            manifest_ready=True,
            project_kind="api",
            review_overlays=[],
            runtime_fail_count=1,
        )
        self.assertFalse(active)

    def test_fail_count_0_no_activation(self):
        active, _ = should_activate_observability(
            manifest_ready=True,
            project_kind="api",
            review_overlays=[],
            runtime_fail_count=0,
        )
        self.assertFalse(active)

    # --- Context keyword signals ---

    def test_intermittent_keyword_triggers(self):
        active, reason = should_activate_observability(
            manifest_ready=True,
            project_kind="web",
            review_overlays=[],
            runtime_fail_count=0,
            context_text="There is an intermittent failure in the API",
        )
        self.assertTrue(active)
        self.assertIn("intermittent", reason)

    def test_flaky_keyword_triggers(self):
        active, reason = should_activate_observability(
            manifest_ready=True,
            project_kind="web",
            review_overlays=[],
            runtime_fail_count=0,
            context_text="flaky test in CI",
        )
        self.assertTrue(active)
        self.assertIn("flaky", reason)

    def test_latency_spike_keyword_triggers(self):
        active, reason = should_activate_observability(
            manifest_ready=True,
            project_kind="api",
            review_overlays=[],
            runtime_fail_count=0,
            context_text="users report latency spike during peak hours",
        )
        self.assertTrue(active)
        self.assertIn("latency spike", reason)

    def test_p99_keyword_triggers(self):
        active, reason = should_activate_observability(
            manifest_ready=True,
            project_kind="api",
            review_overlays=[],
            runtime_fail_count=0,
            context_text="p99 response time increased to 2s",
        )
        self.assertTrue(active)
        self.assertIn("p99", reason)

    def test_cross_service_keyword_triggers(self):
        active, reason = should_activate_observability(
            manifest_ready=True,
            project_kind="fullstack",
            review_overlays=[],
            runtime_fail_count=0,
            context_text="cross-service timeout issue",
        )
        self.assertTrue(active)
        self.assertIn("cross-service", reason)

    def test_normal_bugfix_no_activation(self):
        """Normal small bugfix context → inactive (no investigation keywords)."""
        active, reason = should_activate_observability(
            manifest_ready=True,
            project_kind="web",
            review_overlays=[],
            runtime_fail_count=0,
            context_text="Fix the login button color to be blue",
        )
        self.assertFalse(active)
        self.assertIn("no activation signal", reason)

    def test_empty_context_no_activation(self):
        active, _ = should_activate_observability(
            manifest_ready=True,
            project_kind="web",
            review_overlays=[],
            runtime_fail_count=0,
            context_text="",
        )
        self.assertFalse(active)

    # --- Edge cases ---

    def test_none_values_safe(self):
        """None values for optional params don't crash."""
        active, _ = should_activate_observability(
            manifest_ready=True,
            project_kind="web",
            review_overlays=None,
            runtime_fail_count=None,
            context_text=None,
        )
        self.assertFalse(active)

    def test_none_project_kind(self):
        active, _ = should_activate_observability(
            manifest_ready=True,
            project_kind=None,
            review_overlays=["performance"],
            runtime_fail_count=0,
        )
        self.assertFalse(active)

    def test_multiple_signals_combined(self):
        """Multiple signals → active with combined reason."""
        active, reason = should_activate_observability(
            manifest_ready=True,
            project_kind="api",
            review_overlays=["performance"],
            runtime_fail_count=3,
            context_text="intermittent latency spike",
        )
        self.assertTrue(active)
        # Should mention at least the first two signals
        self.assertIn("performance overlay", reason)
        self.assertIn("runtime_verdict_fail_count=3", reason)


class TestShouldActivateObservabilityOverlayNone(unittest.TestCase):
    """Edge case: review_overlays=None should not crash."""

    def test_none_overlays_handled(self):
        active, _ = should_activate_observability(
            manifest_ready=True,
            project_kind="api",
            review_overlays=None,
            runtime_fail_count=2,
        )
        # Should still activate due to fail_count
        self.assertTrue(active)


if __name__ == "__main__":
    unittest.main()
