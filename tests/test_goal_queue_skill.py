from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def _body(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


def test_goal_queue_skills_define_direction_stack_and_harness_loop():
    for rel in (
        "plugin/skills/goal-queue/SKILL.md",
        "plugin-codex/internal-skills/goal-queue/SKILL.md",
    ):
        body = _body(rel)
        assert "user-invocable: false" in body
        assert "Product Direction Lock" in body
        assert "Technical Stack Lock" in body
        assert "Product Backlog And Slice Plan" in body
        assert "Harness Execution Loop" in body
        assert "Iteration Review" in body
        assert "Backlog Rewrite" in body
        assert "thin vertical" in body
        assert "goal-queue-loop.md" in body
        assert "Gap Discovery Loop" in body
        assert "Stop Conditions" in body
        assert "goal_queue_runner.py" in body
        assert "doc/harness/goal-queue.json" in body
        assert "--max-hours 24" in body
        assert "--require-harness-close" in body
        assert "--require-review-before-next" in body
        assert "preflight" in body
        assert "goal-queue-events.jsonl" in body
        assert "goal-queue-heartbeat.json" in body
        assert "recover" in body
        assert "goal-queue-failure-policy.md" in body
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
        assert "Goal Queue Continuation Gate" in body
        assert "Closing one harness slice is never enough" in body
        assert "MVP scaffold complete" in body
        assert "next slice is already active or queued" in body


def test_goal_queue_run_skills_prevent_slice_close_final_answer():
    for rel in (
        "plugin/skills/run/SKILL.md",
        "plugin-codex/internal-skills/run/SKILL.md",
    ):
        body = _body(rel)
        assert "If this run is a Goal queue child task" in body
        assert "task close is an iteration checkpoint" in body
        assert "choose the next highest-value slice" in body
        assert "next slice is already active/queued" in body


def test_goal_queue_loop_records_continuation_gate():
    body = _body("doc/harness/patterns/goal-queue-loop.md")

    assert "Continuation Gate" in body
    assert "Closing one harness slice is an iteration checkpoint" in body
    assert "final Goal response is allowed only when" in body
    assert "MVP scaffold complete" in body


def test_goal_queue_codex_skill_documents_runtime_substitutions():
    body = _body("plugin-codex/internal-skills/goal-queue/SKILL.md")

    assert "Codex runtime notes" in body
    assert "does not have Claude's `Skill()` chaining primitive" in body
    assert "bare MCP tool names" in body
    assert "${HARNESS_PLUGIN_ROOT}/internal-skills/run/SKILL.md" in body


def test_goal_queue_claude_skill_delegates_slices_to_harness_run():
    body = _body("plugin/skills/goal-queue/SKILL.md")

    assert "internal harness run flow" in body
    assert "AskUserQuestion" in body
    assert "allowed-tools:" in body
