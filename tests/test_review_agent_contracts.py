from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def _text(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_minimum_sufficient_contract_reaches_every_implementation_role():
    role_paths = (
        "plugin/agents/developer.md",
        "plugin/agents/ac-worker.md",
        "plugin-codex/agents/developer.md",
    )
    for path in role_paths:
        body = _text(path).lower()
        assert "minimum-sufficient" in body or "minimum sufficient" in body, path
        assert "stdlib" in body or "standard library" in body, path
        assert "validation" in body, path
        assert "authorization" in body or "auth" in body, path
        assert "concurren" in body, path
        assert "security" in body, path
    for path in ("plugin/skills/develop/SKILL.md", "plugin-codex/internal-skills/develop/SKILL.md"):
        body = _text(path).lower()
        assert "minimum-sufficient" in body
        assert "agents/developer.md" in body


def test_review_agents_are_read_only_and_have_exact_verdict_contract():
    for runtime in ("plugin", "plugin-codex"):
        code = _text(f"{runtime}/agents/code-reviewer.md")
        security = _text(f"{runtime}/agents/security-reviewer.md")
        for body in (code, security):
            assert "read-only" in body.lower()
            assert "`VERDICT: PASS`" in body
            assert "FINDING_COUNTS:" in body
            assert "FIX_NOW" in body
            assert "INVESTIGATE" in body
            assert "OPTIONAL" in body
        assert "excess" in code and "missing" in code
        assert "file:line" in code
        assert "exploitability" in security and "blast radius" in security


def test_review_gate_replaces_overlapping_legacy_review_agents():
    audit = _text("plugin/skills/develop/quality-audit-pipeline.md")
    assert "harness:code-reviewer" in audit
    assert "harness:security-reviewer" in audit
    assert "Do not spawn the old generic adversarial" in audit
    assert "QA must start after the latest review PASS" in audit
    assert "200+ lines" not in audit


def test_design_maps_agent_behaviors_to_reference_projects():
    design = _text("doc/designs/minimal-implementer-and-code-review-gate.md")
    assert "Agent behavior provenance" in design
    assert "Ponytail `skills/ponytail/SKILL.md`" in design
    assert "gstack `ship/sections/adversarial.md`" in design
    assert "oh-my-claudecode `agents/code-reviewer.md`" in design
