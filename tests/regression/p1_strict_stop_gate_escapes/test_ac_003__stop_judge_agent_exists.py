"""AC-003 regression: the retired stop-judge path is gone from both trees.

History: this AC originally pinned a routable `harness:stop-judge` agent as the
sole non-PASS turn-end authority. `2fa09ff` retired the routing and reduced the
file to a non-routable compatibility stub. The compatibility window is now
closed, so the invariant is absence: no stop-judge agent file may come back, and
qualified blockers go straight to `task_blocked`.
"""

import os


REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
AGENT_PATHS = (
    os.path.join(REPO, "plugin", "agents", "stop-judge.md"),
    os.path.join(REPO, "plugin-codex", "agents", "stop-judge.md"),
)


def test_stop_judge_agent_file_is_removed():
    for path in AGENT_PATHS:
        assert not os.path.exists(path), (
            f"retired stop-judge agent reappeared: {path}. Its presence "
            "re-registers a dead agent type in every session."
        )


def test_blocked_env_transition_is_owned_by_task_blocked():
    stop_gate = open(os.path.join(REPO, "plugin", "scripts", "stop_gate.py")).read()
    assert "task_blocked" in stop_gate, "task_blocked transition path missing"
    assert "stop-judge" not in stop_gate, "stop-judge routing remains in stop_gate"
    assert "stop_judge" not in stop_gate, "stop-judge routing remains in stop_gate"


if __name__ == "__main__":
    test_stop_judge_agent_file_is_removed()
    test_blocked_env_transition_is_owned_by_task_blocked()
    print("OK")
