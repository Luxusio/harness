import importlib
import os
import sys
import unittest


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "plugin", "scripts"))


class TestPromoteLearningsFeedbackRules(unittest.TestCase):
    def setUp(self):
        import promote_learnings

        importlib.reload(promote_learnings)
        self.module = promote_learnings

    def test_feedback_rule_fields_use_shared_markdown_validator(self):
        base = {
            "ts": "2026-08-31T12:00:00Z",
            "type": "feedback-rule",
            "key": "feedback-rule-capture",
            "insight": "Capture a reusable correction.",
            "task": "TASK__current",
            "task_run_id": "run-current",
            "trigger": "a user correction recurs",
            "action": "capture the conditional behavior",
            "verification": "check the durable result",
        }
        self.assertTrue(self.module._valid_learning_candidate(base))
        for field in ("trigger", "action", "verification"):
            unsafe = dict(base)
            unsafe[field] = "Unsafe\n## injected"
            self.assertFalse(self.module._valid_learning_candidate(unsafe))

    def test_plain_feedback_does_not_require_rule_fields(self):
        entry = {
            "ts": "2026-08-31T12:00:00Z",
            "type": "feedback",
            "key": "feedback-capture",
            "insight": "Preserve this reusable feedback.",
            "task": "TASK__current",
            "task_run_id": "run-current",
        }
        self.assertTrue(self.module._valid_learning_candidate(entry))


if __name__ == "__main__":
    unittest.main()
