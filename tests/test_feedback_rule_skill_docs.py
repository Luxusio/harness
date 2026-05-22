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
        assert "Self-Healing Candidates" in body
        assert "development, QA, dogfood, and close-gate discoveries" in body
        assert "Status: none | applied | deferred | rejected" in body
        assert "AskUserQuestion" in body
        assert "request_user_input" in body
        assert "user_decision:" in body
        assert "proposed_artifact:" in body


def test_qa_agents_surface_self_healing_candidates_for_handoff():
    for rel in (
        "plugin/agents/qa-cli.md",
        "plugin/agents/qa-api.md",
        "plugin/agents/qa-browser.md",
        "plugin/agents/qa-desktop.md",
        "plugin-codex/agents/qa-cli.md",
        "plugin-codex/agents/qa-api.md",
        "plugin-codex/agents/qa-browser.md",
        "plugin-codex/agents/qa-desktop.md",
    ):
        body = (REPO / rel).read_text(encoding="utf-8")
        assert "Self-Healing Candidates for HANDOFF" in body
        assert "write_critic_qa" in body
        assert "applied" in body
        assert "deferred" in body
        assert "rejected" in body


def test_self_improvement_documents_readable_tier2_format():
    body = (REPO / "plugin/skills/run/self-improvement.md").read_text(encoding="utf-8")

    assert "Feedback-derived rules" in body
    assert "When <trigger>, <action>." in body
    assert "Verify by <observable check>." in body
    assert "judgment, not forced documentation" in body
    assert "Commit-backed promotion checkpoint" in body
    assert "learnings.jsonl` by itself is never enough for `captured`" in body
    assert "committed artifact" in body
