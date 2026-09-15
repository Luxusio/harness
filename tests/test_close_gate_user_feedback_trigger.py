"""Current task flow does not depend on a user-feedback sidecar artifact."""
from __future__ import annotations

from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
DEVELOP_SKILL = REPO / "plugin" / "skills" / "develop" / "SKILL.md"
CLAUDE_RUNTIME = REPO / "plugin" / "CLAUDE.md"
CRITIC_DOCUMENT = REPO / "plugin" / "agents" / "critic-document.md"


def test_develop_skill_phase_86_uses_conversation_requirements():
    text = DEVELOP_SKILL.read_text(encoding="utf-8")
    idx = text.find("Phase 8.6")
    assert idx >= 0, "Phase 8.6 section header missing"
    section = text[idx : idx + 2500]
    assert "conversation" in section.lower()
    assert "USER_FEEDBACK.jsonl" not in section


def test_plugin_claude_md_declares_no_feedback_sidecar():
    text = CLAUDE_RUNTIME.read_text(encoding="utf-8")
    assert "Promote user corrections directly into PLAN.md or durable project" in text
    assert "USER_FEEDBACK.jsonl" not in text


def test_contracts_local_is_not_a_runtime_authority():
    assert not (REPO / "CONTRACTS.local.md").exists()
    assert "CONTRACTS.local.md" not in DEVELOP_SKILL.read_text(encoding="utf-8")


def test_conversation_requirements_remain_owned_by_document_review():
    text = CRITIC_DOCUMENT.read_text(encoding="utf-8")
    assert "Conversation requirement pass" in text
    assert "user requirements supplied in your delegation context" in text
    assert "committed durable surface" in text
    assert "USER_FEEDBACK.jsonl" not in text
