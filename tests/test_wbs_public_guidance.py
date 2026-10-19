"""Public recovery instructions must be executable through the sole WBS API."""
import importlib
import json
import re
import subprocess
from pathlib import Path
from unittest import mock


def assert_guidance(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"next_action", "retry_action"}:
                assert not re.search(r"\b(?:call|run|retry|use|through|via)\s+`?(?:task_(?:start|context|verify|close|blocked)|write_plan)\b", item, re.I), item
            assert_guidance(item)
    elif isinstance(value, list):
        for item in value:
            assert_guidance(item)


def test_public_recovery_and_resume_guidance(tmp_path, monkeypatch):
    fixture = importlib.import_module("test_harness_mcp_server")
    server = fixture.harness_server
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / ".gitignore").write_text("doc/harness/tasks/\n")
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    monkeypatch.delenv("CODEX_SESSION_ID", raising=False)
    monkeypatch.delenv("HARNESS_SESSION_ID", raising=False)
    monkeypatch.setattr(server, "find_repo_root", lambda *a: str(repo))

    def call(action, root="WBS__guidance", **args):
        result = server.call_tool("wbs", dict(action=action, wbs_id=root, **args))
        assert_guidance(result.get("structuredContent", {}))
        return result

    opened = call("open")
    assert not opened.get("isError"), opened
    td = Path(opened["structuredContent"]["task_dir"])
    run = server.read_task_control(str(td))["run_id"]
    plan = '# Plan\n```wbs\n' + json.dumps([dict(id="A", title="Observable outcome", weight=1, check=["true"])]) + '\n```\n'
    assert not call("plan", plan=plan).get("isError")
    for action in ("context", "next", "verify"):
        assert not call(action).get("isError")
    conflict = call("open", root="WBS__other")
    assert conflict.get("isError")
    assert "wbs(action='open')" in conflict["structuredContent"]["next_action"]
    blocked = call("block", blocked_reason="External service unavailable", unblock_condition="Service returns")
    assert not blocked.get("isError")
    assert "wbs(action='open')" in blocked["structuredContent"]["next_action"]
    assert "Call task_start" not in (td / "BLOCKED.md").read_text()
    assert not call("open").get("isError")
    assert server.read_task_control(str(td))["run_id"] == run
    # A committed scaffold whose context projection fails must recover through
    # context, rather than create another generation or name an unavailable tool.
    with mock.patch.object(server, "emit_compact_context", side_effect=RuntimeError("projection unavailable")):
        fallback = call("open")
    assert not fallback.get("isError")
    assert "wbs(action='context'" in fallback["structuredContent"]["next_action"]
    invalid = call("context", root="WBS__missing")
    assert invalid.get("isError")
    assert "wbs(action='open')" in invalid["structuredContent"]["next_action"]
