"""Exercise project admission through the fresh public MCP stdio entrypoint."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

try:
    import pytest
except ModuleNotFoundError as error:
    if error.name != "pytest":
        raise
    from unittest import SkipTest
    raise SkipTest("pytest is required to collect this module") from error


SERVER = Path(__file__).resolve().parents[1] / "plugin/mcp/harness_server.py"


def _env(home: Path) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("HARNESS_", "CLAUDE_", "CODEX_"))}
    env.update(HOME=str(home), CODEX_HOME=str(home / ".codex"),
               PYTHONDONTWRITEBYTECODE="1")
    return env


def _call(repo: Path, home: Path, arguments: dict) -> dict:
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-11-25", "capabilities": {},
            "clientInfo": {"name": "Claude Code", "version": "activation-test"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "wbs", "arguments": arguments}},
    ]
    result = subprocess.run([sys.executable, "-B", str(SERVER)], cwd=repo,
                            input="".join(json.dumps(row) + "\n" for row in requests),
                            capture_output=True, text=True, env=_env(home), timeout=15)
    assert result.returncode == 0, result.stderr
    replies = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    assert [row["id"] for row in replies] == [1, 2], result.stdout
    assert replies[0]["result"]["serverInfo"]["name"] == "harness"
    return replies[1]["result"]


def _tree(root: Path) -> dict[str, tuple[str, str | bytes]]:
    return {
        path.relative_to(root).as_posix(): (
            ("symlink", os.readlink(path)) if path.is_symlink() else
            ("file", path.read_bytes()) if path.is_file() else ("directory", ""))
        for path in root.rglob("*")
    }


def _enable(root: Path) -> None:
    manifest = root / "doc/harness/manifest.yaml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("version: 5\ntype: cli\nlanguages: [python]\n", encoding="utf-8")


@pytest.mark.parametrize("kind", ["plain", "git", "nested-git"])
@pytest.mark.parametrize("arguments", [
    {"action": "open", "objective": "No automatic project initialization"},
    {"action": "migrate", "apply": False},
    {"action": "migrate", "apply": True},
])
def test_public_wbs_refuses_uninitialized_projects_before_any_effects(
    tmp_path: Path, kind: str, arguments: dict,
):
    home = tmp_path / "home"
    home.mkdir()
    if kind == "nested-git":
        outer = tmp_path / "outer"
        _enable(outer)
        # A legitimate outer WBS exists; it never initializes a nested checkout.
        opened = _call(outer, home, {"action": "open", "objective": "Outer initialized project"})
        assert not opened.get("isError"), opened
        repo = outer / "nested"
    else:
        repo = tmp_path / "project"
    repo.mkdir()
    if kind != "plain":
        subprocess.run(["git", "init", "--quiet", str(repo)], check=True,
                       capture_output=True, env=_env(home))
    before = _tree(tmp_path)
    response = _call(repo, home, arguments)
    assert response.get("isError") is True, response
    message = " ".join(part.get("text", "") for part in response.get("content", []))
    assert "not initialized" in message.lower(), message
    assert "harness:setup" in message, message
    assert _tree(tmp_path) == before


@pytest.mark.parametrize("alias", [False, True])
def test_public_open_supports_initialized_non_git_project_and_directory_alias(
    tmp_path: Path, alias: bool,
):
    root = tmp_path / "project"
    _enable(root)
    subdirectory = root / "src"
    subdirectory.mkdir()
    if alias:
        link = tmp_path / "alias"
        link.symlink_to(root, target_is_directory=True)
        subdirectory = link / "src"
    home = tmp_path / "home"
    home.mkdir()
    result = _call(subdirectory, home, {"action": "open", "objective": "Initialized positive"})
    assert not result.get("isError"), result
    assert list((root / "doc/harness/tasks").glob("TASK__*/TASK.json"))


@pytest.mark.parametrize("component", ["manifest", "control-directory"])
def test_public_wbs_rejects_manifest_alias_without_writing_through_it(
    tmp_path: Path, component: str,
):
    original = tmp_path / "original"
    _enable(original)
    project = tmp_path / "project"
    (project / "doc").mkdir(parents=True)
    if component == "manifest":
        (project / "doc/harness").mkdir()
        (project / "doc/harness/manifest.yaml").symlink_to(original / "doc/harness/manifest.yaml")
    else:
        (project / "doc/harness").symlink_to(original / "doc/harness", target_is_directory=True)
    home = tmp_path / "home"
    home.mkdir()
    before = _tree(tmp_path)
    response = _call(project, home, {"action": "open", "objective": "Manifest alias is not setup"})
    assert response.get("isError") is True, response
    message = " ".join(part.get("text", "") for part in response.get("content", []))
    assert "symlink" in message.lower(), message
    assert _tree(tmp_path) == before


@pytest.mark.parametrize("outer_enabled", [False, True])
def test_public_wbs_keeps_nearest_initialized_project_inside_ancestor_git(
    tmp_path: Path, outer_enabled: bool,
):
    home = tmp_path / "home"
    home.mkdir()
    outer = tmp_path / "outer"
    outer.mkdir()
    subprocess.run(["git", "init", "--quiet", str(outer)], check=True,
                   capture_output=True, env=_env(home))
    if outer_enabled:
        _enable(outer)
    project = outer / "child"
    _enable(project)
    subdirectory = project / "src"
    subdirectory.mkdir()
    result = _call(subdirectory, home, {"action": "open", "objective": "Child owns this work"})
    assert not result.get("isError"), result
    assert list((project / "doc/harness/tasks").glob("TASK__*/TASK.json"))
    assert not (outer / "doc/harness/tasks").exists()


@pytest.mark.parametrize('kind', ['plain', 'git'])
@pytest.mark.parametrize('action', ['open', 'migrate', 'verify'])
def test_public_setup_recovery_identifies_project_initialization(tmp_path: Path, kind: str, action: str):
    home = tmp_path / 'home'
    home.mkdir()
    repo = tmp_path / 'project'
    repo.mkdir()
    if kind == 'git':
        subprocess.run(['git', 'init', '--quiet', str(repo)], check=True,
                       capture_output=True, env=_env(home))
    before = _tree(tmp_path)
    arguments = dict(action=action)
    if action != 'migrate':
        arguments['wbs_id'] = 'audit'
    response = _call(repo, home, arguments)
    assert response.get('isError') is True
    error = response['structuredContent']
    assert error['error_code'] == 'HARNESS_NOT_INITIALIZED'
    assert error['field'] == 'project_setup' and error['reason'] == 'setup_required'
    assert 'manifest.yaml' in error['expected']
    assert 'harness:setup' in error['next_action'] and 'selector' not in error['next_action']
    assert _tree(tmp_path) == before
