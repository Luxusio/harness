from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def _body(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


def test_autopilot_skills_define_direction_stack_and_harness_loop():
    for rel in (
        "plugin/skills/autopilot/SKILL.md",
        "plugin-codex/skills/autopilot/SKILL.md",
    ):
        body = _body(rel)
        assert "user-invocable: true" in body
        assert "Product Direction Lock" in body
        assert "Technical Stack Lock" in body
        assert "Product Backlog And Slice Plan" in body
        assert "Harness Execution Loop" in body
        assert "Iteration Review" in body
        assert "Backlog Rewrite" in body
        assert "thin vertical" in body
        assert "autopilot-agile-loop.md" in body
        assert "Gap Discovery Loop" in body
        assert "Stop Conditions" in body
        assert "autopilot_runner.py" in body
        assert "doc/harness/autopilot.yaml" in body
        assert "--max-hours 24" in body
        assert "--require-harness-close" in body
        assert "--require-review-before-next" in body
        assert "preflight" in body
        assert "autopilot-events.jsonl" in body
        assert "autopilot-heartbeat.json" in body
        assert "recover" in body
        assert "autopilot-failure-policy.md" in body
        assert "failure_class" in body
        assert "recommended_action" in body
        assert "USER_DECISION_REQUIRED" in body
        assert "QA returns FAIL" in body
        assert "UX returns FAIL" in body
        assert "task_close" in body
        assert "fresh QA PASS" in body
        assert "required UX" in body
        assert "review_quality" in body
        assert "quality_warnings" in body
        assert "quality_blockers" in body
        assert "warning-only" in body


def test_autopilot_codex_skill_documents_runtime_substitutions():
    body = _body("plugin-codex/skills/autopilot/SKILL.md")

    assert "Codex runtime notes" in body
    assert "does not have Claude's `Skill()` chaining primitive" in body
    assert "bare MCP tool names" in body
    assert "plugin-codex/skills/run/SKILL.md" in body


def test_autopilot_claude_skill_delegates_slices_to_harness_run():
    body = _body("plugin/skills/autopilot/SKILL.md")

    assert 'Skill("harness:run", "<slice description>")' in body
    assert "AskUserQuestion" in body
    assert "allowed-tools:" in body
