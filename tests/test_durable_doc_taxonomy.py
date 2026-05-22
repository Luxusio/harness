from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
BOOTSTRAP = REPO / "plugin" / "skills" / "setup" / "bootstrap.md"
PLAN_WRITE = REPO / "plugin" / "skills" / "plan" / "write-artifacts.md"
CODEX_PLAN = REPO / "plugin-codex" / "skills" / "plan" / "SKILL.md"
CLAUDE_DEVELOP = REPO / "plugin" / "skills" / "develop" / "SKILL.md"
CODEX_DEVELOP = REPO / "plugin-codex" / "skills" / "develop" / "SKILL.md"
QA_BROWSER = REPO / "plugin" / "agents" / "qa-browser.md"
QA_API = REPO / "plugin" / "agents" / "qa-api.md"
CRITIC_DOCUMENT = REPO / "plugin" / "agents" / "critic-document.md"
CODEX_CRITIC_DOCUMENT = REPO / "plugin-codex" / "agents" / "critic-document.md"
DOCUMENT_CRITIC_PLAYBOOK = REPO / "doc" / "harness" / "critics" / "document.md"
TAXONOMY = REPO / "doc" / "common" / "GUIDE__document-taxonomy.md"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_taxonomy_guide_defines_durable_doc_types():
    body = _text(TAXONOMY)

    assert "doc/<area>/<TYPE>__<name>.md" in body
    assert "`REQ__...` records product or system requirements" in body
    assert "`GUIDE__...` records coding, design, testing, or implementation guidance" in body
    assert "`ADR__...` records an important technical decision" in body
    assert "`POLICY__...` records external security, legal, data-handling" in body
    assert "`OBS__...` records observed facts" in body
    assert "`INF__...` records inference or hypotheses" in body
    assert "Keep harness-internal execution rules in skills, agents, scripts, and tests" in body
    assert "New pages, admin/backoffice screens, routes, controllers, and endpoints require" in body
    assert "PLAN.md acceptance criteria do not replace durable requirements" in body


def test_setup_introduces_durable_doc_taxonomy():
    body = _text(BOOTSTRAP)

    assert "doc/<area>/<TYPE>__<name>.md" in body
    assert "REQ" in body
    assert "GUIDE" in body
    assert "ADR" in body
    assert "POLICY" in body
    assert "OBS" in body
    assert "INF" in body
    assert "doc/product/" not in body


def test_plan_requires_durable_docs_decision_for_claude_and_codex():
    for path in (PLAN_WRITE, CODEX_PLAN):
        body = _text(path)

        assert "Durable Docs Decision" in body
        assert "REQ: doc/<area>/REQ__<name>.md | n/a" in body
        assert "GUIDE: doc/<area>/GUIDE__<name>.md | n/a" in body
        assert "ADR: doc/<area>/ADR__<name>.md | n/a" in body
        assert "POLICY: doc/<area>/POLICY__<name>.md | n/a" in body
        assert "doc/auth/ADR__token-storage.md" in body
        assert "doc/common/GUIDE__coding-style.md" in body
        assert "New pages, admin/backoffice screens, routes, controllers, and endpoints" in body
        assert "REQ-required even when additive" in body
        assert "PLAN.md acceptance criteria" in body
        assert "REQ path is required before develop starts" in body
        assert "blocking plan defect" in body
        assert "do not defer this to close" in body
        assert "Original Request / Intent Summary" in body
        assert "conversation summary" in body
        assert "gitignored" in body
        assert "15" in body
        assert "non-empty lines" in body
        assert "doc/product/" not in body


def test_develop_guidance_writes_selected_durable_docs():
    for path in (CLAUDE_DEVELOP, CODEX_DEVELOP):
        body = _text(path)

        assert "Durable docs (REQ/GUIDE/ADR/POLICY)" in body
        assert "Durable Docs Preflight" in body
        assert "before source implementation" in body
        assert "doc/<area>/<TYPE>__<name>.md" in body
        assert "Use `REQ` for user-visible behavior" in body
        assert "Use `GUIDE` for reusable coding, design, testing" in body
        assert "Use `ADR` for significant technical choices" in body
        assert "Use `POLICY` only for external security, legal" in body
        assert "New pages, admin/backoffice screens, routes, controllers, and endpoints require a REQ" in body
        assert "Recheck the actual diff after implementation" in body
        assert "record the correction in DOC_SYNC" in body
        assert "Durable docs: before calling `write_handoff`, include links to `doc/<area>/REQ__*.md`, `GUIDE__*.md`, `ADR__*.md`, or `POLICY__*.md`" in body
        assert "before calling `write_handoff`" in body
        assert "specific non-observable reason" in body
        assert "`not needed` is invalid for new or changed UI/API/backoffice/admin screens" in body
        assert "doc/product/" not in body


def test_qa_agents_read_durable_docs_by_type():
    for path in (QA_BROWSER, QA_API):
        body = _text(path)

        assert "Durable Docs Decision" in body
        assert "doc/<area>/<TYPE>__*.md" in body
        assert "Use `REQ` as behavior/contract verification criteria" in body
        assert "Use `GUIDE` as implementation quality and consistency criteria" in body
        assert "Use `ADR` as architecture intent and tradeoff criteria" in body
        assert "Use `POLICY` as external constraint criteria" in body
        assert "Durable Docs gap" in body
        assert "doc/product/" not in body


def test_document_critic_checks_req_quality():
    playbook = _text(DOCUMENT_CRITIC_PLAYBOOK)
    assert "Durable REQ quality bar" in playbook
    assert "too vague for implementation or QA to verify" in playbook
    assert "observable behavior exists only in task artifacts and not in the REQ" in playbook

    for path in (CRITIC_DOCUMENT, CODEX_CRITIC_DOCUMENT):
        body = _text(path)
        assert "critic-document" in body
        assert "write_critic_document" in body
        assert "`REQ__*.md` that is too vague for future implementation or QA" in body
        assert "Observable behavior introduced by the diff but missing from the REQ" in body
        assert "Do not edit documentation yourself" in body


def test_active_guidance_has_no_product_spec_taxonomy():
    paths = [
        BOOTSTRAP,
        PLAN_WRITE,
        CODEX_PLAN,
        CLAUDE_DEVELOP,
        CODEX_DEVELOP,
        QA_BROWSER,
        QA_API,
        CRITIC_DOCUMENT,
        CODEX_CRITIC_DOCUMENT,
        DOCUMENT_CRITIC_PLAYBOOK,
        TAXONOMY,
    ]
    combined = "\n".join(_text(path) for path in paths)

    assert "doc/product" not in combined
    assert "Product Spec" not in combined
    assert "REQ__product" not in combined
    assert "Product Requirement Decision" not in combined
    assert "Product Spec Decision" not in combined
