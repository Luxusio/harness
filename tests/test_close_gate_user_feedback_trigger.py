"""Current task flow does not depend on a user-feedback sidecar artifact."""
from __future__ import annotations

from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
DEVELOP_SKILL = REPO / "plugin" / "skills" / "develop" / "SKILL.md"
CLAUDE_RUNTIME = REPO / "plugin" / "CLAUDE.md"
CONTRACTS_LOCAL = REPO / "CONTRACTS.local.md"


def test_develop_skill_phase_86_uses_conversation_requirements():
    text = DEVELOP_SKILL.read_text(encoding="utf-8")
    idx = text.find("Phase 8.6")
    assert idx >= 0, "Phase 8.6 section header missing"
    section = text[idx : idx + 2500]
    assert "conversation" in section.lower()
    assert "USER_FEEDBACK.jsonl" not in section


def test_plugin_claude_md_declares_no_feedback_sidecar():
    text = CLAUDE_RUNTIME.read_text(encoding="utf-8")
    assert "does not create a separate user-feedback artifact" in text


def test_contracts_local_c101_present_with_four_fields():
    text = CONTRACTS_LOCAL.read_text(encoding="utf-8")
    idx = text.find("### C-101")
    assert idx >= 0, "C-101 heading missing from CONTRACTS.local.md"
    section = text[idx : idx + 2500]
    for field in ("**Title:**", "**When:**", "**Enforced by:**", "**On violation:**", "**Why:**"):
        assert field in section, f"C-101 missing required field {field}"
    # Substantive content: each Why-line should be >= 80 chars to satisfy CONTRACTS quality bar
    # Extract the line content following each label and check length.
    for field in ("**Title:**", "**Why:**"):
        line_start = section.find(field) + len(field)
        line_end = section.find("\n", line_start)
        body_text = section[line_start:line_end].strip()
        assert len(body_text) >= 20, f"C-101 {field} content too short: {body_text!r}"


def test_contracts_local_c101_uses_conversation_and_critic_document():
    text = CONTRACTS_LOCAL.read_text(encoding="utf-8")
    c101 = text[text.find("### C-101"):]
    assert "current conversation" in c101
    assert "USER_FEEDBACK.jsonl" not in c101
    assert "critic-document" in c101, "C-101 should name critic-document as the enforcer"
