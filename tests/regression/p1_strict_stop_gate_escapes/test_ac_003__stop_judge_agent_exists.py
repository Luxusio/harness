"""AC-003 regression: the retired stop-judge path is a non-routable stub."""

import os


AGENT_PATH = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "plugin", "agents", "stop-judge.md"))


def test_stop_judge_file_exists():
    assert os.path.isfile(AGENT_PATH), f"missing: {AGENT_PATH}"


def test_stop_judge_has_no_agent_protocol():
    body = open(AGENT_PATH).read()
    assert not body.startswith("---\n"), "deprecated stub must not have frontmatter"
    for fragment in ("tools:", "VERDICT_OK_DONE", "VERDICT_OK_BLOCKED", "VERDICT_NO_CONTINUE"):
        assert fragment not in body, f"agent protocol remains in compatibility stub: {fragment}"


def test_stop_judge_names_blocked_env_transition():
    body = open(AGENT_PATH).read()
    assert "Deprecated compatibility path" in body
    assert "not an agent definition" in body
    assert "must not be" in body and "routed" in body
    assert "BLOCKED_ENV" in body, "BLOCKED_ENV transition path missing"
    assert "task_blocked" in body, "task_blocked MCP reference missing"


if __name__ == "__main__":
    test_stop_judge_file_exists()
    test_stop_judge_has_no_agent_protocol()
    test_stop_judge_names_blocked_env_transition()
    print("OK")
