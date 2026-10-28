"""Public recovery instructions must be executable through the sole WBS API."""
import importlib
import json
import re
import subprocess
import sys
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugin/scripts"))


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
    # An explicit isolated caller exercises a genuine exact-session conflict.
    monkeypatch.setenv("HARNESS_SESSION_ID", "public-guidance-isolated-caller")
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
    # Preserved-run resume fails atomically when response preparation fails;
    # it does not publish a partially resumed generation or invent another run.
    with mock.patch.object(server, "emit_compact_context", side_effect=RuntimeError("projection unavailable")):
        fallback = call("open")
    assert fallback.get("isError")
    assert "projection unavailable" in fallback["structuredContent"]["error"]
    assert server.read_task_control(str(td))["run_id"] == run
    assert not call("open").get("isError")
    invalid = call("context", root="WBS__missing")
    assert invalid.get("isError")
    assert "wbs(action='open')" in invalid["structuredContent"]["next_action"]


def test_recovery_outputs_from_gate_registration_and_checkpoint(tmp_path, monkeypatch, capsys):
    gate = importlib.import_module("prewrite_gate")
    for runtime in ("codex", "claude"):
        monkeypatch.setenv("HARNESS_RUNTIME", runtime)
        # Exercise the actual protected-plan deny renderer, including the
        # runtime-qualified tool spelling, rather than only its mapping helper.
        gate._deny("C-05-protected-artifact", str(tmp_path / "PLAN.md"),
                   "plan-skill", "Use the plan owner", str(tmp_path))
        output = capsys.readouterr().out
        assert "write_plan" not in output
        assert "wbs" in output and "wbs_id" in output
        reason = json.loads(output)["hookSpecificOutput"]["permissionDecisionReason"]
        assert 'action="plan", wbs_id=' in reason
        expected = "wbs {" if runtime == "codex" else "mcp__plugin_harness_harness__wbs("
        assert expected in reason

    hook = importlib.import_module("hook_pre_tool_use")
    assert not hasattr(hook, "_report_registration_failure")

    fixture = importlib.import_module("test_harness_mcp_server")
    helper = fixture.HarnessMcpServerTests()
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    td = helper._make_task(str(repo), "TASK__checkpoint")
    checkpoint = importlib.import_module("write_checkpoint")
    path = checkpoint.write_checkpoint(td)
    saved = Path(path).read_text()
    assert "receipt" not in saved.lower()
    assert "complete independent review and QA" in saved
    assert "run WBS checks and close" in saved
    assert "run task_close" not in saved


def test_shipped_runtime_rules_expose_only_current_wbs_actions():
    rules = (Path(__file__).resolve().parents[1] / "plugin/CLAUDE.md").read_text()
    tool_section = rules.split("## 2. MCP tools\n", 1)[1].split("## 3.", 1)[0]
    assert "sole public MCP tool is `wbs`" in tool_section
    for action in ("open", "context", "plan", "check", "next", "dispatch", "verify", "close", "block", "migrate"):
        assert f'wbs(action="{action}")' in tool_section
    assert not re.search(r"\b(?:task_(?:start|context|verify|close|blocked)|write_plan)\b", rules)
    assert "Goal child" not in rules
    assert "fresh_run" in rules
    assert "empty-stream" not in rules


def test_generated_routing_uses_same_wbs_and_preserves_user_content():
    setup = importlib.import_module("setup_finalize")
    user_before = "# Project rules\nKeep user decisions.\n\n"
    user_after = "\n## Local conventions\nUser-owned text.\n"
    original = user_before + "## Harness routing\n<!-- harness:routing-injected -->\nOld generated routing\n<!-- /harness:routing-injected -->\n" + user_after
    rendered = setup._with_routing_block(original)
    assert rendered.startswith(user_before) and rendered.endswith(user_after)
    assert rendered.count(setup.ROUTING_MARKER) == 1
    assert setup._with_routing_block(rendered) == rendered
    assert "Old generated routing" not in rendered
    assert "opens/resumes one WBS" in rendered
    assert "resume the same WBS" in rendered
    assert 'wbs(action="verify")' in rendered and 'wbs(action="close")' in rendered
    assert "Goal child" not in rendered and "sync/create Goal" not in rendered
    assert "task_verify" not in rendered


def test_current_operational_docs_use_supported_recovery_actions():
    repo = Path(__file__).resolve().parents[1]
    readme = (repo / "README.md").read_text()
    guide = (repo / "doc/harness/GUIDE__how-harness-works.md").read_text()
    assert "Goal executor" not in readme and "selecting the next child" not in readme
    assert "goal_storage_root" not in guide and "goal-control-mcp" not in guide
    faq = guide.split("문제 해결 FAQ", 1)[1].split("\n## ", 1)[0]
    # Private error prefixes such as `task_start refused ...` remain accurate.
    # Bare retired operation names followed by Korean call/route particles
    # instead prescribe unavailable public operations in this recovery section.
    assert not re.search(r"`(?:task_(?:start|context|verify|close|blocked)|write_plan)(?:\([^`]*\))?`(?:를|을|로|나)", faq)
    for action in ("open", "context", "plan", "verify", "block"):
        assert f'wbs(action="{action}")' in faq
    assert "fresh_run" in faq
    assert "NON-ATTESTING" not in faq
    assert "정말 작은 작업이면 `execution_mode" not in faq
