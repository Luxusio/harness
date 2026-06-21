"""Runtime-specific MCP tool-name contract tests."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
SERVER_PATH = REPO_ROOT / "plugin" / "mcp" / "harness_server.py"


spec = importlib.util.spec_from_file_location("harness_server_tool_contract", SERVER_PATH)
assert spec and spec.loader
harness_server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness_server)


def _read_all(root: Path) -> str:
    parts: list[str] = []
    for path in root.rglob("*"):
        if path.is_file() and path.suffix in {".md", ".py"}:
            parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)


def test_claude_plugin_docs_do_not_use_legacy_harness_mcp_prefix():
    body = _read_all(REPO_ROOT / "plugin")
    assert "mcp__harness__" not in body


def test_claude_plugin_prefixed_tool_names_exist_in_mcp_server():
    body = _read_all(REPO_ROOT / "plugin")
    mentioned = set(re.findall(r"mcp__plugin_harness_harness__([A-Za-z0-9_]+)(?![A-Za-z0-9_*])", body))
    server_tools = {tool["name"] for tool in harness_server.list_tools()}
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
    assert 'agent_type: "default"' in run
    qa_section = run.split("QA subagent pattern on Codex:", 1)[1].split("When the QA lens returns", 1)[0]
    assert "Agent(subagent_type=" not in qa_section
    assert "harness:qa-browser" not in qa_section
    assert "harness:qa-api" not in qa_section
    assert "harness:qa-cli" not in qa_section
