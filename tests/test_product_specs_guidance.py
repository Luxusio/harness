from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
BOOTSTRAP = REPO / "plugin" / "skills" / "setup" / "bootstrap.md"
CLAUDE_DEVELOP = REPO / "plugin" / "skills" / "develop" / "SKILL.md"
CODEX_DEVELOP = REPO / "plugin-codex" / "skills" / "develop" / "SKILL.md"
PRODUCT_README = REPO / "doc" / "harness" / "product" / "README.md"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_setup_scaffolds_product_spec_directories():
    body = _text(BOOTSTRAP)

    assert "doc/harness/product/" in body
    assert "doc/harness/product/ui" in body
    assert "doc/harness/product/api" in body
    assert "durable Product Specs" in body


def test_develop_guidance_covers_product_specs_for_claude_and_codex():
    for path in (CLAUDE_DEVELOP, CODEX_DEVELOP):
        body = _text(path)

        assert "Product Specs (UI/API intent)" in body
        assert "doc/harness/product/ui/<screen-or-feature>.md" in body
        assert "doc/harness/product/api/<endpoint-or-integration>.md" in body
        assert "user-visible screens, flows, states, localization, or interactions" in body
        assert "externally consumed APIs, webhooks, SDK-facing behavior" in body
        assert "readable prose that states intended observable behavior and verification cues" in body
        assert "Product Specs: links to `doc/harness/product/ui/` or `doc/harness/product/api/`" in body
        assert "not needed — <reason>" in body


def test_qa_uses_product_specs_as_intent_evidence():
    for path in (CLAUDE_DEVELOP, CODEX_DEVELOP):
        body = _text(path)

        assert "pass those paths to the QA lens as intent evidence" in body
        assert "observable behavior and verification cues" in body


def test_product_readme_documents_prose_first_intent_contracts():
    body = _text(PRODUCT_README)

    assert "Product Specs capture durable product intent" in body
    assert "doc/harness/product/ui/" in body
    assert "doc/harness/product/api/" in body
    assert "Write short prose first" in body
    assert "intended observable behavior" in body
    assert "verification cues" in body
