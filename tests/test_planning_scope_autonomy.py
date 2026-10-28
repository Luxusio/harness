"""Instruction regressions for grounded planning and recovery authority."""
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_planning_completeness_has_no_sentence_quota():
    paths = ("plugin/skills/plan/SKILL.md", "plugin/skills/plan/review-phases.md",
             "plugin-codex/internal-skills/plan/SKILL.md")
    for path in paths:
        body = (REPO / path).read_text()
        assert "fewer than 3 sentences" not in body
        assert "compression signal" not in body
        assert "what was examined" in body


def test_discovery_amends_current_outcomes_without_promoting_user_intent():
    for runtime in ("plugin/skills", "plugin-codex/internal-skills"):
        body = (REPO / runtime / "develop/SKILL.md").read_text()
        gate = body.split("## Premise Gate / User Challenge", 1)[1].split("## Error Philosophy", 1)[0]
        assert "necessary helpers and implementation alternatives" in gate
        assert "current PLAN/WBS" in gate and "autonomously" in gate
        assert "material user decision" in gate and "lacks authorization" in gate
        assert "fire the Premise Gate conversational ask" not in body
        assert "user-confirmed direction before changing course" not in body
        assert "fresh-task / continue-anyway" not in body
        assert "run plan skill / check task_id / abort" not in body
        assert "Required planning, independent review and QA cannot be skipped" in body


def test_rolling_wbs_keeps_dispatch_ownership_separate():
    body = (REPO / "plugin/skills/plan/write-artifacts.md").read_text()
    assert "do not freeze test commands or" in body
    assert "implementation layout at intake" in body
    assert "Add ownership only when assigning writers" in body
    assert "Supported implementation work may enter WBS" in body
    assert "cannot introduce a" in body
