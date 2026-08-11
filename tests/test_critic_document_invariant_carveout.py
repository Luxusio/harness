"""critic-document.md does not expose removed MCP evidence/doc writers.

P5 convention: stdlib + project-local only at module top level.
"""
from __future__ import annotations

from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
AGENT = REPO / "plugin" / "agents" / "critic-document.md"


def _read_agent() -> str:
    return AGENT.read_text(encoding="utf-8")


def test_critic_document_tools_frontmatter_has_no_removed_mcp_writers():
    text = _read_agent()
    head = text.split("---", 2)[1] if text.startswith("---") else text[:400]
    assert "mcp__plugin_harness_harness__write_req_doc" not in head
    assert "mcp__plugin_harness_harness__write_handoff" not in head
    assert "mcp__plugin_harness_harness__write_doc_sync" not in head


def test_critic_document_invariant_has_no_mcp_write_carveout():
    text = _read_agent()
    invariant_idx = text.find("Do not edit documentation yourself")
    assert invariant_idx >= 0, "expected 'Do not edit documentation yourself' invariant"
    after = text[invariant_idx : invariant_idx + 800]
    assert "write_req_doc" not in after
    assert "mcp__plugin_harness_harness__" not in after


def test_critic_document_conversation_requirement_pass_section_exists():
    text = _read_agent()
    assert "Conversation requirement pass" in text, (
        "critic-document.md must include a conversation requirement pass"
    )


def test_critic_document_conversation_pass_uses_delegated_context_without_sidecar():
    text = _read_agent()
    rsec_idx = text.find("Conversation requirement pass")
    assert rsec_idx >= 0
    section = text[rsec_idx : rsec_idx + 1200]
    assert "delegation context" in section
    assert "separate prompt-capture artifact" in section
    assert "write_req_doc" not in section
