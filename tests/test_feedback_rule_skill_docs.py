from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def test_develop_skills_require_feedback_rule_judgment():
    for rel in ("plugin/skills/develop/SKILL.md", "plugin-codex/skills/develop/SKILL.md"):
        body = (REPO / rel).read_text(encoding="utf-8")
        assert "Feedback-Derived Rules" in body
        assert "none" in body
        assert "captured" in body
        assert "rejected" in body
        assert "When X, do Y. Verify by Z." in body
        assert "Write behavior rules for Tier 2 docs" in body
        assert "incident-shaped lessons" in body
        assert "Commit-backed Learnings" in body
        assert "learnings.jsonl` is gitignored staging" in body
        assert "committed artifact" in body
        assert "changed a committed" in body


def test_self_improvement_documents_readable_tier2_format():
    body = (REPO / "plugin/skills/run/self-improvement.md").read_text(encoding="utf-8")

    assert "Feedback-derived rules" in body
    assert "When <trigger>, <action>." in body
    assert "Verify by <observable check>." in body
    assert "judgment, not forced documentation" in body
    assert "Commit-backed promotion checkpoint" in body
    assert "learnings.jsonl` by itself is never enough for `captured`" in body
    assert "committed artifact" in body
