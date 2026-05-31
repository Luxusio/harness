"""AC-001 / AC-002: critic-document.md gains write_req_doc carve-out + Retrospective REQ pass section.

P5 convention: stdlib + project-local only at module top level.
"""
from __future__ import annotations

from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
AGENT = REPO / "plugin" / "agents" / "critic-document.md"


def _read_agent() -> str:
    return AGENT.read_text(encoding="utf-8")


def test_critic_document_tools_frontmatter_includes_write_req_doc():
    text = _read_agent()
    head = text.split("---", 2)[1] if text.startswith("---") else text[:400]
    assert "mcp__plugin_harness_harness__write_req_doc" in head, (
        "critic-document.md tools: frontmatter must list write_req_doc since the "
        "Retrospective REQ pass calls it"
    )


def test_critic_document_invariant_has_write_req_doc_carveout():
    text = _read_agent()
    invariant_idx = text.find("Do not edit documentation yourself")
    assert invariant_idx >= 0, "expected 'Do not edit documentation yourself' invariant"
    after = text[invariant_idx : invariant_idx + 800]
    assert "write_req_doc" in after, (
        "Invariant paragraph must reference write_req_doc as the carve-out path "
        "(critic-document calls it during the Retrospective REQ pass)"
    )
    assert "candidate" in after.lower(), (
        "Invariant paragraph must constrain the carve-out to status:candidate writes"
    )


def test_critic_document_retrospective_req_pass_section_exists():
    text = _read_agent()
    assert "Retrospective REQ pass" in text, (
        "AC-002: critic-document.md must include a section titled 'Retrospective REQ pass'"
    )


def test_critic_document_retrospective_references_user_feedback_jsonl():
    text = _read_agent()
    rsec_idx = text.find("Retrospective REQ pass")
    assert rsec_idx >= 0
    section = text[rsec_idx : rsec_idx + 2000]
    assert "USER_FEEDBACK.jsonl" in section, (
        "Retrospective REQ pass must direct the agent to USER_FEEDBACK.jsonl"
    )
    assert "write_req_doc" in section, "Retrospective section must reference write_req_doc"
    assert "status" in section and "candidate" in section, (
        "Retrospective section must specify status: candidate for written REQs"
    )
