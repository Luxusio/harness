import importlib
import os
import sys
import tempfile
import unittest


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "plugin", "scripts"))


class TestPromoteLearningsFeedbackRules(unittest.TestCase):
    def setUp(self):
        import promote_learnings

        importlib.reload(promote_learnings)
        self.module = promote_learnings

    def test_feedback_rule_renders_readable_prose(self):
        entry = {
            "type": "feedback-rule",
            "key": "runtime-specific-plugin-changes",
            "trigger": "changing runtime-specific harness plugin behavior",
            "action": "review both the canonical `plugin/` tree and the runtime-specific tree such as `plugin-codex/`",
            "verification": "explaining in `HANDOFF.md` which side changed and why any other side was left unchanged",
            "reason": "prevents runtime port drift",
            "insight": '{"trigger":"do not render this raw"}',
        }

        body = self.module._entry_detail(entry, 2)

        self.assertIn("When changing runtime-specific harness plugin behavior", body)
        self.assertIn("Verify by explaining in `HANDOFF.md`", body)
        self.assertIn("Why: prevents runtime port drift.", body)
        self.assertNotIn('{"trigger"', body)
        self.assertNotIn("trigger:", body)

    def test_append_pattern_uses_feedback_rule_renderer(self):
        entry = {
            "type": "feedback-rule",
            "key": "feedback-rule-capture",
            "trigger": "a user correction names a reusable future situation",
            "action": "capture only the conditional behavior rule",
            "verification": "recording status none, captured, or rejected in HANDOFF",
            "insight": "raw fallback should not appear",
        }

        with tempfile.TemporaryDirectory() as td:
            path = self.module._append_pattern(td, "general", entry["key"], entry, 2, False)
            with open(path, encoding="utf-8") as f:
                content = f.read()

        self.assertIn("## feedback-rule-capture", content)
        self.assertIn("When a user correction names a reusable future situation", content)
        self.assertIn("Verify by recording status none, captured, or rejected in HANDOFF.", content)
        self.assertNotIn("raw fallback should not appear", content)


if __name__ == "__main__":
    unittest.main()
