from __future__ import annotations

from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
PUBLIC_RUN = REPO / "plugin-codex/skills/run/SKILL.md"
OPENAI_YAML = REPO / "plugin-codex/skills/run/agents/openai.yaml"
INTERNAL_RUN = REPO / "plugin-codex/internal-skills/run/SKILL.md"


def test_public_run_is_thin_implicit_entry_to_canonical_workflow():
    body = PUBLIC_RUN.read_text(encoding="utf-8")
    metadata = OPENAI_YAML.read_text(encoding="utf-8")

    assert "name: run" in body
    assert "user-invocable:" not in body
    assert "repository-mutating" in body
    assert "Do not use for read-only" in body
    assert "../../internal-skills/run/SKILL.md" in body
    assert INTERNAL_RUN.is_file()
    assert "spawn_agent" in body
    assert "conditional security reviewer" in body
    assert "task_verify" in body and "task_close" in body
    assert "allow_implicit_invocation: true" in metadata
    assert "$harness:run" in metadata


def test_codex_routing_surfaces_all_point_to_public_run_entry():
    paths = (
        REPO / "AGENTS.md",
        REPO / "CLAUDE.md",
        REPO / "plugin/CLAUDE.md",
        REPO / "plugin/skills/setup/bootstrap.md",
        REPO / "plugin/scripts/hook_user_prompt_submit.py",
        REPO / "plugin/scripts/hook_post_tool_use.py",
    )
    for path in paths:
        assert "$harness:run" in path.read_text(encoding="utf-8"), path


def test_write_gate_recovery_points_to_public_run_entry():
    prewrite = (REPO / "plugin/scripts/prewrite_gate.py").read_text(encoding="utf-8")
    bash_guard = (REPO / "plugin/scripts/mcp_bash_guard.py").read_text(encoding="utf-8")

    assert prewrite.count("$harness:run") >= 3
    assert "Invoke $harness:run" in bash_guard


def test_setup_verifies_public_run_install_and_implicit_routing():
    report = (REPO / "plugin/skills/setup/verify-report.md").read_text(encoding="utf-8")
    setup = (REPO / "plugin-codex/skills/setup/SKILL.md").read_text(encoding="utf-8")

    for body in (report, setup):
        assert "skills/run/SKILL.md" in body
        assert "implicit" in body
        assert "$harness:run" in body


def test_canonical_run_has_one_normal_qa_and_close_owner():
    run = INTERNAL_RUN.read_text(encoding="utf-8")
    plan = (REPO / "plugin-codex/internal-skills/plan/SKILL.md").read_text(encoding="utf-8")

    assert "Develop owns the implementation-through-close transaction" in run
    assert "Do not run a second QA or close cycle" in run
    assert "Verify recovery (only when develop returned before close)" in run
    assert "Skip the `task_close` call when Phase 3 already closed" in run
    assert "Dual Voice is capability-routed" in plan
    assert "Codex has no Agent fan-out tool" not in plan
    assert "Single-Voice Protocol" not in plan
    assert "Voice count is single in both" not in plan

    develop = (REPO / "plugin-codex/internal-skills/develop/SKILL.md").read_text(encoding="utf-8")
    assert "Phase 9 is the only normal owner of `task_close`" in develop
    assert develop.index("### Phase 8.7: Distilled Change Doc") < develop.index("### Phase 9:")
    assert "current task receipt run" in develop
    assert "checks are not lifecycle gates" in develop
    assert "Do not rerun installation for docs-only edits" in develop


def test_run_skills_use_only_native_goal_and_learn_before_next_child():
    native = (REPO / "doc/harness/patterns/native-goals.md").read_text(encoding="utf-8")
    requirement = (
        REPO / "doc/common/REQ__process__autonomous-task-pack-execution.md"
    ).read_text(encoding="utf-8")

    for path in (
        REPO / "plugin/skills/run/SKILL.md",
        REPO / "plugin-codex/internal-skills/run/SKILL.md",
    ):
        body = path.read_text(encoding="utf-8")
        assert "### Native Goal continuation" in body
        assert "goal_add_task" in body
        assert "goal_next_task" in body
        assert body.index("`task_close` first") < body.index("only then call `goal_next_task`")
        assert "learning promotion" in body
        assert "task_pack_runner.py" not in body
        assert "goal_queue_runner.py" not in body

    assert "## Ordered Children" in native
    assert "## Learning Before Continuation" in native
    assert "Runbook memory" in native
    assert "promote_learnings.py" in native
    assert "ordered native Goal child" in requirement
    assert "goal_finish" in requirement
    assert not (REPO / "plugin/skills/goal-queue/SKILL.md").exists()
    assert not (REPO / "plugin-codex/internal-skills/goal-queue/SKILL.md").exists()
