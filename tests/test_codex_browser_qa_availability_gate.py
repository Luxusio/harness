from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
CODEX_RUN = REPO / "plugin-codex" / "internal-skills" / "run" / "SKILL.md"
CODEX_DEVELOP = REPO / "plugin-codex" / "internal-skills" / "develop" / "SKILL.md"
CLAUDE_RUN = REPO / "plugin" / "skills" / "run" / "SKILL.md"
CLAUDE_DEVELOP = REPO / "plugin" / "skills" / "develop" / "SKILL.md"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_codex_run_browser_qa_is_availability_gated():
    body = _text(CODEX_RUN)

    forbidden = [
        "qa-browser** — NOT supported on Codex",
        "Browser QA is Claude-only on v1.5",
        "accept partial QA via qa-api / qa-cli only",
    ]
    for phrase in forbidden:
        assert phrase not in body

    assert "browser tools are available" in body
    assert "qa-browser" in body
    assert "RECEIPTS.jsonl" in body
    assert "watcher-recorded QA completions" in body


def test_codex_develop_browser_visual_phases_are_not_blanket_deferred():
    body = _text(CODEX_DEVELOP)

    forbidden = [
        "Codex Playwright MCP (deferred v2)",
        "deferred on Codex v1.5",
        "Browser smoke deferred to v2",
        "qa-browser deferred",
        "Visual-smoke (deferred)",
        'Visual Evidence (Codex v1.5: "deferred',
    ]
    for phrase in forbidden:
        assert phrase not in body

    assert "Browser tools are availability-gated on Codex" in body
    assert "run `${HARNESS_PLUGIN_ROOT}/agents/qa-browser.md` inline" in body
    assert "task_verify" in body
    assert "state the fallback in task state or final response" in body


def test_claude_policy_still_requires_qa_browser_delegation():
    run_body = _text(CLAUDE_RUN)
    develop_body = _text(CLAUDE_DEVELOP)

    assert "MUST spawn qa-browser" in run_body
    assert "Skipping leaves no completed qa-browser receipt" in run_body
    assert "Prefer delegating Browser MCP tools (`mcp__chrome-devtools__*`)" in develop_body
    assert "inline use is allowed" in develop_body
    assert "harness:qa-browser" in develop_body
