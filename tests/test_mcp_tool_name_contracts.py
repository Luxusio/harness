"""Runtime-specific MCP tool-name contract tests."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
SERVER_PATH = REPO_ROOT / "plugin" / "mcp" / "harness_server.py"


def _read_all(root: Path) -> str:
    parts: list[str] = []
    for path in root.rglob("*"):
        if path.is_file() and path.suffix in {".md", ".py"}:
            parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)


def test_claude_plugin_docs_do_not_use_legacy_harness_mcp_prefix():
    body = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (REPO_ROOT / "plugin").rglob("*.md")
    )
    assert "mcp__harness__" not in body


def test_claude_plugin_prefixed_tool_names_exist_in_mcp_server():
    body = _read_all(REPO_ROOT / "plugin")
    mentioned = set(re.findall(r"mcp__plugin_harness_harness__([A-Za-z0-9_]+)(?![A-Za-z0-9_*])", body))
    server = SERVER_PATH.read_text(encoding="utf-8")
    server_tools = set(re.findall(r'\{"name": "([a-z_]+)"', server))
    unknown = sorted(name for name in mentioned if name not in server_tools)
    assert not unknown


def test_codex_skills_use_bare_tool_names_not_claude_prefixes():
    body = "\n".join([
        _read_all(REPO_ROOT / "plugin-codex" / "skills"),
        _read_all(REPO_ROOT / "plugin-codex" / "internal-skills"),
    ])
    assert "mcp__harness__" not in body
    assert "mcp__plugin_harness_harness__" not in body
    for name in ("task_start", "task_verify", "task_close"):
        assert name in body


def test_codex_qa_docs_do_not_suggest_claude_agent_subagent_type_call_shape():
    run = (REPO_ROOT / "plugin-codex" / "internal-skills" / "run" / "SKILL.md").read_text(encoding="utf-8")
    assert "QA subagent pattern on Codex" in run
    assert "spawn_agent {" in run
    assert 'task_name: "qa_<lens>_<task_slug>_<run_id>"' in run
    qa_section = run.split("QA subagent pattern on Codex:", 1)[1].split("When the QA lens returns", 1)[0]
    assert "Agent(subagent_type=" not in qa_section
    assert "harness:qa-browser" not in qa_section
    assert "harness:qa-api" not in qa_section
    assert "harness:qa-cli" not in qa_section


def test_task_tools_declare_optional_workspace_and_goal_tools_do_not():
    """harness:batch leads target their worktree task through `workspace`."""
    import sys

    if "harness_server" in sys.modules:
        server = sys.modules["harness_server"]
    else:
        spec = importlib.util.spec_from_file_location("harness_server", SERVER_PATH)
        assert spec and spec.loader
        server = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = server
        spec.loader.exec_module(server)
    schemas = {tool["name"]: tool["inputSchema"] for tool in server.TOOL_DEFS}
    assert {tool["name"] for tool in server.list_tools()} == {"wbs"}
    assert schemas["wbs"]["properties"]["workspace"]["type"] == "string"
    for name in ("task_start", "task_context", "write_plan", "task_verify", "task_close", "task_blocked"):
        props = schemas[name]["properties"]
        assert props.get("workspace", {}).get("type") == "string", name
        assert "workspace" not in schemas[name].get("required", []), name
    for name in ("goal_start", "goal_context", "goal_add_task", "goal_next_task", "goal_finish"):
        assert "workspace" not in schemas[name]["properties"], name
