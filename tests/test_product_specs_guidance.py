from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
BOOTSTRAP = REPO / "plugin" / "skills" / "setup" / "bootstrap.md"
PLAN_WRITE = REPO / "plugin" / "skills" / "plan" / "write-artifacts.md"
CODEX_PLAN = REPO / "plugin-codex" / "skills" / "plan" / "SKILL.md"
CLAUDE_DEVELOP = REPO / "plugin" / "skills" / "develop" / "SKILL.md"
CODEX_DEVELOP = REPO / "plugin-codex" / "skills" / "develop" / "SKILL.md"
QA_BROWSER = REPO / "plugin" / "agents" / "qa-browser.md"
QA_API = REPO / "plugin" / "agents" / "qa-api.md"
PRODUCT_README = REPO / "doc" / "product" / "README.md"
REQ_CAPTURE = REPO / "doc" / "common" / "REQ__process__product-requirement-capture.md"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_setup_uses_area_req_doc_convention():
    body = _text(BOOTSTRAP)

    assert "doc/<area>/REQ__<name>.md" in body
    assert "DDD-style area or bounded" in body
    assert "doc/common/" in body
    assert "doc/product/" not in body


def test_develop_guidance_covers_requirement_docs_for_claude_and_codex():
    for path in (CLAUDE_DEVELOP, CODEX_DEVELOP):
        body = _text(path)

        assert "Requirement docs (UI/API intent)" in body
        assert "doc/<area>/REQ__<name>.md" in body
        assert "DDD-style areas or bounded contexts" in body
        assert "user-visible screens, flows, states, localization, or interactions" in body
        assert "externally consumed APIs, webhooks, SDK-facing behavior" in body
        assert "readable prose that states intended observable behavior and verification cues" in body
        assert "Requirement docs: links to `doc/<area>/REQ__*.md`" in body
        assert "not needed — <reason>" in body
        assert "Requirement Decision" in body
        assert "Existing-screen state changes count" in body
        assert "Observable bugfixes count" in body
        assert "no UI state, user flow, API contract, or observable runtime behavior changed" in body
        assert "doc/product/" not in body


def test_plan_requires_requirement_decision_for_claude_and_codex():
    for path in (PLAN_WRITE, CODEX_PLAN):
        body = _text(path)

        assert "Requirement Decision" in body
        assert "Observable behavior changes: yes | no" in body
        assert "Surface: ui | api | both | none" in body
        assert "Requirement doc: doc/<area>/REQ__<name>.md | n/a" in body
        assert "doc/ui/REQ__filter-bar.md" in body
        assert "doc/api/REQ__oauth-login.md" in body
        assert "Observable bugfixes" in body
        assert "visible behavior changes from wrong to intended" in body
        assert "doc/product/" not in body


def test_qa_agents_report_requirement_gaps():
    for path in (QA_BROWSER, QA_API):
        body = _text(path)

        assert "Requirement Decision" in body
        assert "Requirement gap" in body
        assert "doc/<area>/REQ__*.md" in body
        assert "intended observable behavior" in body
        assert "verification cues" in body
        assert "doc/product/" not in body


def test_qa_uses_req_docs_as_intent_evidence():
    for path in (CLAUDE_DEVELOP, CODEX_DEVELOP):
        body = _text(path)

        assert "pass those paths to the QA lens as intent evidence" in body
        assert "observable behavior and verification cues" in body


def test_req_capture_documents_prose_first_intent_contracts():
    body = _text(REQ_CAPTURE)

    assert "doc/<area>/REQ__<name>.md" in body
    assert "doc/ui/REQ__filter-bar.md" in body
    assert "doc/api/REQ__oauth-login.md" in body
    assert "observable bugfixes" in body
    assert "intended observable behavior" in body
    assert "verification cues" in body
    assert "doc/product/" not in body
