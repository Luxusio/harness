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


def test_public_lifecycle_uses_one_wbs_identity_and_control(tmp_path):
    from test_wbs_input_policy import public, policy_plan
    from test_wbs import node
    from test_managed_wbs import host_snapshot
    repo, td, call, opened = public(tmp_path)
    assert td == repo / "doc/harness/wbs/WBS__sample"
    assert opened["wbs_id"] == "WBS__sample"
    assert set(json.loads((td / "WBS.control.json").read_text())) == {
        "run_id", "execution_mode", "required_lenses", "closed"}
    assert not (td / "TASK.json").exists()
    assert not call("plan", plan=policy_plan([node()], {"source": ["src.txt"]})).get("isError")
    assigned = call("dispatch", operation="prepare", ownership={"A": ["src.txt"]}, host=host_snapshot())
    assert assigned["structuredContent"]["route"] == "direct", assigned
    for action, extra in (("context", {}), ("next", {}), ("check", {"node_id": "A"}),
                          ("verify", {}), ("close", {})):
        result = call(action, **extra)
        assert not result.get("isError"), result
        body = result["structuredContent"]
        assert not ({"task_id", "task_dir", "task_context"} & set(body))
        if "context" in body:
            assert body["context"]["wbs_id"] == opened["wbs_id"]
        if "run_id" in body:
            assert body["run_id"] == opened["run_id"]
    assert json.loads((td / "WBS.control.json").read_text())["closed"] is True


def test_retired_identifiers_require_explicit_migration(tmp_path):
    from test_wbs_input_policy import public
    repo, td, call, opened = public(tmp_path)
    from test_harness_mcp_server import HarnessMcpServerTests
    for selector in ("TASK__sample", "GOAL__sample", "WBS__TASK__sample"):
        result = HarnessMcpServerTests()._call_in_repo(str(repo), "wbs", {
            "action": "context", "wbs_id": selector})
        assert result.get("isError"), result
        assert 'migrate' in result["structuredContent"]["error"]
    assert not (repo / "doc/harness/tasks").exists()


def test_raw_retired_goal_intent_is_imported_without_creating_authority(tmp_path):
    from test_wbs_input_policy import public
    repo, td, call, opened = public(tmp_path)
    legacy = repo / "doc/harness/goals"
    legacy.mkdir()
    raw = json.dumps({"goal_id": "GOAL__old", "objective": "Old intent", "tasks": [
        {"task_id": "TASK__old", "title": "Historical claim", "status": "closed"}]}).encode()
    (legacy / "GOAL__old.json").write_bytes(raw)
    assert call("context").get("isError")
    from test_harness_mcp_server import HarnessMcpServerTests
    result = HarnessMcpServerTests()._call_in_repo(str(repo), "wbs", {"action": "migrate", "apply": True})
    assert not result.get("isError"), result
    import goal_migration
    assert (repo / goal_migration.ARCHIVE / "original/doc/harness/goals/GOAL__old.json").read_bytes() == raw
    assert not (repo / "doc/harness/wbs/WBS__old/WBS.control.json").exists()
    assert not call("context").get("isError")


def test_retained_closed_identity_requires_deliberate_new_generation(tmp_path):
    from test_harness_mcp_server import HarnessMcpServerTests, initialize_fixture_project
    from _lib import new_uuid7
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    initialize_fixture_project(repo)
    old = repo / "doc/harness/tasks/TASK__past"
    old.mkdir(parents=True)
    control = dict(run_id=new_uuid7(), execution_mode="standard",
                   required_lenses=["review-code", "qa-cli"], closed=True)
    raw = json.dumps(control).encode()
    (old / "TASK.json").write_bytes(raw)
    server = HarnessMcpServerTests()
    result = server._call_in_repo(str(repo), "wbs", dict(action="open", wbs_id="WBS__past"))
    assert result.get("isError"), result
    assert result["structuredContent"]["error_code"] == "wbs_migration_required"
    assert not (repo / "doc/harness/wbs/WBS__past").exists()
    result = server._call_in_repo(str(repo), "wbs", dict(action="migrate", apply=True))
    assert not result.get("isError"), result
    result = server._call_in_repo(str(repo), "wbs", dict(action="open", wbs_id="WBS__past"))
    assert result.get("isError"), result
    assert result["structuredContent"]["error_code"] == "historical_wbs_identity"
    result = server._call_in_repo(str(repo), "wbs", dict(action="open", wbs_id="WBS__past", fresh_run=True))
    assert not result.get("isError"), result
    assert result["structuredContent"]["run_id"] != control["run_id"]
    assert not (old / "TASK.json").exists()
    assert (repo / "doc/harness/archive/wbs-namespace-migration/original/doc/harness/tasks/TASK__past/TASK.json").read_bytes() == raw


def test_public_recovery_and_resume_guidance(tmp_path, monkeypatch):
    fixture = importlib.import_module("test_harness_mcp_server")
    server = fixture.harness_server
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / ".gitignore").write_text("doc/harness/wbs/\n")
    fixture.initialize_fixture_project(repo)
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
    td = Path(opened["structuredContent"]["wbs_dir"])
    run = server.read_wbs_control(str(td))["run_id"]
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
    assert "Call wbs_open" not in (td / "BLOCKED.md").read_text()
    assert not call("open").get("isError")
    assert server.read_wbs_control(str(td))["run_id"] == run
    # Preserved-run resume fails atomically when response preparation fails;
    # it does not publish a partially resumed generation or invent another run.
    with mock.patch.object(server, "emit_compact_context", side_effect=RuntimeError("projection unavailable")):
        fallback = call("open")
    assert fallback.get("isError")
    assert "projection unavailable" in fallback["structuredContent"]["error"]
    assert server.read_wbs_control(str(td))["run_id"] == run
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
    td = helper._make_task(str(repo), "WBS__checkpoint")
    checkpoint = importlib.import_module("write_checkpoint")
    path = checkpoint.write_checkpoint(td)
    saved = Path(path).read_text()
    assert "receipt" not in saved.lower()
    assert "complete independent review and QA" in saved
    assert "run WBS checks and close" in saved
    assert "run wbs_close" not in saved


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
    assert "wbs_verify" not in rendered


def test_current_operational_docs_use_supported_recovery_actions():
    repo = Path(__file__).resolve().parents[1]
    readme = (repo / "README.md").read_text()
    guide = (repo / "doc/harness/GUIDE__how-harness-works.md").read_text()
    assert "Goal executor" not in readme and "selecting the next child" not in readme
    assert "goal_storage_root" not in guide and "goal-control-mcp" not in guide
    faq = guide.split("문제 해결 FAQ", 1)[1].split("\n## ", 1)[0]
    # Private error prefixes such as `wbs_open refused ...` remain accurate.
    # Bare retired operation names followed by Korean call/route particles
    # instead prescribe unavailable public operations in this recovery section.
    assert not re.search(r"`(?:task_(?:start|context|verify|close|blocked)|write_plan)(?:\([^`]*\))?`(?:를|을|로|나)", faq)
    for action in ("open", "context", "plan", "verify", "block"):
        assert f'wbs(action="{action}")' in faq
    assert "fresh_run" in faq
    assert "NON-ATTESTING" not in faq
    assert "정말 작은 작업이면 `execution_mode" not in faq
