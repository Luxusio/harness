"""AC-003 regression: plugin/agents/stop-judge.md exists with required verdict branches."""

import os


AGENT_PATH = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "plugin", "agents", "stop-judge.md"))


def test_stop_judge_file_exists():
    assert os.path.isfile(AGENT_PATH), f"missing: {AGENT_PATH}"


def test_stop_judge_has_three_verdicts():
    body = open(AGENT_PATH).read()
    for verdict in ("VERDICT_OK_DONE", "VERDICT_OK_BLOCKED", "VERDICT_NO_CONTINUE"):
        assert verdict in body, f"verdict {verdict} not defined in stop-judge.md"


def test_stop_judge_names_blocked_env_transition():
    body = open(AGENT_PATH).read()
    assert "BLOCKED_ENV" in body, "BLOCKED_ENV transition path missing"
    assert "task_blocked" in body, "task_blocked MCP reference missing"
    assert "unblock_condition" in body, "unblock condition usage not specified"


def test_stop_judge_frontmatter_complete():
    body = open(AGENT_PATH).read()
    assert body.startswith("---\n"), "missing YAML frontmatter"
    fm_end = body.find("\n---\n", 4)
    assert fm_end > 0, "frontmatter not closed"
    fm = body[4:fm_end]
    for field in ("name:", "description:", "model:", "tools:"):
        assert field in fm, f"frontmatter missing {field}"


if __name__ == "__main__":
    test_stop_judge_file_exists()
    test_stop_judge_has_three_verdicts()
    test_stop_judge_names_blocked_env_transition()
    test_stop_judge_frontmatter_complete()
    print("OK")
