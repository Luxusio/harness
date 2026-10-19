"""task_blocked stays as the park record; docs no longer describe a live stop gate.

User decision 2026-09-25 (TASK__remove-blocked-env-pause-path, re-scoped):
the 2026-09-02 plan to delete task_blocked / BLOCKED.md was withdrawn after the
Claude Stop hook was intentionally removed on 2026-09-23. With no turn-end gate
left, task_blocked only records an unfinished task. See
doc/harness/REQ__task-blocked-is-the-park-record.md.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_c17_parking_clause_in_both_contract_copies():
    for path in (REPO / "CONTRACTS.md",
                 REPO / "plugin/skills/setup/templates/CONTRACTS.md"):
        body = _text(path)
        c17 = body[body.index("### C-17"):body.index("### C-18")]
        clause = c17[c17.index("**Parking clause:**"):]
        clause = clause[:clause.index("\n\n")]
        assert 'wbs(action="block")' in clause, path
        assert 'wbs(action="open")' in clause, path
        assert "C-04" in clause, path


def test_template_parking_clause_carries_no_repo_history():
    body = _text(REPO / "plugin/skills/setup/templates/CONTRACTS.md")
    c17 = body[body.index("### C-17"):body.index("### C-18")]
    clause = c17[c17.index("**Parking clause:**"):]
    clause = clause[:clause.index("\n\n")]
    assert not re.search(r"\d{4}-\d{2}-\d{2}", clause)
    assert "doc/harness/" not in clause


def test_parking_remains_available_through_the_sole_wbs_tool():
    from test_harness_mcp_server import harness_server
    assert set(harness_server.TOOLS) == {"wbs"}
    assert "block" in harness_server.TOOLS["wbs"]["inputSchema"]["properties"]["action"]["enum"]
    assert harness_server._EXECUTION["task_blocked"] is harness_server.handle_task_blocked
    assert harness_server.call_tool("task_blocked", {}).get("isError")


def test_runtime_doc_names_park_as_not_completion():
    doc = _text(REPO / "plugin/CLAUDE.md")
    section = doc[doc.index("## 4a."):]
    section = section[:section.index("\n## ", 1)]
    assert any('wbs(action="block")' in line and 'wbs(action="open")' in line and "C-04" in line
               for line in section.splitlines())


def test_docs_no_longer_describe_stop_gate_as_live():
    stale = {
        "doc/harness/SPEC.md": "The hook and Stop gate derive",
        "doc/harness/REQ__subagent-lifecycle-receipt-boundaries.md":
            "stop gate reads those rows",
        "doc/harness/REQ__receipt-subsystem-failures-are-observable.md":
            "stop gate 가 그것을 읽는다",
        "doc/harness/OBS__design-planning-harness-friction.md":
            "`stop_gate._next_action_for_missing` already maps",
        "doc/common/OBS__background-agent-stall.md":
            "`stop_gate.py` runs on the **main session** stop hook",
    }
    for rel, phrase in stale.items():
        assert phrase not in _text(REPO / rel), rel
