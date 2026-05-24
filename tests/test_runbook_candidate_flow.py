from __future__ import annotations

from pathlib import Path


REPO = Path(__file__).resolve().parent.parent


def test_run_self_improvement_documents_runbook_candidate_capture():
    body = (REPO / "plugin" / "skills" / "run" / "self-improvement.md").read_text(encoding="utf-8")

    assert "Runbook candidates from discovered commands" in body
    assert "runbook_memory.py capture" in body
    assert "--failed-command" in body
    assert "--failure-class" in body
    assert "Do not auto-approve candidates at capture time" in body
    assert "Self-Healing Candidates" in body


def test_develop_skills_route_self_healing_commands_to_runbook_candidates():
    for rel, root_var in (
        ("plugin/skills/develop/SKILL.md", "CLAUDE_PLUGIN_ROOT"),
        ("plugin-codex/internal-skills/develop/SKILL.md", "HARNESS_PLUGIN_ROOT"),
    ):
        body = (REPO / rel).read_text(encoding="utf-8")
        assert "runbook_memory.py capture" in body
        assert f"${{{root_var}}}/scripts/runbook_memory.py" in body
        assert "--source-phase" in body
        assert "The candidate is not shared memory yet" in body
        assert "doc/harness/runbooks.yaml" in body

